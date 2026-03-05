"""
先暂停，之后给polymarket用
============================================================
BTC 跨交易所价差监测器 v2（含 USDT/USD 汇率修正）
============================================================

目的：
    实时比较 Binance 和 Coinbase 上 BTC 的价格差异，
    并通过 USDT/USD 实时汇率修正，剔除稳定币偏差，
    观察两个交易所之间是否存在真实的定价差异。

数据源（3 条 WebSocket 连接）：
    1. Binance BTC/USDT trade stream  - 每笔 BTC/USDT 成交实时推送
    2. Binance USDC/USDT trade stream - 用于推算 USDT 的美元价值
       （因为 USDC ≈ USD，所以 1/USDCUSDT ≈ USDT 的真实美元价格）
    3. Coinbase BTC/USD ticker        - 每笔 BTC/USD 成交实时推送

工作原理：
    WebSocket 推送层：
        - 交易所只要有一笔成交发生，就会立刻推送给我们
        - BTC 交易量大时，Binance 一秒可能推送几十到上百条消息
        - 每收到一条推送，代码立刻更新内存中的 latest_prices 字典
        - 三条 WebSocket 连接各自独立运行，互不阻塞

    采样记录层：
        - 每 0.5 秒醒来一次，读取 latest_prices 中的当前值
        - 将两个交易所的价格、价差、USDT 汇率等写入 CSV
        - 注意：两次采样之间的所有中间价格变化不会被记录
          例如 0.5 秒内 Binance 推了 50 笔成交，只有最后一笔被采到

    价差计算：
        - 原始价差：直接用 Binance USDT 价格 - Coinbase USD 价格
        - 修正价差：先将 Binance 价格乘以 USDT/USD 汇率换算成真实美元，
          再与 Coinbase 比较，这样能看出扣除 USDT 偏差后的真实价差

输出：
    - 终端实时打印每次采样的价格和价差
    - CSV 文件保存完整记录，包含原始和修正后的价差
    - 运行结束后打印汇总统计（最大/最小价差）

运行方式：
    pip install websockets
    python btc_price_gap_v2.py

============================================================
"""
import asyncio
import websockets
import json
import csv
import time
from datetime import datetime

# --- 配置 ---
DURATION_SECONDS = 60
CSV_FILENAME = 'binance_vs_coinbase_v2.csv'
SAMPLING_RATE = 0.5

# 共享内存
latest_prices = {
    "binance_btcusdt": None,
    "binance_usdtusd": None,
    "coinbase_btcusd": None,
    "binance_btcusdt_updated": 0,
    "binance_usdtusd_updated": 0,
    "coinbase_btcusd_updated": 0
}

async def connect_binance_btcusdt():
    """Binance BTC/USDT 实时成交"""
    uri = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    async for websocket in websockets.connect(uri):
        try:
            print("✅ 已连接 Binance BTC/USDT...")
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if 'p' in data:
                    latest_prices["binance_btcusdt"] = float(data['p'])
                    latest_prices["binance_btcusdt_updated"] = time.time()
        except Exception as e:
            print(f"⚠️ Binance BTC/USDT 断开: {e}")

async def connect_binance_usdtusd():
    """Binance USDT/USD 实时汇率（通过 USDC 交易对近似）"""
    # Binance 没有直接的 USDT/USD，用 USDCUSDT 反推
    # USDCUSDT 价格 = 1 USDC 值多少 USDT
    # 因为 USDC ≈ USD，所以 1/USDCUSDT ≈ USDT 的美元价值
    uri = "wss://stream.binance.com:9443/ws/usdcusdt@trade"
    async for websocket in websockets.connect(uri):
        try:
            print("✅ 已连接 Binance USDC/USDT（用于推算 USDT/USD）...")
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if 'p' in data:
                    usdc_usdt = float(data['p'])
                    # USDT 的美元价值 ≈ 1 / USDC_USDT 价格
                    # 比如 USDC/USDT = 1.001，说明 1 USDT = 1/1.001 ≈ $0.999
                    latest_prices["binance_usdtusd"] = 1.0 / usdc_usdt
                    latest_prices["binance_usdtusd_updated"] = time.time()
        except Exception as e:
            print(f"⚠️ Binance USDC/USDT 断开: {e}")

async def connect_coinbase():
    """Coinbase BTC/USD 实时成交"""
    uri = "wss://ws-feed.exchange.coinbase.com"
    subscribe_msg = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker"]
    }
    async for websocket in websockets.connect(uri):
        try:
            await websocket.send(json.dumps(subscribe_msg))
            print("✅ 已连接 Coinbase BTC/USD...")
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if 'price' in data:
                    latest_prices["coinbase_btcusd"] = float(data['price'])
                    latest_prices["coinbase_btcusd_updated"] = time.time()
        except Exception as e:
            print(f"⚠️ Coinbase 断开: {e}")

async def record_data():
    """主记录循环"""
    print(f"📝 开始记录... 文件名: {CSV_FILENAME}")
    print(f"⏱️ 计划运行: {DURATION_SECONDS} 秒\n")

    start_time = time.time()
    records = 0
    max_raw_diff = 0
    min_raw_diff = float('inf')
    max_adj_diff = 0
    min_adj_diff = float('inf')

    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        fieldnames = [
            'timestamp', 'local_time',
            'binance_btcusdt', 'usdt_usd_rate', 'binance_btcusd_adjusted',
            'coinbase_btcusd',
            'raw_diff_usd', 'raw_diff_percent',
            'adjusted_diff_usd', 'adjusted_diff_percent'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        await asyncio.sleep(3)

        while time.time() - start_time < DURATION_SECONDS:
            bn_btcusdt = latest_prices["binance_btcusdt"]
            usdt_rate = latest_prices["binance_usdtusd"]
            cb_price = latest_prices["coinbase_btcusd"]

            if bn_btcusdt and cb_price:
                now = time.time()
                local_time_str = datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]

                # 未修正的价差（直接 USDT 当 USD）
                raw_diff = bn_btcusdt - cb_price
                raw_diff_pct = (raw_diff / bn_btcusdt) * 100

                # USDT 汇率修正后的价差
                if usdt_rate:
                    bn_adjusted = bn_btcusdt * usdt_rate
                else:
                    bn_adjusted = bn_btcusdt  # 还没收到汇率就先不修正
                    usdt_rate = 1.0

                adj_diff = bn_adjusted - cb_price
                adj_diff_pct = (adj_diff / bn_adjusted) * 100

                # 统计
                abs_raw = abs(raw_diff)
                abs_adj = abs(adj_diff)
                max_raw_diff = max(max_raw_diff, abs_raw)
                max_adj_diff = max(max_adj_diff, abs_adj)
                if abs_raw > 0:
                    min_raw_diff = min(min_raw_diff, abs_raw)
                if abs_adj > 0:
                    min_adj_diff = min(min_adj_diff, abs_adj)
                records += 1

                # 打印
                print(
                    f"[{local_time_str}] "
                    f"BN: ${bn_btcusdt:,.2f} USDT | "
                    f"USDT≈${usdt_rate:.5f} | "
                    f"BN修正: ${bn_adjusted:,.2f} | "
                    f"CB: ${cb_price:,.2f} | "
                    f"原始差: ${raw_diff:+.2f} | "
                    f"修正差: ${adj_diff:+.2f} ({adj_diff_pct:+.4f}%)"
                )

                writer.writerow({
                    'timestamp': now,
                    'local_time': local_time_str,
                    'binance_btcusdt': bn_btcusdt,
                    'usdt_usd_rate': round(usdt_rate, 6),
                    'binance_btcusd_adjusted': round(bn_adjusted, 2),
                    'coinbase_btcusd': cb_price,
                    'raw_diff_usd': round(raw_diff, 2),
                    'raw_diff_percent': round(raw_diff_pct, 5),
                    'adjusted_diff_usd': round(adj_diff, 2),
                    'adjusted_diff_percent': round(adj_diff_pct, 5)
                })

            await asyncio.sleep(SAMPLING_RATE)

    # 汇总
    print(f"\n{'='*60}")
    print(f"📊 汇总统计")
    print(f"{'='*60}")
    print(f"总记录数:       {records}")
    print(f"--- 未修正（USDT当USD）---")
    print(f"  最大价差:     ${max_raw_diff:.2f}")
    print(f"  最小价差:     ${min_raw_diff:.2f}")
    print(f"--- USDT汇率修正后 ---")
    print(f"  最大价差:     ${max_adj_diff:.2f}")
    print(f"  最小价差:     ${min_adj_diff:.2f}")
    print(f"数据已保存到:   {CSV_FILENAME}")
    print(f"{'='*60}")

async def main():
    await asyncio.gather(
        connect_binance_btcusdt(),
        connect_binance_usdtusd(),
        connect_coinbase(),
        record_data()
    )

if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=DURATION_SECONDS + 10))
    except (asyncio.TimeoutError, KeyboardInterrupt):
        print("\n🛑 程序停止。")
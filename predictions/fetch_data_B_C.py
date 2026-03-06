import asyncio
import websockets
import json
import csv
import time
from datetime import datetime

# --- 配置 ---
DURATION_SECONDS = 60  # 运行1分钟
CSV_FILENAME = 'price_gap.csv'

# 共享变量，用于存储两边的最新价格
latest_prices = {
    "binance": None,
    "coinbase": None
}

async def connect_binance():
    """连接币安 Global WebSocket (需要非美IP)"""
    uri = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ 已连接 Binance...")
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                # 'p' 是价格 (Price)
                price = float(data['p'])
                latest_prices["binance"] = price
    except Exception as e:
        print(f"❌ Binance 连接断开 (可能是IP被封): {e}")

async def connect_coinbase():
    """连接 Coinbase WebSocket"""
    uri = "wss://ws-feed.exchange.coinbase.com"
    subscribe_msg = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker"]
    }
    try:
        async with websockets.connect(uri) as websocket:
            await websocket.send(json.dumps(subscribe_msg))
            print("✅ 已连接 Coinbase...")
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                if 'price' in data:
                    price = float(data['price'])
                    latest_prices["coinbase"] = price
    except Exception as e:
        print(f"❌ Coinbase 连接断开: {e}")

async def record_data():
    """主记录循环：每当价格更新，写入CSV"""
    print(f"📝 开始记录数据 {DURATION_SECONDS} 秒...")
    
    start_time = time.time()
    
    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        fieldnames = ['timestamp', 'local_time', 'binance_price', 'coinbase_price', 'spread', 'spread_percent']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # 只要时间没到，就一直记录
        while time.time() - start_time < DURATION_SECONDS:
            b_price = latest_prices["binance"]
            c_price = latest_prices["coinbase"]
            
            # 只有当两个交易所都有数据时才记录
            if b_price is not None and c_price is not None:
                spread = b_price - c_price
                spread_percent = (spread / c_price) * 100
                
                # 获取当前精确时间
                now = time.time()
                local_time_str = datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]
                
                # 打印到控制台 (每0.5秒打印一次，避免刷屏太快)
                # 这里为了演示效果，我们简单打印
                print(f"[{local_time_str}] Binance: {b_price} | Coinbase: {c_price} | 差价: ${spread:.2f}")
                
                writer.writerow({
                    'timestamp': now,
                    'local_time': local_time_str,
                    'binance_price': b_price,
                    'coinbase_price': c_price,
                    'spread': round(spread, 2),
                    'spread_percent': round(spread_percent, 5)
                })
            
            # 极其快速的采样 (10ms)，捕捉每一个微小波动
            await asyncio.sleep(0.01)

    print(f"\n✅ 任务完成！数据已保存到 {CSV_FILENAME}")

async def main():
    # 并发运行三个任务：连接币安、连接Coinbase、记录数据
    await asyncio.gather(
        connect_binance(),
        connect_coinbase(),
        record_data()
    )

if __name__ == "__main__":
    try:
        # 运行主程序，设置超时自动停止
        asyncio.run(asyncio.wait_for(main(), timeout=DURATION_SECONDS + 5))
    except asyncio.TimeoutError:
        print("⏰ 时间到，程序停止。")
    except KeyboardInterrupt:
        print("🛑 用户手动停止。")
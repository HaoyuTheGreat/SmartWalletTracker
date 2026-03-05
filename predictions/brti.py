"""
============================================================
BRTI 复刻模型准确率验证系统
============================================================

目标：
    自己搭建一个近似的 BRTI（CME CF Bitcoin Real Time Index），
    然后用 Kalshi 公开的真实 BRTI 数据来验证准确率。
    如果误差足够小，未来实盘可以信任自己的数据源，
    在速度上获得优势。
    
    自己猜测：kalshi的btc实时价格的这一秒有可能是前X秒的平均值之类的。

背景：
    Kalshi 的 BTC 15 分钟合约用 BRTI 来结算。
    BRTI 是 CF Benchmarks 每秒从多个成分交易所的订单簿数据
    聚合计算出来的比特币美元价格。
    成分交易所包括：Coinbase, Kraken, Bitstamp, Gemini,
    itBit, LMAX Digital, Bullish, Crypto.com。
    结算方式：合约到期前最后 60 秒的 BRTI 平均值，
    与开盘价比较，判定涨跌。

我要做的事：
    1. 接入成分交易所的 WebSocket 订单簿数据（先从 Coinbase 和 Kraken 开始）
    2. 按照 BRTI 的方法论（取各交易所 mid-price → 算中位数 → 剔除偏离 25% 以上的异常值 → 加权聚合）
       自己计算出一个近似 BRTI 价格，每秒记录一次
    3. 同时定时拉取 Kalshi 公开的 S3 数据作为 ground truth：
       https://kalshi-public-docs.s3.amazonaws.com/external/crypto/btc_current.json
       其中 timeseries.second 数组包含最近 60 秒的真实 BRTI 价格
    4. 逐秒对比我的价格和真实 BRTI，计算偏差

验证标准：
    - 每秒偏差稳定在 $5 以内 → 模型可信，可用于实盘
    - 偏差经常超过 $20-$30 → 需要调整聚合逻辑
    - 重点关注：偏差在合约最后 60 秒是否会放大（因为这段决定结算）

S3 数据说明：
    - 公开免费，不需要认证，直接 curl 即可获取
    - 返回当前 15 分钟合约的信息，包括：
      * maturity_ts_ms  — 合约到期时间戳
      * candlesticks    — 不同时间框架的 OHLC 数据
      * timeseries.second — 最近 60 秒的 BRTI 价格（这是 ground truth）
    - 注意：S3 数据有几秒延迟，不适合实盘，仅用于验证

长期计划：
    验证通过后，实盘时直接用自己的实时 WebSocket 数据做交易决策，
    不依赖 S3 或 Kalshi 页面，速度上比其他用户更快。

============================================================
"""
"""
BRTI 成分交易所 WebSocket 连接
先接四家：Coinbase, Kraken, Bitstamp, Gemini

"""

import asyncio
import json
import time
from datetime import datetime

import websockets

async def coinbase():
    uri = "wss://ws-feed.exchange.coinbase.com"
    async with websockets.connect(uri, max_size = 10_000_000) as ws:
        sub = {
            "type": "subscribe",
            "channels": [{"name": "level2_batch", "product_ids": ["BTC-USD"]}]
        }
        await ws.send(json.dumps(sub))
        print("[Coinbase] Connected!")
        msg = await ws.recv()  # subscriptions 确认
        msg = await ws.recv()  # 第一条 ticker 数据
        print(f"[Coinbase] First message: {msg}")

async def main():
    await asyncio.gather(coinbase())
    print("\n coinbase connected")

if __name__ == "__main__":
    asyncio.run(main())
"""
fetch_sol_prices.py - 抓取 SOL/USDT 每日收盘价写入 BigQuery sol_prices 表

数据源：Binance public klines API (免费，无需 key)
存储：BigQuery `whale_tracker.sol_prices` (通过 bq.upsert_sol_prices MERGE 去重)

增量策略：
  读取 BQ 里已有最新日期，只从那天开始往后拉 Binance 数据，避免重复抓 1400+ 天历史。
  首次运行（BQ 空表）时 start_time=0，抓全量历史。
"""

import time
from datetime import datetime, timezone

import requests

from lib import bq


def _latest_price_date_ms() -> int:
    """返回 BQ sol_prices 里最新一天的 epoch ms。空表返回 0 (抓全量)。"""
    price_map = bq.fetch_sol_price_map()
    if not price_map:
        return 0
    latest_date_str = max(price_map.keys())
    dt = datetime.strptime(latest_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_sol_prices():
    """
    Fetch daily SOL/USDT close prices from Binance public klines API.
    Free, no API key required. Paginates forward from the latest date already in BQ.
    """
    url = "https://api.binance.us/api/v3/klines"
    prices = {}
    start_time = _latest_price_date_ms()
    if start_time:
        print(f"Incremental fetch starting from {datetime.fromtimestamp(start_time/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    else:
        print("Empty sol_prices table. Fetching full history from Binance.")

    while True:
        params = {
            "symbol": "SOLUSD",
            "interval": "1d",
            "startTime": start_time,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        klines = resp.json()
        if not klines:
            break

        for k in klines:
            # kline: [open_time, open, high, low, close, volume, close_time, ...]
            open_time_ms = k[0]
            close_price = float(k[4])
            date_str = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            prices[date_str] = round(close_price, 4)

        if len(klines) < 1000:
            break
        # Advance past the last returned candle (open_time + 1 day)
        start_time = klines[-1][0] + 24 * 60 * 60 * 1000
        time.sleep(0.2)

    if not prices:
        print("No new prices to upsert.")
        return

    bq.upsert_sol_prices(prices)
    print(f"Upserted {len(prices)} daily SOL prices into BigQuery sol_prices table")
    print(f"Range: {min(prices)} to {max(prices)}")


if __name__ == "__main__":
    fetch_sol_prices()

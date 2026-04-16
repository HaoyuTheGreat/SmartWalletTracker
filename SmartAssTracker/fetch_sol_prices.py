import json
import os
import time
from datetime import datetime, timezone
import requests


def fetch_sol_prices():
    """
    Fetch daily SOL/USDT close prices from Binance public klines API.
    Free, no API key required. Paginates forward from SOL/USDT listing date.
    """
    url = "https://api.binance.us/api/v3/klines"
    prices = {}
    start_time = 0  # epoch ms; Binance returns from earliest available

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

    os.makedirs("data", exist_ok=True)
    output_path = "data/sol_price_history.json"
    with open(output_path, "w") as f:
        json.dump(prices, f, indent=2)
    print(f"Saved {len(prices)} daily SOL prices to {output_path}")
    print(f"Range: {min(prices)} to {max(prices)}")


if __name__ == "__main__":
    fetch_sol_prices()

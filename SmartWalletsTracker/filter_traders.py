"""
filter_traders.py - 基于 BQ raw_swaps + analyzed_swaps 做启发式分类
                   结果写入 wallet_classifications 表 (append-only, 保留历史)

流程：
  1. 从 BQ wallets 拿所有钱包 (address 用于 proxy / market_maker 判断)
  2. 对每个钱包：
     - 拉 raw_swaps 做 proxy_bot / high_frequency / market_maker 标签
     - 拉 analyzed_swaps 做持仓聚合 + 胜率 / PnL 计算
     - 符合条件则打上 smart_candidate 标签
  3. 全部钱包一次性 append 到 wallet_classifications (每次运行多一版历史快照)

wallet_classifications 是 append-only 的 —— 每次跑都是一版新快照。
这样以后可以查 "这个钱包上周和本周的分类有什么变化"。
"""

from datetime import datetime, timezone

from lib import bq

SOL_PRICE_USD_FALLBACK = 150

STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
WSOL_MINT = "So11111111111111111111111111111111111111112"
EXCLUDED_MINTS = STABLE_MINTS | {WSOL_MINT}

EXCLUSION_TAGS = {"proxy_bot", "high_frequency", "insufficient_data", "market_maker"}


def is_proxy_transaction(tx, wallet_address):
    """Proxy/bot tx: wallet not in tokenTransfers and no events.swap — only signed, bot did the rest."""
    token_transfers = tx.get("tokenTransfers", [])
    wallet_in_transfers = any(
        tt.get("fromUserAccount") == wallet_address or tt.get("toUserAccount") == wallet_address
        for tt in token_transfers
    )
    has_swap_event = bool(tx.get("events", {}).get("swap", {}))
    return not wallet_in_transfers and not has_swap_event


def calc_daily_frequency(swaps):
    timestamps = [tx.get("timestamp", 0) for tx in swaps]
    first_trade = min(timestamps)
    last_trade = max(timestamps)
    active_days = max((last_trade - first_trade) / 86400, 1)
    daily_frequency = len(swaps) / active_days
    return daily_frequency, active_days


def is_market_maker(swaps, wallet_address):
    """High frequency + symmetric buy/sell on a dominant token → market maker."""
    daily_frequency, _ = calc_daily_frequency(swaps)

    token_buys = {}
    token_sells = {}
    for tx in swaps:
        for tt in tx.get("tokenTransfers", []):
            mint = tt.get("mint", "")
            if tt.get("toUserAccount") == wallet_address:
                token_buys[mint] = token_buys.get(mint, 0) + 1
            elif tt.get("fromUserAccount") == wallet_address:
                token_sells[mint] = token_sells.get(mint, 0) + 1

    all_tokens = set(token_buys.keys()) | set(token_sells.keys())
    if not all_tokens:
        return False, {"buy_sell_ratio": 0.0, "top_token_pct": 0.0, "unique_tokens": 0}

    token_activity = {m: token_buys.get(m, 0) + token_sells.get(m, 0) for m in all_tokens}
    top_token = max(token_activity, key=token_activity.get)
    top_buys = token_buys.get(top_token, 0)
    top_sells = token_sells.get(top_token, 0)

    if max(top_buys, top_sells) > 0:
        buy_sell_ratio = min(top_buys, top_sells) / max(top_buys, top_sells)
    else:
        buy_sell_ratio = 0

    total_activity = sum(token_activity.values())
    top_token_pct = token_activity[top_token] / total_activity * 100 if total_activity else 0

    stats = {
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "top_token_pct": round(top_token_pct, 1),
        "unique_tokens": len(all_tokens),
    }
    is_mm = daily_frequency > 10 and buy_sell_ratio > 0.5 and top_token_pct > 40
    return is_mm, stats


def aggregate_by_token(analyzed_swaps):
    """
    Group swaps into per-token positions. Stablecoins are folded into virtual SOL;
    WSOL is excluded. analyzed_swaps rows come from BQ so token_spent/token_received
    are ARRAY<STRUCT> - we treat them as lists of dicts.
    """
    positions = {}

    def get_pos(mint, symbol):
        if mint not in positions:
            positions[mint] = {
                "symbol": symbol,
                "bought": 0.0,
                "sold": 0.0,
                "sol_in": 0.0,
                "sol_out": 0.0,
            }
        return positions[mint]

    for swap in analyzed_swaps:
        sol_spent = swap.get("sol_spent") or 0
        sol_received = swap.get("sol_received") or 0
        token_spent = swap.get("token_spent") or []
        token_received = swap.get("token_received") or []
        sol_price = swap.get("sol_price_usd") or SOL_PRICE_USD_FALLBACK

        virtual_in = sol_spent
        virtual_out = sol_received
        non_stable_spent = []
        non_stable_received = []

        for t in token_spent:
            if t["mint"] in STABLE_MINTS:
                virtual_in += t["amount"] / sol_price
            elif t["mint"] not in EXCLUDED_MINTS:
                non_stable_spent.append(t)

        for t in token_received:
            if t["mint"] in STABLE_MINTS:
                virtual_out += t["amount"] / sol_price
            elif t["mint"] not in EXCLUDED_MINTS:
                non_stable_received.append(t)

        if non_stable_received:
            sol_per_buy = virtual_in / len(non_stable_received)
            for t in non_stable_received:
                pos = get_pos(t["mint"], t["symbol"])
                pos["bought"] += t["amount"]
                pos["sol_in"] += sol_per_buy

        if non_stable_spent:
            sol_per_sell = virtual_out / len(non_stable_spent)
            for t in non_stable_spent:
                pos = get_pos(t["mint"], t["symbol"])
                pos["sold"] += t["amount"]
                pos["sol_out"] += sol_per_sell

    return positions


def calc_performance(positions):
    closed = []
    inflated_count = 0
    for p in positions.values():
        if p["bought"] <= 0:
            continue
        if p["sold"] >= p["bought"] * 0.95:
            pnl = p["sol_out"] - p["sol_in"]
            closed.append({"pnl_sol": pnl, "win": pnl > 0})
            if p["sold"] > p["bought"] * 1.1:
                inflated_count += 1

    total = len(closed)
    if total == 0:
        return {
            "closed_positions": 0,
            "win_rate": 0.0,
            "total_pnl_sol": 0.0,
            "avg_pnl_sol": 0.0,
            "inflated_positions": 0,
        }

    wins = sum(1 for c in closed if c["win"])
    total_pnl = sum(c["pnl_sol"] for c in closed)
    return {
        "closed_positions": total,
        "win_rate": round(wins / total * 100, 1),
        "total_pnl_sol": round(total_pnl, 3),
        "avg_pnl_sol": round(total_pnl / total, 3),
        "inflated_positions": inflated_count,
    }


def classify_wallet(wallet_id, address, raw_swaps, analyzed_swaps):
    """Compute tags + stats for a single wallet."""
    tags = []

    proxy_count = sum(1 for tx in raw_swaps if is_proxy_transaction(tx, address))
    proxy_pct = proxy_count / len(raw_swaps) * 100 if raw_swaps else 0
    if proxy_pct > 50:
        tags.append("proxy_bot")

    daily_frequency, active_days = calc_daily_frequency(raw_swaps)
    if daily_frequency > 20:
        tags.append("high_frequency")
    if len(raw_swaps) < 20 or active_days < 7:
        tags.append("insufficient_data")

    mm_result, mm_stats = is_market_maker(raw_swaps, address)
    if mm_result:
        tags.append("market_maker")

    positions = aggregate_by_token(analyzed_swaps)
    perf = calc_performance(positions)

    if perf["inflated_positions"] > 0:
        tags.append("data_clipped")

    is_excluded = any(t in EXCLUSION_TAGS for t in tags)
    if (
        not is_excluded
        and perf["closed_positions"] >= 5
        and perf["win_rate"] > 50
        and perf["total_pnl_sol"] > 0
    ):
        tags.append("smart_candidate")

    return {
        "wallet_id": wallet_id,
        "tags": tags,
        "total_swaps": len(raw_swaps),
        "active_days": round(active_days, 1),
        "daily_frequency": round(daily_frequency, 1),
        "proxy_pct": round(proxy_pct, 1),
        **mm_stats,
        **perf,
    }


def main():
    # Batch-fetch once: 2 BQ queries total (vs 122 per-wallet round-trips)
    wallets = bq.fetch_all_wallets()
    all_raw = bq.fetch_raw_swaps_all_wallets()
    all_analyzed = bq.fetch_analyzed_swaps_all_wallets()
    classified_at = datetime.now(timezone.utc).isoformat()

    print(f"Classifying {len(wallets)} wallets | raw_swaps: {sum(len(v) for v in all_raw.values())} | "
          f"analyzed: {sum(len(v) for v in all_analyzed.values())}")

    rows = []
    for w in wallets:
        wallet_id = w["wallet_id"]
        address = w["address"]

        raw_swaps = all_raw.get(wallet_id, [])
        if not raw_swaps:
            continue
        analyzed_swaps = all_analyzed.get(wallet_id, [])

        result = classify_wallet(wallet_id, address, raw_swaps, analyzed_swaps)
        row = {"classified_at": classified_at, **result}
        rows.append(row)

        tag_str = ", ".join(result["tags"]) if result["tags"] else "unclassified"
        print(
            f"[{wallet_id}] {tag_str} | proxy: {result['proxy_pct']}% | "
            f"freq: {result['daily_frequency']}/day | days: {result['active_days']} | "
            f"swaps: {result['total_swaps']} | closed: {result['closed_positions']} | "
            f"win_rate: {result['win_rate']}% | pnl: {result['total_pnl_sol']} SOL"
        )

    if rows:
        bq.insert_classifications(rows)
        smart = sum(1 for r in rows if "smart_candidate" in r["tags"])
        print(f"\nClassified {len(rows)} wallets ({smart} smart candidates) → BQ wallet_classifications")


if __name__ == "__main__":
    main()

"""
filter_traders.py - Heuristic classification on top of BQ raw_swaps + analyzed_swaps.
                   Results are written to wallet_classifications (append-only, history kept).

Flow:
  1. Read all wallets from BQ (address is needed for proxy / market-maker checks).
  2. For each wallet:
     - Use raw_swaps to assign proxy_bot / high_frequency / market_maker tags.
     - Use analyzed_swaps to aggregate positions and compute win rate / PnL.
     - Assign smart_candidate when thresholds are met.
  3. Append every wallet's row to wallet_classifications in a single batch insert
     (each run produces one new historical snapshot).

wallet_classifications is append-only — every run produces a new snapshot, which
lets us later answer "how did this wallet's classification change between last week and this week".
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


# Wallets per fetch batch. Bounds peak memory: one batch's raw_json instead
# of the whole pending set. 25 wallets × ~150 swaps × ~17KB raw_json ≈ 65MB
# JSON (~200MB as Python objects) per batch — comfortable inside 8Gi even if
# a batch happens to contain several 2000-swap heavy wallets.
#
# Why this exists (2026-06 OOM): the previous version fetched raw + analyzed
# swaps for ALL pending wallets in one shot. Fine when the daily increment is
# ~20-50 wallets, fatal when a prior failure leaves the whole fleet pending
# (~1400 wallets ≈ 3.7GB raw_json ≈ 8-10GB as Python objects → signal 9).
# Worse, it deadlocked: classifications are only written at the very end, so
# every OOM left the backlog intact for the next run to die on again.
CLASSIFY_BATCH_SIZE = 25


def main():
    # Incremental: only classify wallets whose analyzed_swaps are newer than
    # their last classification. Cuts the per-run cost from "re-scan every
    # wallet daily" to "only what actually changed since yesterday."
    pending_ids = bq.fetch_wallets_needing_classification()
    if not pending_ids:
        print("No wallets need re-classification — all up to date.")
        return

    wallets = bq.fetch_all_wallets()
    pending_set = set(pending_ids)
    wallets = [w for w in wallets if w["wallet_id"] in pending_set]
    classified_at = datetime.now(timezone.utc).isoformat()

    n_batches = (len(wallets) + CLASSIFY_BATCH_SIZE - 1) // CLASSIFY_BATCH_SIZE
    print(
        f"Classifying {len(wallets)} wallets (incremental) in "
        f"{n_batches} batches of {CLASSIFY_BATCH_SIZE}"
    )

    total_classified = 0
    total_smart = 0
    for start in range(0, len(wallets), CLASSIFY_BATCH_SIZE):
        batch = wallets[start : start + CLASSIFY_BATCH_SIZE]
        batch_ids = [w["wallet_id"] for w in batch]

        batch_raw = bq.fetch_raw_swaps_all_wallets(wallet_ids=batch_ids)
        batch_analyzed = bq.fetch_analyzed_swaps_all_wallets(wallet_ids=batch_ids)

        rows = []
        for w in batch:
            wallet_id = w["wallet_id"]
            address = w["address"]

            raw_swaps = batch_raw.get(wallet_id, [])
            if not raw_swaps:
                continue
            analyzed_swaps = batch_analyzed.get(wallet_id, [])

            result = classify_wallet(wallet_id, address, raw_swaps, analyzed_swaps)
            rows.append({"classified_at": classified_at, **result})

            tag_str = ", ".join(result["tags"]) if result["tags"] else "unclassified"
            print(
                f"[{wallet_id}] {tag_str} | proxy: {result['proxy_pct']}% | "
                f"freq: {result['daily_frequency']}/day | days: {result['active_days']} | "
                f"swaps: {result['total_swaps']} | closed: {result['closed_positions']} | "
                f"win_rate: {result['win_rate']}% | pnl: {result['total_pnl_sol']} SOL"
            )

        # Persist per batch (not once at the end): a crash mid-run keeps every
        # completed batch, and the incremental anti-join excludes them next
        # run — the backlog shrinks monotonically even across failures, which
        # is what breaks the OOM deadlock.
        if rows:
            bq.insert_classifications(rows)
            total_classified += len(rows)
            total_smart += sum(1 for r in rows if "smart_candidate" in r["tags"])

        # Free this batch's swap data before fetching the next one.
        del batch_raw, batch_analyzed
        print(f"  batch {start // CLASSIFY_BATCH_SIZE + 1}/{n_batches} persisted")

    if total_classified:
        print(
            f"\nClassified {total_classified} wallets ({total_smart} smart "
            f"candidates) → BQ wallet_classifications"
        )


if __name__ == "__main__":
    main()

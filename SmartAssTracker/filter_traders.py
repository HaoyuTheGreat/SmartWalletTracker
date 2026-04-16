import csv
import json
import os
from datetime import datetime, timezone

SOL_PRICE_USD = 150  # 先硬编码,后面可接 CoinGecko

STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
WSOL_MINT = "So11111111111111111111111111111111111111112"
EXCLUDED_MINTS = STABLE_MINTS | {WSOL_MINT}

EXCLUSION_TAGS = {"proxy_bot", "high_frequency", "insufficient_data", "market_maker"}


def remove_error_files():
    """Delete swap data files that contain API error responses instead of actual data."""
    folder = "data/wallets_swap_data"
    for filename in os.listdir(folder):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(folder, filename)
        with open(filepath, "r") as f:
            data = json.load(f)
        # Valid swap data is a list; error responses are dicts with an "error" key
        if isinstance(data, dict) and data.get("error"):
            os.remove(filepath)
            print(f"Deleted error file: {filename}")


def load_raw_swaps():
    """
    Load all raw swap data from wallets_swap_data folder.

    Returns:
        dict: wallet_id (first 8 chars) -> list of swap transactions
    """
    folder = "data/wallets_swap_data"
    all_wallets = {}
    for fname in os.listdir(folder):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(folder, fname), "r") as f:
            all_wallets[fname.replace(".json", "")] = json.load(f)
    return all_wallets


def load_analyzed_swaps():
    """Load analyzed swap data. Returns dict wallet_id -> list of swaps."""
    folder = "data/analyzed_swaps_data"
    result = {}
    for fname in os.listdir(folder):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(folder, fname), "r") as f:
            data = json.load(f)
        result[fname.replace(".json", "")] = data.get("swaps", [])
    return result


def load_id_to_address():
    """
    Build a mapping from wallet_id (first 8 chars) to full wallet address.

    Returns:
        dict: e.g. {"6FzAoR31": "6FzAoR316QBi8Ne3ARwZixhbfbBg12YkFhZRNZDS5ath"}
    """
    with open("data/wallets_list.json", "r") as f:
        wallets_list = json.load(f)
    return {w["address"][:8]: w["address"] for w in wallets_list}


def is_proxy_transaction(tx, wallet_address):
    """
    Check if a transaction was executed through a proxy/bot.

    A proxy transaction is one where the wallet address doesn't appear
    in tokenTransfers (as sender or receiver) and has no events.swap data.
    This means the wallet only signed the transaction but didn't directly
    handle any tokens — a bot did it on its behalf.

    Args:
        tx: a single swap transaction dict from Helius
        wallet_address: the full wallet address string

    Returns:
        bool: True if this is a proxy transaction
    """
    token_transfers = tx.get("tokenTransfers", [])
    wallet_in_transfers = any(
        tt.get("fromUserAccount") == wallet_address or tt.get("toUserAccount") == wallet_address
        for tt in token_transfers
    )
    has_swap_event = bool(tx.get("events", {}).get("swap", {}))
    return not wallet_in_transfers and not has_swap_event


def calc_daily_frequency(swaps):
    """
    Calculate average daily trading frequency.

    Args:
        swaps: list of swap transactions

    Returns:
        tuple: (daily_frequency, active_days)
    """
    timestamps = [tx.get("timestamp", 0) for tx in swaps]
    first_trade = min(timestamps)
    last_trade = max(timestamps)
    active_days = max((last_trade - first_trade) / 86400, 1)
    daily_frequency = len(swaps) / active_days
    return daily_frequency, active_days


def is_market_maker(swaps, wallet_address):
    """
    Determine if a wallet behaves like a market maker.

    Checks three conditions:
    1. High trading frequency (>10/day)
    2. Buy/sell symmetry — for the most traded token, buy and sell counts are close
    3. Token concentration — most trades are on a small number of tokens

    Args:
        swaps: list of swap transactions
        wallet_address: full wallet address

    Returns:
        tuple: (is_mm: bool, stats: dict with buy_sell_ratio, top_token_pct, unique_tokens)
    """
    daily_frequency, _ = calc_daily_frequency(swaps)

    # Count buys and sells per token (using tokenTransfers)
    token_buys = {}   # mint -> buy count
    token_sells = {}  # mint -> sell count
    for tx in swaps:
        for tt in tx.get("tokenTransfers", []):
            mint = tt.get("mint", "")
            if tt.get("toUserAccount") == wallet_address:
                token_buys[mint] = token_buys.get(mint, 0) + 1
            elif tt.get("fromUserAccount") == wallet_address:
                token_sells[mint] = token_sells.get(mint, 0) + 1

    # Find the most traded token (by total buys + sells)
    all_tokens = set(list(token_buys.keys()) + list(token_sells.keys()))
    if not all_tokens:
        return False, {}

    token_activity = {}
    for mint in all_tokens:
        token_activity[mint] = token_buys.get(mint, 0) + token_sells.get(mint, 0)

    top_token = max(token_activity, key=token_activity.get)
    top_buys = token_buys.get(top_token, 0)
    top_sells = token_sells.get(top_token, 0)

    # Buy/sell symmetry: ratio close to 1.0 means balanced
    if max(top_buys, top_sells) > 0:
        buy_sell_ratio = min(top_buys, top_sells) / max(top_buys, top_sells)
    else:
        buy_sell_ratio = 0

    # Token concentration: what % of all trades are on the top token
    total_activity = sum(token_activity.values())
    top_token_pct = token_activity[top_token] / total_activity * 100 if total_activity else 0

    unique_tokens = len(all_tokens)

    stats = {
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "top_token_pct": round(top_token_pct, 1),
        "unique_tokens": unique_tokens,
    }

    # Market maker: high frequency + symmetric trading + concentrated on few tokens
    is_mm = daily_frequency > 10 and buy_sell_ratio > 0.5 and top_token_pct > 40

    return is_mm, stats


def aggregate_by_token(analyzed_swaps):
    """
    Group swaps into per-token positions.
    Stablecoins are folded into virtual SOL (USDC/USDT @ $1).
    WSOL is excluded from positions entirely.

    Returns:
        dict: mint -> {symbol, bought, sold, sol_in, sol_out}
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
        token_spent = swap.get("token_spent", [])
        token_received = swap.get("token_received", [])
        sol_price = swap.get("sol_price_usd") or SOL_PRICE_USD

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
    """
    Compute win rate and PnL from aggregated positions.
    A position is 'closed' when sold >= bought * 0.95.
    A closed position is 'inflated' (data_clipped signal) when sold > bought * 1.1,
    which usually means the wallet held this token before our 2000-tx window.
    """
    closed = []
    inflated_count = 0
    for mint, p in positions.items():
        if p["bought"] <= 0:
            continue
        if p["sold"] >= p["bought"] * 0.95:
            pnl = p["sol_out"] - p["sol_in"]
            closed.append({"symbol": p["symbol"], "pnl_sol": pnl, "win": pnl > 0})
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


def classify_wallets(all_wallets, id_to_address, analyzed_wallets):
    """
    Classify wallets based on their trading behavior.

    Args:
        all_wallets: dict of wallet_id -> list of swap transactions
        id_to_address: dict of wallet_id -> full wallet address

    Returns:
        dict: wallet_id -> {tags, proxy_pct, daily_frequency, active_days, total_swaps}
    """
    results = {}
    for wallet_id, swaps in all_wallets.items():
        wallet_address = id_to_address.get(wallet_id, "")
        if not wallet_address:
            continue

        tags = []

        proxy_count = sum(1 for tx in swaps if is_proxy_transaction(tx, wallet_address))
        proxy_pct = proxy_count / len(swaps) * 100 if swaps else 0
        if proxy_pct > 50:
            tags.append("proxy_bot")

        daily_frequency, active_days = calc_daily_frequency(swaps)
        if daily_frequency > 20:
            tags.append("high_frequency")
        #filter out the wallets that have little swap transactions and not really active.
        if len(swaps) < 20 or active_days < 7:
            tags.append("insufficient_data")

        mm_result, mm_stats = is_market_maker(swaps, wallet_address)
        if mm_result:
            tags.append("market_maker")

        analyzed = analyzed_wallets.get(wallet_id, [])
        positions = aggregate_by_token(analyzed)
        perf = calc_performance(positions)

        if perf["inflated_positions"] > 0:
            tags.append("data_clipped")

        is_excluded = any(t in EXCLUSION_TAGS for t in tags)
        if not is_excluded and perf["closed_positions"] >= 5 \
                and perf["win_rate"] > 50 and perf["total_pnl_sol"] > 0:
            tags.append("smart_candidate")

        results[wallet_id] = {
            "tags": tags,
            "proxy_pct": round(proxy_pct, 1),
            "daily_frequency": round(daily_frequency, 1),
            "active_days": round(active_days, 1),
            "total_swaps": len(swaps),
            **mm_stats,
            **perf,
        }
    return results


def save_results(results):
    """Save classification results to CSV (for humans) and JSON (for llm.py)."""
    os.makedirs("data", exist_ok=True)

    csv_path = "data/wallet_analysis.csv"
    fieldnames = [
        "wallet_id", "tags", "total_swaps", "active_days", "daily_frequency",
        "closed_positions", "inflated_positions", "win_rate", "total_pnl_sol",
        "avg_pnl_sol", "proxy_pct", "buy_sell_ratio", "top_token_pct", "unique_tokens",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for wallet_id, info in results.items():
            row = {"wallet_id": wallet_id, "tags": ",".join(info["tags"]) if info["tags"] else "unclassified"}
            for key in fieldnames[2:]:
                row[key] = info.get(key, "")
            writer.writerow(row)
    print(f"Saved CSV: {csv_path}")

    json_path = "data/smart_wallet_candidates.json"
    smart_candidates = [wid for wid, info in results.items() if "smart_candidate" in info["tags"]]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_wallets": len(results),
        "smart_candidates": smart_candidates,
        "wallets": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved JSON: {json_path} ({len(smart_candidates)} smart candidates)")


if __name__ == "__main__":
    remove_error_files()

    all_wallets = load_raw_swaps()
    id_to_address = load_id_to_address()
    analyzed_wallets = load_analyzed_swaps()
    results = classify_wallets(all_wallets, id_to_address, analyzed_wallets)

    for wallet_id, info in results.items():
        tags = info["tags"] if info["tags"] else ["unclassified"]
        print(f"[{wallet_id}] {', '.join(tags)} | proxy: {info['proxy_pct']}% | freq: {info['daily_frequency']}/day | days: {info['active_days']} | swaps: {info['total_swaps']} | closed: {info['closed_positions']} | inflated: {info['inflated_positions']} | win_rate: {info['win_rate']}% | pnl: {info['total_pnl_sol']} SOL")

    save_results(results)

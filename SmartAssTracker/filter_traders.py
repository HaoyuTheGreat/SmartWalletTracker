import json
import os


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


def classify_wallets(all_wallets, id_to_address):
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

        mm_result, mm_stats = is_market_maker(swaps, wallet_address)
        if mm_result:
            tags.append("market_maker")

        results[wallet_id] = {
            "tags": tags,
            "proxy_pct": round(proxy_pct, 1),
            "daily_frequency": round(daily_frequency, 1),
            "active_days": round(active_days, 1),
            "total_swaps": len(swaps),
            **mm_stats,
        }
    return results


if __name__ == "__main__":
    remove_error_files()

    all_wallets = load_raw_swaps()
    id_to_address = load_id_to_address()
    results = classify_wallets(all_wallets, id_to_address)

    for wallet_id, info in results.items():
        tags = info["tags"] if info["tags"] else ["unclassified"]
        print(f"[{wallet_id}] {', '.join(tags)} | proxy: {info['proxy_pct']}% | freq: {info['daily_frequency']}/day | days: {info['active_days']} | swaps: {info['total_swaps']} | b/s ratio: {info.get('buy_sell_ratio', 'N/A')} | top_token: {info.get('top_token_pct', 'N/A')}% | tokens: {info.get('unique_tokens', 'N/A')}")

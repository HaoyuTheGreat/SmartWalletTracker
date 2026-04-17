"""
analyze_wallets.py - 读取 BigQuery raw_swaps，解析成统一结构后写入 analyzed_swaps

优化点 (对比 file-based 老版本):
  1. Token symbol cache 从 BQ 的 analyzed_swaps 里预加载，复用以前解析过的 symbol
     → 重跑几乎不需要调 Helius DAS API
  2. raw_swaps / already-analyzed sigs 一次 SELECT 全量拉回，python 端分组
     → 把 61 次 BQ 查询压缩成 2 次
  3. 所有钱包的解析结果累加到一个大 list，最后一次性 insert_analyzed_swaps
     → 61 次 BQ load job 压缩成 1 次
  4. 首次跑 (BQ 里没有 symbol) 时，回退到本地 data/token_names.json 作为 bootstrap

增量策略：只分析当前 parser_version 没处理过的 signature。
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from lib import bq
from lib.secrets import get_secret

HELIUS_API_KEY = get_secret("HELIUS_API_KEY")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PARSER_VERSION = 7
LOCAL_TOKEN_CACHE = "data/token_names.json"


def parse_jupiter(tx):
    timestamp = tx.get("timestamp")
    swap_event = tx.get("events", {}).get("swap", {})

    token_inputs = swap_event.get("tokenInputs", [])
    token_outputs = swap_event.get("tokenOutputs", [])
    native_input = swap_event.get("nativeInput")
    native_output = swap_event.get("nativeOutput")

    sol_spent = int(native_input.get("amount", 0)) / 10**9 if native_input else 0
    sol_received = int(native_output.get("amount", 0)) / 10**9 if native_output else 0

    non_native_tokens_sold = {}
    for i in token_inputs:
        mint = i.get("mint")
        raw = i.get("rawTokenAmount", {})
        amount = int(raw.get("tokenAmount")) / 10 ** raw.get("decimals")
        non_native_tokens_sold[mint] = non_native_tokens_sold.get(mint, 0) + amount

    non_native_tokens_bought = {}
    for o in token_outputs:
        mint = o.get("mint")
        raw = o.get("rawTokenAmount", {})
        amount = int(raw.get("tokenAmount")) / 10 ** raw.get("decimals")
        non_native_tokens_bought[mint] = non_native_tokens_bought.get(mint, 0) + amount

    return {
        "timestamp": timestamp,
        "sol_spent": sol_spent,
        "sol_received": sol_received,
        "token_spent": non_native_tokens_sold,
        "token_received": non_native_tokens_bought,
    }


def parse_by_token_transfers(tx, wallet_address):
    timestamp = tx.get("timestamp")
    token_spent = {}
    token_received = {}

    for tt in tx.get("tokenTransfers", []):
        mint = tt.get("mint")
        amount = tt.get("tokenAmount", 0)
        if tt.get("fromUserAccount") == wallet_address:
            token_spent[mint] = token_spent.get(mint, 0) + amount
        elif tt.get("toUserAccount") == wallet_address:
            token_received[mint] = token_received.get(mint, 0) + amount

    return {
        "timestamp": timestamp,
        "sol_spent": 0,
        "sol_received": 0,
        "token_spent": token_spent,
        "token_received": token_received,
    }


def parse_swap(tx, wallet_address):
    source = tx.get("source")
    if source == "JUPITER":
        result = parse_jupiter(tx)
        if (
            not result["token_spent"]
            and not result["token_received"]
            and result["sol_spent"] == 0
            and result["sol_received"] == 0
        ):
            return parse_by_token_transfers(tx, wallet_address)
        return result
    return parse_by_token_transfers(tx, wallet_address)


def collect_mints(parsed_swaps):
    all_mints = set()
    for swap in parsed_swaps:
        all_mints.update(swap["token_spent"].keys())
        all_mints.update(swap["token_received"].keys())
    return all_mints


def resolve_token_symbols(all_mints, cache):
    """Resolve new mints via Helius DAS API; cache is updated in place."""
    new_mints = [m for m in all_mints if m not in cache]
    if not new_mints:
        return cache
    print(f"  resolving {len(new_mints)} new token symbols via Helius...")
    for mint in new_mints:
        payload = {
            "jsonrpc": "2.0",
            "id": "resolve",
            "method": "getAsset",
            "params": {"id": mint},
        }
        for attempt in range(3):
            try:
                resp = requests.post(HELIUS_RPC_URL, json=payload, timeout=15)
                data = resp.json()
                symbol = (
                    data.get("result", {})
                    .get("content", {})
                    .get("metadata", {})
                    .get("symbol", mint[:8])
                )
                cache[mint] = symbol
                break
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    time.sleep(2)
                else:
                    print(f"  failed {mint[:8]} after 3 tries: {e}")
                    cache[mint] = mint[:8]
    return cache


def load_initial_token_cache():
    """BQ first; fall back to local JSON if BQ has nothing yet (first run bootstrap)."""
    cache = bq.fetch_token_symbol_cache()
    if cache:
        print(f"Preloaded {len(cache)} token symbols from BQ analyzed_swaps")
        return cache
    if os.path.exists(LOCAL_TOKEN_CACHE):
        with open(LOCAL_TOKEN_CACHE) as f:
            cache = json.load(f)
        print(f"BQ cache empty; bootstrapped {len(cache)} symbols from {LOCAL_TOKEN_CACHE}")
        return cache
    print("No token cache available; will resolve all mints via Helius")
    return {}


def build_row(parsed, wallet_id, signature, token_cache, sol_prices):
    ts = parsed["timestamp"]
    swap_time = datetime.fromtimestamp(ts, tz=timezone.utc)
    date_str = swap_time.strftime("%Y-%m-%d")
    sol_price = sol_prices.get(date_str, 150)

    token_spent = [
        {"mint": m, "symbol": token_cache.get(m, m[:8]), "amount": float(a)}
        for m, a in parsed["token_spent"].items()
    ]
    token_received = [
        {"mint": m, "symbol": token_cache.get(m, m[:8]), "amount": float(a)}
        for m, a in parsed["token_received"].items()
    ]

    return {
        "wallet_id": wallet_id,
        "signature": signature,
        "swap_time": swap_time.isoformat(),
        "sol_price_usd": float(sol_price),
        "sol_spent": float(parsed["sol_spent"]),
        "sol_received": float(parsed["sol_received"]),
        "token_spent": token_spent,
        "token_received": token_received,
        "parser_version": PARSER_VERSION,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    # --- Phase 1: ask BQ only for what needs analyzing ---
    unanalyzed = bq.fetch_unanalyzed_raw_swaps(PARSER_VERSION)
    total_new = sum(len(v) for v in unanalyzed.values())
    print(f"Unanalyzed raw_swaps at v{PARSER_VERSION}: {total_new}")

    if not unanalyzed:
        print("Nothing new to analyze")
        return

    # Only now do we pay for the other preloads
    wallets = bq.fetch_all_wallets()
    id_to_address = {w["wallet_id"]: w["address"] for w in wallets}
    sol_prices = bq.fetch_sol_price_map()
    token_cache = load_initial_token_cache()

    # --- Phase 2: parse in memory ---
    all_rows = []
    for wallet_id, new_raw in unanalyzed.items():
        address = id_to_address.get(wallet_id, "")

        parsed_pairs = []
        for tx in new_raw:
            parsed = parse_swap(tx, address)
            if parsed is None or parsed.get("timestamp") is None:
                continue
            parsed_pairs.append((tx["_signature"], parsed))

        if not parsed_pairs:
            continue

        mints = collect_mints(p for _, p in parsed_pairs)
        resolve_token_symbols(mints, token_cache)

        rows = [build_row(p, wallet_id, sig, token_cache, sol_prices) for sig, p in parsed_pairs]
        all_rows.extend(rows)
        print(f"[{wallet_id}] prepared {len(rows)} rows")

    # --- Phase 3: single batch insert ---
    if all_rows:
        bq.insert_analyzed_swaps(all_rows)
        print(f"\nInserted {len(all_rows)} analyzed_swaps rows in one batch")
    else:
        print("\nNothing new to analyze")


if __name__ == "__main__":
    main()

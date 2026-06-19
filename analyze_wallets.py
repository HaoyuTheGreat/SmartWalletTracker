"""
analyze_wallets.py - Read raw_swaps from BigQuery, parse to unified shape, write analyzed_swaps.

Optimizations vs the old file-based version:
  1. Token symbol cache is preloaded from BQ analyzed_swaps, reusing previously resolved symbols
     → re-runs almost never hit the Helius DAS API.
  2. raw_swaps / already-analyzed signatures are fetched in a single SELECT and grouped in Python
     → 61 BQ queries collapsed into 2.
  3. Every wallet's parsed rows are accumulated into one list and written via a single
     insert_analyzed_swaps call → 61 BQ load jobs collapsed into 1.
  4. First run (no symbols in BQ) falls back to local data/token_names.json as bootstrap.

Incremental strategy: only analyze signatures the current parser_version hasn't processed yet.
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


# Wallets per batch. The fetch loads only this batch's unanalyzed raw_json
# into memory (the OOM bomb was loading ALL of it at once). Conservative vs
# Step 5's 25 because a single heavy wallet's raw_json is larger here; lower
# it if a backfill of very heavy wallets spikes memory.
ANALYZE_BATCH_SIZE = 15


def main():
    # --- Discover who needs analyzing (IDs only, no payloads) ---
    pending_ids = bq.fetch_unanalyzed_wallet_ids(PARSER_VERSION)
    if not pending_ids:
        print("Nothing new to analyze")
        return

    n_batches = (len(pending_ids) + ANALYZE_BATCH_SIZE - 1) // ANALYZE_BATCH_SIZE
    print(
        f"{len(pending_ids)} wallets have unanalyzed raw_swaps at v{PARSER_VERSION} "
        f"— processing in {n_batches} batches of {ANALYZE_BATCH_SIZE}"
    )

    # --- "Reference books": loaded once, reused + accumulated across batches ---
    # Small and unchanging within a run, so loading per-batch would just be
    # wasted queries. token_cache MUST persist so a symbol resolved in an early
    # batch isn't re-fetched from Helius DAS in a later one.
    id_to_address = {w["wallet_id"]: w["address"] for w in bq.fetch_all_wallets()}
    sol_prices = bq.fetch_sol_price_map()
    token_cache = load_initial_token_cache()

    total_rows = 0
    for start in range(0, len(pending_ids), ANALYZE_BATCH_SIZE):
        batch_ids = pending_ids[start : start + ANALYZE_BATCH_SIZE]
        # Pull ONLY this batch's unanalyzed payloads (the bounded "work pile").
        batch_raw = bq.fetch_unanalyzed_raw_swaps(PARSER_VERSION, wallet_ids=batch_ids)

        rows = []
        for wallet_id, new_raw in batch_raw.items():
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
            rows.extend(
                build_row(p, wallet_id, sig, token_cache, sol_prices)
                for sig, p in parsed_pairs
            )

        # Persist THIS batch, then free it — never accumulate across batches
        # (that would just rebuild the OOM bomb in `rows`). Per-batch insert
        # also means a mid-run crash keeps finished batches: the anti-join
        # excludes them next run, so the backlog converges.
        if rows:
            bq.insert_analyzed_swaps(rows)
            total_rows += len(rows)
        del batch_raw, rows
        print(f"  batch {start // ANALYZE_BATCH_SIZE + 1}/{n_batches} done")

    print(f"\nInserted {total_rows} analyzed_swaps rows across {n_batches} batches")


if __name__ == "__main__":
    main()

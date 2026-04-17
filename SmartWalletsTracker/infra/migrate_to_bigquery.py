"""
One-time migration: load local data/ files into BigQuery tables.

Uses WRITE_TRUNCATE on every table, so re-running this script is safe —
it wipes the table and reloads from local files.

Run from repo root:
    python SmartWalletsTracker/infra/migrate_to_bigquery.py
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

PROJECT_ID = "smart-wallets-tracker"
DATASET = "whale_tracker"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

client = bigquery.Client(project=PROJECT_ID)


def table_ref(name):
    return f"{PROJECT_ID}.{DATASET}.{name}"


def load_rows(table_name, rows, schema=None):
    """Bulk-load rows into a BigQuery table, replacing existing data."""
    if not rows:
        print(f"  [{table_name}] no rows, skip")
        return
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    if schema:
        job_config.schema = schema
    else:
        job_config.autodetect = False
    job = client.load_table_from_json(rows, table_ref(table_name), job_config=job_config)
    job.result()
    print(f"  [{table_name}] loaded {len(rows)} rows")


def migrate_wallets():
    print("\n--- Migrating wallets ---")
    with open(DATA_DIR / "wallets_list.json") as f:
        wallets = json.load(f)

    # Build set of wallet_ids that failed data collection
    failed_dir = DATA_DIR / "failed_wallets"
    failed_ids = set()
    if failed_dir.exists():
        for fname in os.listdir(failed_dir):
            if fname.endswith(".json"):
                failed_ids.add(fname.replace(".json", ""))

    # Build set of wallet_ids that have successful swap data
    swap_dir = DATA_DIR / "wallets_swap_data"
    success_ids = set()
    if swap_dir.exists():
        for fname in os.listdir(swap_dir):
            if fname.endswith(".json"):
                success_ids.add(fname.replace(".json", ""))

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for w in wallets:
        addr = w["address"]
        wid = addr[:8]
        if wid in success_ids:
            status = "success"
            last_collected = now_iso
        elif wid in failed_ids:
            status = "failed"
            last_collected = now_iso
        else:
            status = "pending"
            last_collected = None
        rows.append({
            "address": addr,
            "wallet_id": wid,
            "chain": w.get("chain"),
            "source_token": w.get("source_token"),
            "source_token_mint": w.get("source_token_mint"),
            "discovered_at": now_iso,
            "last_collected_at": last_collected,
            "collection_status": status,
        })

    load_rows("wallets", rows)


def migrate_sol_prices():
    print("\n--- Migrating sol_prices ---")
    with open(DATA_DIR / "sol_price_history.json") as f:
        prices = json.load(f)
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {"price_date": date_str, "price_usd": float(p), "updated_at": now_iso}
        for date_str, p in prices.items()
    ]
    load_rows("sol_prices", rows)


def migrate_raw_swaps():
    """
    Returns: dict {wallet_id: {timestamp_int: signature}} so that
    analyzed_swaps migration can backfill signatures by matching timestamps.
    """
    print("\n--- Migrating raw_swaps ---")
    swap_dir = DATA_DIR / "wallets_swap_data"
    now_iso = datetime.now(timezone.utc).isoformat()

    all_rows = []
    sig_lookup = {}  # wallet_id -> {timestamp: signature}

    for fname in sorted(os.listdir(swap_dir)):
        if not fname.endswith(".json"):
            continue
        wid = fname.replace(".json", "")
        sig_lookup[wid] = {}
        with open(swap_dir / fname) as f:
            txs = json.load(f)
        for tx in txs:
            sig = tx.get("signature")
            ts = tx.get("timestamp")
            if sig is None or ts is None:
                continue
            sig_lookup[wid][ts] = sig
            tx_time_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            all_rows.append({
                "wallet_id": wid,
                "signature": sig,
                "tx_time": tx_time_iso,
                "tx_timestamp": ts,
                "source": tx.get("source"),
                "raw_json": json.dumps(tx, ensure_ascii=False),
                "collected_at": now_iso,
            })
        print(f"  {wid}: {len(txs)} txs")

    load_rows("raw_swaps", all_rows)
    return sig_lookup


def migrate_analyzed_swaps(sig_lookup):
    """Uses sig_lookup (from raw_swaps migration) to backfill signatures."""
    print("\n--- Migrating analyzed_swaps ---")
    analyzed_dir = DATA_DIR / "analyzed_swaps_data"
    now_iso = datetime.now(timezone.utc).isoformat()

    all_rows = []
    signatures_missing = 0
    signatures_resolved = 0

    for fname in sorted(os.listdir(analyzed_dir)):
        if not fname.endswith(".json"):
            continue
        wid = fname.replace(".json", "")
        with open(analyzed_dir / fname) as f:
            payload = json.load(f)
        # Old format was a list; new format is {"version": N, "swaps": [...]}
        if isinstance(payload, dict):
            version = payload.get("version")
            swaps = payload.get("swaps", [])
        else:
            version = None
            swaps = payload

        wallet_sigs = sig_lookup.get(wid, {})

        for s in swaps:
            # Parse the "time" string back to TIMESTAMP and to int timestamp
            # Example: "2022-06-20 15:29:57-07:00"
            time_str = s.get("time")
            try:
                dt = datetime.fromisoformat(time_str)
                ts_int = int(dt.timestamp())
                swap_time_iso = dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                continue

            # Try to find matching signature from raw_swaps
            sig = wallet_sigs.get(ts_int)
            if sig:
                signatures_resolved += 1
            else:
                # Fallback: synthetic signature so NOT NULL constraint is satisfied
                sig = f"MIGRATED_{wid}_{ts_int}"
                signatures_missing += 1

            all_rows.append({
                "wallet_id": wid,
                "signature": sig,
                "swap_time": swap_time_iso,
                "sol_price_usd": s.get("sol_price_usd"),
                "sol_spent": s.get("sol_spent"),
                "sol_received": s.get("sol_received"),
                "token_spent": s.get("token_spent", []),
                "token_received": s.get("token_received", []),
                "parser_version": version,
                "analyzed_at": now_iso,
            })

    print(f"  signature resolved from raw_swaps: {signatures_resolved}")
    print(f"  signature synthesized (no raw match): {signatures_missing}")
    load_rows("analyzed_swaps", all_rows)


def main():
    print(f"Migrating local data -> {PROJECT_ID}.{DATASET}")
    migrate_wallets()
    migrate_sol_prices()
    sig_lookup = migrate_raw_swaps()
    migrate_analyzed_swaps(sig_lookup)
    print("\nMigration complete.")


if __name__ == "__main__":
    main()

"""
BigQuery data access layer.

Centralizes all reads/writes so pipeline scripts don't touch the client directly.
Functions are grouped by table.
"""

import json
import os
from datetime import datetime, timezone
from typing import Iterable

from google.cloud import bigquery

PROJECT = os.getenv("GCP_PROJECT", "smart-wallets-tracker")
DATASET = os.getenv("BQ_DATASET", "whale_tracker")

_client = None


def client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT)
    return _client


def _table(name: str) -> str:
    return f"{PROJECT}.{DATASET}.{name}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rows(table: str, rows: list, write_truncate: bool = False):
    """Batch-load rows into a table. Append by default."""
    if not rows:
        return
    disposition = (
        bigquery.WriteDisposition.WRITE_TRUNCATE
        if write_truncate
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    job_config = bigquery.LoadJobConfig(
        write_disposition=disposition,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    job = client().load_table_from_json(rows, _table(table), job_config=job_config)
    job.result()


# ---------------------------------------------------------------------------
# wallets
# ---------------------------------------------------------------------------
def fetch_all_wallets() -> list[dict]:
    """Return every wallet row as a dict."""
    query = f"""
        SELECT address, wallet_id, chain, source_token, source_token_mint,
               last_collected_at, collection_status
        FROM `{_table("wallets")}`
    """
    return [dict(row) for row in client().query(query).result()]


def fetch_wallets_needing_collection(max_age_hours: int = 24) -> list[dict]:
    """
    Wallets that either have never been collected, or were collected >N hours ago.
    Skips wallets marked as 'failed' permanently.
    """
    query = f"""
        SELECT address, wallet_id
        FROM `{_table("wallets")}`
        WHERE collection_status != 'failed'
          AND (last_collected_at IS NULL
               OR TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), last_collected_at, HOUR) > @max_age)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("max_age", "INT64", max_age_hours)]
    )
    return [dict(row) for row in client().query(query, job_config=job_config).result()]


def update_wallet_status(wallet_id: str, status: str):
    """Mark a wallet's collection_status + last_collected_at=now."""
    query = f"""
        UPDATE `{_table("wallets")}`
        SET collection_status = @status,
            last_collected_at = CURRENT_TIMESTAMP()
        WHERE wallet_id = @wid
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("wid", "STRING", wallet_id),
        ]
    )
    client().query(query, job_config=job_config).result()


# ---------------------------------------------------------------------------
# raw_swaps
# ---------------------------------------------------------------------------
def existing_signatures_for_wallet(wallet_id: str) -> set[str]:
    """Signatures already in raw_swaps for a wallet (for deduplication)."""
    query = f"""
        SELECT signature
        FROM `{_table("raw_swaps")}`
        WHERE wallet_id = @wid
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("wid", "STRING", wallet_id)]
    )
    return {row["signature"] for row in client().query(query, job_config=job_config).result()}


def insert_raw_swaps(wallet_id: str, txs: Iterable[dict]):
    """Insert raw Helius txs for a wallet. Caller is responsible for dedup."""
    now = _utcnow_iso()
    rows = []
    for tx in txs:
        sig = tx.get("signature")
        ts = tx.get("timestamp")
        if not sig or ts is None:
            continue
        rows.append({
            "wallet_id": wallet_id,
            "signature": sig,
            "tx_time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "tx_timestamp": ts,
            "source": tx.get("source"),
            "raw_json": json.dumps(tx, ensure_ascii=False),
            "collected_at": now,
        })
    _load_rows("raw_swaps", rows)
    return len(rows)


def fetch_raw_swaps_all_wallets() -> dict[str, list[dict]]:
    """One-shot: all raw swaps grouped by wallet_id. Avoids per-wallet round-trips."""
    query = f"""
        SELECT wallet_id, signature, raw_json
        FROM `{_table("raw_swaps")}`
        ORDER BY wallet_id, tx_timestamp
    """
    result: dict[str, list[dict]] = {}
    for row in client().query(query).result():
        tx = json.loads(row["raw_json"])
        tx["_signature"] = row["signature"]
        result.setdefault(row["wallet_id"], []).append(tx)
    return result


def fetch_unanalyzed_raw_swaps(parser_version: int) -> dict[str, list[dict]]:
    """
    Anti-join: return raw_swaps that don't have a matching row in analyzed_swaps
    at the given parser_version. Lets BQ compute the diff — Python only receives
    rows that actually need processing.

    When everything is up-to-date, this returns {} in ~1s instead of pulling
    ~500MB of raw_json to Python.
    """
    query = f"""
        SELECT r.wallet_id, r.signature, r.raw_json
        FROM `{_table("raw_swaps")}` r
        LEFT JOIN `{_table("analyzed_swaps")}` a
          ON r.wallet_id = a.wallet_id
         AND r.signature = a.signature
         AND a.parser_version = @v
        WHERE a.signature IS NULL
        ORDER BY r.wallet_id, r.tx_timestamp
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("v", "INT64", parser_version)]
    )
    result: dict[str, list[dict]] = {}
    for row in client().query(query, job_config=job_config).result():
        tx = json.loads(row["raw_json"])
        tx["_signature"] = row["signature"]
        result.setdefault(row["wallet_id"], []).append(tx)
    return result


# ---------------------------------------------------------------------------
# analyzed_swaps
# ---------------------------------------------------------------------------
def fetch_token_symbol_cache() -> dict[str, str]:
    """
    Pull every (mint, symbol) pair we've previously resolved, from analyzed_swaps.
    Lets re-runs skip Helius DAS API calls for tokens we've already seen.
    """
    query = f"""
        WITH all_tokens AS (
          SELECT t.mint AS mint, t.symbol AS symbol
          FROM `{_table("analyzed_swaps")}`, UNNEST(token_spent) AS t
          UNION ALL
          SELECT t.mint AS mint, t.symbol AS symbol
          FROM `{_table("analyzed_swaps")}`, UNNEST(token_received) AS t
        )
        SELECT mint, ANY_VALUE(symbol) AS symbol
        FROM all_tokens
        WHERE mint IS NOT NULL AND symbol IS NOT NULL
        GROUP BY mint
    """
    return {row["mint"]: row["symbol"] for row in client().query(query).result()}


def insert_analyzed_swaps(rows: list[dict]):
    """Bulk insert analyzed swap rows. Caller builds the dict per analyzed_swaps schema."""
    _load_rows("analyzed_swaps", rows)


def fetch_analyzed_swaps_all_wallets() -> dict[str, list[dict]]:
    """One-shot: all analyzed swaps grouped by wallet_id. Same pattern as raw_swaps."""
    query = f"""
        SELECT wallet_id, signature, swap_time, sol_price_usd, sol_spent, sol_received,
               token_spent, token_received
        FROM `{_table("analyzed_swaps")}`
        ORDER BY wallet_id, swap_time
    """
    result: dict[str, list[dict]] = {}
    for row in client().query(query).result():
        result.setdefault(row["wallet_id"], []).append(dict(row))
    return result


# ---------------------------------------------------------------------------
# sol_prices
# ---------------------------------------------------------------------------
def upsert_sol_prices(prices: dict[str, float]):
    """
    Merge a {date_str: price} dict into sol_prices table.
    Uses staging + MERGE so re-running doesn't duplicate rows.
    """
    if not prices:
        return
    now = _utcnow_iso()
    staging_rows = [
        {"price_date": d, "price_usd": float(p), "updated_at": now}
        for d, p in prices.items()
    ]
    staging_table = f"{DATASET}._staging_sol_prices_{int(datetime.now().timestamp())}"

    # Create and load staging table
    schema = [
        bigquery.SchemaField("price_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("price_usd", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("updated_at", "TIMESTAMP"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client().load_table_from_json(
        staging_rows, f"{PROJECT}.{staging_table}", job_config=job_config
    ).result()

    # MERGE into target
    merge_sql = f"""
        MERGE `{_table("sol_prices")}` T
        USING `{PROJECT}.{staging_table}` S
        ON T.price_date = S.price_date
        WHEN MATCHED THEN UPDATE SET price_usd = S.price_usd, updated_at = S.updated_at
        WHEN NOT MATCHED THEN INSERT (price_date, price_usd, updated_at)
             VALUES (S.price_date, S.price_usd, S.updated_at)
    """
    client().query(merge_sql).result()

    # Clean up staging
    client().delete_table(f"{PROJECT}.{staging_table}", not_found_ok=True)


def fetch_sol_price_map() -> dict[str, float]:
    """Return {'YYYY-MM-DD': price} map for the whole table."""
    query = f"SELECT price_date, price_usd FROM `{_table('sol_prices')}`"
    return {
        row["price_date"].strftime("%Y-%m-%d"): row["price_usd"]
        for row in client().query(query).result()
    }


# ---------------------------------------------------------------------------
# wallet_classifications
# ---------------------------------------------------------------------------
def insert_classifications(rows: list[dict]):
    """Append new classification results (never overwrite history)."""
    _load_rows("wallet_classifications", rows)

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


def fetch_wallets_needing_collection(max_age_hours: int = 48) -> list[dict]:
    """
    Wallets that either have never been collected, or were collected >N hours ago.
    Skips wallets marked as 'failed' permanently.

    Default 48h (was 24h): ~65% of daily-eligible wallets had no new swaps, so a
    24h window spent more Helius credits than needed. A 48h window roughly halves
    collection volume and smooths day-to-day load, at the cost of new swaps
    surfacing up to 2 days late — fine for a months-horizon tracker. Conservative
    middle ground; can shorten back toward 24h as the pipeline is optimized. See
    ADR 015 for why a per-wallet signature probe was rejected in favor of this.
    """
    query = f"""
        SELECT address, wallet_id
        FROM `{_table("wallets")}`
        WHERE collection_status != 'failed'
          AND (last_collected_at IS NULL
               OR TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), last_collected_at, HOUR) > @max_age)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("max_age", "INT64", max_age_hours)
        ]
    )
    return [dict(row) for row in client().query(query, job_config=job_config).result()]


def bulk_update_wallet_status(wallet_ids: list[str], status: str):
    """Mark many wallets' collection_status + last_collected_at=now in ONE
    UPDATE statement.

    Replaces the old per-wallet version: at 1300+ wallets/run that meant
    1300+ sequential DML jobs (~1-2s each, plus BigQuery's DML-per-table
    quota pressure). Batched, it's one statement per flush."""
    if not wallet_ids:
        return
    query = f"""
        UPDATE `{_table("wallets")}`
        SET collection_status = @status,
            last_collected_at = CURRENT_TIMESTAMP()
        WHERE wallet_id IN UNNEST(@wids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids),
        ]
    )
    client().query(query, job_config=job_config).result()


def fetch_wallets_needing_backfill(limit: int | None = None) -> list[dict]:
    """Wallets whose deep history is worth pulling: their LATEST classification is
    both smart_candidate (we care about them) and data_clipped (stats built on a
    2000-tx-truncated window), and they haven't been backfilled yet.

    Self-targeting + idempotent: re-running picks up newly clipped smart
    candidates and skips ones already stamped backfilled_at. wallet_classifications
    is append-only, so we take the most recent row per wallet.
    """
    query = f"""
        WITH latest AS (
          SELECT wallet_id, tags,
                 ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) AS rn
          FROM `{_table("wallet_classifications")}`
        )
        SELECT w.address, w.wallet_id
        FROM `{_table("wallets")}` w
        JOIN latest c ON c.wallet_id = w.wallet_id AND c.rn = 1
        WHERE w.backfilled_at IS NULL
          AND 'smart_candidate' IN UNNEST(c.tags)
          AND 'data_clipped' IN UNNEST(c.tags)
    """
    if limit is not None:
        query += f"\n        LIMIT {int(limit)}"
    return [dict(row) for row in client().query(query).result()]


def oldest_signatures_by_wallet(wallet_ids: list[str]) -> dict[str, str]:
    """The OLDEST signature we already have per wallet — the starting `before`
    cursor for a reverse (older-than) backfill traversal. ONE query."""
    if not wallet_ids:
        return {}
    query = f"""
        SELECT wallet_id, signature FROM (
          SELECT wallet_id, signature,
                 ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY tx_timestamp ASC) AS rn
          FROM `{_table("raw_swaps")}`
          WHERE wallet_id IN UNNEST(@wids)
        )
        WHERE rn = 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids)]
    )
    return {
        row["wallet_id"]: row["signature"]
        for row in client().query(query, job_config=job_config).result()
    }


def mark_wallets_backfilled(wallet_ids: list[str]):
    """Stamp backfilled_at=now so re-runs skip these wallets (whether they were
    fully traversed or stopped at the safety cap). ONE UPDATE."""
    if not wallet_ids:
        return
    query = f"""
        UPDATE `{_table("wallets")}`
        SET backfilled_at = CURRENT_TIMESTAMP()
        WHERE wallet_id IN UNNEST(@wids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids)]
    )
    client().query(query, job_config=job_config).result()


# ---------------------------------------------------------------------------
# raw_transfers  (token TRANSFER history; parallels raw_swaps, parsed later)
# ---------------------------------------------------------------------------
def fetch_wallets_needing_transfers(limit: int | None = None) -> list[dict]:
    """smart_candidate wallets whose TRANSFER history hasn't been collected yet.

    Scoped to smart candidates on purpose: transfers exist to fix PnL for the
    wallets we actually evaluate as smart money. Pulling them for MM/bot/excluded
    wallets (already set aside) would just burn credits. Uses the latest
    classification per wallet (wallet_classifications is append-only); self-
    targeting via transfers_collected_at, so re-running picks up newly-promoted
    smart candidates and skips done ones.
    """
    query = f"""
        WITH latest AS (
          SELECT wallet_id, tags,
                 ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) AS rn
          FROM `{_table("wallet_classifications")}`
        )
        SELECT w.address, w.wallet_id
        FROM `{_table("wallets")}` w
        JOIN latest c ON c.wallet_id = w.wallet_id AND c.rn = 1
        WHERE w.transfers_collected_at IS NULL
          AND 'smart_candidate' IN UNNEST(c.tags)
    """
    if limit is not None:
        query += f"\n        LIMIT {int(limit)}"
    return [dict(row) for row in client().query(query).result()]


def existing_transfer_signatures_by_wallet(
    wallet_ids: list[str],
) -> dict[str, set[str]]:
    """Signatures already in raw_transfers, grouped by wallet — the dedup baseline
    for transfer collection. Mirrors existing_signatures_by_wallet (raw_swaps)."""
    if not wallet_ids:
        return {}
    query = f"""
        SELECT wallet_id, signature
        FROM `{_table("raw_transfers")}`
        WHERE wallet_id IN UNNEST(@wids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids)]
    )
    result: dict[str, set[str]] = {}
    for row in client().query(query, job_config=job_config).result():
        result.setdefault(row["wallet_id"], set()).add(row["signature"])
    return result


def insert_transfer_rows(rows: list[dict]):
    """Bulk-load pre-built rows into raw_transfers. Rows are built by the generic
    build_raw_swap_rows (same shape), just a different target table."""
    _load_rows("raw_transfers", rows)


def mark_wallets_transfers_collected(wallet_ids: list[str]):
    """Stamp transfers_collected_at=now so re-runs skip these wallets."""
    if not wallet_ids:
        return
    query = f"""
        UPDATE `{_table("wallets")}`
        SET transfers_collected_at = CURRENT_TIMESTAMP()
        WHERE wallet_id IN UNNEST(@wids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids)]
    )
    client().query(query, job_config=job_config).result()


# ---------------------------------------------------------------------------
# raw_swaps
# ---------------------------------------------------------------------------
def existing_signatures_by_wallet(wallet_ids: list[str]) -> dict[str, set[str]]:
    """Signatures already in raw_swaps, grouped by wallet — ONE query.

    Replaces the per-wallet N+1 version (one BQ query per wallet, 1300+
    sequential round-trips per run). The whole snapshot for ~220K signatures
    is ~50-100MB of Python sets — bounded and cheap compared to the
    accumulated cost of thousands of query jobs."""
    if not wallet_ids:
        return {}
    query = f"""
        SELECT wallet_id, signature
        FROM `{_table("raw_swaps")}`
        WHERE wallet_id IN UNNEST(@wids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("wids", "STRING", wallet_ids)]
    )
    result: dict[str, set[str]] = {}
    for row in client().query(query, job_config=job_config).result():
        result.setdefault(row["wallet_id"], set()).add(row["signature"])
    return result


def build_raw_swap_rows(wallet_id: str, txs: Iterable[dict]) -> list[dict]:
    """Convert raw Helius txs into raw_swaps row dicts WITHOUT loading them.

    Split out from the old insert_raw_swaps so the collector can buffer rows
    across wallets and flush in large batches (one load job per ~5000 rows
    instead of one per wallet)."""
    now = _utcnow_iso()
    rows = []
    for tx in txs:
        sig = tx.get("signature")
        ts = tx.get("timestamp")
        if not sig or ts is None:
            continue
        rows.append(
            {
                "wallet_id": wallet_id,
                "signature": sig,
                "tx_time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "tx_timestamp": ts,
                "source": tx.get("source"),
                "raw_json": json.dumps(tx, ensure_ascii=False),
                "collected_at": now,
            }
        )
    return rows


def insert_raw_swap_rows(rows: list[dict]):
    """Bulk-load pre-built raw_swaps rows (possibly spanning many wallets)."""
    _load_rows("raw_swaps", rows)


def fetch_raw_swaps_all_wallets(
    wallet_ids: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Raw swaps grouped by wallet_id.

    If `wallet_ids` is given, only those wallets are fetched — used by
    incremental classification to avoid pulling all raw_json (which can be
    multi-GB once the dataset matures).
    """
    base = f"""
        SELECT wallet_id, signature, raw_json
        FROM `{_table("raw_swaps")}`
    """
    job_config = None
    if wallet_ids is None:
        query = base + " ORDER BY wallet_id, tx_timestamp"
    else:
        if not wallet_ids:
            return {}
        query = (
            base + " WHERE wallet_id IN UNNEST(@ids) ORDER BY wallet_id, tx_timestamp"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", wallet_ids)]
        )

    result: dict[str, list[dict]] = {}
    for row in client().query(query, job_config=job_config).result():
        tx = json.loads(row["raw_json"])
        tx["_signature"] = row["signature"]
        result.setdefault(row["wallet_id"], []).append(tx)
    return result


def fetch_raw_transfers_all_wallets(
    wallet_ids: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Raw transfers grouped by wallet_id (parsed JSON). Mirrors
    fetch_raw_swaps_all_wallets but reads raw_transfers. Classification uses these
    to find which token mints a wallet transferred in/out — those positions have
    no on-chain cost basis, so they're excluded from PnL."""
    base = f"""
        SELECT wallet_id, raw_json
        FROM `{_table("raw_transfers")}`
    """
    job_config = None
    if wallet_ids is None:
        query = base + " ORDER BY wallet_id, tx_timestamp"
    else:
        if not wallet_ids:
            return {}
        query = (
            base + " WHERE wallet_id IN UNNEST(@ids) ORDER BY wallet_id, tx_timestamp"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", wallet_ids)]
        )
    result: dict[str, list[dict]] = {}
    for row in client().query(query, job_config=job_config).result():
        result.setdefault(row["wallet_id"], []).append(json.loads(row["raw_json"]))
    return result


def fetch_unanalyzed_wallet_ids(parser_version: int) -> list[str]:
    """Wallet_ids that have at least one raw_swap not yet analyzed at this
    parser_version. Cheap: returns only the ID list, NOT the raw_json.

    Lets analyze_wallets discover "who needs work" without pulling any payloads,
    so it can then fetch the heavy raw_json one batch of wallets at a time
    (see fetch_unanalyzed_raw_swaps(wallet_ids=...)). This is the memory-bound
    half of the chunked Step-4 design — the snapshot of all unanalyzed payloads
    is what used to OOM on backfill / parser_version bumps.
    """
    query = f"""
        SELECT DISTINCT r.wallet_id
        FROM `{_table("raw_swaps")}` r
        LEFT JOIN `{_table("analyzed_swaps")}` a
          ON r.wallet_id = a.wallet_id
         AND r.signature = a.signature
         AND a.parser_version = @v
        WHERE a.signature IS NULL
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("v", "INT64", parser_version)]
    )
    return [row["wallet_id"] for row in client().query(query, job_config=job_config).result()]


def fetch_unanalyzed_raw_swaps(
    parser_version: int, wallet_ids: list[str] | None = None
) -> dict[str, list[dict]]:
    """
    Anti-join: return raw_swaps that don't have a matching row in analyzed_swaps
    at the given parser_version. Lets BQ compute the diff — Python only receives
    rows that actually need processing.

    `wallet_ids` (optional) scopes the result to one batch of wallets, so the
    caller can process unanalyzed payloads in bounded chunks instead of pulling
    every unanalyzed row at once. Without it, behavior is unchanged (all
    unanalyzed rows) — but at scale that snapshot is the OOM bomb, so the
    pipeline always passes a batch.
    """
    where_ids = ""
    params = [bigquery.ScalarQueryParameter("v", "INT64", parser_version)]
    if wallet_ids is not None:
        if not wallet_ids:
            return {}
        where_ids = "AND r.wallet_id IN UNNEST(@ids)"
        params.append(bigquery.ArrayQueryParameter("ids", "STRING", wallet_ids))

    query = f"""
        SELECT r.wallet_id, r.signature, r.raw_json
        FROM `{_table("raw_swaps")}` r
        LEFT JOIN `{_table("analyzed_swaps")}` a
          ON r.wallet_id = a.wallet_id
         AND r.signature = a.signature
         AND a.parser_version = @v
        WHERE a.signature IS NULL
          {where_ids}
        ORDER BY r.wallet_id, r.tx_timestamp
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
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


def fetch_analyzed_swaps_all_wallets(
    wallet_ids: list[str] | None = None,
) -> dict[str, list[dict]]:
    """All analyzed swaps grouped by wallet_id.

    If `wallet_ids` is given, only those wallets are fetched — used by
    incremental classification to skip wallets whose data hasn't changed.
    """
    base = f"""
        SELECT wallet_id, signature, swap_time, sol_price_usd, sol_spent, sol_received,
               token_spent, token_received
        FROM `{_table("analyzed_swaps")}`
    """
    job_config = None
    if wallet_ids is None:
        query = base + " ORDER BY wallet_id, swap_time"
    else:
        if not wallet_ids:
            return {}
        query = base + " WHERE wallet_id IN UNNEST(@ids) ORDER BY wallet_id, swap_time"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", wallet_ids)]
        )

    result: dict[str, list[dict]] = {}
    for row in client().query(query, job_config=job_config).result():
        result.setdefault(row["wallet_id"], []).append(dict(row))
    return result


def fetch_wallets_needing_classification() -> list[str]:
    """Wallet_ids whose analyzed_swaps are newer than their last classification.

    Includes wallets never classified before. Lets filter_traders skip wallets
    whose data hasn't changed since the previous run — turning a full re-scan
    of every wallet into an incremental pass over only what actually changed.
    """
    query = f"""
        WITH last_classified AS (
          SELECT wallet_id, MAX(classified_at) AS last_at
          FROM `{_table("wallet_classifications")}`
          GROUP BY wallet_id
        )
        SELECT DISTINCT a.wallet_id
        FROM `{_table("analyzed_swaps")}` a
        LEFT JOIN last_classified lc USING(wallet_id)
        WHERE lc.last_at IS NULL OR a.analyzed_at > lc.last_at
    """
    return [row["wallet_id"] for row in client().query(query).result()]


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


# ---------------------------------------------------------------------------
# wallet_candidates / wallet_sources / ingestion_runs  (Phase 4: ingestion)
# ---------------------------------------------------------------------------
"""
    Merge candidates into wallet_candidates.
        - New (address, source) pair  -> INSERT with status='pending'
        - Existing pair               -> UPDATE raw_metrics (status preserved,
                                        so 'promoted' / 'filtered_out' stick)
    Returns (new_count, updated_count).
    """
def upsert_wallet_candidates(
    candidates: list,  # list of adapters.Candidate
    source: str,
    source_query_id: str | None,
) -> tuple[int, int]:
    
    if not candidates:
        return (0, 0)
    #The timestamp of a batch of candidates.
    now = _utcnow_iso()
    staging_rows = [
        {
            "address": c.address,
            "chain": c.chain,
            "source": source,
            "source_query_id": source_query_id,
            "discovered_at": now,
            "raw_metrics": json.dumps(c.raw_metrics, default=str, ensure_ascii=False),
        }
        for c in candidates
    ]
    staging_table = f"{DATASET}._staging_candidates_{int(datetime.now().timestamp())}"
    schema = [
        bigquery.SchemaField("address", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("chain", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source_query_id", "STRING"),
        bigquery.SchemaField("discovered_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("raw_metrics", "JSON"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client().load_table_from_json(
        staging_rows, f"{PROJECT}.{staging_table}", job_config=job_config
    ).result()

    # Count "new" before MERGE (rows in staging not yet in target).
    count_sql = f"""
        SELECT COUNT(*) AS n
        FROM `{PROJECT}.{staging_table}` S
        LEFT JOIN `{_table("wallet_candidates")}` T
          ON T.address = S.address AND T.source = S.source
        WHERE T.address IS NULL
    """
    new_count = list(client().query(count_sql).result())[0]["n"]

    merge_sql = f"""
        MERGE `{_table("wallet_candidates")}` T
        USING `{PROJECT}.{staging_table}` S
          ON T.address = S.address AND T.source = S.source
        WHEN MATCHED THEN UPDATE SET
            raw_metrics = S.raw_metrics,
            source_query_id = S.source_query_id
        WHEN NOT MATCHED THEN INSERT (
            address, chain, source, source_query_id, discovered_at, raw_metrics, status
        ) VALUES (
            S.address, S.chain, S.source, S.source_query_id, S.discovered_at,
            S.raw_metrics, 'pending'
        )
    """
    client().query(merge_sql).result()
    client().delete_table(f"{PROJECT}.{staging_table}", not_found_ok=True)
    return (new_count, len(staging_rows) - new_count)


def upsert_wallet_sources(addresses: list[str], source: str):
    """
    Upsert (address, source) provenance.
      - New      -> first_seen_at = last_seen_at = now, seen_count = 1
      - Existing -> last_seen_at = now, seen_count += 1
    """
    if not addresses:
        return
    now = _utcnow_iso()
    staging_rows = [{"address": a, "source": source, "ts": now} for a in addresses]
    staging_table = f"{DATASET}._staging_sources_{int(datetime.now().timestamp())}"
    schema = [
        bigquery.SchemaField("address", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ts", "TIMESTAMP", mode="REQUIRED"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client().load_table_from_json(
        staging_rows, f"{PROJECT}.{staging_table}", job_config=job_config
    ).result()

    merge_sql = f"""
        MERGE `{_table("wallet_sources")}` T
        USING `{PROJECT}.{staging_table}` S
          ON T.address = S.address AND T.source = S.source
        WHEN MATCHED THEN UPDATE SET
            last_seen_at = S.ts,
            seen_count = T.seen_count + 1
        WHEN NOT MATCHED THEN INSERT (
            address, source, first_seen_at, last_seen_at, seen_count
        ) VALUES (
            S.address, S.source, S.ts, S.ts, 1
        )
    """
    client().query(merge_sql).result()
    client().delete_table(f"{PROJECT}.{staging_table}", not_found_ok=True)


def fetch_exchange_wallet_addresses() -> set[str]:
    """CEX / market-maker deposit addresses — used by filter as a blacklist."""
    query = f"SELECT address FROM `{_table('exchange_wallets')}`"
    return {row["address"] for row in client().query(query).result()}


def fetch_existing_wallet_addresses() -> set[str]:
    """Addresses already in the wallets table — don't re-insert."""
    query = f"SELECT address FROM `{_table('wallets')}`"
    return {row["address"] for row in client().query(query).result()}


def insert_wallets_from_candidates(
    addresses: list[str], source: str, chain: str = "solana"
):
    """
    Promote candidate addresses into the wallets table.
    wallet_id convention: address[:8] (matches infra/migrate_to_bigquery.py).
    Caller must ensure these addresses are NOT already in wallets.
    """
    if not addresses:
        return
    now = _utcnow_iso()
    rows = [
        {
            "address": addr,
            "wallet_id": addr[:8],
            "chain": chain,
            "discovered_at": now,
            "collection_status": "pending",
            "status": "active",
            "promoted_from": source,
        }
        for addr in addresses
    ]
    _load_rows("wallets", rows)


def mark_candidates_promoted(addresses: list[str], source: str):
    """Mark wallet_candidates.status='promoted' (they made it into wallets)."""
    if not addresses:
        return
    query = f"""
        UPDATE `{_table("wallet_candidates")}`
        SET status = 'promoted'
        WHERE source = @source AND address IN UNNEST(@addrs)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("source", "STRING", source),
            bigquery.ArrayQueryParameter("addrs", "STRING", addresses),
        ]
    )
    client().query(query, job_config=job_config).result()


def mark_candidates_filtered(address_to_reason: dict[str, str], source: str):
    """
    Mark wallet_candidates.status='filtered_out' with filter_reason.
    Grouped by reason so we issue one UPDATE per reason (not per address).
    """
    if not address_to_reason:
        return
    from collections import defaultdict

    by_reason: dict[str, list[str]] = defaultdict(list)
    for addr, reason in address_to_reason.items():
        by_reason[reason].append(addr)
    for reason, addrs in by_reason.items():
        query = f"""
            UPDATE `{_table("wallet_candidates")}`
            SET status = 'filtered_out', filter_reason = @reason
            WHERE source = @source AND address IN UNNEST(@addrs)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("reason", "STRING", reason),
                bigquery.ArrayQueryParameter("addrs", "STRING", addrs),
            ]
        )
        client().query(query, job_config=job_config).result()


def log_ingestion_run(
    run_id: str,
    source: str,
    started_at: str,
    finished_at: str,
    status: str,  # 'success' | 'failed' | 'partial'
    candidates_fetched: int = 0,
    candidates_new: int = 0,
    promoted_to_wallets: int = 0,
    credits_used: int = 0,
    error_message: str | None = None,
):
    """Append one row to ingestion_runs (observability)."""
    row = {
        "run_id": run_id,
        "source": source,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "candidates_fetched": candidates_fetched,
        "candidates_new": candidates_new,
        "promoted_to_wallets": promoted_to_wallets,
        "credits_used": credits_used,
        "error_message": error_message,
    }
    _load_rows("ingestion_runs", [row])

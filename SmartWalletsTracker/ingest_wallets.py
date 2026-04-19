"""
Step 0 of the pipeline: pull wallet candidates from external sources,
filter them, and promote qualified ones into the wallets table.

Flow (per source):
    adapter.fetch_candidates()
        -> wallet_candidates (buffer, all rows)
        -> wallet_sources    (provenance: seen_count++)
        -> filter:
             - in CEX blacklist       -> filtered_out (known_cex)
             - already in wallets     -> promoted (but not re-inserted)
             - otherwise              -> inserted into wallets + promoted
        -> ingestion_runs (one row per run, success or failure)

Designed so adding a new source = adding one file under lib/adapters/ +
one line in get_sources(). No change to this orchestrator.
"""

import sys
import traceback
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from lib import bq
from lib.adapters import DuneAdapter, SourceAdapter

load_dotenv()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_sources() -> list[SourceAdapter]:
    """Add new adapters here. The orchestrator treats them uniformly."""
    return [DuneAdapter()]


def run_source(adapter: SourceAdapter) -> dict:
    """Run ingestion for one source. Always logs to ingestion_runs (success or failure)."""
    run_id = str(uuid.uuid4())
    source = adapter.source_name
    started_at = _utcnow_iso()

    print(f"[{source}] run {run_id[:8]} started")

    summary = {
        "source": source,
        "run_id": run_id,
        "candidates_fetched": 0,
        "candidates_new": 0,
        "promoted_to_wallets": 0,
        "status": "failed",
        "error_message": None,
    }

    try:
        # 1. Pull from source
        candidates = adapter.fetch_candidates()
        summary["candidates_fetched"] = len(candidates)
        print(f"[{source}] fetched {len(candidates)} candidates")

        if not candidates:
            summary["status"] = "success"
            return summary

        # 2. Buffer + provenance
        new_count, updated_count = bq.upsert_wallet_candidates(
            candidates, source, adapter.source_query_id
        )
        summary["candidates_new"] = new_count
        print(f"[{source}] candidates: {new_count} new, {updated_count} already buffered")

        addresses = [c.address for c in candidates]
        bq.upsert_wallet_sources(addresses, source)

        # 3. Filter
        cex_set = bq.fetch_exchange_wallet_addresses()
        existing_wallets = bq.fetch_existing_wallet_addresses()

        to_insert: list[str] = []
        to_mark_promoted: list[str] = []
        to_filter: dict[str, str] = {}

        for c in candidates:
            if c.address in cex_set:
                to_filter[c.address] = "known_cex"
            elif c.address in existing_wallets:
                # Already tracked (manual add or prior promotion) — keep provenance but don't re-insert
                to_mark_promoted.append(c.address)
            else:
                to_insert.append(c.address)
                to_mark_promoted.append(c.address)

        # 4. Apply outcomes
        if to_insert:
            bq.insert_wallets_from_candidates(to_insert, source)
            print(f"[{source}] inserted {len(to_insert)} new wallets")
        if to_mark_promoted:
            bq.mark_candidates_promoted(to_mark_promoted, source)
        if to_filter:
            bq.mark_candidates_filtered(to_filter, source)
            print(f"[{source}] filtered out {len(to_filter)} (CEX blacklist)")

        summary["promoted_to_wallets"] = len(to_insert)
        summary["status"] = "success"

    except Exception as e:
        summary["error_message"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"
        print(f"[{source}] FAILED: {e}", file=sys.stderr)

    finally:
        bq.log_ingestion_run(
            run_id=run_id,
            source=source,
            started_at=started_at,
            finished_at=_utcnow_iso(),
            status=summary["status"],
            candidates_fetched=summary["candidates_fetched"],
            candidates_new=summary["candidates_new"],
            promoted_to_wallets=summary["promoted_to_wallets"],
            error_message=summary["error_message"],
        )

    return summary


def main():
    print("=" * 60)
    print("Wallet Ingestion — Step 0")
    print("=" * 60)

    all_summaries = []
    for adapter in get_sources():
        summary = run_source(adapter)
        all_summaries.append(summary)
        print(f"[{summary['source']}] status={summary['status']} "
              f"fetched={summary['candidates_fetched']} "
              f"new={summary['candidates_new']} "
              f"promoted={summary['promoted_to_wallets']}")

    print("\nIngestion complete.")


if __name__ == "__main__":
    main()

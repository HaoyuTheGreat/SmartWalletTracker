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

# Cap how many brand-new wallets we promote from candidates -> wallets per run.
# The Dune query returns a big pool (~5000); we drip-feed to bound Helius API
# cost on the next stage. Pool is exhausted over time; candidates come ordered
# by volume DESC, so each day we promote the next tier down.
DAILY_PROMOTION_LIMIT = 45  # raised 20 -> 45 (more headroom on the Helius Dev plan)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


"""
DuneAdapter() is class instantiation(创建一个class的实例)
DuneAdapater是我们从lib.adapters.dune_adapter.py拿到的class，是一个实例。
So inside get_sources() DuneAdapter() creates one instance(实例) of that class. 
and the instance is wrapped in a list and returned. So get_sources() returns a list containing one DuneAdapter instance.
"""


def get_sources() -> list[SourceAdapter]:
    """
    Declare which data sources to ingest for this pipeline run.

    Returns a list of SourceAdapter instances-each one is set up with the API key and query ID it needs.
    The pipeline iterates over this list and runs each adapter sequentially — no selection logic;
    every adapter in the list gets processed in order.

    Adding a new source (e.g. BirdEye, Arkham): write a new adapter class
    that implements SourceAdapter, then append an instance to this list.
    No changes needed to ingest_wallets.main() or run_source() — that's the
    Adapter Pattern + Open/Closed Principle at work.

    Future example:
        return [DuneAdapter(), BirdEyeAdapter(), ArkhamAdapter()]
    """
    dune_obj = DuneAdapter()
    return [dune_obj]


"""
The worker function. It takes one adapter, calls its fetch_candidates() method,
which is the moment the API call actually happens. Then writes results to BigQuery,
applies filtering, promotes wallets, logs audit.
"""


def run_source(adapter: SourceAdapter) -> dict:
    """Run ingestion for one source. Always logs to ingestion_runs (success or failure)."""
    # Generates a unique random identifier for this ingestion run, stored as a string.
    # Every time the run_source(adapter) is called, generate a run id for each data source.
    run_id = str(uuid.uuid4())
    # Access the source_name attribute on the adapter instance-for DuneAdapter, this returns the string "dune"
    # It is stored in the source variable for later use in BQ writes(which source did this data come from)
    source = adapter.source_name
    started_at = _utcnow_iso()

    print(f"[{source}] run {run_id[:8]} started")

    # pre-initialize the summary dict before any work begins, for 3 reasons:
    # 1.Guaranteed availability in 'finally', even if a step below crashes,
    #   'summary' already exists and can be written to ingestion_runs.
    #   If we built it at the end, an early crash would lose the audit trail.
    # 2.Pessimistic default(status=failed), only flips to 'success' if every step completes.
    #   Any crash in between, the log row keeps 'failed', so there would not be false-positive success records.
    # 3.Single source of truth, the same dict feeds both the BQ audit logs(in the 'finally' block)
    #   and the caller's console output
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
        # new_count: the new address and source pair added.(If a addr appears in multiple sources, those sources will be added.)
        # updated_count: how many address and source pair are updated.
        new_count, updated_count = bq.upsert_wallet_candidates(
            candidates, source, adapter.source_query_id
        )
        summary["candidates_new"] = new_count
        print(
            f"[{source}] candidates: {new_count} new, {updated_count} already buffered"
        )
        #Extract address strings from object candidates.
        addresses = [c.address for c in candidates]
        # Provenance table: write the addr to the table of wallet_source.
        bq.upsert_wallet_sources(addresses, source)

        # 3. Filter
        cex_set = bq.fetch_exchange_wallet_addresses()
        existing_wallets = bq.fetch_existing_wallet_addresses()

        # Allocate empty containers, then the loop below will classify each candidate and put it into the right container.
        to_insert_all: list[str] = []  # new wallets, ordered by priority
        already_tracked: list[str] = []  # already in wallets, just refresh provenance
        to_filter: dict[str, str] = {}

        for c in candidates:
            if c.address in cex_set:
                # CEX hot wallets aren't "smart money" traders — flag them for status='filtered_out'.
                to_filter[c.address] = "known_cex"
            elif c.address in existing_wallets:
                # Already in wallets table — don't re-INSERT, but still refresh wallet_sources provenance.
                already_tracked.append(c.address)
            else:
                # New & qualified — eligible to be promoted into wallets (top N selected later).
                # Has all the New&Qualified addr.
                to_insert_all.append(c.address)

        # 4. Rate-limited promotion: take the top N new wallets this run,
        # leave the rest in wallet_candidates with status='pending' for future runs.
        to_insert_today = to_insert_all[:DAILY_PROMOTION_LIMIT] # The addr that are ready to be promoted.
        deferred_count = len(to_insert_all) - len(to_insert_today)

        # Skip the INSERT + log if there's nothing to promote (e.g., all 5000 candidates
        # were either CEX-filtered or already in wallets table).
        if to_insert_today:
            # INSERT into the wallets table (business-tier). status='promoted' is marked separately below.
            bq.insert_wallets_from_candidates(to_insert_today, source)
            print(
                f"[{source}] promoted {len(to_insert_today)} new wallets "
                f"({deferred_count} deferred to future runs)"
            )
        elif deferred_count == 0:
            print(f"[{source}] no new wallets to promote")

        promoted_now = to_insert_today + already_tracked
        if promoted_now:
            bq.mark_candidates_promoted(promoted_now, source)
        if to_filter:
            bq.mark_candidates_filtered(to_filter, source)
            print(f"[{source}] filtered out {len(to_filter)} (CEX blacklist)")

        summary["promoted_to_wallets"] = len(to_insert_today)
        summary["status"] = "success"

    except Exception as e:
        summary["error_message"] = (
            f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"
        )
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
    # In this loop, iterates over each instance returned by get_sources().
    for adapter in get_sources():
        summary = run_source(adapter)
        all_summaries.append(summary)
        print(
            f"[{summary['source']}] status={summary['status']} "
            f"fetched={summary['candidates_fetched']} "
            f"new={summary['candidates_new']} "
            f"promoted={summary['promoted_to_wallets']}"
        )

    print("\nIngestion complete.")


if __name__ == "__main__":
    main()

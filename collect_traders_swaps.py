"""
collect_traders_swaps.py - Daily incremental collection of wallet swaps into BQ.

Flow:
  1. Read wallets that need a refresh from BQ (last fetched >48h ago, or never).
  2. ONE snapshot query: all existing signatures for those wallets (dedup baseline).
  3. Fan out async workers (see lib.async_collect); each STREAMS one wallet's
     Helius pages newest-first, stopping at a known signature (incremental) or at
     MAX_TX_PER_WALLET (cost cap).
  4. A single writer buffers row-batches and flushes to raw_swaps, then marks the
     fully-fetched wallets ok.
  5. Bulk-mark wallets with no data at all as 'failed'.

The async engine (concurrency, retry, streaming, the single writer) lives in
lib.async_collect and is shared with backfill_swaps.py; this file only owns the
daily wallet selection + per-wallet outcome logic.

Cost note (2026-06, ADR 015): the Enhanced Transactions API (parsed swap history)
costs ~100x a standard RPC call. We call it once per eligible wallet, and ~65% of
those have no new swaps — wasted spend. A cheap getSignaturesForAddress
"probe-first" idea was prototyped and rejected (a dry-run showed it would skip
only ~7%, because that endpoint can't filter by tx type and these wallets are
constantly active in non-swap ways). The pragmatic lever instead: the refresh
window is 48h, not 24h, which roughly halves collection volume.
"""

import asyncio
import sys
import time

from lib import bq
from lib.async_collect import CollectionError, iter_new_swaps, run_collection

sys.stdout.reconfigure(encoding="utf-8")

MAX_TX_PER_WALLET = 2000  # Daily per-wallet cap to bound API spend


async def _process_wallet(client, w, rows_q, sig_map):
    """Daily refresh of one wallet: stream newest-first, stop at a known sig, push
    rows + a 'done' outcome (ok / up-to-date→ok / failed) to the writer queue."""
    wallet_id = w["wallet_id"]
    address = w["address"]
    existing_sigs = sig_map.get(wallet_id, set())

    got = 0
    try:
        async for batch in iter_new_swaps(
            client, address, existing_sigs, max_tx=MAX_TX_PER_WALLET
        ):
            got += len(batch)
            await rows_q.put(("rows", wallet_id, batch))
    except CollectionError as e:
        # HARD API failure — leave the wallet untouched (not ok, last_collected_at
        # unchanged) so it stays eligible and retries next run. Rows already pushed
        # are deduped on retry, never duplicated.
        await rows_q.put(("done", wallet_id, "error"))
        print(f"[{wallet_id}] API error — will retry next run: {e}")
        return

    if got:
        await rows_q.put(("done", wallet_id, "ok"))
        print(f"[{wallet_id}] fetched {got} new swaps")
    elif existing_sigs:
        # No new data, but we have history — wallet is just up to date.
        await rows_q.put(("done", wallet_id, "ok"))
        print(f"[{wallet_id}] up to date")
    else:
        # Never had any data AND Helius returned nothing — mark failed.
        await rows_q.put(("done", wallet_id, "failed"))
        print(f"[{wallet_id}] failed (no swaps found)")


def main():
    start = time.monotonic()

    # Refresh window lives in lib.bq (default 48h, see ADR 015) — call with no
    # arg so there's ONE source of truth. (A previous explicit max_age_hours=24
    # here silently overrode the 48h default, so the documented change never
    # actually took effect.)
    wallets = bq.fetch_wallets_needing_collection()
    print(f"Found {len(wallets)} wallets needing collection")
    if not wallets:
        return

    # ONE snapshot query instead of one query per wallet (the old N+1).
    sig_map = bq.existing_signatures_by_wallet([w["wallet_id"] for w in wallets])
    print(f"Loaded signature snapshot for {len(sig_map)} wallets")

    async def process(client, w, rows_q):
        await _process_wallet(client, w, rows_q, sig_map)

    stats = asyncio.run(
        run_collection(
            wallets,
            process,
            lambda ids: bq.bulk_update_wallet_status(ids, "ok"),
            bq.insert_raw_swap_rows,
        )
    )

    if stats["failed"]:
        bq.bulk_update_wallet_status(stats["failed"], "failed")
        print(f"Marked {len(stats['failed'])} wallets failed")

    api_errors = stats["errors"]
    # Fail LOUD on a broad outage: if most wallets hit API errors, data is
    # silently not being collected. Raise so the pipeline reports failure (Step 3
    # is fail-hard → honest status dot goes red) instead of a false SUCCESS — the
    # exact failure mode that hid the 2026-06 host change for a day.
    if wallets and api_errors > len(wallets) * 0.5:
        raise RuntimeError(
            f"Helius API hard-failed on {api_errors}/{len(wallets)} wallets — "
            "likely an endpoint/auth outage. Failing the run loudly."
        )
    if api_errors:
        print(f"{api_errors} wallets had API errors (will retry next run)")

    print(f"Done in {time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    main()

"""
backfill_swaps.py - One-off, re-runnable backfill of older swap history.

Daily collection (collect_traders_swaps.py) only goes FORWARD (newest-first,
stops at the first known signature), so any gap in a wallet's OLDEST history is
never self-healed. This script pages OLDER, from each wallet's oldest known
signature down to its true beginning (or BACKFILL_MAX_TX), for wallets whose
stats are built on a truncated window: tagged smart_candidate AND data_clipped
and not yet backfilled.

Self-targeting + idempotent: it re-derives its targets from BQ each run, so
re-running picks up newly clipped smart candidates and skips ones already stamped
backfilled_at. Crash-safe + resumable: rows are flushed before the marker, and
the next run resumes from the oldest sig now in BQ.

Run:
    python backfill_swaps.py        # backfill ALL eligible wallets
    python backfill_swaps.py 10     # backfill only 10 (small test run)

Shares the async engine (concurrency, retry, streaming, single writer) with the
daily collector via lib.async_collect; this file only owns the backfill wallet
selection, the reverse-traversal per-wallet logic, and the backfilled marker.

Finding (2026-06): only ~4/199 clipped smart candidates are actually at the 2000
cap; most data_clipped is transfer-caused (tokens transferred in, not bought via
swap), which swap-backfill can't fix — that's what transfer ingestion is for.
Backfill still helps the cap-truncated ones and heals wallets whose initial
collection never reached their beginning.
"""

import asyncio
import sys
import time

from lib import bq
from lib.async_collect import CollectionError, iter_new_swaps, run_collection

sys.stdout.reconfigure(encoding="utf-8")

# Backfill per-wallet cap. Smart swing traders rarely exceed a few thousand
# lifetime swaps, so 10k covers them while still stopping a mislabeled market
# maker before it runs away (MMs do 50k+/yr). Daily collection keeps the 2k cap.
BACKFILL_MAX_TX = 10000


async def _process_wallet(client, w, rows_q, sig_map, oldest_map):
    """Backfill one wallet: page OLDER than its oldest known signature (reverse
    traversal) up to BACKFILL_MAX_TX, push rows + a 'done' outcome.

    got=0 just means the wallet was already at its true beginning — still 'done'
    (mark backfilled). A failure leaves it unmarked so the next run retries it;
    rows already flushed are deduped (their sigs are in the snapshot)."""
    wallet_id = w["wallet_id"]
    address = w["address"]
    existing_sigs = sig_map.get(wallet_id, set())

    got = 0
    try:
        async for batch in iter_new_swaps(
            client,
            address,
            existing_sigs,
            max_tx=BACKFILL_MAX_TX,
            start_before=oldest_map.get(wallet_id),
            stop_at_known=False,
        ):
            got += len(batch)
            await rows_q.put(("rows", wallet_id, batch))
    except CollectionError as e:
        await rows_q.put(("done", wallet_id, "error"))
        print(f"[{wallet_id}] backfill error — will retry next run: {e}")
        return

    # got>0 (filled older history) or got==0 (already at its beginning) → either
    # way the wallet is now fully backfilled.
    await rows_q.put(("done", wallet_id, "ok"))
    print(f"[{wallet_id}] backfilled {got} older swaps")


def main(limit=None):
    start = time.monotonic()

    wallets = bq.fetch_wallets_needing_backfill(limit=limit)
    print(f"Found {len(wallets)} wallets needing backfill")
    if not wallets:
        return

    wids = [w["wallet_id"] for w in wallets]
    sig_map = bq.existing_signatures_by_wallet(wids)
    oldest_map = bq.oldest_signatures_by_wallet(wids)
    print(f"Loaded snapshot + oldest-cursor for {len(wids)} wallets")

    async def process(client, w, rows_q):
        await _process_wallet(client, w, rows_q, sig_map, oldest_map)

    stats = asyncio.run(run_collection(wallets, process, bq.mark_wallets_backfilled))

    api_errors = stats["errors"]
    if wallets and api_errors > len(wallets) * 0.5:
        raise RuntimeError(
            f"Backfill hard-failed on {api_errors}/{len(wallets)} wallets — "
            "likely an endpoint/auth outage. Failing the run loudly."
        )
    if api_errors:
        print(f"{api_errors} wallets had API errors (will retry next backfill run)")

    print(f"Backfill done in {time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    n = sys.argv[1] if len(sys.argv) > 1 else ""
    main(limit=int(n) if n.isdigit() else None)

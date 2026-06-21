"""
collect_traders_swaps.py - Pull wallet swap txs from Helius into BigQuery raw_swaps.

Flow:
  1. Read wallets that need a refresh from BQ (last fetched >48h ago, or never fetched).
  2. ONE snapshot query: all existing signatures for those wallets (dedup baseline).
  3. Fan out CONCURRENCY async workers; each STREAMS one wallet's Helius pages
     newest-first (one page in memory), stopping at a known signature (incremental)
     or at MAX_TX_PER_WALLET (cost cap).
  4. Workers push row-batches to a SINGLE writer coroutine, which buffers across
     all wallets and flushes to raw_swaps every FLUSH_ROWS rows, then marks the
     fully-fetched wallets ok.
  5. Bulk-mark wallets with no data at all as 'failed'.

Concurrency note (2026-06): collection is I/O-bound (each wallet is mostly network
wait), so N worker coroutines fetch wallets in parallel via httpx while ONE writer
coroutine owns every BigQuery write (so there are no buffer races). Worker count
caps in-flight requests — each worker makes ≤1 request at a time — so it doubles
as the rate limiter (no separate semaphore needed). Blocking BQ calls run in a
thread executor so they never freeze the event loop. ~10-20x faster than the old
serial loop, bounded by the Helius rate limit rather than by Python.

Cost note (2026-06, ADR 015): the Enhanced Transactions API (parsed swap
history) costs ~100x a standard RPC call. We call it once per eligible wallet,
and ~65% of those wallets turn out to have no new swaps — wasted spend. A
cheap getSignaturesForAddress "probe-first" idea was prototyped and rejected
(a dry-run showed it would skip only ~7%, because that endpoint can't filter
by transaction type and these wallets are constantly active in non-swap ways).
The pragmatic lever instead: the refresh window is 48h, not 24h, which roughly
halves collection volume. The precise per-wallet fix is deferred to post-upgrade.

Memory note (2026-06 OOM fix): the previous design did one signature query +
one load job + one status UPDATE *per wallet*. At 1300+ wallets/run the
accumulated job state + transient buffers grew past the 8Gi container limit
and the run died on signal 9. This version is bounded by construction: one
snapshot query, a single row buffer capped at FLUSH_ROWS, a bounded work queue
(backpressure), and batched DML.

Per-wallet streaming (2026-06): iter_new_swaps yields pages instead of returning
a whole wallet's txs, so even a heavy/uncapped wallet holds only ~one page at a
time. This is the prerequisite for lifting MAX_TX_PER_WALLET (the backfill work).
"""

import asyncio
import sys
import time

import httpx

from lib import bq
from lib.secrets import get_secret

sys.stdout.reconfigure(encoding="utf-8")

HELIUS_API_KEY = get_secret("HELIUS_API_KEY")
MAX_TX_PER_WALLET = 2000  # Hard cap per wallet per run to bound API spend
PAGE_SIZE = 100
# Flush the cross-wallet row buffer at this size. ~5000 rows × ~10KB raw_json
# ≈ 50MB peak — bounds memory regardless of how many wallets a run processes.
FLUSH_ROWS = 5000

# Number of concurrent fetch workers. Each makes ≤1 request at a time, so this
# also caps in-flight requests (the de-facto rate limiter). The Enhanced API is
# slow per request (~1.4s), so even 40 workers stay well under the 50 RPS limit;
# tune vs observed throughput and 429s.
CONCURRENCY = 40
# Max row-batches queued from workers to the writer. Bounds memory via
# backpressure: when full, workers block until the writer drains.
ROWS_QUEUE_MAX = 50

# In-run retry policy for a single page fetch. 429/5xx are transient → retry with
# backoff. 401/403/400 are persistent → fail fast. 404 is AMBIGUOUS on this API:
# Helius signals "no swaps in this window" with a 404 + JSON body, while a retired
# host (#16) returns a 404 with a NON-JSON body — so for 404 the body decides
# (parseable JSON = nothing-new, non-JSON = dead endpoint). Failing fast on a dead
# endpoint keeps a host outage loud in seconds instead of burning an hour retrying.
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
HARD_FAIL_STATUS = {400, 401, 403}  # persistent failures regardless of body


class CollectionError(Exception):
    """A HARD Helius failure for one wallet (network error / dead endpoint /
    non-JSON body). Raised by iter_new_swaps so the caller can tell a real
    failure apart from "genuinely nothing new" — the latter just ends the
    generator. Conflating the two is what let a Helius host change silently
    break collection for a full day (2026-06, see DEVLOG #16)."""


async def _fetch_page(client: httpx.AsyncClient, url: str, label: str):
    """GET one page from Helius, with in-run retries on TRANSIENT errors.

    Returns the parsed JSON body (a list of txs on success, or a structured dict
    like {'error': 'Failed to find events...'} for "no swaps" — the caller
    interprets the content; a non-list just means nothing-new).

    Raises CollectionError when retries are exhausted, or immediately on a
    persistent failure (401/403/400, or a 404 with a non-JSON body = dead
    endpoint). Failing fast lets the caller's fail-loud fire quickly during a
    real outage instead of wasting retries.
    """
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(url)
        except httpx.RequestError as e:
            last_err = f"network error: {e}"  # transient → retry
        else:
            status = resp.status_code
            if status in RETRYABLE_STATUS:
                last_err = f"HTTP {status}: {resp.text[:80]}"  # 429/5xx → retry
            elif status in HARD_FAIL_STATUS:
                # auth / bad request — persistent, fail fast regardless of body.
                raise CollectionError(
                    f"{label}: HTTP {status} (not retryable): {resp.text[:80]}"
                )
            else:
                # 200, or 404. The body decides: parseable JSON is handed to the
                # caller (a list = data; a dict like {"error": "Failed to find
                # events..."} = nothing-new). A non-JSON body (e.g. a retired
                # host's bare "Not Found", #16) is a real dead-endpoint failure.
                try:
                    return resp.json()
                except ValueError:
                    raise CollectionError(
                        f"{label}: HTTP {status} non-JSON (dead endpoint?): {resp.text[:80]}"
                    )

        # Transient failure — back off (1s, 2s) before retrying, unless this was
        # the last attempt. asyncio.sleep (not time.sleep) so the event loop keeps
        # serving other workers while this one waits.
        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(2**attempt)

    raise CollectionError(f"{label}: failed after {MAX_ATTEMPTS} attempts — {last_err}")


async def iter_new_swaps(client: httpx.AsyncClient, address: str, existing_sigs: set):
    """
    Async-yield pages of NEW SWAP txs for a wallet, newest-first, stopping at a
    known signature (incremental) or at MAX_TX_PER_WALLET (cost cap).

    Streams page-by-page instead of accumulating the whole wallet: at any moment
    only one page (~100 txs) is held in memory, so memory stays bounded no matter
    how many txs a wallet has. Pages within a wallet are sequential (each needs
    the previous page's `before` cursor); concurrency is across wallets.

    Raises CollectionError on a HARD failure; ends normally (no yield) when there
    is genuinely nothing new. The raise-vs-return split is the streaming
    equivalent of the old None-vs-[] return — the caller must never mistake a
    failure for "up to date".
    """
    fetched = 0
    before = None

    while fetched < MAX_TX_PER_WALLET:
        # Host is mainnet.helius-rpc.com (per the Helius dashboard) — the old
        # api-mainnet.helius-rpc.com alias was retired and now 404s.
        url = (
            f"https://mainnet.helius-rpc.com/v0/addresses/{address}/transactions/"
            f"?api-key={HELIUS_API_KEY}&type=SWAP&limit={PAGE_SIZE}"
        )
        if before:
            url += f"&before={before}"

        # Transport + retry + error classification live in _fetch_page; here we
        # only interpret the content. A hard failure raises CollectionError.
        page = await _fetch_page(client, url, address[:8])

        if not page:
            return
        if not isinstance(page, list):
            # Structured response like {'error': 'Failed to find events within
            # the search period'} — a legit "no swaps in this window", NOT a
            # failure. End the generator.
            return

        fresh = []
        stop_early = False
        for tx in page:
            sig = tx.get("signature")
            if not sig:
                continue
            if sig in existing_sigs:
                # Helius returns newest-first: hitting a known sig means
                # everything older is also already in BQ.
                stop_early = True
                break
            fresh.append(tx)
            fetched += 1
            if fetched >= MAX_TX_PER_WALLET:
                break

        if fresh:
            yield fresh

        if stop_early or len(page) < PAGE_SIZE:
            return

        before = page[-1]["signature"]
        # No throttle sleep here — the worker count caps the request rate.


async def _worker(client, work_q, rows_q, sig_map):
    """Pull wallets from work_q and stream each one's pages, pushing items onto
    rows_q for the writer:
      ("rows", wallet_id, batch)        — a page of new txs to persist
      ("done", wallet_id, outcome)      — wallet finished; outcome in
                                          {"ok", "failed", "error"}
    A worker makes ≤1 request at a time, so CONCURRENCY workers ≈ CONCURRENCY
    in-flight requests. One wallet's failure never touches the others.
    """
    while True:
        try:
            w = work_q.get_nowait()
        except asyncio.QueueEmpty:
            return  # no wallets left — this worker is done

        wallet_id = w["wallet_id"]
        address = w["address"]
        existing_sigs = sig_map.get(wallet_id, set())

        got = 0
        try:
            async for batch in iter_new_swaps(client, address, existing_sigs):
                got += len(batch)
                await rows_q.put(("rows", wallet_id, batch))
        except CollectionError as e:
            # HARD API failure — leave the wallet untouched (not ok, last_collected_at
            # unchanged) so it stays eligible and retries next run. Any rows already
            # pushed for it are deduped on retry, never duplicated.
            await rows_q.put(("done", wallet_id, "error"))
            print(f"[{wallet_id}] API error — will retry next run: {e}")
            continue

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


async def _writer(rows_q, stats):
    """The ONLY coroutine that touches the row buffer and BigQuery, so there are
    no races. Accumulates rows from every worker into one buffer; flushes a full
    buffer to raw_swaps, THEN marks the wallets whose rows are now persisted ok
    (insert-rows-then-mark-ok ordering → crash-safe, same invariant as the old
    serial flush). Blocking BQ calls run in a thread executor so the event loop
    keeps serving workers while a flush is in flight.

    A wallet's "done" item always arrives AFTER all its "rows" items (FIFO queue),
    so by the time we mark it ok its rows are already in the buffer / flushed.
    """
    loop = asyncio.get_running_loop()
    buffer: list[dict] = []
    pending_ok: list[str] = []

    async def flush():
        if buffer:
            await loop.run_in_executor(None, bq.insert_raw_swap_rows, list(buffer))
            print(f"  flushed {len(buffer)} rows to raw_swaps")
            buffer.clear()
        if pending_ok:
            await loop.run_in_executor(
                None, bq.bulk_update_wallet_status, list(pending_ok), "ok"
            )
            pending_ok.clear()

    while True:
        item = await rows_q.get()
        if item is None:
            break  # sentinel: all workers done
        if item[0] == "rows":
            _, wallet_id, batch = item
            buffer.extend(bq.build_raw_swap_rows(wallet_id, batch))
            if len(buffer) >= FLUSH_ROWS:
                await flush()
        else:  # ("done", wallet_id, outcome)
            _, wallet_id, outcome = item
            if outcome == "ok":
                pending_ok.append(wallet_id)
            elif outcome == "failed":
                stats["failed"].append(wallet_id)
            else:  # "error"
                stats["errors"] += 1

    await flush()  # final partial flush of whatever's left in the jar


async def _collect_async(wallets, sig_map):
    """Drive CONCURRENCY workers + one writer over a shared httpx client."""
    stats = {"failed": [], "errors": 0}
    work_q: asyncio.Queue = asyncio.Queue()
    for w in wallets:
        work_q.put_nowait(w)
    rows_q: asyncio.Queue = asyncio.Queue(maxsize=ROWS_QUEUE_MAX)

    async with httpx.AsyncClient(timeout=30) as client:
        writer = asyncio.create_task(_writer(rows_q, stats))
        workers = [
            asyncio.create_task(_worker(client, work_q, rows_q, sig_map))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.gather(*workers)  # all wallets processed
        await rows_q.put(None)  # sentinel → writer flushes remainder and stops
        await writer
    return stats


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

    stats = asyncio.run(_collect_async(wallets, sig_map))

    if stats["failed"]:
        bq.bulk_update_wallet_status(stats["failed"], "failed")
        print(f"Marked {len(stats['failed'])} wallets failed")

    api_errors = stats["errors"]
    # Fail LOUD on a broad outage: if most wallets hit API errors, data is
    # silently not being collected. Raise so the pipeline reports failure
    # (Step 3 is fail-hard → honest status dot goes red) instead of a false
    # SUCCESS — the exact failure mode that hid the 2026-06 host change for a day.
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

"""
async_collect.py - Shared async engine for pulling Helius swaps into raw_swaps.

Used by both the daily collector (collect_traders_swaps.py) and the historical
backfill (backfill_swaps.py), and reusable for future passes (e.g. transfers).
Each caller supplies a per-wallet coroutine (process_wallet) and a "mark done"
function (mark_ok_fn); this module owns the concurrency + writing.

Concurrency (2026-06): collection is I/O-bound (each wallet is mostly network
wait), so CONCURRENCY worker coroutines fetch wallets in parallel via httpx while
ONE writer coroutine owns every BigQuery write (so there are no buffer races).
Worker count caps in-flight requests — each worker makes ≤1 request at a time —
so it doubles as the rate limiter (no separate semaphore needed). Blocking BQ
calls run in a thread executor so they never freeze the event loop. ~5x faster
than the old serial loop, bounded by the Helius rate limit rather than by Python.

Memory: bounded by construction — a single row buffer capped at FLUSH_ROWS, a
bounded work queue (backpressure), per-wallet page streaming (iter_new_swaps
yields one page at a time), and batched DML.
"""

import asyncio
import time

import httpx

from lib import bq
from lib.secrets import get_secret

HELIUS_API_KEY = get_secret("HELIUS_API_KEY")
PAGE_SIZE = 100  # Helius Enhanced API max page size; we use the ceiling.
# Flush the cross-wallet row buffer at this size. ~5000 rows × ~10KB raw_json
# ≈ 50MB peak — bounds memory regardless of how many wallets a run processes.
FLUSH_ROWS = 5000

# Number of concurrent fetch workers (caps in-flight requests). The RATE is
# bounded separately by REQUESTS_PER_SEC below — worker count alone is NOT a rate
# limiter: when many wallets are "up to date" (one fast request each), 40 workers
# burst well over 50 RPS and storm 429s. So concurrency caps in-flight, the rate
# limiter caps requests/sec.
CONCURRENCY = 40
# Max row-batches queued from workers to the writer. Bounds memory via
# backpressure: when full, workers block until the writer drains.
ROWS_QUEUE_MAX = 50

# Hard ceiling on request INITIATIONS per second across all workers. Independent
# of per-request speed, so fast "up to date" checks can't burst over the limit.
# 40 still left ~11% of wallets 429-ing at full scale, so the Enhanced API's
# effective limit is below the headline 50 RPS (it's the ~100x-cost endpoint) —
# 25 gives comfortable margin. Tune up cautiously if 429s stay at zero. See
# _RateLimiter.
REQUESTS_PER_SEC = 25

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


class _RateLimiter:
    """Spaces request initiations at least 1/rate apart → ≤ rate requests/sec
    across all workers, no matter how fast individual requests return. Holding the
    lock across the small spacing sleep serializes the gate (not the requests),
    which is what paces the fleet evenly (no bursts)."""

    def __init__(self, rate: int):
        self._min_interval = 1.0 / rate
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = max(now, self._next) + self._min_interval


# Created fresh per run by run_collection (bound to that run's event loop); used
# by _fetch_page. None when _fetch_page is called outside a run (e.g. unit tests),
# in which case no rate limiting is applied.
_rate_limiter: "_RateLimiter | None" = None


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
        if _rate_limiter is not None:
            await _rate_limiter.acquire()  # global ≤REQUESTS_PER_SEC across workers
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


async def iter_new_swaps(
    client: httpx.AsyncClient,
    address: str,
    existing_sigs: set,
    *,
    max_tx: int,
    start_before: str | None = None,
    stop_at_known: bool = True,
):
    """
    Async-yield pages of SWAP txs for a wallet, in two modes:

    Daily (start_before=None, stop_at_known=True): start at the newest tx, page
    back, and STOP at the first signature we already have — incremental refresh.

    Backfill (start_before=<oldest known sig>, stop_at_known=False): start just
    BELOW the oldest tx we have and page OLDER, filling history beneath the daily
    cap. There's no overlap to stop on, so a known sig is just skipped (not a stop
    signal); traversal ends at a short page (the wallet's true beginning) or max_tx.

    Either way it streams page-by-page (~one page held at a time), and a short
    page (< PAGE_SIZE) is the "reached the end" signal. Raises CollectionError on
    a HARD failure; ends normally when there's nothing more — the caller must
    never mistake a failure for "up to date".
    """
    fetched = 0
    before = start_before

    while fetched < max_tx:
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
                if stop_at_known:
                    # Daily, newest-first: a known sig means everything older is
                    # already in BQ — stop.
                    stop_early = True
                    break
                continue  # backfill: skip a dup but keep paging older
            fresh.append(tx)
            fetched += 1
            if fetched >= max_tx:
                break

        if fresh:
            yield fresh

        if stop_early or len(page) < PAGE_SIZE:
            return

        before = page[-1]["signature"]
        # No throttle sleep here — the worker count caps the request rate.


async def _writer(rows_q, stats, mark_ok_fn):
    """The ONLY coroutine that touches the row buffer and BigQuery, so there are
    no races. Accumulates rows from every worker into one buffer; flushes a full
    buffer to raw_swaps, THEN marks the wallets whose rows are now persisted
    (insert-rows-then-mark ordering → crash-safe). Blocking BQ calls run in a
    thread executor so the event loop keeps serving workers while a flush runs.

    Workers push:
      ("rows", wallet_id, batch)   — a page of new txs to persist
      ("done", wallet_id, outcome) — wallet finished; outcome ∈ {ok, failed, error}
    A wallet's "done" always arrives AFTER all its "rows" (FIFO), so by the time
    we mark it, its rows are already buffered / flushed.
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
            # mark_ok_fn marks a batch of completed wallet_ids: status=ok for
            # daily collection, backfilled_at=now for backfill.
            await loop.run_in_executor(None, mark_ok_fn, list(pending_ok))
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


async def _worker_loop(client, work_q, rows_q, process_wallet):
    """Pull wallets off the shared queue until empty, running process_wallet on
    each. CONCURRENCY of these run at once → CONCURRENCY in-flight requests."""
    while True:
        try:
            w = work_q.get_nowait()
        except asyncio.QueueEmpty:
            return  # no wallets left — this worker is done
        await process_wallet(client, w, rows_q)


async def run_collection(wallets, process_wallet, mark_ok_fn):
    """Drive CONCURRENCY workers + one writer over a shared httpx client.

    process_wallet(client, wallet, rows_q): async, handles ONE wallet and pushes
      ("rows"/"done") items onto rows_q (daily vs backfill differ only here).
    mark_ok_fn(wallet_ids): marks a batch of completed wallets (status=ok for
      daily, backfilled_at for backfill).
    Returns stats = {"failed": [...], "errors": int}.
    """
    global _rate_limiter
    _rate_limiter = _RateLimiter(REQUESTS_PER_SEC)  # fresh, bound to this run's loop

    stats = {"failed": [], "errors": 0}
    work_q: asyncio.Queue = asyncio.Queue()
    for w in wallets:
        work_q.put_nowait(w)
    rows_q: asyncio.Queue = asyncio.Queue(maxsize=ROWS_QUEUE_MAX)

    async with httpx.AsyncClient(timeout=30) as client:
        writer = asyncio.create_task(_writer(rows_q, stats, mark_ok_fn))
        workers = [
            asyncio.create_task(_worker_loop(client, work_q, rows_q, process_wallet))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.gather(*workers)  # all wallets processed
        await rows_q.put(None)  # sentinel → writer flushes remainder and stops
        await writer
    return stats

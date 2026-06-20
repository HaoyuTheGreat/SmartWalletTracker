"""
collect_traders_swaps.py - Pull wallet swap txs from Helius into BigQuery raw_swaps.

Flow:
  1. Read wallets that need a refresh from BQ (last fetched >48h ago, or never fetched).
  2. ONE snapshot query: all existing signatures for those wallets (dedup baseline).
  3. For each wallet, STREAM Helius pages newest-first (one page in memory at a
     time); stop at a known signature (incremental) or MAX_TX_PER_WALLET (cap).
  4. Buffer rows across wallets AND mid-wallet; flush to raw_swaps every
     FLUSH_ROWS rows, then bulk-mark fully-fetched wallets ok.
  5. Bulk-mark wallets with no data at all as 'failed'.

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
and the run died on signal 9. This version is bounded by construction:
one snapshot query, a row buffer capped at FLUSH_ROWS, and batched DML.

Per-wallet streaming (2026-06): iter_new_swaps yields pages instead of returning
a whole wallet's txs, so even a heavy/uncapped wallet holds only ~one page at a
time. This is the prerequisite for lifting MAX_TX_PER_WALLET (the backfill work).
"""

import sys
import time

import requests

from lib import bq
from lib.secrets import get_secret

sys.stdout.reconfigure(encoding="utf-8")

HELIUS_API_KEY = get_secret("HELIUS_API_KEY")
MAX_TX_PER_WALLET = 2000  # Hard cap per wallet per run to bound API spend
PAGE_SIZE = 100
# Flush the cross-wallet row buffer at this size. ~5000 rows × ~10KB raw_json
# ≈ 50MB peak — bounds memory regardless of how many wallets a run processes.
FLUSH_ROWS = 5000

# In-run retry policy for a single page fetch. RETRYABLE = transient (will likely
# succeed soon); everything else (4xx) is persistent, so we fail fast instead of
# wasting MAX_ATTEMPTS×backoff on it. Classifying 404 as non-retryable is what
# keeps a host outage (#16) failing loudly in seconds instead of burning an hour
# retrying ~1500 dead requests.
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CollectionError(Exception):
    """A HARD Helius failure for one wallet (network error / dead endpoint /
    non-JSON body). Raised by iter_new_swaps so the caller can tell a real
    failure apart from "genuinely nothing new" — the latter just ends the
    generator. Conflating the two is what let a Helius host change silently
    break collection for a full day (2026-06, see DEVLOG #16)."""


def _fetch_page(url: str, label: str):
    """GET one page from Helius, with in-run retries on TRANSIENT errors.

    Returns the parsed JSON body (a list of txs on success, or a structured dict
    like {'error': ...} for "no swaps" — the caller interprets the content).

    Raises CollectionError when retries are exhausted, or immediately on a
    non-retryable error (4xx other than 429 — e.g. 404 dead endpoint, 401/403
    auth). Retrying those wastes time; failing fast lets the caller's fail-loud
    fire quickly during a real outage.
    """
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=30)
        except requests.exceptions.RequestException as e:
            last_err = f"network error: {e}"  # transient → retry
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    # 200 but unparseable body — rare; treat as transient.
                    last_err = f"non-JSON 200: {resp.text[:80]}"
            elif resp.status_code in RETRYABLE_STATUS:
                last_err = f"HTTP {resp.status_code}: {resp.text[:80]}"  # 429/5xx → retry
            else:
                # 4xx: 404 (retired endpoint), 401/403 (auth), 400 (bad request).
                # Retrying won't help — fail fast.
                raise CollectionError(
                    f"{label}: HTTP {resp.status_code} (not retryable): {resp.text[:80]}"
                )

        # Transient failure — back off (1s, 2s) before retrying, unless this was
        # the last attempt.
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(2**attempt)

    raise CollectionError(f"{label}: failed after {MAX_ATTEMPTS} attempts — {last_err}")


def iter_new_swaps(address: str, existing_sigs: set):
    """
    Yield pages of NEW SWAP txs for a wallet, newest-first, stopping at a known
    signature (incremental) or at MAX_TX_PER_WALLET (cost cap).

    Streams page-by-page instead of accumulating the whole wallet: at any moment
    only one page (~100 txs) is held in memory, so memory stays bounded no matter
    how many txs a wallet has. The caller flushes the cross-wallet row buffer as
    pages arrive (so a single heavy wallet can't blow up memory). This is the
    prerequisite for lifting MAX_TX_PER_WALLET in the backfill work.

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
        page = _fetch_page(url, address[:8])

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
        time.sleep(0.2)


def main():
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

    pending_rows: list[dict] = []  # buffered raw_swaps rows, possibly many wallets
    pending_ok: list[str] = []  # wallets whose rows are buffered (or up to date)
    failed: list[str] = []
    api_errors = 0  # wallets whose Helius call hard-failed (will retry next run)

    def flush():
        """Insert buffered rows, THEN mark their wallets ok — in that order.

        If we crash between the two, the next run re-fetches those wallets but
        the freshly-inserted rows are already in the signature snapshot, so
        fetching stops immediately and nothing is duplicated. The reverse
        order (status first) could mark a wallet ok whose rows never landed.
        """
        nonlocal pending_rows, pending_ok
        if pending_rows:
            bq.insert_raw_swap_rows(pending_rows)
            print(f"  flushed {len(pending_rows)} rows to raw_swaps")
        if pending_ok:
            bq.bulk_update_wallet_status(pending_ok, "ok")
        pending_rows = []
        pending_ok = []

    for w in wallets:
        wallet_id = w["wallet_id"]
        address = w["address"]
        existing_sigs = sig_map.get(wallet_id, set())

        got = 0
        try:
            for batch in iter_new_swaps(address, existing_sigs):
                pending_rows.extend(bq.build_raw_swap_rows(wallet_id, batch))
                got += len(batch)
                # Flush mid-wallet too: a heavy wallet can exceed FLUSH_ROWS on
                # its own. The in-progress wallet is NOT yet in pending_ok, so a
                # flush here lands its rows WITHOUT marking it ok — crash-safe
                # (on a crash the wallet re-fetches and the rows dedup).
                if len(pending_rows) >= FLUSH_ROWS:
                    flush()
        except CollectionError as e:
            # HARD API failure — leave the wallet untouched (don't mark ok, don't
            # advance last_collected_at) so it stays eligible and retries next
            # run. Any rows already flushed for it are deduped on retry, never
            # duplicated. Never mask an outage as "up to date".
            api_errors += 1
            print(f"[{wallet_id}] API error — will retry next run: {e}")
            time.sleep(0.2)
            continue

        # Wallet fully traversed — only now is it safe to mark its outcome.
        if got:
            pending_ok.append(wallet_id)
            print(f"[{wallet_id}] fetched {got} new swaps")
        elif existing_sigs:
            # No new data, but we have history — wallet is just up to date.
            pending_ok.append(wallet_id)
            print(f"[{wallet_id}] up to date")
        else:
            # Never had any data AND Helius returned nothing — mark failed.
            failed.append(wallet_id)
            print(f"[{wallet_id}] failed (no swaps found)")

        if len(pending_rows) >= FLUSH_ROWS:
            flush()

        time.sleep(0.2)

    flush()
    if failed:
        bq.bulk_update_wallet_status(failed, "failed")
        print(f"Marked {len(failed)} wallets failed")

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


if __name__ == "__main__":
    main()

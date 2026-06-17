"""
collect_traders_swaps.py - Pull wallet swap txs from Helius into BigQuery raw_swaps.

Flow:
  1. Read wallets that need a refresh from BQ (last fetched >48h ago, or never fetched).
  2. ONE snapshot query: all existing signatures for those wallets (dedup baseline).
  3. For each wallet, page Helius newest-first; stop at a known signature
     (incremental) or at MAX_TX_PER_WALLET (cost cap).
  4. Buffer rows across wallets; flush to raw_swaps every FLUSH_ROWS rows
     (bounded memory), then bulk-mark the flushed wallets ok.
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


def fetch_new_swaps(address: str, existing_sigs: set) -> list:
    """
    Fetch SWAP txs from Helius for a wallet, newest-first, stopping when we
    hit a signature we already have (incremental).
    Returns the list of new txs (not yet in BQ).
    """
    new_txs = []
    before = None

    while len(new_txs) < MAX_TX_PER_WALLET:
        url = (
            f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions/"
            f"?api-key={HELIUS_API_KEY}&type=SWAP&limit={PAGE_SIZE}"
        )
        if before:
            url += f"&before={before}"

        try:
            response = requests.get(url, timeout=30)
            page = response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Request failed: {e}")
            break

        if not page:
            break
        if not isinstance(page, list):
            print(f"  Unexpected response: {str(page)[:100]}")
            break

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
            new_txs.append(tx)
            if len(new_txs) >= MAX_TX_PER_WALLET:
                break

        if stop_early or len(page) < PAGE_SIZE:
            break

        before = page[-1]["signature"]
        time.sleep(0.2)

    return new_txs


def main():
    wallets = bq.fetch_wallets_needing_collection(max_age_hours=24)
    print(f"Found {len(wallets)} wallets needing collection")
    if not wallets:
        return

    # ONE snapshot query instead of one query per wallet (the old N+1).
    sig_map = bq.existing_signatures_by_wallet([w["wallet_id"] for w in wallets])
    print(f"Loaded signature snapshot for {len(sig_map)} wallets")

    pending_rows: list[dict] = []  # buffered raw_swaps rows, possibly many wallets
    pending_ok: list[str] = []  # wallets whose rows are buffered (or up to date)
    failed: list[str] = []

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
        new_txs = fetch_new_swaps(address, existing_sigs)

        if new_txs:
            pending_rows.extend(bq.build_raw_swap_rows(wallet_id, new_txs))
            pending_ok.append(wallet_id)
            print(f"[{wallet_id}] fetched {len(new_txs)} new swaps")
        elif existing_sigs:
            # No new data, but we have history — wallet is just up to date
            pending_ok.append(wallet_id)
            print(f"[{wallet_id}] up to date")
        else:
            # Never had any data AND Helius returned nothing — mark failed
            failed.append(wallet_id)
            print(f"[{wallet_id}] failed (no swaps found)")

        # Drop the parsed-JSON list before the next wallet — a heavy wallet's
        # txs can be 50MB+ as Python objects.
        del new_txs

        if len(pending_rows) >= FLUSH_ROWS:
            flush()

        time.sleep(0.2)

    flush()
    if failed:
        bq.bulk_update_wallet_status(failed, "failed")
        print(f"Marked {len(failed)} wallets failed")


if __name__ == "__main__":
    main()

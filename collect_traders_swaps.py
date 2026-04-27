"""
collect_traders_swaps.py - Pull wallet swap txs from Helius into BigQuery raw_swaps.

Flow:
  1. Read wallets that need a refresh from BQ (last fetched >24h ago, or never fetched).
  2. For each wallet, look up its existing signatures in BQ, then page Helius newest-first.
  3. Stop as soon as we hit a known signature (incremental — avoids pulling all 2000 again).
  4. Bulk-insert the new txs into raw_swaps.
  5. Update wallets.collection_status = 'ok' / 'failed'.
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

    for w in wallets:
        wallet_id = w["wallet_id"]
        address = w["address"]
        print(f"[{wallet_id}] fetching...")

        existing_sigs = bq.existing_signatures_for_wallet(wallet_id)
        new_txs = fetch_new_swaps(address, existing_sigs)

        if new_txs:
            inserted = bq.insert_raw_swaps(wallet_id, new_txs)
            bq.update_wallet_status(wallet_id, "ok")
            print(f"[{wallet_id}] inserted {inserted} new swaps")
        elif existing_sigs:
            # No new data, but we have history — wallet is just up to date
            bq.update_wallet_status(wallet_id, "ok")
            print(f"[{wallet_id}] up to date")
        else:
            # Never had any data AND Helius returned nothing — mark failed
            bq.update_wallet_status(wallet_id, "failed")
            print(f"[{wallet_id}] failed (no swaps found)")

        time.sleep(0.2)


if __name__ == "__main__":
    main()

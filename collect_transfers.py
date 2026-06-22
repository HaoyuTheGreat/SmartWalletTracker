"""
collect_transfers.py - Collect token TRANSFER history into raw_transfers.

Mirrors the daily swap collector but pulls type=TRANSFER into a separate table,
scoped to smart_candidate wallets (the ones whose PnL we actually evaluate).
For now a one-time pull per wallet (transfers_collected_at marks done ones);
re-running picks up newly-promoted smart candidates and skips finished ones.

Why: tokens transferred INTO a wallet (not bought via swap) currently make its
positions look "sold without buying" (data_clipped), which distorts PnL. This
gets the raw transfer data into the warehouse so a later step can fix the cost
basis / PnL (late binding: collect raw now, parse later). See notes/TOMORROW.md.

Run:
    python collect_transfers.py        # all smart-candidate wallets needing transfers
    python collect_transfers.py 10     # only 10 (small test run)

Reuses the shared async engine (lib.async_collect): same concurrency, retry, rate
limiter, and single writer; this file only owns the transfer wallet selection and
per-wallet logic, writing to raw_transfers.
"""

import asyncio
import sys
import time

from lib import bq
from lib.async_collect import CollectionError, iter_new_swaps, run_collection

sys.stdout.reconfigure(encoding="utf-8")

TRANSFER_MAX_TX = 10000  # per-wallet cap on transfer history pulled


async def _process_wallet(client, w, rows_q, sig_map):
    """Pull one wallet's transfers (newest-first, stop at a known sig), push rows
    + a 'done' outcome. got=0 just means no new transfers — still done."""
    wallet_id = w["wallet_id"]
    address = w["address"]
    existing_sigs = sig_map.get(wallet_id, set())

    got = 0
    try:
        async for batch in iter_new_swaps(
            client, address, existing_sigs, max_tx=TRANSFER_MAX_TX, tx_type="TRANSFER"
        ):
            got += len(batch)
            await rows_q.put(("rows", wallet_id, batch))
    except CollectionError as e:
        await rows_q.put(("done", wallet_id, "error"))
        print(f"[{wallet_id}] transfer error — will retry next run: {e}")
        return

    await rows_q.put(("done", wallet_id, "ok"))
    print(f"[{wallet_id}] {got} transfers")


def main(limit=None):
    start = time.monotonic()

    wallets = bq.fetch_wallets_needing_transfers(limit=limit)
    print(f"Found {len(wallets)} wallets needing transfer collection")
    if not wallets:
        return

    wids = [w["wallet_id"] for w in wallets]
    sig_map = bq.existing_transfer_signatures_by_wallet(wids)
    print(f"Loaded transfer-signature snapshot for {len(wids)} wallets")

    async def process(client, w, rows_q):
        await _process_wallet(client, w, rows_q, sig_map)

    stats = asyncio.run(
        run_collection(
            wallets,
            process,
            bq.mark_wallets_transfers_collected,
            bq.insert_transfer_rows,
        )
    )

    api_errors = stats["errors"]
    if wallets and api_errors > len(wallets) * 0.5:
        raise RuntimeError(
            f"Transfer collection hard-failed on {api_errors}/{len(wallets)} wallets "
            "— likely an endpoint/auth outage. Failing the run loudly."
        )
    if api_errors:
        print(f"{api_errors} wallets had API errors (will retry next run)")

    print(f"Transfers done in {time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    n = sys.argv[1] if len(sys.argv) > 1 else ""
    main(limit=int(n) if n.isdigit() else None)

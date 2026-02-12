import json, sys

sys.stdout.reconfigure(encoding='utf-8')

with open("raw_txs_2o8UXR.json", "r", encoding="utf-8") as f:
    txs = json.load(f)

TARGET = "2o8UXRk7iwaaW36FGNvMTxbUctftnRcYvrKwA13Mdj2K"
PAYMENT_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

no_transfers = 0
only_payment = 0
no_buy_sell = 0
dust_filtered = 0
counted = 0

dust_examples = []
no_buy_sell_examples = []

for tx in txs:
    tt_list = tx.get("tokenTransfers", [])
    if not tt_list:
        no_transfers += 1
        continue

    target = None
    for tt in tt_list:
        if tt.get("mint") not in PAYMENT_MINTS:
            target = tt
            break

    if not target:
        only_payment += 1
        continue

    is_buy = target.get("toUserAccount") == TARGET
    is_sell = target.get("fromUserAccount") == TARGET
    if not is_buy and not is_sell:
        no_buy_sell += 1
        if len(no_buy_sell_examples) < 5:
            no_buy_sell_examples.append({
                "sig": tx.get("signature", "")[:30],
                "target_mint": target.get("mint", "")[:10],
                "from": target.get("fromUserAccount", "")[:10],
                "to": target.get("toUserAccount", "")[:10],
                "num_transfers": len(tt_list),
                "all_mints": [tt.get("mint", "")[:10] for tt in tt_list],
            })
        continue

    sol_change = 0
    for acc in tx.get("accountData", []):
        if acc["account"] == TARGET:
            sol_change = acc["nativeBalanceChange"] / 1e9
            break

    stable_change = 0.0
    for tt in tt_list:
        mint = tt.get("mint", "")
        if mint in PAYMENT_MINTS and mint != "So11111111111111111111111111111111111111112":
            amount = tt.get("tokenAmount", 0)
            if tt.get("toUserAccount") == TARGET:
                stable_change += amount
            elif tt.get("fromUserAccount") == TARGET:
                stable_change -= amount

    if abs(sol_change) < 0.005 and abs(stable_change) < 0.01:
        dust_filtered += 1
        if len(dust_examples) < 5:
            dust_examples.append({
                "sig": tx.get("signature", "")[:30],
                "sol_change": round(sol_change, 6),
                "stable_change": round(stable_change, 4),
                "target_mint": target.get("mint", "")[:10],
                "token_amount": target.get("tokenAmount", 0),
                "is_buy": is_buy,
                "is_sell": is_sell,
                "num_transfers": len(tt_list),
            })
        continue

    counted += 1

print(f"=== Transaction Filter Analysis ===")
print(f"Total txs: {len(txs)}")
print(f"Counted (used in report): {counted}")
print(f"---")
print(f"No tokenTransfers: {no_transfers}")
print(f"Only payment tokens (SOL/USDC/USDT swap): {only_payment}")
print(f"Target wallet not buyer/seller: {no_buy_sell}")
print(f"Dust filtered (sol<0.005 & stable<0.01): {dust_filtered}")
print(f"Sum filtered: {no_transfers + only_payment + no_buy_sell + dust_filtered}")
print()

print("=== Dust filtered examples ===")
for ex in dust_examples:
    print(f"  {ex}")
print()

print("=== Not buy/sell examples ===")
for ex in no_buy_sell_examples:
    print(f"  {ex}")
print()

# Also check: how many unique "only_payment" trades are SOL<->USDC swaps?
sol_usdc_swaps = 0
for tx in txs:
    tt_list = tx.get("tokenTransfers", [])
    if not tt_list:
        continue
    mints_in_tx = set(tt.get("mint", "") for tt in tt_list)
    non_payment = mints_in_tx - PAYMENT_MINTS
    if not non_payment:
        sol_usdc_swaps += 1

print(f"=== SOL<->Stablecoin swaps (no meme coin): {sol_usdc_swaps} ===")

import json
import os

def load_swaps():
    swap_loaders = "data/wallets_swap_data"
    filenames = os.listdir(swap_loaders)
    all_wallets = {}
    for swap_json_file in filenames:
        filepath = os.path.join(swap_loaders, swap_json_file)
        with open(filepath, "r", encoding="utf-8") as file:
            swaps = json.load(file)
        wallet_ids = swap_json_file.replace(".json", "")
        all_wallets[wallet_ids] = swaps
        #print(f"[{wallet_ids}] {len(swaps)} 条 SWAP")
    #print(f"total of {len(swaps)} swaps")
    return all_wallets

# In this function, we are trying to return what are the platforms those wallet addresses used to make SWAP transactions.
def check_sources(all_wallets):
    source_counts = {}
    for _, swaps in all_wallets.items():
        for tx in swaps:
            source = tx.get("source", "UNKNOWN")
            if source in source_counts:
                source_counts[source] += 1
            else:
                source_counts[source] = 1
    for source, count in source_counts.items():
        print(f"{source}: {count}")

if __name__ == "__main__":
    all_wallets = load_swaps()
    check_sources(all_wallets)

import json
import os

def load_swaps():
    swap_loaders = "data/wallets_swap_data"
    filenames = os.listdir(swap_loaders)
    all_wallets = {}
    for swap_json_file in filenames:
        if not swap_json_file.endswith(".json"):
            continue
        filepath = os.path.join(swap_loaders, swap_json_file)
        with open(filepath, "r", encoding="utf-8") as file:
            swaps = json.load(file)
        wallet_ids = swap_json_file.replace(".json", "")
        all_wallets[wallet_ids] = swaps
        #print(f"[{wallet_ids}] {len(swaps)} 条 SWAP")
        #print(f"total of {len(swaps)} swaps")
    #print(f"length of all_wallets: {len(all_wallets)}")
    return all_wallets


def load_analyzed_swaps():
    analyzed_loaders = "data/analyzed_swaps_data"
    analyzed_filenames = os.listdir(analyzed_loaders)
    all_analyzed_wallets = {}
    for analyzed_file in analyzed_filenames:
        if not analyzed_file.endswith(".json"):
            continue
        analyzed_filepath = os.path.join(analyzed_loaders, analyzed_file)
        with open(analyzed_filepath, "r", encoding = "utf-8") as file:
            analyzed_swaps = json.load(file)
        if isinstance(analyzed_swaps, dict):
            analyzed_swaps = analyzed_swaps.get("swaps", [])
        analyzed_wallet_ids = analyzed_file.replace(".json", "")
        all_analyzed_wallets[analyzed_wallet_ids] = analyzed_swaps
        #print(f"[{analyzed_wallet_ids}] : {len(analyzed_swaps)}")
    return all_analyzed_wallets

def compare_counts(all_wallets, all_analyzed_wallets):
    for wallet_id, swaps in all_wallets.items():
        raw_count = len(swaps)
        if wallet_id not in all_analyzed_wallets:
            print(f"[{wallet_id}] 还没有被分析")
            continue
        analyzed_count = len(all_analyzed_wallets[wallet_id])
        print(f"[{wallet_id}] 原始: {raw_count}, 分析后: {analyzed_count}")
        if raw_count != analyzed_count:
            print(f"  有 {raw_count - analyzed_count} 条记录没有被分析")
            
def check_duplicate_tx(all_wallets):
    for wallet_id, swaps in all_wallets.items():
        signature_list = []
        for tx in swaps:
            signature = tx.get("signature")
            signature_list.append(signature)
        if len(signature_list) != len(set(signature_list)):
            dupes = len(signature_list) - len(set(signature_list))
            print(f"[{wallet_id}] has {dupes} duplicate transactions")

    #print(len(signature_list))    
    #print(len(set(signature_list)))


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

def check_empty_swaps(all_analyzed_wallets):
    for wallet_id, swaps in all_analyzed_wallets.items():
        empty_count = 0
        for swap in swaps:
            if (not swap.get("token_spent") and not swap.get("token_received") 
                and swap.get("sol_spent", 0) == 0 and swap.get("sol_received", 0) == 0):
                empty_count += 1
        if empty_count > 0:
            print(f"[{wallet_id}] {empty_count} 条真正的空记录")


if __name__ == "__main__":
    all_wallets = load_swaps()
    #check_sources(all_wallets)
    
    all_analyzed_wallets = load_analyzed_swaps()
    
    compare_counts(all_wallets, all_analyzed_wallets)
    
    check_duplicate_tx(all_wallets)
    check_empty_swaps(all_analyzed_wallets)

    

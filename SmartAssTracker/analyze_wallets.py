import json
from datetime import datetime, timezone
import pytz
import requests
import os
from dotenv import load_dotenv

load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


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


def parse_swap(tx):
    timestamp = tx.get("timestamp")
    swap_event = tx.get("events", {}).get("swap", {})

    token_inputs = swap_event.get("tokenInputs", [])
    token_outputs = swap_event.get("tokenOutputs", [])
    native_input = swap_event.get("nativeInput")
    native_output = swap_event.get("nativeOutput")

    sol_spent = int(native_input.get("amount", 0)) / 10**9 if native_input else 0
    sol_received = int(native_output.get("amount", 0)) / 10**9 if native_output else 0

    non_native_tokens_sold = {}
    for i in token_inputs:
        mint = i.get("mint")
        raw = i.get("rawTokenAmount", {})
        amount = int(raw.get("tokenAmount")) / 10 ** raw.get("decimals")

        if mint in non_native_tokens_sold:
            non_native_tokens_sold[mint] += amount
        else:
            non_native_tokens_sold[mint] = amount

    non_native_token_bought = {}
    for i in token_outputs:
        mint_output = i.get("mint")
        raw_output = i.get("rawTokenAmount", {})
        amount_output = int(raw_output.get("tokenAmount")) / 10 ** raw_output.get(
            "decimals"
        )

        if mint_output in non_native_token_bought:
            non_native_token_bought[mint_output] += amount_output
        else:
            non_native_token_bought[mint_output] = amount_output

    return {
        "timestamp": timestamp,
        "token_inputs": token_inputs,
        "token_outputs": token_outputs,
        "native_input": native_input,
        "native_output": native_output,
        "sol_spent": sol_spent,
        "sol_received": sol_received,
        "token_spent": non_native_tokens_sold,
        "token_received": non_native_token_bought,
    }


# This function is to collect all the mint addresses of the tokens.
def collect_mints(parsed_swaps):
    # In the transactions, the same mint address or token might appear multiple times, so we use set() to avoid duplicates.
    all_mints = set()
    # Iterates over all the transactions, the mint address are in "token_spent" and "token_received".
    for swap in parsed_swaps:
        all_mints.update(swap["token_spent"].keys())
        all_mints.update(swap["token_received"].keys())
    return all_mints


# This function is to resolve the tokens' symbols
def resolve_token_symbol(all_mints):
    # Creating a new empty dict to store the result of address + symbol.
    symbol_names = {}
    # Iterates over every address in all_mints and send request to Helius to find the symbol name.
    for mint in all_mints:
        # The format of JSON-RPC request(Helius DAS API required)
        payload = {
            "jsonrpc": "2.0",  # Telling the server we are using JSON-RPC 2.0.
            "id": "test",
            "method": "getAsset",  # Telling the server which method we are pulling, "getAsset" tells which asset
            "params": {
                "id": mint
            },  # Telling the server we are pulling the mint address of that asset.
        }
        response = requests.post(url, json=payload, timeout=10)
        symbol_result = response.json()
        symbol = symbol_result.get("result", {}).get("content", {}).get("metadata", {}).get("symbol", mint[:8])
        # adding the key-value pair to the dict.
        symbol_names[mint] = symbol
    return symbol_names


if __name__ == "__main__":
    all_wallets = load_swaps()
    #检查是否有这个repo，这个repo包含所有分析好的钱包。
    os.makedirs("data/analyzed_swaps_data", exist_ok = True)

    pacificTime = pytz.timezone("America/Los_Angeles")

    output_swaps_tx = []
    #在开始找token的symbol和地址之前先读取已有的（token_names.json)
    if os.path.exists("token_names.json"):
            with open("token_names.json", "r") as f:
                all_token_names = json.load(f)
    else:
            all_token_names = {}
            
    for wallet_ids, swaps in all_wallets.items():
        output_path = f"data/analyzed_swaps_data/{wallet_ids}.json"
        if os.path.exists(output_path):
            print(f"[{wallet_ids}] exists, skip")
            continue
        print(f"Analyzing [{wallet_ids}].........")
        parsed_swaps = [parse_swap(tx) for tx in swaps]
        sorted_time_asc = sorted(parsed_swaps, key=lambda x: x["timestamp"])
        all_mints = collect_mints(parsed_swaps)
        token_names = resolve_token_symbol(all_mints)
        #把新的token地址和symbol加进去
        all_token_names.update(token_names)
        output_swaps_tx = []
        
        # 在这个swap_data.json里：
        # nativeInput = SOL I paid; nativeOutput = SOL I received; tokenInputs = the token I paid; tokenOutputs = the token I received.
        for parsed in sorted_time_asc:
            converted_time = datetime.fromtimestamp(parsed["timestamp"], tz=pacificTime)
            trade = {
                "time": str(converted_time),
                "sol_spent": parsed["sol_spent"],
                "sol_received": parsed["sol_received"],
            }
            token_spent = []
            for mint, amount in parsed["token_spent"].items():
                token_spent.append(
                    {
                        "mint": mint,
                        "symbol": token_names[mint],
                        "amount": amount,
                    }
                )
            token_received = []
            for mint, amount in parsed["token_received"].items():
                token_received.append(
                    {
                        "mint": mint,
                        "symbol": token_names[mint],
                        "amount": amount,
                    }
                )
            trade["token_spent"] = token_spent
            trade["token_received"] = token_received
            output_swaps_tx.append(trade)
        with open(f"data/analyzed_swaps_data/{wallet_ids}.json", "w", encoding = "utf-8") as f:
            json.dump(output_swaps_tx, f, indent=2)
        print(f"[{wallet_ids}].json saved successfully.")

    # This part is write a json file that will contains the token's address and names.
    # By doing this, for the next time we are trying to find the symbol of a token, we can first search for it in this file,
    # so that we do not have to request API pull again.
    try:
        with open("token_names.json", "w", encoding="utf-8") as file:
            json.dump(all_token_names, file, indent=2)
        print(f"The token_names file is saved")
    except Exception as e:
        print(f"Failed to save the token_names: {e}")

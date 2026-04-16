import json
import time
from datetime import datetime, timezone
import pytz
import requests
import os
from dotenv import load_dotenv
load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
VERSION = 7

def load_swaps():
    swap_loaders = "data/wallets_swap_data"
    #getting the filenames of every file in the directory of wallets_swap_data. 
    #os.listdir() return a list of string, in this case, a list of filenames.
    filenames = os.listdir(swap_loaders)
    all_wallets = {}
    
    #Iterating over each file name in a list of file names.
    for swap_json_file in filenames:
        #if a file does not end with .json, skip this file and go for the next loop.
        if not swap_json_file.endswith(".json"):
            continue
        #Joining the parent directory of those files with those file names to have a whole file path.
        #open() only accept the whole file path, can not directly open and load a whole directory.
        filepath = os.path.join(swap_loaders, swap_json_file)
        #Open and load every file in wallets_swap_data
        with open(filepath, "r", encoding="utf-8") as file:
            swaps = json.load(file)
        #Getting the wallets' ids by replacing each json file's ".json" with empty string.
        wallet_ids = swap_json_file.replace(".json", "")
        #Using the wallets' ids as the key from above to get the swap data(value).
        all_wallets[wallet_ids] = swaps
        #print(f"[{wallet_ids}] {len(swaps)} 条 SWAP")
    #print(f"total of {len(swaps)} swaps")
    #Return a dict of all wallets. wallet ids are the keys and their swap data is the value.
    with open("data/wallets_list.json", "r", encoding="utf-8") as f:
        wallets_list = json.load(f)
    id_to_address = {w["address"][:8]: w["address"] for w in wallets_list}
    return all_wallets, id_to_address


def parse_jupiter(tx):
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
    
def parse_by_token_transfers(tx, wallet_address):
    timestamp = tx.get("timestamp")
    token_spent = {}
    token_received = {}

    for tt in tx.get("tokenTransfers", []):
        mint = tt.get("mint")
        amount = tt.get("tokenAmount", 0)

        if tt.get("fromUserAccount") == wallet_address:
            # 钱包花出去的
            if mint in token_spent:
                token_spent[mint] += amount
            else:
                token_spent[mint] = amount

        elif tt.get("toUserAccount") == wallet_address:
            # 钱包收到的
            if mint in token_received:
                token_received[mint] += amount
            else:
                token_received[mint] = amount
        # 其他的（手续费等）跳过

    return {
        "timestamp": timestamp,
        "sol_spent": 0,
        "sol_received": 0,
        "token_spent": token_spent,
        "token_received": token_received,
    }


def parse_swap(tx, wallet_address):
    #Getting "source" from the swap json file to know which platform of those transactions took place.
    source = tx.get("source")
    if source == "JUPITER":
        result = parse_jupiter(tx)
        if not result["token_spent"] and not result["token_received"] and result["sol_spent"] == 0 and result["sol_received"] == 0:
            return parse_by_token_transfers(tx, wallet_address)
        return result
    elif source == "PUMP_AMM":
        return parse_by_token_transfers(tx, wallet_address)
    else:
        return parse_by_token_transfers(tx, wallet_address)

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
def resolve_token_symbol(all_mints, cache):
    # Creating a new empty dict to store the result of address + symbol.
    symbol_names = {}
    # Iterates over every address in all_mints and send request to Helius to find the symbol name.
    for mint in all_mints:
        if mint in cache:
            symbol_names[mint] = cache[mint]
            continue
        # The format of JSON-RPC request(Helius DAS API required)
        payload = {
            "jsonrpc": "2.0",  # Telling the server we are using JSON-RPC 2.0.
            "id": "test",
            "method": "getAsset",  # Telling the server which method we are pulling, "getAsset" tells which asset
            "params": {
                "id": mint
            },  # Telling the server we are pulling the mint address of that asset.
        }
        # Retry up to 3 times in case of network timeout
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=15)
                symbol_result = response.json()
                symbol = symbol_result.get("result", {}).get("content", {}).get("metadata", {}).get("symbol", mint[:8])
                symbol_names[mint] = symbol
                break
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    print(f"  Retry {attempt + 1}/3 for {mint[:8]}... ({e})")
                    time.sleep(2)
                else:
                    print(f"  Failed to resolve {mint[:8]} after 3 attempts, using mint prefix")
                    symbol_names[mint] = mint[:8]
    return symbol_names


if __name__ == "__main__":
    all_wallets, id_to_address = load_swaps()
    os.makedirs("data/analyzed_swaps_data", exist_ok = True)
    pacificTime = pytz.timezone("America/Los_Angeles")

    output_swaps_tx = []
    #Before searching for token's symbol, see if the token is already stored in token_names.json
    if os.path.exists("data/token_names.json"):
            with open("data/token_names.json", "r") as f:
                all_token_names = json.load(f)
    else:
            all_token_names = {}

    # Load historical SOL price table (fetched by fetch_sol_prices.py)
    if os.path.exists("data/sol_price_history.json"):
        with open("data/sol_price_history.json", "r") as f:
            sol_price_history = json.load(f)
    else:
        sol_price_history = {}
        print("WARNING: data/sol_price_history.json not found. Run fetch_sol_prices.py first.")
            
    for wallet_ids, swaps in all_wallets.items():
        output_path = f"data/analyzed_swaps_data/{wallet_ids}.json"
        if os.path.exists(output_path):
            with open(output_path, "r") as f:
                existing = json.load(f)
            if isinstance(existing, dict) and existing.get("version") == VERSION:
                print(f"[{wallet_ids}] up to date, skip")
                continue
        print(f"Analyzing [{wallet_ids}].........")
        wallet_address = id_to_address.get(wallet_ids, "")
        parsed_swaps = [parse_swap(tx, wallet_address) for tx in swaps]
        parsed_swaps = [p for p in parsed_swaps if p is not None]
        sorted_time_asc = sorted(parsed_swaps, key=lambda x: x["timestamp"])
        all_mints = collect_mints(parsed_swaps)
        token_names = resolve_token_symbol(all_mints, all_token_names)
        #把新的token地址和symbol加进去
        all_token_names.update(token_names)
        output_swaps_tx = []
        
        # 在这个swap_data.json里：
        # nativeInput = SOL I paid; nativeOutput = SOL I received; tokenInputs = the token I paid; tokenOutputs = the token I received.
        for parsed in sorted_time_asc:
            converted_time = datetime.fromtimestamp(parsed["timestamp"], tz=pacificTime)
            utc_date_str = datetime.fromtimestamp(parsed["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d")
            sol_price_usd = sol_price_history.get(utc_date_str, 150)
            trade = {
                "time": str(converted_time),
                "sol_price_usd": sol_price_usd,
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
        output_data = {
            "version": VERSION,
            "swaps": output_swaps_tx
        }
        with open(f"data/analyzed_swaps_data/{wallet_ids}.json", "w", encoding = "utf-8") as f:
            json.dump(output_data, f, indent=2)
        print(f"[{wallet_ids}].json saved successfully.")

    # This part is write a json file that will contains the token's address and names.
    # By doing this, for the next time we are trying to find the symbol of a token, we can first search for it in this file,
    # so that we do not have to request API pull again.
    try:
        with open("data/token_names.json", "w", encoding="utf-8") as file:
            json.dump(all_token_names, file, indent=2)
        print(f"The token_names file is saved")
    except Exception as e:
        print(f"Failed to save the token_names: {e}")

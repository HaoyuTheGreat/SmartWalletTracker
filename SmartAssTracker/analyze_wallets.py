import json
from datetime import datetime, timezone
import pytz

def load_swaps():
    with open("swap_data.json", "r", encoding = "utf-8") as file:
        swaps = json.load(file)
    print(f"total of {len(swaps)} swaps")
    return swaps


def parse_swap(tx):
    timestamp = tx.get("timestamp")
    swap_event = tx.get("events", {}).get("swap", {})
    
    token_inputs = swap_event.get("tokenInputs", [])
    token_outputs = swap_event.get("tokenOutputs", [])
    native_input = swap_event.get("nativeInput")
    native_output = swap_event.get("nativeOutput")
    
    sol_spent = int(native_input.get("amount", 0)) / 10**9 if native_input else 0
    sol_received = int(native_output.get("amount", 0)) / 10 ** 9 if native_output else 0
    
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
        amount_output = int(raw_output.get("tokenAmount")) / 10 ** raw_output.get("decimals")
        
        if mint_output in non_native_token_bought:
            non_native_token_bought[mint_output] += amount_output
        else:
            non_native_token_bought[mint_output] = amount_output
        
    return{
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

if __name__ == "__main__":
    swaps = load_swaps()
    pacificTime = pytz.timezone('America/Los_Angeles')
    
    for tx in swaps:
        parsed = parse_swap(tx)
        converted_time = datetime.fromtimestamp(parsed["timestamp"],tz = pacificTime)
        
        #在这个swap_data.json里：
        #nativeInput = SOL I paid; nativeOutput = SOL I received; tokenInputs = the token I paid; tokenOutputs = the token I received.
        print(f"TimeL {converted_time}")
        print(f"SOL spent: {parsed['sol_spent']} SOL")
        print(f"SOL received: {parsed['sol_received']} SOL")
        for mint, amount in parsed['token_spent'].items():
            print(f"SELL: {amount: ,.2f} ({mint[:6]}...{mint[-4:]})")
        for mint, amount in parsed['token_received'].items():
            print(f"BUY: {amount: ,.2f} ({mint[:6]}...{mint[-4:]})")
        print()
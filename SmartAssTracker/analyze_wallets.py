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
    
    return{
        "timestamp": timestamp,
        "token_inputs": token_inputs,
        "token_outputs": token_outputs,
        "native_input": native_input,
        "native_output": native_output,
    }

if __name__ == "__main__":
    swaps = load_swaps()
    pacificTime = pytz.timezone('America/Los_Angeles')
    
    for tx in swaps:
        parsed = parse_swap(tx)
        converted_time = datetime.fromtimestamp(parsed["timestamp"],tz = pacificTime)
        
        print(f"TimeL {converted_time}")
        print(f"Token Sold: {len(parsed['token_inputs'])}")
        print(f"Token Bought: {len(parsed['token_outputs'])}")
        print(f"SOL out: {parsed['native_input']}")
        print(f"SOL in: {parsed['native_output']}")
        print()
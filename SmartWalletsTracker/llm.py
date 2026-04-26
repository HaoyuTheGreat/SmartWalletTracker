"""
llm.py - Use the Claude API to detect whether a wallet is a market maker.

Reads each wallet's swap history from data/raw_swaps/, formats it into a prompt,
asks Claude to decide whether the wallet behaves like a market maker, and writes
the verdicts to data/analysis_results.json.

Usage:
  python llm.py

Input:  data/raw_swaps/<wallet_address>.json
Output: data/llm_analysis_results.json
"""

import anthropic
import json
import os
import time

from lib.secrets import get_secret

# === Config ===
RAW_SWAPS_DIR = "data/analyzed_swaps_data"
OUTPUT_PATH = "data/llm_analysis_results.json"

# Claude client
client = anthropic.Anthropic(api_key=get_secret("CLAUDE_API_KEY"))


def format_swap_for_llm(swap):
    """Convert a single swap record into an LLM-friendly summary."""

    # Determine buy vs sell
    if swap.get("token_received"):
        direction = "buy"
        token = swap["token_received"][0]["symbol"]
        # Whether the cost was SOL or a stablecoin
        if swap.get("token_spent"):
            amount = swap["token_spent"][0]["amount"]
        else:
            amount = swap.get("sol_spent", 0)
    elif swap.get("token_spent"):
        direction = "sell"
        token = swap["token_spent"][0]["symbol"]
        # Whether the proceeds were SOL or a stablecoin
        if swap.get("token_received"):
            amount = swap["token_received"][0]["amount"]
        else:
            amount = swap.get("sol_received", 0)
    else:
        direction = "unknown"
        token = "unknown"
        amount = 0

    return {
        "time": swap.get("time", ""),
        "direction": direction,
        "token": token,
        "amount": round(amount, 2)
    }


def is_market_maker(wallet_address, swaps):
    """Ask Claude whether this wallet looks like a market maker."""

    swap_text = ""
    for swap in swaps:
        swap_text += f"- time: {swap['time']}, direction: {swap['direction']}, token: {swap['token']}, amount: {swap['amount']}\n"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": f"""
Here are the swap records for a Solana wallet:
{swap_text}

Decide whether this wallet behaves like a market maker. Market-maker traits:
1. Buy and sell counts are nearly balanced.
2. Holding period is extremely short (seconds to minutes).
3. Trading frequency is very high.
4. The same token is bought and sold repeatedly.

Reply in exactly this format:
market_maker: yes / no
reason: (one short sentence)
                """
            }
        ]
    )

    return message.content[0].text


def load_wallet_swaps(wallet_address):
    """Load swap records for a single wallet."""
    file_path = os.path.join(RAW_SWAPS_DIR, f"{wallet_address}.json")

    if not os.path.exists(file_path):
        print(f"   file not found: {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Accept either a bare list or a dict containing a "swaps" field
    if isinstance(data, list):
        return data
    else:
        return data.get("swaps", [])


def analyze_all_wallets():
    """Run the LLM check on every wallet file."""

    if not os.path.exists(RAW_SWAPS_DIR):
        print(f"directory not found: {RAW_SWAPS_DIR}")
        return

    wallet_files = [f for f in os.listdir(RAW_SWAPS_DIR) if f.endswith(".json")]
    print(f"found {len(wallet_files)} wallet files")

    results = []

    for i, wallet_file in enumerate(wallet_files):
        wallet_address = wallet_file.replace(".json", "")
        print(f"\n[{i+1}/{len(wallet_files)}] analyzing: {wallet_address[:20]}...")

        raw_swaps = load_wallet_swaps(wallet_address)
        if not raw_swaps:
            print(f"   no swap records, skipping")
            continue

        print(f"   {len(raw_swaps)} swap records")

        formatted_swaps = [format_swap_for_llm(s) for s in raw_swaps]

        try:
            result_text = is_market_maker(wallet_address, formatted_swaps)
            print(f"   Claude verdict: {result_text.strip()}")

            # Parse the yes/no on the first line.
            first_line = result_text.split("\n")[0].lower()
            is_mm = "yes" in first_line

            results.append({
                "wallet": wallet_address,
                "is_market_maker": is_mm,
                "claude_response": result_text.strip(),
                "swap_count": len(raw_swaps)
            })

        except Exception as e:
            print(f"   Claude API call failed: {e}")
            continue

        # Light pacing to avoid rate limits
        time.sleep(0.5)

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    mm_count = sum(1 for r in results if r["is_market_maker"])
    print(f"\n{'='*60}")
    print(f" Done")
    print(f"{'='*60}")
    print(f" total wallets:    {len(results)}")
    print(f" market makers:    {mm_count}")
    print(f" non market-makers:{len(results) - mm_count}")
    print(f" output:           {OUTPUT_PATH}")


if __name__ == "__main__":
    print(f"{'='*60}")
    print(f" Analyze Wallets - Market-maker detection")
    print(f"{'='*60}")

    analyze_all_wallets()

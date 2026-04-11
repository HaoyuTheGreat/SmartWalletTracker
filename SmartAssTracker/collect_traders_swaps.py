
import requests
import json
import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding='utf-8')
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

def load_wallets():
  with open("data/wallets_list.json", "r", encoding = "utf-8") as f:
    wallets = json.load(f)
  return wallets


def connect_Helius():
    if not HELIUS_API_KEY:
        print("No Helius API key found. Please check .env file.")
        return False
    # 用一个简单的请求测试连接
    test_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    helius_url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{test_address}/transactions/?api-key={HELIUS_API_KEY}"
    response = requests.get(helius_url, timeout=10)

    if response.status_code == 200:
        print("Helius Connected Successfully.")
        return True
    else:
        print(f"Connection Failed, status code: {response.status_code}")
        return False
  

if __name__ == "__main__":
    #This wallets variable is a list, because load_wallets() returned a list.
    wallets = load_wallets()
    os.makedirs("data/wallets_swap_data", exist_ok = True)
    os.makedirs("data/failed_wallets", exist_ok = True)
    failed_wallets = []
    #Iterating over every wallet in the list of wallets
    for wallet_info in wallets:
      #Getting the address from each wallet. Using ["KEY"] gets the corresponding value in dict.
      address = wallet_info["address"]
      
      output_path = f"data/wallets_swap_data/{address[:8]}.json"
      #Checking if the wallet already exists.
      if os.path.exists(output_path):
        print(f"[{address[:8]}] exists, skip")
        continue
      if os.path.exists(f"data/failed_wallets/{address[:8]}.json"):
        print(f"[{address[:8]}] previously failed, skip")
        continue
 
      data_pages = []

      before = None
      while len(data_pages) < 2000:
        url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions/?api-key={HELIUS_API_KEY}&type=SWAP&limit=100"
        if before:
          url += f"&before={before}"
        try:
            response = requests.get(url, timeout=30)
            page = response.json()
        except requests.exceptions.RequestException:
            print(f"[{address[:8]}] Request Failed, Skip")
            break
        if not page:
          break
        if not isinstance(page, list):
          print(f"[{address[:8]}]Returned Unexpected Data：{str(page)[:100]}")
          break
        data_pages.extend(page)
        before = page[-1]["signature"]
        time.sleep(1)
      data = data_pages[:2000]
      print(f"[{address[:8]}] Received total of {len(data)}swap transactions")
      if data:
        with open(f"data/wallets_swap_data/{address[:8]}.json", "w", encoding="utf-8") as f:
          json.dump(data, f, indent=2)
      else:
        with open(f"data/failed_wallets/{address[:8]}.json", "w", encoding = "utf-8") as f:
          json.dump(wallet_info, f, indent = 2)
      
      time.sleep(1)





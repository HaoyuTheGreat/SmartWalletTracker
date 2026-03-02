"""
collect_traders.py - 交易者收集器（smart_wallet_finder 流水线第一步）

功能：
  读取 tokens.json 里的代币列表，
  用 Helius API 查每个代币的交易记录，
  找出参与交易的钱包地址。

用法：
  python collect_traders.py

=== 更新日志 ===
2026-02-26: 初始版本
2026-02-27: 移除 DexScreener，改为读取 tokens.json
"""

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

def load_tokens():
  with open("tokens.json", "r", encoding = "utf-8") as file:
    tokens = json.load(file)
  return tokens



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
    wallet = "AUFHo8kwiLArai4NyLEgLnWxFdz7LiVa6rpfsJtzGTTR"
    url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{wallet}/transactions/?api-key={HELIUS_API_KEY}&type=SWAP"
    
    response = requests.get(url, timeout=10)
    data = response.json()
    
    print(f"返回了 {len(data)} 条 SWAP 交易")
    
    # 看看第一条交易长什么样
    if data:
      with open("swap_data.json", "w", encoding="utf-8") as f:
          json.dump(data, f, indent=2)
      print(f"已保存 {len(data)} 条交易到 swap_data.json")




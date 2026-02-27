"""
collect_tokens.py - 代币筛选器（smart_wallet_finder 流水线第零步）

功能：
  从 DexScreener 拉取 Solana 链上的代币列表，
  按条件筛选出有基本面的候选代币，
  输出到 data/candidate_tokens.json。

筛选条件：
  - 流动性 > $500K
  - 24h 交易量 > $100K
  - 上线时间 > 30 天
  - 排除 Pump.fun 合约

用法：
  python collect_tokens.py

=== 更新日志 ===
2026-02-26: 初始版本
"""

import requests  # 发送 HTTP 请求，用来调用 DexScreener API
import json      # 读写 JSON 文件
import os        # 文件路径操作
import sys       # 系统相关，比如退出程序
import time      # 控制请求频率，避免被限速
from datetime import datetime, timezone  # 时间处理

sys.stdout.reconfigure(encoding='utf-8')

# === 配置 ===
OUTPUT_DIR = "data"

# DexScreener API 基础地址
DEXSCREENER_BASE_URL = "https://api.dexscreener.com"


def test_connection_DexScreener():
    """测试能否正常连接 DexScreener API"""
    print("正在测试 DexScreener 连接...")

    #把这个网址当为字符串赋值给url，f"..."为f-string, 意思是字符串里面可以嵌入变量，用{}包起来。 这样的话，如果我们要改地址，就只需要改“"https://api.dexscreener.com"“。
    # 用一个简单的请求测试：查询 SOL/USDC 交易对
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q=SOL/USDC"
    #这个网址的服务器返回的所有东西，包括状态码、header、body 等等，都塞进了 response 这个变量里。 timeout = 10 意思是如果服务器在十秒内无反应，就终止并返回错误。
    response = requests.get(url, timeout=10)

    # 检查状态码，200 表示请求成功
    if response.status_code == 200:
        #data 为 dictionary, 这一行是把response里的body部分解析成python dictionary 然后存进data。
        data = response.json()
        #data.get("pairs", []), 意思为从这个大字典里的东西中，只把pairs那部分取出来（pairs为key，把他的值全部取出来），如果找不到就返回[]。
        pair_count = len(data.get("pairs", []))
        print(f"连接成功！返回了 {pair_count} 个交易对")
        return True
    else:
        print(f"连接失败，状态码: {response.status_code}")
        return False


if __name__ == "__main__":
    print(f"{'='*60}")
    print(f" Collect Tokens - Solana 代币筛选")
    print(f"{'='*60}")

    test_connection_DexScreener()
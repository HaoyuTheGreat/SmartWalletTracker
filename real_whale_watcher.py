"""
这个脚本的作用：Publisher（发布者），负责实时监听 Solana 链上的 USDC 和 USDT 稳定币交易，
解析出发送方、接收方和具体金额，并发送到 Google Cloud Pub/Sub Topic。

【最近修改记录】：
  1. 新增 USDT 监控：同时订阅 USDC 和 USDT 两种稳定币的链上交易日志。
  2. 重构解析函数：parse_usdc_transfers → parse_stablecoin_transfers，支持多币种解析。
  3. 修复了 block_time 获取位置错误的 Bug（从 resp.block_time 改为 tx.block_time）。
  4. 优化了 AsyncClient 的生命周期管理，使用 async with 防止连接池泄漏。
  5. 使用 asyncio.create_task() 后台处理交易解析，避免阻塞 WebSocket 消息循环。
  6. 增加了 Semaphore 限流（最多 10 个并发 HTTP 请求）+ 指数退避重试，防止 429 错误。
  7. 增加了 WebSocket 断线自动重连机制。

工作流程：
  1. 通过 WebSocket 实时连接 Helius RPC 节点（Solana 主网）。
  2. 同时订阅 USDC 和 USDT 两种稳定币的链上交易日志（只拿到交易签名/流水号）。
  3. 拿到签名后，通过 asyncio.create_task() 在后台发起 HTTP RPC 请求，获取完整交易详情。
  4. 对比 preTokenBalances 和 postTokenBalances，解析出每笔交易的发送方、接收方、币种和金额。
  5. 打包成 JSON（包含 source, token, signature, sender, receiver, amount, timestamp）。
  6. 调用 publisher.publish() 发送到 Pub/Sub Topic (SolanaWhaleTracker)。

整体架构：
  Helius RPC ──(WebSocket)──→ 本脚本(拿签名) ──(HTTP, 后台异步)──→ 解析出金额
       │                                                                │
       │  订阅: USDC + USDT                                             ▼
       │                                                     Pub/Sub Topic (SolanaWhaleTracker)
       │                                                          ├── saver-to-bigquery-v2 (自动存BigQuery)
       │                                                          └── pull_test (test_sub.py 调试用)
       │
       └── 断线自动重连（5秒间隔）

Payload 格式（每条消息）：
  {
      "source": "Solana_Mainnet",
      "token": "USDC" | "USDT",
      "signature": "5abc...xyz",
      "sender": "7Kbx...abc",
      "receiver": "9Def...xyz",
      "amount": 150000.00,
      "timestamp": "2026-02-05T12:34:56+00:00"
  }
"""

import asyncio
import os
import json
import random

#importing Google Cloud Pub/Sub Python client library.
#We are using this for: 
# 1. Create the publisherClient(the publisher client); 
# 2. Build the topic_path, which is pointing to my SolonaWhaleTracker topic; 
# 3. Call publisher.publish() to push the captured transaction data to Pub/Sub
from google.cloud import pubsub_v1

#Provides the WebSocket client to connect to a Solona RPC node(wss://api.mainnet-beta.solana.com), which is how we get a real-time stream of on-chain events
from solana.rpc.websocket_api import connect
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from solders.rpc.config import RpcTransactionLogsFilterMentions 

from datetime import datetime, timezone
from solana.rpc.async_api import AsyncClient
from solders.signature import Signature


# Sets an environment variable(GOOGLE_APPLICATION_CREDENTIALS) to point to a file called key.json in the current working directory.
# It tells the Google Cloud client library(the pubsub_v1) how to authenticate. 
# key.json is a service account key file we have downloaded from the GCP console, which contains credentials(project ID, private key, client email, etc) 
# that allow the script to publish messages to my Pub/Sub topic.
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"


project_id = "angular-theorem-486301-n3" 


topic_id = "SolanaWhaleTracker"       

# Creates a Pub/Sub publisher client-an object that knows how to send messages to Pub/Sub
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(project_id, topic_id)

#Creates a Solana Pubkey object from the USDC mint address string.
#EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v is the official on-chain address of the USDC token on Solana
#Pubkey.from_string() converts that human-readable base58 string into a proper Pubkey object that the Solana libraries can work with
# Stablecoin mint addresses on Solana
USDC_MINT_STR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT_STR = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

USDC_MINT = Pubkey.from_string(USDC_MINT_STR)
USDT_MINT = Pubkey.from_string(USDT_MINT_STR)

# Both USDC and USDT have 6 decimals on Solana
STABLECOIN_DECIMALS = 6

# Map mint address -> token name for lookup
STABLECOIN_MAP = {
    USDC_MINT_STR: "USDC",
    USDT_MINT_STR: "USDT",
}

HELIUS_KEY = "13d0159a-4cb2-4668-a95d-faa268f0e0fb"
SOLANA_RPC_URL = "https://mainnet.helius-rpc.com/?api-key=13d0159a-4cb2-4668-a95d-faa268f0e0fb"
SOLANA_WS_URL = "wss://mainnet.helius-rpc.com/?api-key=13d0159a-4cb2-4668-a95d-faa268f0e0fb"

# Rate limiting: only allow 2 concurrent HTTP requests to avoid 429 errors
# The free public RPC has strict rate limits
MAX_CONCURRENT_REQUESTS = 10
http_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

print("Connecting Solana main network, preparing to capture big whale.....")


async def parse_stablecoin_transfers(http_client, sig_str):
    """
    Fetch the full transaction by signature and parse USDC/USDT transfers
    by comparing preTokenBalances vs postTokenBalances.

    Returns a list of transfer dicts: [{sender, receiver, token, amount, timestamp}, ...]
    """
    sig = Signature.from_string(sig_str)

    resp = await http_client.get_transaction(
        sig,
        encoding="jsonParsed",
        max_supported_transaction_version=0,
        commitment=Commitment("confirmed")
    )

    tx = resp.value
    if tx is None:
        return []

    meta = tx.transaction.meta
    if meta is None:
        return []

    block_time = tx.block_time
    timestamp = None
    if block_time:
        timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat()

    # Check each stablecoin (USDC and USDT) for balance changes
    transfers = []

    for mint_str, token_name in STABLECOIN_MAP.items():
        pre_balances = {}
        post_balances = {}

        if meta.pre_token_balances:
            for bal in meta.pre_token_balances:
                if str(bal.mint) == mint_str and bal.owner:
                    owner = str(bal.owner)
                    amount = int(bal.ui_token_amount.amount)
                    pre_balances[owner] = pre_balances.get(owner, 0) + amount

        if meta.post_token_balances:
            for bal in meta.post_token_balances:
                if str(bal.mint) == mint_str and bal.owner:
                    owner = str(bal.owner)
                    amount = int(bal.ui_token_amount.amount)
                    post_balances[owner] = post_balances.get(owner, 0) + amount

        # Calculate who gained and who lost this stablecoin
        all_owners = set(pre_balances.keys()) | set(post_balances.keys())
        changes = {}
        for owner in all_owners:
            pre = pre_balances.get(owner, 0)
            post = post_balances.get(owner, 0)
            diff = post - pre
            if diff != 0:
                changes[owner] = diff

        senders = {k: v for k, v in changes.items() if v < 0}
        receivers = {k: v for k, v in changes.items() if v > 0}

        for sender, sent_amount in senders.items():
            for receiver, received_amount in receivers.items():
                amount = abs(sent_amount) / (10 ** STABLECOIN_DECIMALS)
                transfers.append({
                    "sender": sender,
                    "receiver": receiver,
                    "token": token_name,
                    "amount": amount,
                    "timestamp": timestamp,
                })
                break

    return transfers


async def process_transaction(http_client, signature):
    """
    Background task: wait a moment for the tx to be available,
    then fetch, parse, and publish to Pub/Sub.
    Uses a semaphore to limit concurrent requests and avoid 429 rate limits.
    """
    # Wait for a slot in the semaphore (limits concurrent HTTP requests)
    async with http_semaphore:
        try:
            # Small delay so the transaction is available via HTTP RPC
            await asyncio.sleep(2)

            # Retry up to 3 times with increasing delay if rate limited
            transfers = None
            for attempt in range(3):
                try:
                    transfers = await parse_stablecoin_transfers(http_client, signature)
                    break  # Success, exit retry loop
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s backoff
                    else:
                        raise

            if not transfers:
                # Could not parse transfers, publish raw data as fallback
                payload = {
                    "source": "Solana_Mainnet",
                    "token": "unknown",
                    "signature": signature,
                    "sender": None,
                    "receiver": None,
                    "amount": None,
                    "timestamp": None,
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                publisher.publish(topic_path, data_bytes)
                return

            # Publish each parsed transfer to Pub/Sub
            for t in transfers:
                payload = {
                    "source": "Solana_Mainnet",
                    "token": t["token"],
                    "signature": signature,
                    "sender": t["sender"],
                    "receiver": t["receiver"],
                    "amount": t["amount"],
                    "timestamp": t["timestamp"],
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                publisher.publish(topic_path, data_bytes)

                print(
                    f"\n🔥 Captured! {t['amount']:,.2f} {t['token']} | "
                    f"{t['sender'][:6]}... -> {t['receiver'][:6]}... | "
                    f"Sig: {signature[:8]}..."
                )

        except Exception as e:
            print(f"\nFailed: {signature[:8]}... {type(e).__name__}: {e}")


async def main():
    # === 【修复点】使用 async with 包裹 HTTP 客户端，确保安全关闭 ===
    async with AsyncClient(SOLANA_RPC_URL) as http_client:

        # Auto-reconnect loop: if WebSocket drops, reconnect and resubscribe
        while True:
            try:
                async with connect(SOLANA_WS_URL) as websocket:

                    usdc_filter = RpcTransactionLogsFilterMentions(USDC_MINT)
                    usdt_filter = RpcTransactionLogsFilterMentions(USDT_MINT)

                    await websocket.logs_subscribe(
                        filter_=usdc_filter,
                        commitment=Commitment("confirmed")
                    )
                    await websocket.logs_subscribe(
                        filter_=usdt_filter,
                        commitment=Commitment("confirmed")
                    )

                    print("Connected to Helius! Monitoring USDC + USDT...")

                    async for message in websocket:
                        try:
                            if not hasattr(message[0], 'result'):
                                continue

                            value = message[0].result.value
                            signature = str(value.signature)

                            # Heartbeat: shows WebSocket is receiving data
                            print(f"Heard signal: {signature[:8]}...", end="\r")

                            # === 【新增点】抽样限制 (如果你发现还是报错，取消注释下面两行) ===
                            # if random.random() > 0.1:
                            #     continue

                            # Launch parsing in background so it doesn't block WebSocket
                            asyncio.create_task(
                                process_transaction(http_client, signature)
                            )

                        except Exception:
                            pass

            except Exception as e:
                print(f"WebSocket disconnected: {e}")
                print("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped monitoring.")
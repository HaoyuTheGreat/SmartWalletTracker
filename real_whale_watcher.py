"""
这个脚本的作用：Publisher（发布者），负责监听 Solana 链上 USDC 交易，并把捕获到的交易数据发送到 Pub/Sub Topic。

它本身不存数据、不做分析，只管往 Topic 里扔消息。
Topic 收到消息后会自动分发给下游的所有订阅（BigQuery存储、Pull调试等），跟这个脚本无关。

工作流程：
  1. 通过 WebSocket 连接 Solana 主网 RPC 节点
  2. 订阅所有涉及 USDC 的链上交易日志
  3. 每捕获一笔交易，打包成 JSON（包含签名、日志等）
  4. 调用 publisher.publish() 发送到 Topic (SolanaWhaleTracker)

整体架构：
  Solana主网 ──WebSocket──→ 本脚本 ──Pub/Sub──→ Topic (SolanaWhaleTracker)
                                                    ├── saver-to-bigquery-v2 (自动存BigQuery)
                                                    └── pull_test (test_sub.py 调试用)
"""

import asyncio
import os
import json
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
USDC_MINT = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

print("Connecting Solana main network, preparing to capture big whale.....")

async def main():
    #Opens a WebSocket connection to Solana's public mainnet RPC node.
    #connect() establishes a persistent, two-way connection(unlike HTTP which is request-response). This lets you receive a live stream of on-chain events in real time.
    #async with ... as websocket: ensures the connection is properly closed when the code exits or an error occurs
    async with connect("wss://api.mainnet-beta.solana.com") as websocket:
        
        # --- 修正了这里的订阅逻辑 ---
        # 新版 solana-py 要求必须构建一个 Filter 对象
        usdc_filter = RpcTransactionLogsFilterMentions(USDC_MINT)
        
        await websocket.logs_subscribe(
            filter_=usdc_filter,              # 过滤条件：只听 USDC
            commitment=Commitment("confirmed") # 确认级别：confirmed
        )
        
        print("✅ 监听已启动！等待链上数据...")

        async for message in websocket:
            try:
                # 这是一个 list，通常第一个元素是结果
                # 新版 solders 返回的对象结构可能略有不同，我们加个保险
                if not hasattr(message[0], 'result'):
                    continue

                value = message[0].result.value
                logs = value.logs
                signature = value.signature
                
                payload = {
                    "source": "Solana_Mainnet",
                    "token": "USDC",
                    "signature": str(signature),
                    "raw_logs": logs
                }
                
                # 发送给 Google Pub/Sub
                data_bytes = json.dumps(payload).encode("utf-8")
                # 异步发送，不等待结果，防止阻塞
                publisher.publish(topic_path, data_bytes)
                
                print(f"🔥 捕获交易! Sig: {str(signature)[:8]}...")
                
            except Exception as e:
                # 打印一下错误，方便调试，如果是心跳包错误则忽略
                # print(f"Debug: {e}")
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 停止监听")
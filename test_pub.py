import os
import time
import json
from google.cloud import pubsub_v1

# 1. 设置凭证 (告诉谷歌你是谁)
# 这里的 key.json 就是刚才下载的那个文件
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# 2. 配置参数
project_id = "angular-theorem-486301-n3"  
topic_id = "SolanaWhaleTracker"

publisher = pubsub_v1.PublisherClient()
# 拼接成完整的路径: projects/{project_id}/topics/{topic_id}
topic_path = publisher.topic_path(project_id, topic_id)

print(f"准备向 {topic_path} 发送数据...")

# 3. 模拟一条鲸鱼数据 (假装这是从 Solana 抓来的)
whale_data = {
    "timestamp": time.time(),
    "wallet": "Huobi_Hot_Wallet",
    "amount": 5000000,
    "token": "USDC",
    "action": "TRANSFER_IN"
}

whale_data2 = {
    "timestamp": time.time(),
    "wallet": "hot_pot",
    "amount": 22,
    "token": "USDC",
    "action": "TRANSFER_IN"
}

# Pub/Sub 只接收 bytes 类型，所以要转成 json string 再转 bytes
data = json.dumps(whale_data).encode("utf-8")

# 4. 发送！
future = publisher.publish(topic_path, data)
print(f"发送成功！消息ID: {future.result()}")
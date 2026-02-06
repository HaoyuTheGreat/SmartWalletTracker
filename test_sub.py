"""
这个脚本的作用：调试/监控工具，实时在终端打印 Pub/Sub Topic 收到的消息。
只是用来看一眼消息有没有正常发出来，不会存到 BigQuery 里。

流程：
  Publisher 发消息 → Topic (SolanaWhaleTracker)
                        ├── saver-to-bigquery-v2  (BigQuery订阅，自动存表，跟这个脚本无关)
                        └── pull_test             (Pull订阅，这个脚本用来拉取并打印消息)

注意：pull_test 和 saver-to-bigquery-v2 互不影响，删掉 pull_test 不会影响 BigQuery 的数据写入。
"""

import os
from google.cloud import pubsub_v1

# 1. 还是那个身份验证
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# 2. 配置参数
project_id = "angular-theorem-486301-n3"  
# 注意：这里填的是 Subscription 的名字！不是 Topic 的名字！
# 你刚才在 Topic 界面点 "Create Subscription" 创建的那个 ID (比如 'solana-whale-alerts-sub')
subscription_id = "pull_test" 

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(project_id, subscription_id)

def callback(message):
    # 收到消息时会触发这个函数
    print(f"收到鲸鱼信号: {message.data.decode('utf-8')}")
    
    # 关键！发送 Ack (告诉谷歌我收到了，别再发了)
    message.ack()

print(f"正在监听 {subscription_path} ...")

# 3. 开启监听 (这是个后台线程，会一直跑)
streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)

# 让主程序不退出，一直等着
try:
    streaming_pull_future.result()
except KeyboardInterrupt:
    streaming_pull_future.cancel()
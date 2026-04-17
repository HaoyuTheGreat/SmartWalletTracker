"""
analyze_wallets.py - 用 Claude API 分析钱包是否为做市商

功能：
  读取 data/raw_swaps/ 目录下每个钱包的 swap 记录，
  格式化后发给 Claude API 判断是否为做市商，
  输出结果到 data/analysis_results.json。

用法：
  python analyze_wallets.py

输入：data/raw_swaps/<wallet_address>.json
输出：data/analysis_results.json
"""

import anthropic
import json
import os
import time

from lib.secrets import get_secret

# === 配置 ===
RAW_SWAPS_DIR = "data/analyzed_swaps_data"
OUTPUT_PATH = "data/llm_analysis_results.json"

# 初始化 Claude 客户端
client = anthropic.Anthropic(api_key=get_secret("CLAUDE_API_KEY"))


def format_swap_for_llm(swap):
    """把一条 swap 记录转换成 LLM 容易理解的格式"""

    # 判断买入还是卖出
    if swap.get("token_received"):
        direction = "买入"
        token = swap["token_received"][0]["symbol"]
        # 花出去的是 SOL 还是稳定币
        if swap.get("token_spent"):
            amount = swap["token_spent"][0]["amount"]
        else:
            amount = swap.get("sol_spent", 0)
    elif swap.get("token_spent"):
        direction = "卖出"
        token = swap["token_spent"][0]["symbol"]
        # 收回来的是 SOL 还是稳定币
        if swap.get("token_received"):
            amount = swap["token_received"][0]["amount"]
        else:
            amount = swap.get("sol_received", 0)
    else:
        direction = "未知"
        token = "未知"
        amount = 0

    return {
        "time": swap.get("time", ""),
        "direction": direction,
        "token": token,
        "amount": round(amount, 2)
    }


def is_market_maker(wallet_address, swaps):
    """发给 Claude 判断这个钱包是否为做市商"""

    # 格式化所有 swap 记录成文字
    swap_text = ""
    for swap in swaps:
        swap_text += f"- 时间：{swap['time']}，方向：{swap['direction']}，代币：{swap['token']}，金额：{swap['amount']}\n"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": f"""
这是一个 Solana 钱包的交易记录：
{swap_text}

请判断这个钱包是否是做市商，做市商的特征是：
1. 买卖次数极度平衡，买入和卖出数量接近
2. 持有时间极短，几秒到几分钟
3. 交易频率极高
4. 同一个代币反复买卖

请只回答：
是否做市商：是 / 否
原因：（一句话）
                """
            }
        ]
    )

    return message.content[0].text


def load_wallet_swaps(wallet_address):
    """读取某个钱包的 swap 记录"""
    file_path = os.path.join(RAW_SWAPS_DIR, f"{wallet_address}.json")

    if not os.path.exists(file_path):
        print(f"   找不到文件: {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 兼容两种格式：直接是列表，或者是带 swaps 字段的字典
    if isinstance(data, list):
        return data
    else:
        return data.get("swaps", [])


def analyze_all_wallets():
    """批量分析所有钱包"""

    # 找到所有钱包文件
    if not os.path.exists(RAW_SWAPS_DIR):
        print(f"找不到目录: {RAW_SWAPS_DIR}")
        return

    wallet_files = [f for f in os.listdir(RAW_SWAPS_DIR) if f.endswith(".json")]
    print(f"找到 {len(wallet_files)} 个钱包文件")

    results = []

    for i, wallet_file in enumerate(wallet_files):
        wallet_address = wallet_file.replace(".json", "")
        print(f"\n[{i+1}/{len(wallet_files)}] 正在分析: {wallet_address[:20]}...")

        # 读取 swap 记录
        raw_swaps = load_wallet_swaps(wallet_address)
        if not raw_swaps:
            print(f"   没有 swap 记录，跳过")
            continue

        print(f"   共 {len(raw_swaps)} 条 swap 记录")

        # 格式化
        formatted_swaps = [format_swap_for_llm(s) for s in raw_swaps]

        # 发给 Claude 判断
        try:
            result_text = is_market_maker(wallet_address, formatted_swaps)
            print(f"   Claude 判断: {result_text.strip()}")

            # 解析是否做市商
            is_mm = "是" in result_text.split("\n")[0]

            results.append({
                "wallet": wallet_address,
                "is_market_maker": is_mm,
                "claude_response": result_text.strip(),
                "swap_count": len(raw_swaps)
            })

        except Exception as e:
            print(f"   Claude API 调用失败: {e}")
            continue

        # 避免请求太快被限速
        time.sleep(0.5)

    # 保存结果
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 打印汇总
    mm_count = sum(1 for r in results if r["is_market_maker"])
    print(f"\n{'='*60}")
    print(f" 分析完成")
    print(f"{'='*60}")
    print(f" 总钱包数:   {len(results)}")
    print(f" 做市商:     {mm_count}")
    print(f" 非做市商:   {len(results) - mm_count}")
    print(f" 结果保存到: {OUTPUT_PATH}")


if __name__ == "__main__":
    print(f"{'='*60}")
    print(f" Analyze Wallets - 做市商检测")
    print(f"{'='*60}")

    analyze_all_wallets()
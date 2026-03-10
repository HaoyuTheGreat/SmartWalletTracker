"""_summary_
    这个文件是是查看钱包的当前持仓。
    Returns:
        _type_: _description_
"""
import requests
import time
from config import API_KEY

TARGET_WALLET = "A3vFkGBrj4MRugemEXWdSFztWgBXi9eQPtD3V28Va7WH"

# 跳过 Wrapped SOL（会在 SOL 余额里体现）
SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
}

# 稳定币（单独展示，不混在 meme 币里）
STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
}


def fetch_holdings(wallet):
    """用 Helius DAS API 拉取钱包当前所有代币持仓（含 Helius 自带价格）"""
    url = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
    holdings = []
    page = 1

    print(f"🔍 正在查询 {wallet[:6]}... 的持仓...")

    while True:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "getAssetsByOwner",
            "params": {
                "ownerAddress": wallet,
                "page": page,
                "limit": 1000,
                "displayOptions": {"showFungible": True}
            }
        }
        try:
            resp = requests.post(url, json=payload)
            data = resp.json()
            items = data.get("result", {}).get("items", [])
            if not items:
                break

            for item in items:
                iface = item.get("interface", "")
                if iface not in ("FungibleToken", "FungibleAsset"):
                    continue

                mint = item.get("id", "")
                if mint in SKIP_MINTS:
                    continue

                is_stable = mint in STABLECOIN_MINTS

                token_info = item.get("token_info", {})
                raw_balance = token_info.get("balance", 0)
                decimals = token_info.get("decimals", 0)
                balance = raw_balance / (10 ** decimals) if decimals > 0 else raw_balance

                if balance <= 0:
                    continue

                # 代币名称/symbol
                content = item.get("content", {})
                metadata = content.get("metadata", {})
                symbol = metadata.get("symbol", mint[:6] + "...")
                name = metadata.get("name", "")

                # Helius 自带价格（部分代币有）
                price_info = token_info.get("price_info", {})
                helius_price = price_info.get("price_per_token", 0)

                holdings.append({
                    "mint": mint,
                    "symbol": symbol,
                    "name": name,
                    "balance": balance,
                    "decimals": decimals,
                    "helius_price": helius_price,
                    "is_stable": is_stable,
                })

            if len(items) < 1000:
                break
            page += 1

        except Exception as e:
            print(f"❌ 查询出错: {e}")
            break

    has_price = sum(1 for h in holdings if h["helius_price"] > 0)
    print(f"   找到 {len(holdings)} 个代币持仓（Helius 有价格: {has_price} 个）")
    return holdings


def fetch_prices_dexscreener(mints):
    """用 DexScreener 批量查价格（补漏）"""
    prices = {}

    for i in range(0, len(mints), 30):
        chunk = mints[i:i+30]
        ids = ",".join(chunk)
        try:
            resp = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{ids}")
            data = resp.json()
            for pair in data:
                mint_addr = pair.get("baseToken", {}).get("address", "")
                price_usd = pair.get("priceUsd")
                if not mint_addr or not price_usd:
                    continue
                liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
                if mint_addr not in prices or liquidity > prices[mint_addr][1]:
                    prices[mint_addr] = (float(price_usd), liquidity)
        except Exception as e:
            print(f"   ⚠️ DexScreener 出错: {e}")
        time.sleep(0.3)

    return {k: v[0] for k, v in prices.items()}


if __name__ == "__main__":
    holdings = fetch_holdings(TARGET_WALLET)

    if not holdings:
        print("没有找到持仓")
        exit()

    # 分离稳定币和代币
    stables = [h for h in holdings if h["is_stable"]]
    tokens = [h for h in holdings if not h["is_stable"]]

    # 稳定币价格直接 = $1
    for h in stables:
        h["price_usd"] = 1.0
        h["price_source"] = "-"
        h["value_usd"] = h["balance"]

    # 代币价格：Helius 优先，DexScreener 补漏
    dex_prices = {}
    no_price_mints = [h["mint"] for h in tokens if h["helius_price"] <= 0]
    if no_price_mints:
        print(f"💲 {len(no_price_mints)} 个代币无 Helius 价格，用 DexScreener 补漏...")
        dex_prices = fetch_prices_dexscreener(no_price_mints)
        print(f"   DexScreener 补了 {len(dex_prices)} 个价格")

    for h in tokens:
        if h["helius_price"] > 0:
            h["price_usd"] = h["helius_price"]
            h["price_source"] = "Helius"
        elif h["mint"] in dex_prices:
            h["price_usd"] = dex_prices[h["mint"]]
            h["price_source"] = "DexScreener"
        else:
            h["price_usd"] = 0
            h["price_source"] = "N/A"
        h["value_usd"] = h["balance"] * h["price_usd"]

    tokens.sort(key=lambda x: x["value_usd"], reverse=True)

    # === 打印报告 ===
    stable_total = sum(h["value_usd"] for h in stables)
    token_total = sum(h["value_usd"] for h in tokens)
    grand_total = stable_total + token_total

    print(f"\n{'='*75}")
    print(f"📦 持仓报告: {TARGET_WALLET[:6]}...")
    print(f"{'='*75}")

    # 稳定币
    if stables:
        print(f"\n💵 稳定币:")
        for h in stables:
            print(f"  {h['symbol']:<8} ${h['value_usd']:>12,.2f}")
        print(f"  {'小计':<8} ${stable_total:>12,.2f}")

    # 代币
    print(f"\n🪙 代币:")
    print(f"  {'代币':<12} {'数量':>15} {'单价($)':>12} {'价值($)':>12} {'来源':>10}")
    print(f"  {'-'*71}")

    for h in tokens:
        symbol = h["symbol"][:10]
        if h["price_usd"] > 0:
            print(f"  {symbol:<12} {h['balance']:>15,.2f} {h['price_usd']:>12.8f} {h['value_usd']:>12,.2f} {h['price_source']:>10}")
        else:
            print(f"  {symbol:<12} {h['balance']:>15,.2f} {'N/A':>12} {'~0':>12} {'N/A':>10}")

    print(f"  {'-'*71}")
    print(f"  {'代币小计':>53} ${token_total:>11,.2f}")

    print(f"\n{'='*75}")
    print(f"  {'总持仓价值':>53} ${grand_total:>11,.2f}")
    print(f"{'='*75}")
    print()

    # 统计
    valued = [h for h in tokens if h["value_usd"] > 1]
    dust = [h for h in tokens if h["value_usd"] <= 1 and h["price_usd"] > 0]
    no_price = [h for h in tokens if h["price_usd"] <= 0]
    print(f"📊 有价值的代币: {len(valued)} 个 (${sum(h['value_usd'] for h in valued):,.2f})")
    print(f"🗑️  灰尘 (<$1): {len(dust)} 个")
    print(f"❓ 无法定价: {len(no_price)} 个")

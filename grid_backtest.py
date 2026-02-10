import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================= 配置区域 =================
SYMBOL = "BTC-USD"       # 交易对
START_CAPITAL = 1000     # 模拟本金 (USD)
LOWER_PRICE = 60000    # 网格下限
UPPER_PRICE = 71500      # 网格上限
GRID_NUM = 50            # 网格数量 (格数越多，频次越高，单笔利润越低)
PERIOD = "1mo"           # 拉取多长时间的数据 (1mo, 3mo, 1y)
INTERVAL = "1h"          # K线粒度 (15m, 1h, 1d)
# ===========================================

def run_grid_backtest():
    print(f"🚀 正在拉取 {SYMBOL} 过去 {PERIOD} 的 {INTERVAL} 数据...")
    
    # 1. 获取真实数据
    # 1. 获取真实数据
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL, progress=False)
    
    if df.empty:
        print("❌ 数据拉取失败，请检查网络或代币名称")
        return

    # === 修复开始: 处理 yfinance 新版的多层索引问题 ===
    # 如果下载的数据只有一列 Close，有些版本会把它包成 DataFrame，我们需要把它变成 Series
    if isinstance(df.columns, pd.MultiIndex):
        # 如果列名是多层的 (Price, Ticker)，我们把它拍扁
        try:
            prices = df['Close'].iloc[:, 0] # 取 Close 下的第一列
        except:
            prices = df.iloc[:, 0] # 备用方案
    else:
        # 旧版逻辑
        prices = df['Close']
        
    # 再次确保它是 Series (一维数组)，而不是 DataFrame (二维表格)
    if isinstance(prices, pd.DataFrame):
        prices = prices.squeeze()
    # === 修复结束 ===

    print(f"✅ 成功获取 {len(prices)} 条K线数据")
    print(f"   最高价: ${prices.max():.2f}") # 这行现在不会报错了
    print(f"   最低价: ${prices.min():.2f}")
    
    # 2. 初始化网格
    grids = np.linspace(LOWER_PRICE, UPPER_PRICE, GRID_NUM + 1)
    # 计算每格资金 (简单模型：资金平均分配)
    amount_per_grid = START_CAPITAL / GRID_NUM 
    
    # 3. 开始回测
    cash = START_CAPITAL
    btc_holdings = 0
    grid_positions = [False] * GRID_NUM # 记录每一格是否持仓 (True=买了, False=空仓)
    
    # 初始建仓：假设当前价格以下的网格全部买入 (底仓)
    initial_price = prices.iloc[0].item() # 获取第一个价格
    for i in range(GRID_NUM):
        if initial_price > grids[i]:
            # 价格在网格之上，买入做底仓
            cost = amount_per_grid
            btc_bought = cost / grids[i]
            cash -= cost
            btc_holdings = 1
            grid_positions[i] = True
            
    print(f"\n💡 初始建仓完成: 持有 BTC {btc_holdings:.4f}, 剩余现金 ${cash:.2f}")
    
    trade_count = 0
    grid_profit = 0
    
    buy_signals = [] # 用于画图
    sell_signals = []
    
    last_price = initial_price
    
    # 逐行遍历价格
    for time, price in prices.items():
        #price = price.item() # 转换为浮点数
        
        # 遍历所有网格线
        for i in range(GRID_NUM):
            grid_price = grids[i]
            
            # 情况 A: 穿网格向下 (卖飞买回 / 抄底) -> BUY
            # 逻辑：只要价格跌破这一格，且我们没货，就买
            if price < grid_price and not grid_positions[i]:
                # 买入
                cost = amount_per_grid
                btc_bought = cost / grid_price
                
                if cash >= cost:
                    cash -= cost
                    btc_holdings += btc_bought
                    grid_positions[i] = True
                    trade_count += 1
                    buy_signals.append((time, price))
            
            # 情况 B: 穿网格向上 (获利卖出) -> SELL
            # 逻辑：只要价格涨破这一格，且我们有货，就卖
            # 这里的利润计算比较粗略，实际上是高抛低吸的差价
            elif price > grid_price and grid_positions[i]:
                # 卖出
                revenue = amount_per_grid # 假设卖出获得的钱等于投入的钱+利润 (为了简化，这里按固定金额计算)
                # 实际上卖出的 BTC 数量是固定的
                # 精确计算：
                # 之前买入花了 amount_per_grid，买入价是 grid_price (近似)
                # 现在卖出，卖出价是 price
                # 这是一个简化的套利模型
                
                # 卖掉这一格对应的币
                # 这一格对应的币量 ≈ amount_per_grid / grid_price
                btc_to_sell = amount_per_grid / grid_price
                
                revenue = btc_to_sell * price
                profit = revenue - amount_per_grid
                
                cash += revenue
                btc_holdings -= btc_to_sell
                grid_profit += profit
                grid_positions[i] = False
                trade_count += 1
                sell_signals.append((time, price))
                
        last_price = price

    # 4. 结算
    final_price = prices.iloc[-1].item()
    total_assets = cash + (btc_holdings * final_price)
    total_return = (total_assets - START_CAPITAL) / START_CAPITAL * 100
    
    print("\n" + "="*30)
    print(f"📊 回测结果 (区间 ${LOWER_PRICE} - ${UPPER_PRICE})")
    print("="*30)
    print(f"交易次数: {trade_count} 次")
    print(f"网格利润 (已落袋): ${grid_profit:.2f}")
    print(f"浮动盈亏 (币价波动): ${(total_assets - START_CAPITAL - grid_profit):.2f}")
    print(f"最终总资产: ${total_assets:.2f}")
    print(f"总收益率: {total_return:.2f}%")
    
    # 5. 画图
    plt.figure(figsize=(14, 7))
    plt.plot(prices.index, prices, label='BTC Price', color='gray', alpha=0.6)
    
    # 画所有网格线
    for g in grids:
        plt.axhline(y=g, color='blue', linestyle='--', alpha=0.1, linewidth=0.5)
        
    # 画买卖点
    if buy_signals:
        bx, by = zip(*buy_signals)
        plt.scatter(bx, by, marker='^', color='green', label='Buy', s=30, zorder=5)
    if sell_signals:
        sx, sy = zip(*sell_signals)
        plt.scatter(sx, sy, marker='v', color='red', label='Sell', s=30, zorder=5)
        
    plt.title(f"Grid Strategy Backtest: {SYMBOL} ({PERIOD})")
    plt.legend()
    plt.grid(True, alpha=0.2)
    plt.show()

if __name__ == "__main__":
    run_grid_backtest()
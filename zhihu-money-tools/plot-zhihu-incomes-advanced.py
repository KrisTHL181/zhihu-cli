import json
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from datetime import datetime

def plot_advanced_analysis():
    try:
        with open("zhihu_income_report.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 1. 数据准备
        df = pd.DataFrame(data["details"])
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        # --- 技术指标计算 ---
        # EMA 均线簇
        df['ema7'] = df['income_yuan'].ewm(span=7, adjust=False).mean()
        df['ema21'] = df['income_yuan'].ewm(span=21, adjust=False).mean()
        df['ema28'] = df['income_yuan'].ewm(span=28, adjust=False).mean()
        
        # 布林带 (基于20日均线)
        df['ma20'] = df['income_yuan'].rolling(window=20).mean()
        df['std20'] = df['income_yuan'].rolling(window=20).std()
        df['upper'] = df['ma20'] + (df['std20'] * 2)
        df['lower'] = df['ma20'] - (df['std20'] * 2)
        
        # MACD 计算 (标准参数: 12, 26, 9)
        exp1 = df['income_yuan'].ewm(span=12, adjust=False).mean()
        exp2 = df['income_yuan'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['hist'] = df['macd'] - df['signal']

        # --- 开始绘图 ---
        # 创建两个子图，高度比例为 3:1
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), sharex=True, 
                                       gridspec_kw={'height_ratios': [3, 1]})
        
        # 【主图：EMA + 布林带】
        # 原始数据
        ax1.bar(df['date'], df['income_yuan'], color='#0084ff', alpha=0.15, label='Daily Actual')
        
        # 绘制布林带填充
        ax1.fill_between(df['date'], df['lower'], df['upper'], color='gray', alpha=0.1, label='Bollinger Bands (2σ)')
        ax1.plot(df['date'], df['upper'], color='gray', linestyle='--', alpha=0.3, linewidth=0.8)
        ax1.plot(df['date'], df['lower'], color='gray', linestyle='--', alpha=0.3, linewidth=0.8)

        # 绘制 EMA 均线簇
        ax1.plot(df['date'], df['ema7'], color='#ff9800', linewidth=2, label='EMA 7 (Short)')
        ax1.plot(df['date'], df['ema21'], color='#4caf50', linewidth=1.5, label='EMA 21 (Medium)')
        ax1.plot(df['date'], df['ema28'], color='#9c27b0', linewidth=1.5, label='EMA 28 (Long)')

        ax1.set_title(f"Zhihu Income Advanced Analysis (Total: {data['summary']['total_income_yuan']} CNY)", fontsize=18)
        ax1.set_ylabel("Income (CNY)", fontsize=12)
        ax1.legend(loc='upper left', ncol=2)
        ax1.grid(True, linestyle=':', alpha=0.4)

        # 【副图：MACD】
        # 绘制 MACD 线和信号线
        ax2.plot(df['date'], df['macd'], color='#2196f3', label='MACD Line', linewidth=1)
        ax2.plot(df['date'], df['signal'], color='#ff5722', label='Signal Line', linewidth=1)
        
        # 绘制 MACD 柱状图 (红色代表动量减弱，绿色代表动量增强)
        colors = ['#26a69a' if x > 0 else '#ef5350' for x in df['hist']]
        ax2.bar(df['date'], df['hist'], color=colors, alpha=0.5, label='MACD Hist')
        
        ax2.set_ylabel("MACD Momentum", fontsize=12)
        ax2.axhline(0, color='black', linewidth=0.5, alpha=0.5) # 零轴
        ax2.legend(loc='upper left')
        ax2.grid(True, linestyle=':', alpha=0.4)

        # 标注最新状态
        plt.figtext(0.1, 0.05, f"MACD Hist: {df['hist'].iloc[-1]:.3f}", 
                    fontsize=12, fontweight='bold', color='darkblue')

        plt.tight_layout()
        plt.savefig("income_advanced_analysis.png", dpi=300, bbox_inches='tight')
        print("✅ 高级分析图表已生成: income_advanced_analysis.png")
        plt.show()

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    plot_advanced_analysis()

import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_analysis():
    try:
        with open("zhihu_income_report.json", encoding="utf-8") as f:
            data = json.load(f)

        # 转换数据为 Pandas DataFrame
        df = pd.DataFrame(data["details"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        # 计算 EMA (7天窗口)
        df["ema7"] = df["income_yuan"].ewm(span=7, adjust=False).mean()

        # 计算线性拟合
        x_idx = np.arange(len(df))
        slope, intercept = np.polyfit(x_idx, df["income_yuan"], 1)
        trend_line = slope * x_idx + intercept

        # 开始绘图
        plt.figure(figsize=(15, 8))

        # 1. 原始收益 (淡化处理，作为背景)
        plt.bar(df["date"], df["income_yuan"], color="#0084ff", alpha=0.2, label="Daily Actual")

        # 2. EMA(7) 曲线 (灵敏反映近一周走势)
        plt.plot(df["date"], df["ema7"], color="#ff9800", linewidth=2.5, label="EMA (7-Day Momentum)")

        # 3. 线性趋势线 (长期表现)
        plt.plot(
            df["date"],
            trend_line,
            color="red",
            linestyle="--",
            linewidth=1.5,
            label=f"Long-term Trend (Slope: {slope:.4f})",
        )

        # 图表装饰
        plt.title(f"Zhihu Income Advanced Analysis (Total: {data['summary']['total_income_yuan']} CNY)", fontsize=16)
        plt.ylabel("Income (CNY / Yuan)", fontsize=12)
        plt.legend(loc="upper left")
        plt.grid(True, linestyle=":", alpha=0.4)

        # 标注最新 EMA 状态
        latest_ema = df["ema7"].iloc[-1]
        plt.annotate(
            f"Current Momentum: {latest_ema:.2f} CNY",
            xy=(df["date"].iloc[-1], latest_ema),
            xytext=(20, 20),
            textcoords="offset points",
            arrowprops=dict(arrowstyle="->", color="black"),
        )

        plt.tight_layout()
        plt.savefig("income_analysis.png", dpi=500, bbox_inches="tight")
        plt.show()

    except Exception as e:
        print(f"❌ 运行失败: {e}")


if __name__ == "__main__":
    plot_analysis()

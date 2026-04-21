import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


def plot_derivative_analysis():
    try:
        # 1. 加载与预处理
        with open("zhihu_income_report.json", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data["details"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        y = df["income_yuan"].values

        # 2. 数据平滑 (求高阶导数前必须平滑，否则全是噪声)
        # window_length 必须为奇数，polyorder 为拟合多项式的阶数
        window_size = 7 if len(df) > 7 else len(df) // 2 * 2 + 1
        smoothed_y = savgol_filter(y, window_length=window_size, polyorder=3)

        # 3. 计算各阶导数 (使用 numpy 的梯度计算)
        v = np.gradient(smoothed_y)  # 一阶导：增长速度 (Velocity)
        a = np.gradient(v)  # 二阶导：加速度 (Acceleration)
        j = np.gradient(a)  # 三阶导：突跳/加加速度 (Jerk)

        # 4. 绘图
        fig, axes = plt.subplots(4, 1, figsize=(12, 16), sharex=True)
        plt.subplots_adjust(hspace=0.3)

        # 图 0: 原始收益与平滑曲线
        axes[0].bar(df["date"], y, color="#0084ff", alpha=0.2, label="Actual Income")
        axes[0].plot(df["date"], smoothed_y, color="#0084ff", lw=2, label="Smoothed Trend")
        axes[0].set_title("Base: Daily Income & Trend", fontsize=14)

        # 图 1: 一阶导 (速度 - 收益在涨吗？)
        axes[1].plot(df["date"], v, color="#ff9800", lw=2, label="1st Deriv: Velocity")
        axes[1].axhline(0, color="black", lw=0.5, ls="--")
        axes[1].set_title("1st Derivative: Growth Speed (Profitability)", fontsize=12)

        # 图 2: 二阶导 (加速度 - 涨得更快了吗？)
        axes[2].plot(df["date"], a, color="#e91e63", lw=2, label="2nd Deriv: Acceleration")
        axes[2].axhline(0, color="black", lw=0.5, ls="--")
        axes[2].set_title("2nd Derivative: Momentum (Algorithm Push)", fontsize=12)

        # 图 3: 三阶导 (Jerk - 爆发力如何？)
        axes[3].plot(df["date"], j, color="#9c27b0", lw=2, label="3rd Deriv: Jerk")
        axes[3].axhline(0, color="black", lw=0.5, ls="--")
        axes[3].set_title("3rd Derivative: Market Impact (Inflection Points)", fontsize=12)

        # 细节美化
        for ax in axes:
            ax.legend(loc="upper left")
            ax.grid(True, alpha=0.3)

        plt.xlabel("Date")
        plt.tight_layout()
        plt.savefig("derivative_analysis.png", dpi=300)
        plt.show()

    except Exception as e:
        print(f"❌ 运行失败: {e}")


if __name__ == "__main__":
    plot_derivative_analysis()

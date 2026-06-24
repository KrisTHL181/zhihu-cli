import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.creator_tools._smoothing import compute_smoothed, smoothing_label

DATA_DIR = Path.home() / ".zhihu-cli"
INPUT_FILE = DATA_DIR / "exports" / "zhihu_income_report.json"
OUTPUT_FILE = DATA_DIR / "plots" / "income_analysis.png"


def plot_analysis() -> None:
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INPUT_FILE, encoding="utf-8") as f:
            data = json.load(f)

        # Convert data to Pandas DataFrame
        df = pd.DataFrame(data["details"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        # Calculate smoothed trend (7-day window, MA or EMA per config)
        df["trend7"] = compute_smoothed(df["income_yuan"], window=7)
        trend_label = smoothing_label(7)

        # Compute linear fit
        x_idx = np.arange(len(df))
        slope, intercept = np.polyfit(x_idx, df["income_yuan"], 1)
        trend_line = slope * x_idx + intercept

        # Start plotting
        plt.figure(figsize=(15, 8))

        # 1. Raw income (faded, as background)
        plt.bar(df["date"], df["income_yuan"], color="#0084ff", alpha=0.2, label="Daily Actual")

        # 2. Smoothed trend curve
        plt.plot(df["date"], df["trend7"], color="#ff9800", linewidth=2.5, label=trend_label)

        # 3. Linear trend line (long-term performance)
        plt.plot(
            df["date"],
            trend_line,
            color="red",
            linestyle="--",
            linewidth=1.5,
            label=f"Long-term Trend (Slope: {slope:.4f})",
        )

        # Chart decoration
        plt.title(
            f"Zhihu Income Analysis (Total: {data['summary']['total_income_yuan']} CNY)  [{trend_label}]",
            fontsize=16,
        )
        plt.ylabel("Income (CNY / Yuan)", fontsize=12)
        plt.legend(loc="upper left")
        plt.grid(True, linestyle=":", alpha=0.4)

        # Annotate latest trend value
        latest_trend = df["trend7"].iloc[-1]
        plt.annotate(
            f"{trend_label}: {latest_trend:.2f} CNY",
            xy=(df["date"].iloc[-1], latest_trend),
            xytext=(20, 20),
            textcoords="offset points",
            arrowprops=dict(arrowstyle="->", color="black"),
        )

        plt.tight_layout()
        plt.savefig(OUTPUT_FILE, dpi=cache_manager.get_plot_dpi(), bbox_inches="tight")
        plt.show()

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    plot_analysis()

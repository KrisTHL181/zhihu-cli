import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from zhihu_cli.content.handlers.cache_manager import cache_manager

DATA_DIR = Path.home() / ".zhihu-cli"
INPUT_FILE = DATA_DIR / "exports" / "zhihu_income_report.json"
OUTPUT_FILE = DATA_DIR / "plots" / "derivative_analysis.png"


def plot_derivative_analysis() -> None:
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 1. Load and preprocess
        with open(INPUT_FILE, encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data["details"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        y = df["income_yuan"].values

        # 2. Smooth data (required before computing higher-order derivatives)
        # window_length must be odd, polyorder is the fitting polynomial order
        window_size = 7 if len(df) > 7 else len(df) // 2 * 2 + 1
        smoothed_y = savgol_filter(y, window_length=window_size, polyorder=3)

        # 3. Compute derivatives via numpy gradient
        v = np.gradient(smoothed_y)  # 1st derivative: growth speed (Velocity)
        a = np.gradient(v)  # 2nd derivative: acceleration (Acceleration)
        j = np.gradient(a)  # 3rd derivative: jerk (Jerk)

        # 4. Plot
        fig, axes = plt.subplots(4, 1, figsize=(12, 16), sharex=True)
        plt.subplots_adjust(hspace=0.3)

        # Subplot 0: raw income and smoothed curve
        axes[0].bar(df["date"], y, color="#0084ff", alpha=0.2, label="Actual Income")
        axes[0].plot(df["date"], smoothed_y, color="#0084ff", lw=2, label="Smoothed Trend")
        axes[0].set_title("Base: Daily Income & Trend", fontsize=14)

        # Subplot 1: 1st derivative (velocity — is income growing?)
        axes[1].plot(df["date"], v, color="#ff9800", lw=2, label="1st Deriv: Velocity")
        axes[1].axhline(0, color="black", lw=0.5, ls="--")
        axes[1].set_title("1st Derivative: Growth Speed (Profitability)", fontsize=12)

        # Subplot 2: 2nd derivative (acceleration — is growth accelerating?)
        axes[2].plot(df["date"], a, color="#e91e63", lw=2, label="2nd Deriv: Acceleration")
        axes[2].axhline(0, color="black", lw=0.5, ls="--")
        axes[2].set_title("2nd Derivative: Momentum (Algorithm Push)", fontsize=12)

        # Subplot 3: 3rd derivative (jerk — how strong is the impulse?)
        axes[3].plot(df["date"], j, color="#9c27b0", lw=2, label="3rd Deriv: Jerk")
        axes[3].axhline(0, color="black", lw=0.5, ls="--")
        axes[3].set_title("3rd Derivative: Market Impact (Inflection Points)", fontsize=12)

        # Style polish
        for ax in axes:
            ax.legend(loc="upper left")
            ax.grid(True, alpha=0.3)

        plt.xlabel("Date")
        plt.tight_layout()
        plt.savefig(OUTPUT_FILE, dpi=cache_manager.get_plot_dpi())
        plt.show()

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    plot_derivative_analysis()

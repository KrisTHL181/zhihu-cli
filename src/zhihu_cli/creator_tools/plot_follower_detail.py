"""Plot follower detail data as a line chart from follower_detail.json."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.creator_tools._smoothing import compute_smoothed, smoothing_label

DATA_DIR = Path.home() / ".zhihu-cli"
INPUT_FILE = DATA_DIR / "exports" / "follower_detail.json"
OUTPUT_FILE = DATA_DIR / "plots" / "follower_detail.png"


def plot_follower_detail() -> None:
    """Generate a multi-panel line chart visualizing follower trends over time."""
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INPUT_FILE, encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data["list"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        # ── ensure numeric columns (API may return strings) ──────────
        int_cols = [
            "post_follow",
            "cancel_follow",
            "pre_follow",
            "total_follow",
            "active_follow",
        ]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # active_follow_ratio is a percentage string like "97.9%"
        if "active_follow_ratio" in df.columns:
            df["active_follow_ratio"] = (
                df["active_follow_ratio"].astype(str).str.rstrip("%").pipe(pd.to_numeric, errors="coerce")
            )

        # ── compute derived series ──────────────────────────────────
        trend_label = smoothing_label(7)
        df["post_follow_trend"] = compute_smoothed(df["post_follow"], window=7)
        df["cancel_follow_trend"] = compute_smoothed(df["cancel_follow"], window=7)
        df["pre_follow_trend"] = compute_smoothed(df["pre_follow"], window=7)
        df["cum_net"] = df["pre_follow"].cumsum()

        summary = data.get("summary", {})

        # ── compute summary stats ──────────────────────────────────
        total_new = int(df["post_follow"].sum())
        total_cancel = int(df["cancel_follow"].sum())
        net_change = total_new - total_cancel

        # ================================================================
        # Figure 1: Daily Change & Cumulative (dual y-axis)
        # ================================================================
        fig1, ax1 = plt.subplots(figsize=(16, 8))
        ax1_twin = ax1.twinx()

        # left axis: daily bars + net MA
        ax1.fill_between(df["date"], df["post_follow"], alpha=0.15, color="#4caf50", label="_nolegend_")
        ax1.bar(df["date"], df["post_follow"], color="#4caf50", alpha=0.7, width=0.8, label="Post Follow (new)")
        ax1.bar(
            df["date"], -df["cancel_follow"], color="#f44336", alpha=0.7, width=0.8, label="Cancel Follow (unfollow)"
        )
        ax1.plot(df["date"], df["pre_follow_trend"], color="#2196f3", linewidth=2, label=f"Net ({trend_label})")
        ax1.axhline(y=0, color="#666", linewidth=0.8, linestyle="-")

        ax1.set_ylabel("Daily Count", fontsize=11, color="#333")
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

        # right axis: cumulative lines
        ax1_twin.plot(df["date"], df["total_follow"], color="#673ab7", linewidth=2.5, label="Total Followers (API)")
        ax1_twin.plot(
            df["date"],
            df["cum_net"],
            color="#00bcd4",
            linewidth=2,
            linestyle="--",
            label="Cumulative Net (Σ daily net)",
        )

        ax1_twin.set_ylabel("Cumulative Followers", fontsize=11, color="#333")
        ax1_twin.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

        # merge legends from both axes
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax1_twin.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

        ax1.set_title(
            f"Follower Detail — Daily Change & Cumulative\n"
            f"Period: {summary.get('date_range', 'N/A')}  |  "
            f"Total New: {total_new:,}  |  "
            f"Total Cancel: {total_cancel:,}  |  "
            f"Net: {net_change:+}",
            fontsize=12,
            fontweight="bold",
        )
        ax1.grid(True, linestyle=":", alpha=0.35)

        fig1.tight_layout()
        fig1.savefig(OUTPUT_FILE, dpi=cache_manager.get_plot_dpi(), bbox_inches="tight")
        print(f"Plot saved: {OUTPUT_FILE}")

        # ================================================================
        # Figure 2: Active Follow Ratio
        # ================================================================
        RATIO_FILE = DATA_DIR / "plots" / "follower_detail_ratio.png"

        fig2, ax2 = plt.subplots(figsize=(16, 6))

        ax2.plot(df["date"], df["active_follow_ratio"], color="#ff9800", linewidth=2, label="Active Follow Ratio")
        ax2.fill_between(df["date"], df["active_follow_ratio"], alpha=0.08, color="#ff9800")

        avg_ratio = df["active_follow_ratio"].mean()
        ax2.axhline(y=avg_ratio, color="#e91e63", linewidth=1, linestyle="--", label=f"Mean ({avg_ratio:.1f}%)")

        ax2.set_ylabel("Ratio (%)", fontsize=11)
        ax2.set_xlabel("Date", fontsize=11)
        ax2.set_title("Follower Detail — Active Follow Ratio", fontsize=14, fontweight="bold")
        ax2.legend(loc="upper left", fontsize=9)
        ax2.grid(True, linestyle=":", alpha=0.35)
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))

        fig2.tight_layout()
        fig2.savefig(RATIO_FILE, dpi=cache_manager.get_plot_dpi(), bbox_inches="tight")
        print(f"Plot saved: {RATIO_FILE}")

        # show both figures sequentially
        plt.show()

    except FileNotFoundError:
        print(f"Data file not found: {INPUT_FILE}")
        print("Run first: zhihu tools creator follower fetch")
    except Exception as e:
        print(f"Error generating plot: {e}")


if __name__ == "__main__":
    plot_follower_detail()

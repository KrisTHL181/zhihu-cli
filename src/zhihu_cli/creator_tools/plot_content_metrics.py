"""Plot content metrics from content_metrics/*.json — combined daily + cumulative time-series."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from zhihu_cli.content.handlers.cache_manager import cache_manager

DATA_DIR = Path.home() / ".zhihu-cli"
METRICS_DIR = DATA_DIR / "exports" / "content_metrics"
OUTPUT_FILE = DATA_DIR / "plots" / "content_metrics.png"
ENGAGEMENT_FILE = DATA_DIR / "plots" / "content_metrics_engagement.png"

METRIC_KEYS = ["pv", "show", "upvote", "like", "collect", "comment", "share"]

METRIC_LABELS = {
    "pv": "PV",
    "show": "Show",
    "upvote": "Upvote",
    "like": "Like",
    "collect": "Collect",
    "comment": "Comment",
    "share": "Share",
}

METRIC_COLORS = {
    "pv": "#2196f3",
    "show": "#4caf50",
    "upvote": "#ff9800",
    "like": "#e91e63",
    "collect": "#9c27b0",
    "comment": "#00bcd4",
    "share": "#ff5722",
}


def _is_aggr_format(data: object) -> bool:
    """Return True if the loaded JSON is in --aggr format (dict with 'totals' key)."""
    return isinstance(data, dict) and "totals" in data


def _is_daily_list(data: object) -> bool:
    """Return True if the loaded JSON is a list of daily records (each with a 'date' field)."""
    return isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and "date" in data[0]


def plot_content_metrics() -> None:
    """Generate a combined daily+cumulative chart from all content_metrics JSON files."""
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not METRICS_DIR.exists():
            print(f"Metrics directory not found: {METRICS_DIR}")
            print("Run first: zhihu tools creator metrics")
            return

        json_files = sorted(METRICS_DIR.glob("*.json"))
        if not json_files:
            print(f"No JSON files found in: {METRICS_DIR}")
            print("Run first: zhihu tools creator metrics")
            return

        # ── load and categorise all files ────────────────────────────
        aggr_files: list[dict] = []
        daily_records: list[dict] = []  # flat list of individual daily records
        daily_source_files: set[str] = set()
        unknown_files: list[str] = []

        for fp in json_files:
            try:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  [Skip] Failed to read {fp.name}: {e}")
                continue

            if _is_daily_list(data):
                daily_source_files.add(fp.name)
                for record in data:
                    if record.get("date"):
                        daily_records.append(record)
            elif _is_aggr_format(data):
                aggr_files.append({"name": fp.stem.replace("metrics_full_", ""), **data})
            else:
                unknown_files.append(fp.name)

        # ── status report ────────────────────────────────────────────
        print(f"Scanned {len(json_files)} files in content_metrics/")
        print(f"  Daily (time-series):  {len(daily_records)} records from {len(daily_source_files)} files")
        print(f"  Aggregated (--aggr):  {len(aggr_files)} files")
        if unknown_files:
            print(f"  Unknown format:       {len(unknown_files)} files")
            for uf in unknown_files[:5]:
                print(f"    - {uf}")

        # ── determine primary data source ────────────────────────────
        if daily_records:
            _plot_daily_time_series(daily_records, file_count=len(daily_source_files))
        elif aggr_files:
            _plot_aggr_summary(aggr_files)
        else:
            print("No plottable data found.")
            return

    except Exception as e:
        print(f"Error generating plot: {e}")
        import traceback

        traceback.print_exc()


def _plot_daily_time_series(records: list[dict], file_count: int = 0) -> None:
    """Aggregate daily records by date, then plot combined daily + cumulative chart."""
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    # ensure numeric columns
    for col in METRIC_KEYS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # ── aggregate by date: sum all metrics across all content items ──
    agg = df.groupby("date")[METRIC_KEYS].sum().reset_index()
    agg = agg.sort_values("date")

    # ── clip to configured start-date ──────────────────────────────
    start_str = cache_manager.get_start_date()
    try:
        start_dt = pd.Timestamp(start_str)
        before = len(agg)
        agg = agg[agg["date"] >= start_dt]
        skipped = before - len(agg)
        if skipped > 0:
            print(f"  Start-date filter ({start_str}): dropped {skipped} days ({before} -> {len(agg)} days)")
    except Exception:
        pass

    # ── compute 7-day moving averages for smoother lines ────────────
    for col in METRIC_KEYS:
        agg[f"{col}_ma7"] = agg[col].rolling(7, center=True).mean()

    # ── compute cumulative series for each metric ───────────────────
    cum_cols = {}
    for col in METRIC_KEYS:
        cname = f"cum_{col}"
        agg[cname] = agg[col].cumsum()
        cum_cols[col] = cname

    # ── summary stats ───────────────────────────────────────────────
    totals = {col: int(agg[col].sum()) for col in METRIC_KEYS}
    final_cum = {col: int(agg[cum_cols[col]].iloc[-1]) for col in METRIC_KEYS}
    date_range = f"{agg['date'].min().strftime('%Y-%m-%d')} -> {agg['date'].max().strftime('%Y-%m-%d')}"
    total_records = len(records)
    content_count = df["type"].nunique() if "type" in df.columns else "?"

    # ── metric groupings for dual-scale plotting ───────────────────
    LOG_METRICS = ["pv", "show"]
    LINEAR_METRICS = ["upvote", "like", "collect", "comment", "share"]

    # ================================================================
    # Figure 1: Combined — Daily (7-day MA) + Cumulative (dual y-axis)
    # Style: mirrors follower_detail plot — daily on left, cumulative on right
    # ================================================================
    fig1, ax1 = plt.subplots(figsize=(18, 10))
    ax1_twin = ax1.twinx()

    # --- left axis (log): PV, Show — daily MA + cumulative -----------
    for key in LOG_METRICS:
        color = METRIC_COLORS[key]
        # daily MA (solid, thinner, with fill)
        ax1.plot(
            agg["date"],
            agg[f"{key}_ma7"],
            linewidth=1.8,
            color=color,
            label=f"{METRIC_LABELS[key]} (daily MA, Σ={totals[key]:,})",
        )
        ax1.fill_between(agg["date"], agg[f"{key}_ma7"], alpha=0.08, color=color)
        # cumulative (dashed, thicker)
        ax1.plot(
            agg["date"],
            agg[cum_cols[key]],
            linewidth=2.5,
            color=color,
            linestyle="--",
            label=f"Cum. {METRIC_LABELS[key]} (Σ={final_cum[key]:,})",
        )
        ax1.fill_between(agg["date"], agg[cum_cols[key]], alpha=0.04, color=color)

    ax1.set_yscale("log")
    ax1.set_ylabel("PV / Show (log scale)", fontsize=11, color="#1a237e")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

    # --- right axis (linear): engagement — daily MA + cumulative -----
    for key in LINEAR_METRICS:
        color = METRIC_COLORS[key]
        # daily MA (solid, thinner, with fill)
        ax1_twin.plot(
            agg["date"],
            agg[f"{key}_ma7"],
            linewidth=1.5,
            color=color,
            label=f"{METRIC_LABELS[key]} (daily MA, Σ={totals[key]:,})",
        )
        ax1_twin.fill_between(agg["date"], agg[f"{key}_ma7"], alpha=0.05, color=color)
        # cumulative (dashed, thicker)
        ax1_twin.plot(
            agg["date"],
            agg[cum_cols[key]],
            linewidth=2.0,
            color=color,
            linestyle="--",
            label=f"Cum. {METRIC_LABELS[key]} (Σ={final_cum[key]:,})",
        )
        ax1_twin.fill_between(agg["date"], agg[cum_cols[key]], alpha=0.03, color=color)

    ax1_twin.set_ylabel("Engagement (linear scale)", fontsize=11, color="#333")
    ax1_twin.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

    # --- merged legend (2 columns, like follower plot) ---------------
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax1_twin.get_legend_handles_labels()
    ax1.legend(
        lines1 + lines2,
        labels1 + labels2,
        loc="upper left",
        fontsize=7,
        ncol=2,
        framealpha=0.85,
    )

    # --- title (follower-plot style) ----------------------------------
    ax1.set_title(
        f"Content Metrics — Daily (7-day MA) & Cumulative\n"
        f"Period: {date_range}  |  Files: {file_count or content_count}  |  "
        f"Records: {total_records:,}",
        fontsize=13,
        fontweight="bold",
    )
    ax1.grid(True, linestyle=":", alpha=0.35)
    ax1.set_xlabel("Date", fontsize=11)

    fig1.tight_layout()
    fig1.savefig(OUTPUT_FILE, dpi=300, bbox_inches="tight")
    print(f"Plot saved: {OUTPUT_FILE}")

    # ================================================================
    # Figure 2: Engagement Rate — total engagement per Show over time
    # Mirrors the follower_detail "Active Follow Ratio" supplementary chart
    # ================================================================
    # total daily engagement = sum of all 5 engagement metrics
    agg["eng_total"] = agg[LINEAR_METRICS].sum(axis=1)
    agg["show_val"] = agg["show"]
    # engagement rate = engagement / Show as percentage
    agg["eng_rate"] = agg["eng_total"] / agg["show_val"].replace(0, pd.NA) * 100
    agg["eng_rate_ma7"] = agg["eng_rate"].rolling(7, center=True).mean()

    fig2, ax2 = plt.subplots(figsize=(16, 6))

    ax2.plot(
        agg["date"],
        agg["eng_rate_ma7"],
        color="#ff9800",
        linewidth=2,
        label="Engagement Rate (7-day MA)",
    )
    ax2.fill_between(agg["date"], agg["eng_rate_ma7"], alpha=0.08, color="#ff9800")

    avg_rate = agg["eng_rate"].mean()
    ax2.axhline(
        y=avg_rate,
        color="#e91e63",
        linewidth=1,
        linestyle="--",
        label=f"Mean ({avg_rate:.1f}%)",
    )

    ax2.set_ylabel("Rate (%)", fontsize=11)
    ax2.set_xlabel("Date", fontsize=11)
    ax2.set_title(
        "Content Metrics — Engagement Rate  (Σ engagement / Show)",
        fontsize=14,
        fontweight="bold",
    )
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, linestyle=":", alpha=0.35)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))

    fig2.tight_layout()
    fig2.savefig(ENGAGEMENT_FILE, dpi=300, bbox_inches="tight")
    print(f"Plot saved: {ENGAGEMENT_FILE}")

    plt.show()


def _plot_aggr_summary(aggr_files: list[dict]) -> None:
    """Plot summary view when data was fetched with --aggr.

    Each file has a 'totals' dict — aggregate across all files by content type
    and show a grouped bar chart. Also try to extract yesterday/today per-day data.
    """
    print("\n⚠️  Data was fetched with --aggr (aggregated totals, no daily time-series).")
    print("   For time-series plotting, re-fetch without --aggr:")
    print("     zhihu tools creator metrics          (no --aggr flag)")
    print()

    # ── collect per-file totals ──────────────────────────────────────
    rows: list[dict] = []
    yesterday_rows: list[dict] = []
    today_rows: list[dict] = []

    for af in aggr_files:
        totals = af.get("totals", {})
        ftype = totals.get("type") or af.get("name", "?").split("_", 1)[1] if "_" in af.get("name", "") else "?"
        # try to extract content type from filename
        if ftype == "?":
            name = af.get("name", "")
            for ct in ("answer", "article", "pin"):
                if ct in name:
                    ftype = ct
                    break

        row = {"type": ftype}
        for key in METRIC_KEYS:
            row[key] = int(totals.get(key, 0) or 0)
        rows.append(row)

        # extract yesterday / today daily data if present
        for tag, rlist in [("yesterday", yesterday_rows), ("today", today_rows)]:
            day = af.get(tag)
            if day and isinstance(day, dict) and day.get("date"):
                drow = {"date": day["date"], "type": ftype}
                for key in METRIC_KEYS:
                    drow[key] = int(day.get(key, 0) or 0)
                rlist.append(drow)

    df_totals = pd.DataFrame(rows)

    # ================================================================
    # Figure 1: Grouped bar chart — totals by metric
    # ================================================================
    fig1, ax1 = plt.subplots(figsize=(16, 8))

    # aggregate totals across all files per metric
    metric_totals = {key: int(df_totals[key].sum()) for key in METRIC_KEYS}
    bars = ax1.bar(
        list(METRIC_LABELS.values()),
        [metric_totals[k] for k in METRIC_KEYS],
        color=[METRIC_COLORS[k] for k in METRIC_KEYS],
        alpha=0.85,
        width=0.6,
    )

    # annotate bars with values
    for bar_obj, key in zip(bars, METRIC_KEYS):
        height = bar_obj.get_height()
        ax1.text(
            bar_obj.get_x() + bar_obj.get_width() / 2.0,
            height + max(metric_totals.values()) * 0.01,
            f"{metric_totals[key]:,}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax1.set_title(
        f"Content Metrics — Aggregated Totals (--aggr)\n"
        f"{len(aggr_files)} content items across {df_totals['type'].nunique()} types",
        fontsize=13,
        fontweight="bold",
    )
    ax1.set_ylabel("Total Count", fontsize=11)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax1.grid(True, axis="y", linestyle=":", alpha=0.35)
    ax1.tick_params(axis="x", rotation=30)

    fig1.tight_layout()
    fig1.savefig(OUTPUT_FILE, dpi=300, bbox_inches="tight")
    print(f"Plot saved: {OUTPUT_FILE}")

    # ================================================================
    # Figure 2: yesterday/today per-day detail (if available)
    # ================================================================
    if yesterday_rows or today_rows:
        all_daily = yesterday_rows + today_rows
        if all_daily:
            df_daily = pd.DataFrame(all_daily)
            for col in METRIC_KEYS:
                df_daily[col] = pd.to_numeric(df_daily[col], errors="coerce").fillna(0).astype(int)
            agg_daily = df_daily.groupby("date")[METRIC_KEYS].sum().reset_index()
            agg_daily = agg_daily.sort_values("date")

            DAILY_FILE = DATA_DIR / "plots" / "content_metrics_daily.png"
            fig2, ax2 = plt.subplots(figsize=(16, 7))

            x = range(len(agg_daily))
            width = 0.12
            for i, key in enumerate(METRIC_KEYS):
                offset = (i - len(METRIC_KEYS) / 2 + 0.5) * width
                ax2.bar(
                    [xi + offset for xi in x],
                    agg_daily[key],
                    width=width * 0.9,
                    color=METRIC_COLORS[key],
                    alpha=0.85,
                    label=METRIC_LABELS[key],
                )

            ax2.set_xticks(x)
            ax2.set_xticklabels([d.strftime("%m-%d") for d in agg_daily["date"]], rotation=45, ha="right")
            ax2.set_ylabel("Count", fontsize=11)
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
            ax2.set_title(
                "Content Metrics — Yesterday & Today Detail (from --aggr data)",
                fontsize=13,
                fontweight="bold",
            )
            ax2.legend(loc="upper left", fontsize=9, ncol=2)
            ax2.grid(True, axis="y", linestyle=":", alpha=0.35)

            fig2.tight_layout()
            fig2.savefig(DAILY_FILE, dpi=300, bbox_inches="tight")
            print(f"Plot saved: {DAILY_FILE}")

    plt.show()


if __name__ == "__main__":
    plot_content_metrics()

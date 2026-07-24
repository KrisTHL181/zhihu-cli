"""Compute and display comprehensive statistics from content_metrics/*.json files.

Handles both daily-list (default) and aggregated (``--aggr``) formats,
mirroring the detection logic in :mod:`plot_content_metrics`.

Invoked via ``zhihu tools creator stats``.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from zhihu_cli.output import (
    blank,
    divider,
    echo,
    f_bold,
    f_dim,
    f_label,
    f_meta,
    f_num,
    heading,
    info,
    print_json,
    print_table,
    section,
    warning,
)

DATA_DIR = Path.home() / ".zhihu-cli"
METRICS_DIR = DATA_DIR / "exports" / "content_metrics"

# ── metric definitions (mirrors plot_content_metrics.py) ────────────────────

METRIC_KEYS = ["pv", "show", "upvote", "like", "collect", "comment", "share"]

METRIC_LABELS: dict[str, str] = {
    "pv": "PV",
    "show": "Show",
    "upvote": "Upvote",
    "like": "Like",
    "collect": "Collect",
    "comment": "Comment",
    "share": "Share",
}

EXTRA_METRIC_KEYS = [
    "play",
    "new_follow_uv",
    "follower_conversion_rate",
    "pageshow_uv",
    "positive_interact_rate",
    "re_pin",
    "finish_read_percent",
    "positive_interact_percent",
    "follower_translate",
]

EXTRA_METRIC_LABELS: dict[str, str] = {
    "play": "Play",
    "new_follow_uv": "New Follow UV",
    "follower_conversion_rate": "Follow Conv Rate",
    "pageshow_uv": "Page Show UV",
    "positive_interact_rate": "Pos. Interact Rate",
    "re_pin": "Re-Pin",
    "finish_read_percent": "Finish Read %",
    "positive_interact_percent": "Pos. Interact %",
    "follower_translate": "Follow Translate",
}

ALL_METRIC_KEYS = METRIC_KEYS + EXTRA_METRIC_KEYS
ALL_METRIC_LABELS = {**METRIC_LABELS, **EXTRA_METRIC_LABELS}

# ── format detection (same logic as plot_content_metrics.py) ────────────────


def _is_aggr_format(data: object) -> bool:
    """Return True if the loaded JSON is in --aggr format (dict with 'totals' key)."""
    return isinstance(data, dict) and "totals" in data


def _is_daily_list(data: object) -> bool:
    """Return True if the loaded JSON is a list of daily records (each with a 'date' field)."""
    return isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and "date" in data[0]


# ── stats helpers ───────────────────────────────────────────────────────────


def _compute_stats(values: list[float]) -> dict:
    """Compute descriptive statistics for a list of numeric values.

    :param values: List of numeric values (must be non-empty).
    :returns: Dict with count, sum, mean, median, std, min, max, q1, q3.
    """
    n = len(values)
    sorted_vals = sorted(values)
    return {
        "count": n,
        "sum": sum(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "std": statistics.stdev(values) if n >= 2 else 0.0,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
        "q1": sorted_vals[n // 4] if n >= 4 else sorted_vals[0],
        "q3": sorted_vals[3 * n // 4] if n >= 4 else sorted_vals[-1],
    }


def _fmt_num(value: float, is_int: bool = True) -> str:
    """Format a number for display — integers get comma grouping; floats get 2 decimal places.

    :param value: The numeric value.
    :param is_int: If True, treat as integer (comma grouping, no decimals).
    """
    if is_int:
        return f"{int(value):,}"
    return f"{value:.2f}"


def _is_integer_metric(key: str) -> bool:
    """Return True if the metric is naturally an integer (count-based).

    :param key: Metric key name.
    """
    return key not in (
        "positive_interact_rate",
        "finish_read_percent",
        "positive_interact_percent",
        "follower_conversion_rate",
    )


# ── daily-format stats ─────────────────────────────────────────────────────


def _compute_daily_stats(daily_records: list[dict], daily_source_files: set[str]) -> dict:
    """Compute comprehensive statistics from daily-format records.

    :param daily_records: Flat list of individual daily metric dicts.
    :param daily_source_files: Set of source file names.
    :returns: Nested dict with all computed statistics.
    """
    # ── determine which metric keys are actually present ─────────────────
    present_metrics = [k for k in ALL_METRIC_KEYS if k in daily_records[0]]

    # ── overview ────────────────────────────────────────────────────────
    dates = sorted({r["date"] for r in daily_records if r.get("date")})
    types = sorted({r.get("type", "?") for r in daily_records})

    # Group records by type for per-type breakdown
    type_records: dict[str, list[dict]] = defaultdict(list)
    for r in daily_records:
        type_records[r.get("type", "?")].append(r)

    # ── per-metric stats (across ALL individual daily records) ───────────
    per_metric: dict[str, dict] = {}
    for key in present_metrics:
        values = [float(r.get(key, 0) or 0) for r in daily_records]
        per_metric[key] = _compute_stats(values)

    # ── daily aggregate stats (sum per day → stats on those daily totals)
    daily_agg: dict[str, list[float]] = defaultdict(list)
    date_values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in daily_records:
        d = r["date"]
        for key in present_metrics:
            date_values[d][key] += float(r.get(key, 0) or 0)

    for d in dates:
        for key in present_metrics:
            daily_agg[key].append(date_values[d][key])

    daily_stats: dict[str, dict] = {}
    for key in present_metrics:
        vals = daily_agg[key]
        nonzero = [v for v in vals if v > 0]
        daily_stats[key] = {
            **_compute_stats(vals),
            "nonzero_days": len(nonzero),
        }

    # ── per-type breakdown ───────────────────────────────────────────────
    type_stats: dict[str, dict] = {}
    for ctype, recs in type_records.items():
        type_metric_stats: dict[str, dict] = {}
        for key in present_metrics:
            values = [float(r.get(key, 0) or 0) for r in recs]
            type_metric_stats[key] = _compute_stats(values)
        type_stats[ctype] = {
            "record_count": len(recs),
            "metrics": type_metric_stats,
        }

    # ── top content (re-read files for per-file totals) ──────────────────
    file_sums: list[dict] = []
    for fp_stem in sorted(daily_source_files):
        fp = METRICS_DIR / fp_stem
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        item_sum: dict[str, float] = {}
        item_type = "?"
        for r in data:
            item_type = r.get("type", item_type)
            for key in present_metrics:
                item_sum[key] = item_sum.get(key, 0) + float(r.get(key, 0) or 0)
        item_sum["_type"] = item_type
        item_sum["_file"] = fp_stem
        item_sum["_records"] = len(data)
        file_sums.append(item_sum)

    # top/bottom N per metric
    top_n = 10
    top_content: dict[str, list[dict]] = {}
    for key in present_metrics:
        sorted_items = sorted(file_sums, key=lambda x: x.get(key, 0), reverse=True)
        top_content[key] = [
            {"file": it["_file"], "type": it["_type"], "value": it.get(key, 0), "records": it["_records"]}
            for it in sorted_items[:top_n]
            if it.get(key, 0) > 0
        ]

    # ── assemble result ──────────────────────────────────────────────────
    return {
        "format": "daily",
        "file_count": len(daily_source_files),
        "record_count": len(daily_records),
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "content_types": {t: len(type_records[t]) for t in types},
        "per_metric": per_metric,
        "daily_aggregate": daily_stats,
        "per_type": type_stats,
        "top_content": top_content,
    }


# ── aggr-format stats ──────────────────────────────────────────────────────


def _compute_aggr_stats(aggr_files: list[dict]) -> dict:
    """Compute statistics from aggregated-format files.

    :param aggr_files: List of parsed --aggr JSON dicts.
    :returns: Nested dict with all computed statistics.
    """
    present_metrics = [k for k in ALL_METRIC_KEYS if k in aggr_files[0].get("totals", {})]

    # ── per-metric stats from totals ─────────────────────────────────────
    metric_values: dict[str, list[float]] = defaultdict(list)
    type_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    yesterday_vals: dict[str, list[float]] = defaultdict(list)
    today_vals: dict[str, list[float]] = defaultdict(list)
    has_daily_detail = False

    for af in aggr_files:
        totals = af.get("totals", {})
        ctype = totals.get("type") or _infer_type_from_name(af.get("name", ""))

        for key in present_metrics:
            val = float(totals.get(key, 0) or 0)
            metric_values[key].append(val)
            type_values[ctype][key].append(val)

        # yesterday / today
        for tag, collector in [("yesterday", yesterday_vals), ("today", today_vals)]:
            day = af.get(tag)
            if day and isinstance(day, dict):
                has_daily_detail = True
                for key in present_metrics:
                    collector[key].append(float(day.get(key, 0) or 0))

    # ── per-metric stats ─────────────────────────────────────────────────
    per_metric = {}
    for key in present_metrics:
        vals = metric_values[key]
        if vals:
            per_metric[key] = _compute_stats(vals)

    # ── per-type breakdown ───────────────────────────────────────────────
    type_stats = {}
    for ctype, tm in type_values.items():
        type_metric_stats = {}
        for key in present_metrics:
            vals = tm[key]
            if vals:
                type_metric_stats[key] = _compute_stats(vals)
        type_stats[ctype] = {
            "item_count": len(tm[present_metrics[0]]) if present_metrics else 0,
            "metrics": type_metric_stats,
        }

    # ── yesterday / today summary ────────────────────────────────────────
    daily_comparison = {}
    if has_daily_detail:
        for tag, collector in [("yesterday", yesterday_vals), ("today", today_vals)]:
            daily_comparison[tag] = {}
            for key in present_metrics:
                vals = collector[key]
                if vals:
                    daily_comparison[tag][key] = {
                        "sum": sum(vals),
                        "mean": statistics.mean(vals) if vals else 0,
                    }

    return {
        "format": "aggr",
        "file_count": len(aggr_files),
        "content_types": {t: len(type_values[t][present_metrics[0]]) for t in type_values if present_metrics},
        "per_metric": per_metric,
        "per_type": type_stats,
        "daily_comparison": daily_comparison,
    }


def _infer_type_from_name(name: str) -> str:
    """Infer content type from file stem fragment.

    :param name: Fragment like ``answer_10146656462`` or ``pin_2061600059276387141``.
    """
    for ct in ("answer", "article", "pin"):
        if ct in name:
            return ct
    return "?"


# ── display helpers ────────────────────────────────────────────────────────


def _display_daily_stats(stats: dict) -> None:
    """Print a styled terminal report for daily-format statistics.

    :param stats: Result dict from :func:`_compute_daily_stats`.
    """
    heading("Content Metrics Statistics (Daily)")

    # ── overview ─────────────────────────────────────────────────────────
    section("Overview")
    echo(f"  {f_label('Files:')}    {f_num(stats['file_count'])}")
    rec_count = f"{stats['record_count']:,}"
    echo(f"  {f_label('Records:')}  {f_num(rec_count)}")
    dr = stats["date_range"]
    echo(f"  {f_label('Period:')}   {f_num(dr['start'])} {f_dim('→')} {f_num(dr['end'])}")
    echo(f"  {f_label('Types:')}    {', '.join(f'{t}={f_num(n)}' for t, n in stats['content_types'].items())}")

    # ── per-metric summary table ─────────────────────────────────────────
    section("Per-Metric Summary (individual daily records)")
    per_metric = stats["per_metric"]
    columns = ["Metric", "Count", "Sum", "Mean", "Median", "Std", "Q1", "Q3", "Min", "Max"]
    rows: list[list[str]] = []
    for key in METRIC_KEYS:
        if key not in per_metric:
            continue
        s = per_metric[key]
        is_int = _is_integer_metric(key)
        rows.append(
            [
                METRIC_LABELS[key],
                _fmt_num(s["count"]),
                _fmt_num(s["sum"], is_int),
                _fmt_num(s["mean"], is_int),
                _fmt_num(s["median"], is_int),
                _fmt_num(s["std"], is_int),
                _fmt_num(s["q1"], is_int),
                _fmt_num(s["q3"], is_int),
                _fmt_num(s["min"], is_int),
                _fmt_num(s["max"], is_int),
            ]
        )
    print_table(title=None, columns=columns, rows=rows)

    # ── extra metrics ────────────────────────────────────────────────────
    extra_present = [k for k in EXTRA_METRIC_KEYS if k in per_metric]
    if extra_present:
        section("Extended Metrics")
        ext_columns = ["Metric", "Count", "Sum", "Mean", "Median", "Std", "Q1", "Q3", "Min", "Max"]
        ext_rows: list[list[str]] = []
        for key in extra_present:
            s = per_metric[key]
            is_int = _is_integer_metric(key)
            ext_rows.append(
                [
                    EXTRA_METRIC_LABELS.get(key, key),
                    _fmt_num(s["count"]),
                    _fmt_num(s["sum"], is_int),
                    _fmt_num(s["mean"], is_int),
                    _fmt_num(s["median"], is_int),
                    _fmt_num(s["std"], is_int),
                    _fmt_num(s["q1"], is_int),
                    _fmt_num(s["q3"], is_int),
                    _fmt_num(s["min"], is_int),
                    _fmt_num(s["max"], is_int),
                ]
            )
        print_table(title=None, columns=ext_columns, rows=ext_rows)

    # ── daily aggregate stats ────────────────────────────────────────────
    section("Daily Aggregate Stats (summed across all content per day)")
    daily_stats = stats["daily_aggregate"]
    da_columns = [
        "Metric",
        "Days",
        "NonZero",
        "Daily Mean",
        "Daily Median",
        "Daily Std",
        "Daily Q1",
        "Daily Q3",
        "Best Day",
        "Worst Day",
    ]
    da_rows: list[list[str]] = []
    for key in METRIC_KEYS:
        if key not in daily_stats:
            continue
        s = daily_stats[key]
        is_int = _is_integer_metric(key)
        da_rows.append(
            [
                METRIC_LABELS[key],
                _fmt_num(s["count"]),
                _fmt_num(s["nonzero_days"]),
                _fmt_num(s["mean"], is_int),
                _fmt_num(s["median"], is_int),
                _fmt_num(s["std"], is_int),
                _fmt_num(s["q1"], is_int),
                _fmt_num(s["q3"], is_int),
                _fmt_num(s["max"], is_int),
                _fmt_num(s["min"], is_int),
            ]
        )
    print_table(title=None, columns=da_columns, rows=da_rows)

    # ── per-type breakdown ───────────────────────────────────────────────
    section("Per-Type Breakdown (total sum per content type)")
    type_stats = stats["per_type"]
    pt_columns = ["Metric"] + [f"{t}\n({type_stats[t]['record_count']} recs)" for t in sorted(type_stats)]
    # Build rows per metric
    all_types = sorted(type_stats.keys())
    first_metrics = next(iter(type_stats.values()))["metrics"]
    pt_rows: list[list[str]] = []
    for key in METRIC_KEYS:
        if key not in first_metrics:
            continue
        is_int = _is_integer_metric(key)
        row = [METRIC_LABELS[key]]
        for t in all_types:
            ms = type_stats[t]["metrics"].get(key, {})
            row.append(_fmt_num(ms.get("sum", 0), is_int))
        pt_rows.append(row)
    print_table(title=None, columns=pt_columns, rows=pt_rows)

    # ── top content ──────────────────────────────────────────────────────
    section("Top Content by Metric")
    top_content = stats["top_content"]
    for key in METRIC_KEYS:
        if key not in top_content or not top_content[key]:
            continue
        items = top_content[key]
        echo(f"\n  {f_bold(METRIC_LABELS[key])}:")
        for i, item in enumerate(items[:5]):
            short_name = item["file"].replace("metrics_full_", "")
            item_type = item["type"]
            item_recs = item["records"]
            val_str = _fmt_num(item["value"], _is_integer_metric(key))
            echo(
                f"    {f_dim(f'{i + 1}.')} {f_num(val_str)}  {short_name}  {f_meta(f'({item_type}, {item_recs} days)')}"
            )

    blank()
    divider()
    echo(
        f"  {f_label('Data source:')} {f_num(stats['file_count'])} files, {f_num(f'{stats["record_count"]:,}')} daily records"
    )


def _display_aggr_stats(stats: dict, synthesized: bool = False) -> None:
    """Print a styled terminal report for aggregated-format statistics.

    :param stats: Result dict from :func:`_compute_aggr_stats`.
    :param synthesized: If True, the data was synthesized from daily files.
    """
    heading("Content Metrics Statistics (Aggregated)")

    if synthesized:
        info("Derived from daily time-series files (per-item lifetime sums).")
    else:
        warning("Data was fetched with --aggr (lifetime totals, no daily time-series).")
        info("For richer daily statistics, re-fetch without --aggr:  zhihu tools creator metrics")
    blank()

    # ── overview ─────────────────────────────────────────────────────────
    section("Overview")
    echo(f"  {f_label('Files:')}  {f_num(stats['file_count'])}")
    echo(f"  {f_label('Types:')}  {', '.join(f'{t}={f_num(n)}' for t, n in stats['content_types'].items())}")

    # ── per-metric summary ───────────────────────────────────────────────
    section("Per-Metric Summary (lifetime totals per content item)")
    per_metric = stats["per_metric"]
    columns = ["Metric", "Items", "Sum", "Mean", "Median", "Std", "Q1", "Q3", "Min", "Max"]
    rows: list[list[str]] = []
    for key in METRIC_KEYS:
        if key not in per_metric:
            continue
        s = per_metric[key]
        is_int = _is_integer_metric(key)
        rows.append(
            [
                METRIC_LABELS[key],
                _fmt_num(s["count"]),
                _fmt_num(s["sum"], is_int),
                _fmt_num(s["mean"], is_int),
                _fmt_num(s["median"], is_int),
                _fmt_num(s["std"], is_int),
                _fmt_num(s["q1"], is_int),
                _fmt_num(s["q3"], is_int),
                _fmt_num(s["min"], is_int),
                _fmt_num(s["max"], is_int),
            ]
        )
    print_table(title=None, columns=columns, rows=rows)

    # ── extra metrics ────────────────────────────────────────────────────
    extra_present = [k for k in EXTRA_METRIC_KEYS if k in per_metric]
    if extra_present:
        section("Extended Metrics")
        ext_columns = ["Metric", "Items", "Sum", "Mean", "Median", "Std", "Q1", "Q3", "Min", "Max"]
        ext_rows: list[list[str]] = []
        for key in extra_present:
            s = per_metric[key]
            is_int = _is_integer_metric(key)
            ext_rows.append(
                [
                    EXTRA_METRIC_LABELS.get(key, key),
                    _fmt_num(s["count"]),
                    _fmt_num(s["sum"], is_int),
                    _fmt_num(s["mean"], is_int),
                    _fmt_num(s["median"], is_int),
                    _fmt_num(s["std"], is_int),
                    _fmt_num(s["q1"], is_int),
                    _fmt_num(s["q3"], is_int),
                    _fmt_num(s["min"], is_int),
                    _fmt_num(s["max"], is_int),
                ]
            )
        print_table(title=None, columns=ext_columns, rows=ext_rows)

    # ── per-type breakdown ───────────────────────────────────────────────
    type_stats = stats["per_type"]
    if len(type_stats) > 1:
        section("Per-Type Breakdown (total sum per content type)")
        all_types = sorted(type_stats.keys())
        first_metrics = next(iter(type_stats.values()))["metrics"]
        pt_columns = ["Metric"] + [f"{t}\n({type_stats[t]['item_count']} items)" for t in all_types]
        pt_rows: list[list[str]] = []
        for key in METRIC_KEYS:
            if key not in first_metrics:
                continue
            is_int = _is_integer_metric(key)
            row = [METRIC_LABELS[key]]
            for t in all_types:
                ms = type_stats[t]["metrics"].get(key, {})
                row.append(_fmt_num(ms.get("sum", 0), is_int))
            pt_rows.append(row)
        print_table(title=None, columns=pt_columns, rows=pt_rows)

    # ── yesterday / today comparison ─────────────────────────────────────
    daily_comparison = stats.get("daily_comparison", {})
    if daily_comparison:
        section("Yesterday / Today Comparison")
        yt_columns = ["Metric", "Yesterday Sum", "Yesterday Mean", "Today Sum", "Today Mean"]
        yt_rows: list[list[str]] = []
        for key in METRIC_KEYS:
            y = daily_comparison.get("yesterday", {}).get(key, {})
            t = daily_comparison.get("today", {}).get(key, {})
            if not y and not t:
                continue
            is_int = _is_integer_metric(key)
            yt_rows.append(
                [
                    METRIC_LABELS[key],
                    _fmt_num(y.get("sum", 0), is_int),
                    _fmt_num(y.get("mean", 0), is_int),
                    _fmt_num(t.get("sum", 0), is_int),
                    _fmt_num(t.get("mean", 0), is_int),
                ]
            )
        if yt_rows:
            print_table(title=None, columns=yt_columns, rows=yt_rows)

    blank()
    divider()
    echo(f"  {f_label('Data source:')} {f_num(stats['file_count'])} aggregated files")


# ── main entry point ───────────────────────────────────────────────────────


def compute_and_display_stats(output_json: bool = False, aggr: bool = False) -> None:
    """Scan all content_metrics JSON files, compute statistics, and display them.

    Handles both daily-list and aggregated ``--aggr`` formats, dispatching
    to the appropriate computation and display routines.

    :param output_json: If True, output a JSON dict instead of styled terminal output.
    :param aggr: If True, force using only aggregated-format files (skip daily files).
    """
    if not METRICS_DIR.exists():
        if output_json:
            print_json({"error": "metrics directory not found", "path": str(METRICS_DIR)})
        else:
            warning(f"Metrics directory not found: {METRICS_DIR}")
            info("Run first: zhihu tools creator metrics")
        return

    json_files = sorted(METRICS_DIR.glob("*.json"))
    if not json_files:
        if output_json:
            print_json({"error": "no JSON files found", "path": str(METRICS_DIR)})
        else:
            warning(f"No JSON files found in: {METRICS_DIR}")
            info("Run first: zhihu tools creator metrics")
        return

    # ── load and categorise all files ────────────────────────────────────
    aggr_files: list[dict] = []
    daily_records: list[dict] = []
    daily_source_files: set[str] = set()
    unknown_files: list[str] = []

    for fp in json_files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            if not output_json:
                warning(f"Failed to read {fp.name}: {exc}")
            continue

        if _is_aggr_format(data):
            aggr_files.append({"name": fp.stem.replace("metrics_full_", ""), **data})
        elif _is_daily_list(data):
            daily_source_files.add(fp.name)
            for record in data:
                if record.get("date"):
                    daily_records.append(record)
        else:
            unknown_files.append(fp.name)

    # ── determine primary data source ────────────────────────────────────
    if not daily_records and not aggr_files:
        if output_json:
            print_json({"error": "no valid data found", "unknown_files": unknown_files})
        else:
            warning("No valid daily or aggregated data found.")
            info("Run first: zhihu tools creator metrics")
        return

    if aggr:
        # --aggr mode: derive per-item lifetime totals from daily files
        # (or use existing aggr-format files if present).
        if aggr_files:
            stats = _compute_aggr_stats(aggr_files)
            stats["scan_summary"] = {
                "total_files": len(json_files),
                "daily_files": len(daily_source_files),
                "daily_records": len(daily_records),
                "aggr_files": len(aggr_files),
                "unknown_files": len(unknown_files),
                "mode": "aggr",
                "note": "Used pre-existing aggr-format files on disk.",
            }
        elif daily_source_files:
            # Synthesize aggr data: sum each daily file into a per-item totals dict
            synth_aggr: list[dict] = []
            for fp_stem in sorted(daily_source_files):
                fp = METRICS_DIR / fp_stem
                try:
                    raw = json.loads(fp.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(raw, list) or not raw:
                    continue
                ctype = raw[0].get("type", "?")
                totals: dict[str, float] = {}
                for rec in raw:
                    for k, v in rec.items():
                        if k in ("type", "date"):
                            continue
                        try:
                            totals[k] = totals.get(k, 0) + float(v or 0)
                        except (ValueError, TypeError):
                            pass
                synth_aggr.append(
                    {
                        "name": fp_stem.replace("metrics_full_", ""),
                        "type": ctype,
                        "totals": totals,
                    }
                )
            stats = _compute_aggr_stats(synth_aggr)
            stats["scan_summary"] = {
                "total_files": len(json_files),
                "daily_files": len(daily_source_files),
                "daily_records": len(daily_records),
                "aggr_files": len(aggr_files),
                "unknown_files": len(unknown_files),
                "mode": "aggr",
                "note": f"Synthesized from {len(synth_aggr)} daily files (per-item sums).",
            }
        else:
            if output_json:
                print_json({"error": "no data to aggregate", "unknown_files": unknown_files})
            else:
                warning("No data available for aggregation.")
            return
    elif daily_records:
        stats = _compute_daily_stats(daily_records, daily_source_files)
        # ── add scan summary ─────────────────────────────────────────────────
        stats["scan_summary"] = {
            "total_files": len(json_files),
            "daily_files": len(daily_source_files),
            "daily_records": len(daily_records),
            "aggr_files": len(aggr_files),
            "unknown_files": len(unknown_files),
            "mode": "auto",
        }
    else:
        stats = _compute_aggr_stats(aggr_files)
        stats["scan_summary"] = {
            "total_files": len(json_files),
            "daily_files": len(daily_source_files),
            "daily_records": len(daily_records),
            "aggr_files": len(aggr_files),
            "unknown_files": len(unknown_files),
            "mode": "auto",
        }
    if unknown_files:
        stats["unknown_file_names"] = unknown_files[:20]

    if output_json:
        print_json(stats)
        return

    # ── status line ──────────────────────────────────────────────────────
    if aggr:
        n_items = stats.get("per_metric", {}).get("pv", {}).get("count", 0)
        info(f"Scanned {len(json_files)} files -> {n_items} items (--aggr mode)")
    else:
        info(
            f"Scanned {len(json_files)} files → "
            f"{len(daily_source_files)} daily ({len(daily_records):,} records), "
            f"{len(aggr_files)} aggregated"
        )
    if unknown_files:
        warning(f"Skipped {len(unknown_files)} unknown-format files")

    # ── dispatch to display ──────────────────────────────────────────────
    if aggr or stats.get("format") == "aggr":
        is_synth = stats.get("scan_summary", {}).get("note", "").startswith("Synthesized")
        _display_aggr_stats(stats, synthesized=is_synth)
    else:
        _display_daily_stats(stats)

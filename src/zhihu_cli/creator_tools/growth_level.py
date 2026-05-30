"""Fetch and display Zhihu creator growth-level (创作分) data."""

from __future__ import annotations

import json
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state

GROWTH_URL = "https://www.zhihu.com/creator/account/growth-level"

INDICATOR_LABELS: dict[int, str] = {
    0: "创作活跃度",
    1: "创作垂直度",
    2: "内容优质分",
    3: "创作影响力",
    4: "关注者亲密度",
    5: "社区成就分",
}


def _fetch_score_info() -> dict[str, Any]:
    html_text = fetch_page_html(GROWTH_URL)
    creators = get_page_state(html_text, "creators")
    return creators["home"]["scoreInfo"]


def _fmt_num(n: int) -> str:
    return f"{n:,}"


def _ratio_bar(score: int, max_val: int, width: int = 14) -> str:
    if max_val == 0:
        return ""
    pct = min(score / max_val, 1.0)
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {pct * 100:.0f}%"


def show_growth_level(json_output: bool = False) -> None:
    score_info = _fetch_score_info()
    radar = score_info["radar"]
    overall = score_info["score"]

    if json_output:
        click.echo(json.dumps(score_info, ensure_ascii=False, indent=2))
        return

    console = Console()
    table = Table(title="创作者等级 (Growth Level)", highlight=True)
    table.add_column("维度", style="cyan", no_wrap=True)
    table.add_column("得分", justify="right", style="green")
    table.add_column("等级均值", justify="right", style="yellow")
    table.add_column("原始值", justify="right", style="green")
    table.add_column("原始均值", justify="right", style="yellow")
    table.add_column("满分", justify="right", style="dim")
    table.add_column("占比", style="magenta")

    indicators = radar["indicator"]
    scores = radar["score"]
    avg_scores = radar["avgScore"]
    values = radar["value"]
    avg_values = radar["avgValue"]

    for i in range(6):
        label = indicators[i].get("name") or INDICATOR_LABELS.get(i, "")
        dim_max = indicators[i]["max"]
        table.add_row(
            label,
            _fmt_num(scores[i]),
            _fmt_num(avg_scores[i]),
            _fmt_num(values[i]),
            _fmt_num(avg_values[i]),
            _fmt_num(dim_max),
            _ratio_bar(scores[i], dim_max),
        )

    console.print(table)

    # Overall summary
    level = overall["level"]
    total_score = overall["score"]
    total_upper = overall["scoreUpper"]
    ratio = overall["ratio"]
    yesterday = overall.get("yesterdayUpdatedScore", 0)

    console.print()
    summary = Table(title="总览", highlight=True)
    summary.add_column("指标", style="cyan")
    summary.add_column("数值", justify="right", style="green")
    summary.add_row("创作等级", f"Lv.{level}")
    summary.add_row("创作总分", f"{_fmt_num(total_score)} / {_fmt_num(total_upper)} ({ratio}%)")
    summary.add_row("昨日增长", f"+{_fmt_num(yesterday)}")
    console.print(summary)

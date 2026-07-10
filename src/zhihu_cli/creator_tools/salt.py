"""Fetch and display Zhihu salt value (盐值) data.

Parses SSR data from two endpoints:

* ``/appview/credit`` — current score (``basis``), permissions/权益, changed permissions.
* ``/appview/credit/credit-interpret`` — weekly score history (``creditRecord``),
  dimension breakdown (``creditFields``), action contribution stats (``actionContriStatus``),
  and dimension descriptions (``creditDesc``).
"""

from __future__ import annotations

import json
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state

CREDIT_URL = "https://www.zhihu.com/appview/credit"
CREDIT_INTERPRET_URL = "https://www.zhihu.com/appview/credit/credit-interpret"

DIMENSION_LABELS: dict[str, str] = {
    "lawAbiding": "遵守公约",
    "friendlyReaction": "友善互动",
    "valuableCreation": "内容创作",
    "communityBuilding": "社区建设",
    "userProfile": "个人加分",
}

DIMENSION_ORDER = ["lawAbiding", "friendlyReaction", "valuableCreation", "communityBuilding", "userProfile"]

STATUS_LABELS: dict[str, str] = {
    "publicEdit": "公共编辑",
    "report": "举报",
    "oppose": "加权反对",
}


def _fetch_credit_data() -> dict[str, Any]:
    """Fetch and parse the credit page (salt value overview + permissions)."""
    html_text = fetch_page_html(CREDIT_URL)
    credit = get_page_state(html_text, "credit")
    return credit


def _fetch_credit_interpret_data() -> dict[str, Any]:
    """Fetch and parse the credit-interpret page (score history + descriptions)."""
    html_text = fetch_page_html(CREDIT_INTERPRET_URL)
    credit_interpret = get_page_state(html_text, "creditInterpret")
    return credit_interpret


def _fmt_num(n: int) -> str:
    return f"{n:,}"


def _ratio_bar(score: int, max_val: int = 1000, width: int = 16) -> str:
    """Render a unicode bar showing score / max_val ratio."""
    if max_val == 0:
        return ""
    pct = min(score / max_val, 1.0)
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {pct * 100:.0f}%"


def show_salt(json_output: bool = False) -> None:
    """Fetch and display Zhihu salt value data.

    :param json_output: If True, print raw JSON instead of styled tables.
    """
    credit = _fetch_credit_data()
    credit_interpret = _fetch_credit_interpret_data()

    if json_output:
        result = {
            "credit": credit,
            "creditInterpret": credit_interpret,
        }
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    console = Console()

    # ── 1. Salt score overview ──────────────────────────────────────────
    basis = credit.get("basis", {})
    total_score = basis.get("totalScore", 0)
    total_status = basis.get("totalStatus", "")
    basis_date = basis.get("date", "")

    overview = Table(title="知乎盐值 (Salt Value)", highlight=True)
    overview.add_column("指标", style="cyan", no_wrap=True)
    overview.add_column("数值", justify="right", style="green")

    overview.add_row("当前盐值", f"{_fmt_num(total_score)} / 1000")
    overview.add_row("等级", total_status)
    overview.add_row("更新日期", basis_date)
    overview.add_row("占比", _ratio_bar(total_score, 1000))
    console.print(overview)

    # ── 2. Dimension breakdown (from creditRecord) ─────────────────────
    credit_record = credit_interpret.get("creditRecord", [])
    if credit_record:
        latest = credit_record[0]
        fields = latest.get("creditFields", {})

        console.print()
        dim_table = Table(title="维度得分", highlight=True)
        dim_table.add_column("维度", style="cyan", no_wrap=True)
        dim_table.add_column("得分", justify="right", style="green")
        dim_table.add_column("占比", style="magenta")

        for dim_key in DIMENSION_ORDER:
            label = DIMENSION_LABELS.get(dim_key, dim_key)
            score = fields.get(dim_key, 0)
            dim_table.add_row(label, _fmt_num(score), _ratio_bar(score, 1000))

        console.print(dim_table)

    # ── 3. Weekly score history ────────────────────────────────────────
    if len(credit_record) > 1:
        console.print()
        hist_table = Table(title="周变化记录", highlight=True)
        hist_table.add_column("日期", style="cyan", no_wrap=True)
        hist_table.add_column("变化", justify="center", style="yellow", width=8)
        hist_table.add_column("盐值", justify="right", style="green")
        hist_table.add_column("详情", style="dim")

        for entry in credit_record:
            date_str = entry["date"]
            update = entry.get("updateRecord", {})
            delta = update.get("score", 0)
            detail = update.get("detail", "")
            ts = entry.get("totalScore", 0)

            if delta > 0:
                delta_str = f"+{delta}"
            elif delta < 0:
                delta_str = str(delta)
            else:
                delta_str = "—"

            hist_table.add_row(date_str, delta_str, _fmt_num(ts), detail)

        console.print(hist_table)

    # ── 4. Permissions / 权益 ──────────────────────────────────────────
    permissions = credit.get("permissions", [])
    if permissions:
        console.print()
        perm_table = Table(title="盐值权益", highlight=True)
        perm_table.add_column("权益", style="cyan", no_wrap=True)
        perm_table.add_column("要求分数", justify="right", style="yellow")
        perm_table.add_column("状态", justify="center", style="green")

        for group in permissions:
            group_title = group.get("title", "")
            for sp in group.get("subpermission", []):
                subtitle = sp.get("subtitle", "?")
                required = sp.get("requiredScore", 0)
                acquired = sp.get("status", False)
                status_icon = "✅" if acquired else "❌"
                perm_table.add_row(
                    f"[bold]{group_title}[/bold]  {subtitle}",
                    _fmt_num(required),
                    status_icon,
                )

        console.print(perm_table)

    # ── 5. Action contribution stats ───────────────────────────────────
    action_stats = credit_interpret.get("actionContriStatus", {})
    if action_stats:
        console.print()
        action_table = Table(title="社区贡献统计", highlight=True)
        action_table.add_column("行为", style="cyan", no_wrap=True)
        action_table.add_column("统计", style="green")

        for key, label in STATUS_LABELS.items():
            stat = action_stats.get(key, {})
            if not stat:
                continue
            if key == "publicEdit":
                action_table.add_row(
                    label,
                    f"编辑 {_fmt_num(stat.get('total', 0))} 次  |  "
                    f"有益问题 {_fmt_num(stat.get('beneficialQuestions', 0))} 个",
                )
            elif key == "report":
                action_table.add_row(
                    label,
                    f"举报 {_fmt_num(stat.get('total', 0))} 次  |  "
                    f"有效举报 {_fmt_num(stat.get('effectiveReports', 0))} 次",
                )
            elif key == "oppose":
                stat_display = stat.get("isDisplay", True)
                if stat_display:
                    action_table.add_row(
                        label,
                        f"反对 {_fmt_num(stat.get('total', 0))} 次  |  "
                        f"折叠回答 {_fmt_num(stat.get('collapsedAnswers', 0))} 个  |  "
                        f"标记回答 {_fmt_num(stat.get('markedAnswers', 0))} 个",
                    )

        if action_table.row_count > 0:
            console.print(action_table)

    # ── 6. Dimension descriptions ──────────────────────────────────────
    credit_desc = credit_interpret.get("creditDesc", {})
    if credit_desc:
        console.print()
        desc_table = Table(title="维度说明", highlight=True)
        desc_table.add_column("维度", style="cyan", no_wrap=True)
        desc_table.add_column("项目", style="green")

        for dim_key in DIMENSION_ORDER:
            label = DIMENSION_LABELS.get(dim_key, dim_key)
            items = credit_desc.get(dim_key, [])
            if items:
                item_names = [it.get("title", "?") for it in items]
                desc_table.add_row(label, ", ".join(item_names))
            else:
                desc_table.add_row(label, "[dim](无具体项目)[/dim]")

        console.print(desc_table)


if __name__ == "__main__":
    show_salt()

"""Fetch and persist follower profile/analysis (关注者画像) from Zhihu API."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.output import (
    error,
    f_label,
    f_name,
    f_num,
    f_tag,
    file_saved,
    heading,
    info,
    print_table,
    section,
    stat,
    warning,
)
from zhihu_cli.paths import zhihu_home

DB_FILE: str = str(zhihu_home() / "exports" / "follower_profile.json")
PROFILE_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/aggregation/tab/follow/profile"

BAR_WIDTH: int = 18


def _bar(ratio: float, width: int = BAR_WIDTH) -> str:
    """Render a filled/empty bar for *ratio* (0–1) as an ANSI-styled string.

    :param ratio: Fraction of the bar to fill.
    :param width: Total bar width in characters.
    :returns: A cyan filled run followed by a dimmed track.
    """
    import click

    filled = max(0, min(width, round(ratio * width)))
    return click.style("█" * filled, fg="cyan") + click.style("░" * (width - filled), dim=True)


def _truncate_display(text: str, max_width: int = 40) -> str:
    """Truncate *text* to *max_width* terminal columns, appending an ellipsis.

    Titles from the API mix ASCII and CJK, so a character count is the wrong
    measure — a CJK glyph occupies two columns and would overflow the cell.

    :param text: The raw title.
    :param max_width: Maximum display width in columns.
    :returns: The possibly-truncated title.
    """
    from unicodedata import east_asian_width

    text = text.replace("\n", " ").strip()
    width = 0
    kept: list[str] = []
    for char in text:
        char_width = 2 if east_asian_width(char) in ("W", "F") else 1
        if width + char_width > max_width:
            return "".join(kept) + "…"
        kept.append(char)
        width += char_width
    return text


def _distribution_rows(data: list[dict[str, Any]], top_n: int = 10) -> list[list[str]]:
    """Build table rows for one distribution section.

    :param data: Raw ``{name, value, real_value}`` entries from the API.
    :param top_n: Maximum number of entries to show.
    :returns: Rows of ``[name, share, bar, count]``.
    """
    rows: list[list[str]] = []
    for item in data[:top_n]:
        real = item["real_value"]
        rows.append(
            [
                f_label(item["name"]),
                f_num(f"{item['value'] * 100:5.1f}%"),
                _bar(item["value"]),
                f_num(f"{real:,}"),
            ]
        )
    return rows


def _distribution(title: str, data: list[dict[str, Any]], top_n: int = 10) -> None:
    """Print one distribution section as a table, or an empty notice.

    :param title: Section heading, e.g. ``"性别分布"``.
    :param data: Raw entries from the API.
    :param top_n: Maximum number of entries to show.
    """
    if not data:
        return
    section(title)
    print_table(
        title=None,
        columns=["类别", "占比", "分布", "人数"],
        rows=_distribution_rows(data, top_n),
        show_header=True,
    )


def _active_time_section(active_time: list[dict[str, Any]]) -> None:
    """Print the 24-hour follower activity profile as a bar table.

    :param active_time: Raw ``{name, value, real_value}`` entries, one per hour.
    """
    section("活跃时段")
    if not active_time:
        info("(no data)")
        return

    peak = max(item["real_value"] for item in active_time) or 1
    rows: list[list[str]] = []
    for item in active_time:
        is_peak = item["real_value"] == peak
        name = f_name(item["name"]) if is_peak else f_label(item["name"])
        count = f_num(f"{item['real_value']:,}") + (f"  {f_tag('peak')}" if is_peak else "")
        rows.append([name, _bar(item["real_value"] / peak, width=40), f_num(f"{item['value'] * 100:5.1f}%"), count])

    print_table(title=None, columns=["时段", "分布", "占比", "人数"], rows=rows)


def _content_rows(items: list[dict[str, Any]], limit: int = 5) -> list[list[str]]:
    """Build table rows for a list of content items.

    :param items: Raw content entries with ``content_title``/``follow_num``.
    :param limit: Maximum number of entries to show.
    :returns: Rows of ``[type, title, followers]``.
    """
    rows: list[list[str]] = []
    for item in items[:limit]:
        rows.append(
            [
                f_tag(item.get("content_type", "")),
                _truncate_display(item.get("content_title", "")),
                f_num(f"{item.get('follow_num', 0):,}"),
            ]
        )
    return rows


def run_task() -> None:
    """Fetch the follower profile, persist it to JSON, and render a styled report."""
    headers = cache_manager.load_headers()
    if not headers:
        error("No cached headers found. Run: zhihu auth paste")
        return

    info("Fetching follower profile ...")

    try:
        resp = session.get(PROFILE_URL, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as e:
        error(f"Error fetching follower profile: {e}")
        return

    show_flag = data.get("show", False)
    profile = data.get("profile", {})
    interaction = data.get("interaction", {})

    if profile.get("status") != 1:
        error(f"Profile data not available (status={profile.get('status')}, show={show_flag}).")
        return

    output = {
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "show": show_flag,
        "profile": profile,
        "interaction": interaction,
    }

    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── Display summary ──
    all_follower = profile.get("all_follower", {})

    heading("关注者画像 Follower Profile")

    _distribution("关注来源", all_follower.get("source", []))
    _distribution("活跃度", all_follower.get("activeness", []))
    _active_time_section(all_follower.get("active_time", []))
    _distribution("性别分布", all_follower.get("gender", []))
    _distribution("年龄分布", all_follower.get("age", []))
    _distribution("操作系统", all_follower.get("os", []))
    _distribution("Top 地域", all_follower.get("location", []))
    _distribution("Top 兴趣", all_follower.get("interest", []))

    content = all_follower.get("content", [])
    if content:
        section("关注者来源内容 (Top 5)")
        print_table(title=None, columns=["类型", "标题", "关注人数"], rows=_content_rows(content))

    creator_follow = interaction.get("creator_follow", [])
    if creator_follow:
        section("创作者关注 (Top 5)")
        print_table(
            title=None,
            columns=["创作者", "关注人数"],
            rows=[[f_name(u["name"]), f_num(f"{u['follow_num']:,}")] for u in creator_follow[:5]],
        )

    interact_content = interaction.get("content", [])
    if interact_content:
        section("互动内容来源 (Top 5)")
        print_table(title=None, columns=["类型", "标题", "关注人数"], rows=_content_rows(interact_content))

    reason = profile.get("reason", "")
    if reason:
        section("画像说明")
        info(f"  {reason}")

    if not show_flag:
        warning("画像数据暂不展示 (show=False)")

    section("输出")
    stat("最后更新", f_num(output["last_update"]))
    file_saved(DB_FILE)


if __name__ == "__main__":
    run_task()

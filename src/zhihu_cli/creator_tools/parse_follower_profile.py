"""Fetch and persist follower profile/analysis (关注者画像) from Zhihu API."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session

DB_FILE: str = str(Path.home() / ".zhihu-cli" / "exports" / "follower_profile.json")
PROFILE_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/aggregation/tab/follow/profile"


def _format_section(data: list[dict[str, Any]], top_n: int = 10) -> None:
    for item in data[:top_n]:
        name = item["name"]
        pct = item["value"] * 100
        real = item["real_value"]
        print(f"  {name:<20} {pct:>5.1f}%  ({real:,})")


def run_task() -> None:
    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return

    print("--- Fetching follower profile ---")

    try:
        resp = session.get(PROFILE_URL, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as e:
        print(f"Error fetching follower profile: {e}")
        return

    profile = data.get("profile", {})
    interaction = data.get("interaction", {})

    if profile.get("status") != 1:
        print("Profile data not available (status != 1).")
        return

    output = {
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile,
        "interaction": interaction,
    }

    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Display summary
    all_follower = profile.get("all_follower", {})

    print("\n=== 关注来源 ===")
    _format_section(all_follower.get("source", []))

    print("\n=== 活跃度 ===")
    _format_section(all_follower.get("activeness", []))

    print("\n=== 性别分布 ===")
    _format_section(all_follower.get("gender", []))

    print("\n=== 年龄分布 ===")
    _format_section(all_follower.get("age", []))

    print("\n=== 操作系统 ===")
    _format_section(all_follower.get("os", []))

    print("\n=== Top 地域 ===")
    _format_section(all_follower.get("location", []))

    print("\n=== Top 兴趣 ===")
    _format_section(all_follower.get("interest", []))

    print("\n=== 关注者来源内容 (Top 5) ===")
    for item in all_follower.get("content", [])[:5]:
        title = item["content_title"][:60]
        print(f"  {title:<60} 关注: {item['follow_num']:,}")

    print("\n=== 创作者关注 (Top 5) ===")
    for u in interaction.get("creator_follow", [])[:5]:
        print(f"  {u['name']:<20} 关注数: {u['follow_num']:,}")

    print(f"\nFile saved: {DB_FILE}")


if __name__ == "__main__":
    run_task()

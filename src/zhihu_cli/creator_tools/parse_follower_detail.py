"""Fetch and persist follower analytics (关注者分析) from Zhihu API."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session

DB_FILE: str = str(Path.home() / ".zhihu-cli" / "exports" / "follower_detail.json")
FOLLOWER_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/aggregation/tab/follow/detail"


def run_task(days: int = 90) -> None:
    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return

    print(f"--- Fetching follower detail for last {days} days ---")

    params: dict[str, str] = {"day": str(-days)}

    try:
        resp = session.get(FOLLOWER_URL, params=params, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as e:
        print(f"Error fetching follower data: {e}")
        return

    chart = data.get("Chart", {})
    items = data.get("List", [])

    if not items:
        print("No follower data returned.")
        return

    date_range = f"{items[-1]['date']} ~ {items[0]['date']}"
    latest = items[0]

    output = {
        "summary": {
            "date_range": date_range,
            "days_fetched": len(items),
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "latest": {
                "date": latest["date"],
                "post_follow": latest["post_follow"],
                "cancel_follow": latest["cancel_follow"],
                "pre_follow": latest["pre_follow"],
                "total_follow": latest["total_follow"],
                "active_follow": latest["active_follow"],
                "active_follow_ratio": latest["active_follow_ratio"],
            },
        },
        "chart": chart,
        "list": items,
    }

    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Date range: {date_range}")
    print(f"Days fetched: {len(items)}")
    print(f"Latest ({latest['date']}):")
    print(f"  Post follow:    {latest['post_follow']}")
    print(f"  Cancel follow:  {latest['cancel_follow']}")
    print(f"  Net (pre):      {latest['pre_follow']}")
    print(f"  Total follow:   {latest['total_follow']}")
    print(f"  Active follow:  {latest['active_follow']}")
    print(f"  Active ratio:   {latest['active_follow_ratio']}")
    print(f"\nFile saved: {DB_FILE}")


if __name__ == "__main__":
    run_task()

"""Zhihu notifications handler."""

import re
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.waterfall import stream_handler

NOTIFICATIONS_URL = "https://www.zhihu.com/api/v4/notifications/v2/default?limit=20"


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_notification(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content", {})
    actors = content.get("actors", [])
    actor_name = actors[0].get("name", "") if actors else ""
    actor_url_token = actors[0].get("url_token", "") if actors else ""
    actor_link = actors[0].get("link", "") if actors else ""

    target = content.get("target", {})
    extend = content.get("extend", {})

    target_info = item.get("target", {})
    resource_type = target_info.get("resource_type", "")

    return {
        "id": item.get("id", ""),
        "time": fmt_time(item.get("create_time")),
        "timestamp": item.get("create_time", 0),
        "is_read": item.get("is_read", True),
        "merge_count": item.get("merge_count", 1),
        "verb": content.get("verb", ""),
        "actor_name": actor_name,
        "actor_url_token": actor_url_token,
        "actor_link": actor_link,
        "target_text": target.get("text", ""),
        "target_link": target.get("link", ""),
        "comment_text": _strip_html(extend.get("text", "")),
        "comment_html": target_info.get("content", ""),
        "resource_type": resource_type,
    }


def _parse_notifications(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [_parse_notification(item) for item in data.get("data", [])]


def fetch_notifications(limit: int = 20, max_items: int | None = None) -> list[dict[str, Any]]:
    """Fetch Zhihu notifications with pagination."""
    stream = stream_handler(
        NOTIFICATIONS_URL,
        _parse_notifications,
        delay=0.5,
    )
    items: list[dict[str, Any]] = []
    for item in stream:
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items

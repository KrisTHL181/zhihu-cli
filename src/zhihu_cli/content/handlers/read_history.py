"""Zhihu read history handler."""

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.waterfall import stream_handler


def _parse_history_item(card: dict[str, Any]) -> dict[str, Any] | None:
    data = card.get("data", {})
    if not data:
        return None

    header = data.get("header", {})
    content = data.get("content", {})
    extra = data.get("extra", {})
    action = data.get("action", {})
    matrix = data.get("matrix", [])

    stats_text = ""
    if matrix:
        stats_text = matrix[0].get("data", {}).get("text", "")

    return {
        "content_type": extra.get("content_type", ""),
        "content_token": extra.get("content_token", ""),
        "question_token": extra.get("question_token", ""),
        "title": header.get("title", ""),
        "url": action.get("url", ""),
        "author_name": content.get("author_name", ""),
        "summary": content.get("summary", ""),
        "cover_image": content.get("cover_image", ""),
        "stats_text": stats_text,
        "read_time": fmt_time(extra.get("read_time")),
        "icon": header.get("icon", ""),
    }


def _parse_history_items(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        parsed = _parse_history_item(item)
        if parsed is not None:
            yield parsed


def stream_read_history(limit: int = 20, max_items: int | None = None) -> Iterable[dict[str, Any]]:
    url = "https://www.zhihu.com/api/v4/unify-consumption/read_history?offset=0"
    return stream_handler(url, _parse_history_items, limit=limit, max_items=max_items)


def fetch_read_history(limit: int = 20, max_items: int | None = 20) -> list[dict[str, Any]]:
    return list(stream_read_history(limit, max_items))

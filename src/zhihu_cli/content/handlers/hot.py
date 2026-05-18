"""Zhihu hot list handler."""

from typing import Any

from zhihu_cli.content.handlers.requests import session

HOT_LIST_URL = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50&desktop=true"


def _parse_hot_item(item: dict[str, Any]) -> dict[str, Any]:
    target = item.get("target", {})
    url = target.get("url", "")
    url = url.replace("api.zhihu.com", "www.zhihu.com").replace("/questions/", "/question/")
    return {
        "feed_id": item.get("id", ""),
        "card_id": item.get("card_id", ""),
        "title": target.get("title", ""),
        "excerpt": target.get("excerpt", ""),
        "target_type": target.get("type", ""),
        "target_id": str(target.get("id", "")),
        "url": url,
        "author": target.get("author", {}).get("name", ""),
        "heat": item.get("detail_text", ""),
        "trend": item.get("trend", 0),
        "debut": item.get("debut", False),
        "card_label": item.get("card_label", {}).get("type", ""),
        "answer_count": target.get("answer_count", 0),
        "follower_count": target.get("follower_count", 0),
        "comment_count": target.get("comment_count", 0),
        "created_time": target.get("created", 0),
        "children": item.get("children", []),
    }


def fetch_hot_list(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch the Zhihu hot list."""
    url = f"https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit={limit}&desktop=true"
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()
    items = []
    for item in data.get("data", []):
        items.append(_parse_hot_item(item))
    return items

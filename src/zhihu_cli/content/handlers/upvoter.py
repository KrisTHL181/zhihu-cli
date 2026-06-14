"""Fetch upvoters for Zhihu answers and articles.

Endpoints:
- GET /api/v4/answers/{answer_id}/upvoters?offset=0&limit=20
- GET /api/v4/articles/{article_id}/upvoters?offset=0&limit=20
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers.waterfall import stream_handler

UPVOTER_API: dict[str, str] = {
    "answers": "https://www.zhihu.com/api/v4/answers/{item_id}/upvoters",
    "articles": "https://www.zhihu.com/api/v4/articles/{item_id}/upvoters",
}


def _parse_upvoter(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single upvoter from the API response."""
    return {
        "id": item.get("id", ""),
        "url_token": item.get("url_token", ""),
        "name": item.get("name", ""),
        "headline": item.get("headline", ""),
        "avatar_url": (item.get("avatar_url_template") or "").replace("{size}", "xl"),
        "gender": item.get("gender", -1),
        "is_org": item.get("is_org", False),
        "follower_count": item.get("follower_count", 0),
        "is_following": item.get("is_following", False),
        "is_followed": item.get("is_followed", False),
        "is_privacy": item.get("is_privacy", False),
        "is_advertiser": item.get("is_advertiser", False),
        "member_upvote_cnt": item.get("member_upvote_cnt", 0),
        "zhihu_influence": item.get("zhihu_influence", ""),
        "relationship_endorse": item.get("relationship_endorse", ""),
        "url": f"https://www.zhihu.com/people/{item.get('url_token', '')}",
        "raw": item,
    }


def _parse_upvoters(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_upvoter(item)


def fetch_upvoters(
    item_type: str,
    item_id: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch upvoters for an answer or article (paginated).

    Args:
        item_type: 'answers' or 'articles'.
        item_id: The Zhihu answer ID or article ID.
        limit: Number of items per page (max 20).
        max_items: Maximum total items to fetch (None = fetch all).

    Returns:
        List of parsed upvoter dicts.
    """
    api_template = UPVOTER_API.get(item_type)
    if api_template is None:
        raise ValueError(f"Unsupported item type for upvoters: {item_type}")

    url = f"{api_template.format(item_id=item_id)}?limit={limit}&offset=0"
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_upvoters):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items

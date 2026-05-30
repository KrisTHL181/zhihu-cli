from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state
from zhihu_cli.content.utils.html2markdown import converter


def parse_pin_metadata(item: dict[str, Any]) -> dict[str, Any]:
    pin_id = item.get("id", "")
    title = item.get("title", "") or f"pin {pin_id}"
    excerpt = item.get("excerpt", "")
    content_preview = excerpt or (item.get("content", "")[:200] if item.get("content") else "")

    voteup_count = item.get("voteup_count", 0)
    comment_count = item.get("comment_count", 0)

    created = item.get("created", 0)
    updated = item.get("updated", 0)

    author = item.get("author", {})
    author_name = author.get("name", "unknown")

    # Pin content may be in list format
    content_raw = item.get("content", "")
    if isinstance(content_raw, list) and content_raw:
        content_raw = content_raw[0].get("content", "")

    url = item.get("url", "")
    if not url and pin_id:
        url = f"https://www.zhihu.com/pin/{pin_id}"

    return {
        "id": pin_id,
        "title": title,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {"voteup_count": voteup_count, "comment_count": comment_count},
        "author": {
            "name": author_name,
            "headline": author.get("headline", ""),
        },
        "comment_permission": item.get("comment_permission", ""),
    }


def scrape_pin(pin_url: str) -> tuple[dict[str, Any], str]:
    entities = get_page_state(fetch_page_html(pin_url))
    item = entities.get("pins", {})
    if not item:
        raise ValueError("No pins data found in entities")

    item_data = next(iter(item.values()))

    content = item_data.get("content", "")  # Pin content may be nested
    if isinstance(content, list) and content:
        content = content[0].get("content", "")
    return parse_pin_metadata(item_data), converter.convert(content)

from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state
from zhihu_cli.content.utils.html2markdown import converter


def _pick(item: dict[str, Any], *keys: str) -> Any:
    """Return the value of the first of *keys* that *item* actually carries.

    Pins reach this module in two shapes: API responses spell fields in
    snake_case, while page-state entities use camelCase.  Only one of the
    two spellings is ever present, and the missing one would otherwise be
    read as a default rather than as a mismatch.

    :param item: Pin entity to read from.
    :param keys: Candidate keys, most preferred first.
    :returns: The first non-``None`` value found, or ``None`` if there is none.
    """
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return None


def parse_pin_metadata(item: dict[str, Any], users: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a pin's metadata dict.

    :param item: Pin entity, either from the page state or from the API.
    :param users: The page state's ``users`` entity map. Page-scraped pins
        reference their author by url_token (a plain string) rather than
        inlining the author object, so the reference is resolved against
        this map; API responses inline the author as a dict instead.
    """
    pin_id = item.get("id", "")
    title = item.get("title", "") or f"pin {pin_id}"
    excerpt = item.get("excerpt", "")
    content_preview = excerpt or (item.get("content", "")[:200] if item.get("content") else "")

    # Pins do not separate 赞同 from 点赞, so the reaction count is the
    # vote count. ``voteup_count`` is what the field was originally read
    # as, but no pin response actually carries it.
    voteup_count = _pick(item, "voteup_count", "reaction_count", "reactionCount") or 0
    comment_count = _pick(item, "comment_count", "commentCount") or 0

    created = item.get("created", 0)
    updated = item.get("updated", 0)

    author = item.get("author") or {}
    if isinstance(author, str):
        # Page entities address users by url_token; the entry itself can
        # still be null when the user is not included in the payload.
        author = (users or {}).get(author) or {}
    elif not isinstance(author, dict):
        author = {}
    author_name = author.get("name") or "unknown"

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
            "headline": author.get("headline") or "",
        },
        "comment_permission": _pick(item, "comment_permission", "commentPermission") or "",
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
    return parse_pin_metadata(item_data, entities.get("users", {})), converter.convert(content)

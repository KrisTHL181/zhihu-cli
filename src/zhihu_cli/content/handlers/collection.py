from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers.requests import session


def _safe_json(resp) -> dict[str, Any]:
    """Parse JSON response, returning an error dict for non-JSON bodies."""
    if not resp.text.strip():
        return {"success": resp.ok, "status_code": resp.status_code}
    try:
        return resp.json()
    except Exception:
        return {"error": True, "status_code": resp.status_code, "message": resp.text[:200]}


def collect(item_type: str, item_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/collections/contents/{item_type}/{item_id}")
    return _safe_json(resp)


def add_to_collection(item_type: str, item_id: str, collection_id: str) -> dict[str, Any]:
    resp = session.post(
        f"https://www.zhihu.com/api/v4/collections/{collection_id}/contents?content_id={item_id}&content_type={item_type}"
    )
    return _safe_json(resp)


def delete_to_collection(item_type: str, item_id: str, collection_id: str) -> dict[str, Any]:
    resp = session.delete(
        f"https://www.zhihu.com/api/v4/collections/{collection_id}/contents?content_id={item_id}&content_type={item_type}"
    )
    return _safe_json(resp)


def create_collection(title: str, description: str, is_public: bool) -> dict[str, Any]:
    resp = session.post(
        "https://www.zhihu.com/api/v4/collections?include=updated_time%2Canswer_count%2Cfollower_count",
        json={"title": title, "description": description, "is_public": is_public},
    )
    return _safe_json(resp)


def delete_collection(collection_id: str) -> dict[str, Any]:
    resp = session.delete(f"https://www.zhihu.com/api/v4/collections/{collection_id}")
    return _safe_json(resp)


def get_collection_meta(collection_id: str) -> dict[str, Any]:
    """Fetch metadata for a single collection (title, creator, counts, etc.).

    :param collection_id: Numeric collection ID.
    :returns: Collection metadata dict (empty dict on failure).
    """
    resp = session.get(
        f"https://www.zhihu.com/api/v4/collections/{collection_id}"
        "?include=title%2Cdescription%2Ccreator%2Citem_count%2Cfollower_count"
        "%2Cis_public%2Canswer_count%2Clike_count%2Cview_count%2Ccomment_count"
        "%2Ccreated_time%2Cupdated_time"
    )
    data = _safe_json(resp)
    return data.get("collection", {})


def _parse_collection_contents(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Parse a page of collection contents API response into normalized item dicts."""
    from zhihu_cli.content.handlers import fmt_time

    for item in data.get("data", []):
        ttype = item.get("type", "unknown")
        author = item.get("author", {})

        # Answers nest the title under `question.title`; articles/pins carry it at top level.
        if ttype == "answer":
            title = item.get("question", {}).get("title", "")
        else:
            title = item.get("title", "")

        yield {
            "type": ttype,
            "id": item.get("id"),
            "url": item.get("url", ""),
            "title": title,
            "author_name": author.get("name", ""),
            "author_url_token": author.get("url_token", ""),
            "author_headline": author.get("headline", ""),
            "excerpt": item.get("excerpt", ""),
            "voteup_count": item.get("voteup_count", 0),
            "comment_count": item.get("comment_count", 0),
            "thanks_count": item.get("thanks_count", 0),
            "collection_count": item.get("collection_count", 0),
            "created_time": fmt_time(item.get("created_time")),
            "updated_time": fmt_time(item.get("updated_time")),
            "collect_time": fmt_time(item.get("collect_time")),
            "is_collapsed": item.get("is_collapsed", False),
        }


def list_collection_contents(
    collection_id: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """List all items inside a collection with waterfall pagination.

    :param collection_id: Numeric collection ID.
    :param limit: Items per API page.
    :param max_items: Stop after this many items (None = exhaust all pages).
    :returns: List of normalized item dicts.
    """
    from zhihu_cli.content.handlers.waterfall import stream_handler

    url = f"https://www.zhihu.com/api/v4/collections/{collection_id}/contents?offset=0&limit={limit}"
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_collection_contents):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def list_collections(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    from zhihu_cli.content.handlers.following import _parse_following_collections
    from zhihu_cli.content.handlers.waterfall import stream_handler

    url = (
        f"https://www.zhihu.com/api/v4/people/{url_token}/collections"
        "?include=data%5B*%5D.updated_time%2Canswer_count%2Cfollower_count"
        "%2Ccreator%2Cdescription%2Cis_following%2Ccomment_count%2Ccreated_time"
        f"&offset=0&limit={limit}"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_following_collections):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items

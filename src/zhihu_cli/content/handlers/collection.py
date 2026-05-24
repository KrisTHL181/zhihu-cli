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
    for item in stream_handler(url, _parse_following_collections, delay=1.0):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items

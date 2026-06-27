"""Report (举报) functionality for Zhihu content."""

from __future__ import annotations

from typing import Any

from zhihu_cli.content.handlers.requests import session


def fetch_report_reasons(object_type: str = "answer") -> dict[str, Any]:
    """Fetch available report reasons for a given object type.

    Args:
        object_type: One of 'answer', 'question', 'article', 'comment', 'pin'.
    """
    resp = session.get(f"https://www.zhihu.com/api/v4/reports/reasons/v2?object_type={object_type}")
    resp.raise_for_status()
    return resp.json()


def _iter_reasons(data: dict[str, Any]) -> Any:
    """Yield flat reason dicts from the nested API response."""
    for node in data.get("nodes", []):
        # Standalone reason node (no category)
        if node.get("type") == "reason":
            yield {
                "id": node["id"],
                "text": node["text"],
                "category": "",
            }
            continue

        # Entry node with nested child reasons
        if node.get("type") != "entry" or "entry" not in node:
            continue
        category = node.get("text", "")
        for child in node["entry"].get("nodes", []):
            if child.get("type") != "reason":
                continue
            yield {
                "id": child["id"],
                "text": child["text"],
                "category": category,
            }


def flatten_reasons(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the reason tree into a list of {id, text, category} dicts."""
    return list(_iter_reasons(data))


def submit_report(
    resource_id: str,
    object_type: str,
    reason_id: str,
    reason_key: str | None = None,
    custom_reason: str = "",
    url: str = "",
    reported_resource: list | None = None,
) -> dict[str, Any]:
    """Submit a report for a Zhihu resource.

    Args:
        resource_id: The numeric ID of the resource to report.
        object_type: One of 'answer', 'question', 'article', 'comment', 'pin'.
        reason_id: The reason ID from fetch_report_reasons.
        reason_key: The reason key (defaults to reason_id if not provided).
        custom_reason: Optional custom explanation text.
        url: The URL of the reported content.
        reported_resource: Additional resource info (usually empty list).
    """
    if reason_key is None:
        reason_key = reason_id
    if reported_resource is None:
        reported_resource = []

    payload = {
        "resource_id": resource_id,
        "reported_resource": reported_resource,
        "type": object_type,
        "reason_id": reason_id,
        "reason_key": reason_key,
        "custom_reason": custom_reason,
        "source": "web",
        "url": url,
        "pictures": [],
    }

    resp = session.post(
        "https://www.zhihu.com/api/v4/reports",
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()

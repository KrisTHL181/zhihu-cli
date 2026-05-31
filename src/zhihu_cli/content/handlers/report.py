"""Report (举报) functionality for Zhihu content."""

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


def flatten_reasons(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the reason tree into a list of {id, text, category} dicts."""
    reasons: list[dict[str, Any]] = []
    for node in data.get("nodes", []):
        category = node.get("text", "")
        if node.get("type") == "entry" and "entry" in node:
            for child in node["entry"].get("nodes", []):
                if child.get("type") == "reason":
                    reasons.append(
                        {
                            "id": child["id"],
                            "text": child["text"],
                            "category": category,
                        }
                    )
        elif node.get("type") == "reason":
            reasons.append(
                {
                    "id": node["id"],
                    "text": node["text"],
                    "category": "",
                }
            )
    return reasons


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

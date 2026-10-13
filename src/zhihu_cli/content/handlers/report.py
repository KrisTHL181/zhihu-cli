"""Report (举报) functionality for Zhihu content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.wait import wait

if TYPE_CHECKING:
    from collections.abc import Callable


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


def _report_succeeded(resp: Any) -> bool:
    """Interpret a ``/api/v4/reports`` response as success or failure.

    The endpoint reports its outcome loosely (``is_reported``, ``success``, or
    neither), so anything that is not an explicit failure counts as accepted.

    :param resp: The decoded JSON response body.
    :returns: True when the report was not rejected.
    """
    if not isinstance(resp, dict):
        return True
    if resp.get("error"):
        return False
    return resp.get("is_reported") is not False and resp.get("success") is not False


def _is_forbidden(exc: Exception) -> bool:
    """Return True when *exc* looks like an HTTP 403 (risk control or captcha).

    :param exc: The exception raised by a failed submission.
    """
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 403:
        return True
    return "403" in str(exc)


def submit_reports_batch(
    targets: list[dict[str, Any]],
    delay: float = 2.0,
    on_result: Callable[[int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Submit reports for many resources, one at a time, spaced by *delay*.

    Each target is a dict carrying the fields :func:`submit_report` needs:
    ``resource_id``, ``object_type``, ``reason_id``, plus optional
    ``custom_reason`` and ``url``. Extra keys (labels, line numbers) are copied
    through to the result untouched.

    A failing item is recorded and the run continues — except on a 403, which
    means risk control kicked in, so the remaining items are marked ``skipped``
    and no further requests are sent.

    :param targets: Report targets, in submission order.
    :param delay: Seconds between submissions. Reports are always throttled,
        even for an authenticated profile, because Zhihu's write-action rate
        limit is far tighter than its read limit.
    :param on_result: Optional callback invoked with ``(index, result)`` as soon
        as each item finishes, so the caller can stream progress.
    :returns: One result dict per target — the target fields plus ``success``,
        and either ``response`` or ``error``.
    """
    results: list[dict[str, Any]] = []
    aborted = False

    for index, target in enumerate(targets):
        if aborted:
            entry: dict[str, Any] = {**target, "success": False, "skipped": True, "error": "skipped after a 403"}
        else:
            if index:
                wait(delay, forced_to_wait=True)
            try:
                resp = submit_report(
                    resource_id=target["resource_id"],
                    object_type=target["object_type"],
                    reason_id=target["reason_id"],
                    custom_reason=target.get("custom_reason", ""),
                    url=target.get("url", ""),
                )
                entry = {**target, "success": _report_succeeded(resp), "response": resp}
                if not entry["success"]:
                    entry["error"] = str(resp.get("error") or "report rejected by Zhihu")
            except Exception as e:  # noqa: BLE001 — one bad item must not kill the batch
                entry = {**target, "success": False, "error": str(e)}
                aborted = _is_forbidden(e)

        results.append(entry)
        if on_result is not None:
            on_result(index, entry)

    return results

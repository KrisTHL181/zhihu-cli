"""Delete your own Zhihu content — answers, questions, articles, and pins.

Zhihu has no single "delete" API: each content type lives under its own
collection endpoint, and all four are plain ``DELETE`` requests against
the numeric ID.

Two quirks are worth knowing.  Failures arrive as a JSON error envelope
(``{"error": {"code": ..., "message": ...}}``) rather than an empty
body, so the server's own wording is surfaced instead of a bare status
code.  And a missing pin answers ``403`` where the other three answer
``404`` — so on this route a 403 reads as "no such pin, or not yours",
*not* as a sign that the session has expired.
"""

from __future__ import annotations

from typing import Any

from zhihu_cli.content.handlers.requests import session

#: Delete endpoint per content type.  Keys are singular, matching the
#: spelling users type on the command line.
DELETE_ENDPOINTS: dict[str, str] = {
    "answer": "https://www.zhihu.com/api/v4/answers/{item_id}",
    "question": "https://www.zhihu.com/api/v4/questions/{item_id}",
    "article": "https://www.zhihu.com/api/v4/articles/{item_id}",
    "pin": "https://www.zhihu.com/api/v4/pins/{item_id}",
}


def _error_message(payload: Any, status_code: int) -> str:
    """Build a human-readable reason from Zhihu's error envelope.

    :param payload: Parsed response body, if any.
    :param status_code: HTTP status, used when the body carries no message.
    :returns: The server's own message when present, else ``HTTP <code>``.
    """
    if isinstance(payload, dict):
        error_field = payload.get("error")
        if isinstance(error_field, dict) and error_field.get("message"):
            return str(error_field["message"])
        if isinstance(error_field, str) and error_field:
            return error_field
    return f"HTTP {status_code}"


def delete_content(item_type: str, item_id: str) -> dict[str, Any]:
    """Delete one of your own items by ID.

    :param item_type: A key of :data:`DELETE_ENDPOINTS` — ``answer``,
        ``question``, ``article``, or ``pin``.
    :param item_id: Numeric Zhihu ID of the item to delete.
    :returns: Parsed API response, or ``{}`` when the server replies with
        an empty body (which is the success case for most of these).
    :raises ValueError: if *item_type* is not a known content type.
    :raises RuntimeError: if Zhihu refuses the deletion, carrying the
        server's own message.
    """
    template = DELETE_ENDPOINTS.get(item_type)
    if template is None:
        expected = ", ".join(DELETE_ENDPOINTS)
        raise ValueError(f"Unknown content type {item_type!r}; expected one of: {expected}")

    resp = session.delete(template.format(item_id=item_id))

    payload: Any = {}
    if resp.text.strip():
        try:
            payload = resp.json()
        except Exception:
            payload = {}

    if not resp.ok:
        raise RuntimeError(_error_message(payload, resp.status_code))

    return payload if isinstance(payload, dict) else {"data": payload}

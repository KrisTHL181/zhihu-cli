"""Paid consultation (付费咨询) handler — fetch self-answers filtered by consultation status."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.waterfall import stream_handler

INFINITY_BASE = "https://www.zhihu.com/api/v4/infinity/self/answers"


def _extract_question_text(question: dict[str, Any]) -> str:
    """Extract plain-text content from a question's ``content`` blocks."""
    blocks = question.get("content", [])
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("content", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


def _parse_consult_answer(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single consultation item from the infinity API."""
    question = item.get("question", {})
    questioner = item.get("questioner", {})
    responder = item.get("responder", {})
    services = item.get("services", [])
    service_title = services[0].get("title", "") if services else ""

    question_text = _extract_question_text(question)
    # First line as title, rest as excerpt
    lines = question_text.split("\n")

    return {
        "type": "consult",
        "id": item.get("id", ""),
        "conversation_id": question.get("conversation_id", ""),
        "question_id": question.get("id", ""),
        "title": lines[0] if lines else "",
        "excerpt": "\n".join(lines[1:]) if len(lines) > 1 else "",
        "url": f"https://www.zhihu.com/consult/conversation/{item.get('id', '')}",
        "created_time": fmt_time(question.get("created_at")),
        "expires_at": fmt_time(item.get("expires_at")),
        "status": item.get("status", ""),
        "price": item.get("price", 0),
        "audience_price": item.get("audience_price", 0),
        "service_title": service_title,
        "is_public": item.get("is_public", False),
        "is_anonymous": item.get("is_anonymous", False),
        "questioner_name": questioner.get("fullname", ""),
        "questioner_id": questioner.get("id", ""),
        "responder_name": responder.get("fullname", ""),
        "responder_id": responder.get("id", ""),
    }


def _parse_answer_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_consult_answer(item)


def fetch_consult_answers(
    status: str,
    limit: int = 20,
    max_items: int | None = None,
    sub_status: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch self-answers filtered by consultation *status*.

    :param status: One of ``"unanswered"``, ``"closed"``, ``"other"``.
    :param limit: Page size for each API request.
    :param max_items: Cap on total items returned (``None`` = unlimited).
    :param sub_status: Optional sub-filter (used with ``status="closed"``,
        e.g. ``"all"``).
    :returns: Normalized list of consultation dicts.
    """
    params = [f"status={status}"]
    if sub_status is not None:
        params.append(f"sub_status={sub_status}")

    url = f"{INFINITY_BASE}?{'&'.join(params)}&offset=0&limit={limit}"

    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_answer_list):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items

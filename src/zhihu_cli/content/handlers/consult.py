"""Paid consultation (付费咨询) handler — fetch self-answers filtered by consultation status."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session
from zhihu_cli.content.handlers.waterfall import stream_handler

INFINITY_BASE = "https://www.zhihu.com/api/v4/infinity/self/answers"

CONVERSATION_URL_RE = re.compile(r"https?://(?:www\.)?zhihu\.com/consult/conversation/(\d+)(?:/answer)?")


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
        "first_answer_at": fmt_time(item.get("first_answer_at")) if item.get("first_answer_at") else "",
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


def parse_conversation_id(url_or_id: str) -> str:
    """Extract a conversation ID from a consult conversation URL or a raw numeric ID.

    :param url_or_id: A full URL like
        ``https://www.zhihu.com/consult/conversation/123456`` (the
        ``/answer`` suffix is optional) or a bare numeric ID string.
    :returns: The conversation ID as a string.
    :raises ValueError: If the input doesn't look like a valid consult URL or ID.
    """
    m = CONVERSATION_URL_RE.search(url_or_id)
    if m:
        return m.group(1)
    if url_or_id.strip().isdigit():
        return url_or_id.strip()
    raise ValueError(f"Invalid consult conversation URL or ID: {url_or_id!r}")


def fetch_conversation_detail(conversation_id: str) -> dict[str, Any]:
    """Fetch full detail for a single consultation conversation.

    Fetches the SSR page at
    ``https://www.zhihu.com/consult/conversation/<id>/answer`` and
    extracts ``js-initialData`` → ``initialState.archive`` via
    :func:`~zhihu_cli.content.handlers.requests.get_page_state`.

    :param conversation_id: The conversation (answer) ID string.
    :returns: A normalized dict with ``conversation_id``, ``status``,
        ``price``, ``questioner``, ``responder``, ``messages``, and more.
    :raises ValueError: If the page doesn't contain archive data.
    """
    url = f"https://www.zhihu.com/consult/conversation/{conversation_id}/answer"
    html = fetch_page_html(url)
    archive = get_page_state(html, key="archive")

    conv = archive.get("conversation")
    if not conv:
        raise ValueError(f"No archive data for conversation {conversation_id} (not found or not accessible)")

    # ── messages (ordered) ─────────────────────────────────────────────
    message_dict = archive.get("messageDict", {})
    messages: list[dict[str, Any]] = []
    for msg in conv.get("messages", []):
        msg_id = str(msg.get("id", ""))
        enriched = message_dict.get(msg_id, {})
        text: str = enriched.get("text", "") or _extract_question_text({"content": msg.get("content", [])})
        images: list[str] = enriched.get("images", []) or []
        messages.append(
            {
                "id": msg.get("id"),
                "type": msg.get("type", ""),
                "text": text,
                "images": images,
                "created_at": fmt_time(msg.get("createdAt")),
                "is_first_question": bool(msg.get("isFirstQuestion")),
            }
        )

    # ── services ───────────────────────────────────────────────────────
    services = conv.get("services", [])
    service_title = services[0].get("title", "") if services else ""

    questioner = archive.get("questioner", {})
    responder = archive.get("responder", {})
    user = archive.get("user", {})

    return {
        "conversation_id": conv.get("id", ""),
        "status": conv.get("status", ""),
        "price": conv.get("price", 0),
        "audience_price": conv.get("audiencePrice", 0),
        "actual_income": conv.get("actualIncome", 0),
        "actual_income_title": conv.get("actualIncomeTitle", ""),
        "pay_status": conv.get("payStatus", ""),
        "is_public": conv.get("isPublic", False),
        "is_anonymous": conv.get("isAnonymous", False),
        "is_expired": conv.get("isExpired", False),
        "expires_at": fmt_time(conv.get("expiresAt")),
        "first_answer_at": fmt_time(conv.get("firstAnswerAt")) if conv.get("firstAnswerAt") else None,
        "like_count": conv.get("likeCount", 0),
        "purchase_count": conv.get("purchaseCount", 0),
        "service_title": service_title,
        "questioner": {
            "name": questioner.get("fullname", ""),
            "avatar_url": questioner.get("avatarUrl", ""),
            "id": questioner.get("id"),
        },
        "responder": {
            "name": responder.get("fullname", ""),
            "avatar_url": responder.get("avatarUrl", ""),
            "id": responder.get("id", ""),
        },
        "user_identity": user.get("identity", ""),
        "can_read": user.get("canRead", False),
        "is_liked": user.get("isLiked", False),
        "messages": messages,
        "url": f"https://www.zhihu.com/consult/conversation/{conv.get('id', '')}",
    }


def send_consult_answer(
    conversation_id: str,
    content_text: str,
    image_url: str | None = None,
    image_width: int = 0,
    image_height: int = 0,
) -> dict[str, Any]:
    """Send an answer message in a consultation conversation.

    Posts to the infinity conversations API to reply to a paid-consultation
    question.  Supports plain-text answers and optional image attachments.

    :param conversation_id: The conversation (answer) ID string.
    :param content_text: The answer text content.
    :param image_url: Optional image URL to attach (Zhihu CDN URL).
    :param image_width: Image width in pixels (used when *image_url* is set).
    :param image_height: Image height in pixels (used when *image_url* is set).
    :returns: API response dict with ``conversation_id``, ``created_at``,
        and ``message_id``.
    :raises RuntimeError: If the API returns an error (e.g. 403).
    """
    content_blocks: list[dict[str, Any]] = []

    if content_text:
        content_blocks.append(
            {
                "contentType": 0,
                "stability": 0,
                "content": content_text,
                "duration": 0,
                "filename": None,
                "height": 0,
                "md5": None,
                "original_url": None,
                "type": "text",
                "url": None,
                "watermark": None,
                "watermark_url": None,
                "width": 0,
            }
        )

    if image_url:
        content_blocks.append(
            {
                "contentType": 1,
                "stability": 0,
                "content": image_url,
                "duration": 0,
                "filename": None,
                "height": image_height,
                "md5": None,
                "original_url": None,
                "type": "image",
                "url": image_url,
                "watermark": None,
                "watermark_url": None,
                "width": image_width,
            }
        )

    payload: dict[str, Any] = {
        "reserve_at": 0,
        "conversation_type": 0,
        "content": content_blocks,
        "is_public": 0,
        "is_anonymous": 0,
        "service_no": None,
        "type": "answer",
    }

    url = f"https://api.zhihu.com/api/v4/infinity/conversations/{conversation_id}/messages"
    resp = session.post(url, json=payload)

    data: dict[str, Any] = resp.json()
    if resp.status_code == 403 and "error" in data:
        raise RuntimeError(f"Failed to send answer: {data['error'].get('message', data['error'])}")
    resp.raise_for_status()

    return data


def fetch_consult_answers(
    status: str,
    limit: int = 20,
    max_items: int | None = None,
    sub_status: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch self-answers filtered by consultation *status*.

    :param status: One of ``"unanswered"``, ``"closed"``, ``"other"``,
        ``"answered"``.
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


def fetch_answering_with_detail(
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch in-progress (answering) consultations enriched with answer messages.

    Fetches the answered list from the infinity API, then for each item
    fetches the SSR conversation detail page to extract the answer
    message(s).

    :param limit: Page size for the list API request.
    :param max_items: Cap on total items returned (``None`` = unlimited).
    :returns: Normalized list of answering consultation dicts, each with
        an extra ``answers`` key (list of answer message dicts) and a
        ``_detail`` key (the raw conversation detail or ``None`` on
        fetch failure).
    """
    items = fetch_consult_answers("answered", limit=limit, max_items=max_items)
    for item in items:
        conversation_id = item.get("conversation_id", "")
        if not conversation_id:
            item["answers"] = []
            item["_detail"] = None
            continue
        try:
            detail = fetch_conversation_detail(conversation_id)
            answer_msgs = [m for m in detail.get("messages", []) if m.get("type") == "answer"]
            item["answers"] = answer_msgs
            item["_detail"] = detail
        except Exception:
            item["answers"] = []
            item["_detail"] = None
    return items

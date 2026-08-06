"""Handler for Zhihu Direct Answer (知乎直答) session history and chat API."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from typing import Any

from zhihu_cli.content.handlers.requests import session

ZHIDA_HISTORY_URL = "https://zhida.zhihu.com/ai_ingress/ai_chat/get_session_history?include_pro=true&include_v2=true"
ZHIDA_SESSION_URL = "https://zhida.zhihu.com/ai_ingress/session/{session_id}/list"
ZHIDA_COMPLETION_URL = "https://zhida.zhihu.com/ai_ingress/stream/completion"

# Available chat models
CHAT_MODELS: dict[str, str] = {
    "deepseek-r1": "CM_DEEP_SEEK_R1",
    "deepseek-v4": "CM_DEEP_SEEK_R1",  # alias
}

# Available chat modes
CHAT_MODES: dict[str, str] = {
    "fast": "FAST",
    "deep-thinking": "DEEP_SEARCH",
}

# Available knowledge bases
_KNOWLEDGE_BASES: dict[str, str] = {
    "global": "KBT_GLOBAL",
    "zhihu": "KBT_ZHIHU",
    "paper": "KBT_PAPER",
    "personal": "KBT_PERSONAL_KNOWLEDGE_BASE",
}

# Friendly presets
KNOWLEDGE_PRESETS: dict[str, list[str]] = {
    "all": ["KBT_GLOBAL", "KBT_ZHIHU", "KBT_PAPER", "KBT_PERSONAL_KNOWLEDGE_BASE"],
    "zhihu-only": ["KBT_ZHIHU"],
    "paper-only": ["KBT_PAPER"],
    "global-only": ["KBT_GLOBAL"],
    "personal-only": ["KBT_PERSONAL_KNOWLEDGE_BASE"],
    "none": [],
}

DEFAULT_KNOWLEDGE_IDS = KNOWLEDGE_PRESETS["all"]


def resolve_knowledge_ids(spec: str | None) -> list[str]:
    """Resolve a user-provided knowledge spec into a list of knowledge-base IDs.

    *spec* is a comma-separated string of preset names (see
    :data:`KNOWLEDGE_PRESETS`) and/or raw ``KBT_*`` IDs.  An empty string
    or ``"none"`` disables all knowledge bases.  When *spec* is :data:`None`,
    return the default set (``"all"``).

    :param spec: Comma-separated preset names / raw IDs, or :data:`None`.
    :returns: Resolved list of knowledge-base ID strings.
    """
    if spec is None:
        return list(DEFAULT_KNOWLEDGE_IDS)

    result: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        # Exact match for a raw KBT_ ID
        if part.startswith("KBT_"):
            result.append(part)
        elif part in KNOWLEDGE_PRESETS:
            result.extend(KNOWLEDGE_PRESETS[part])
        elif part == "none":
            return []
        else:
            # Unknown — pass through as-is in case new IDs are added server-side
            result.append(part)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for kid in result:
        if kid not in seen:
            seen.add(kid)
            deduped.append(kid)
    return deduped


_CITE_RE = re.compile(r"<cite[^>]*>.*?</cite>", re.DOTALL)


def _strip_cite_tags(text: str) -> str:
    """Remove ``<cite>`` tags and their contents from *text*.

    :param text: Raw answer text with cite tags.
    :returns: Cleaned text.
    """
    return _CITE_RE.sub("", text).strip()


def _parse_history_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw history item from the zhida API.

    :param item: Raw item dict from the API response.
    :returns: Normalised dict with ``title``, ``id``, ``summary``,
              ``send_time``, ``is_favorite``, ``tab_type``.
    """
    return {
        "title": item.get("title", ""),
        "id": item.get("id", ""),
        "summary": item.get("summary", ""),
        "send_time": item.get("send_time", 0),
        "is_favorite": item.get("is_favorite", False),
        "tab_type": item.get("tab_type", ""),
    }


def _parse_session_turn(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single turn (item) from the session detail API.

    :param item: Raw turn dict with ``message_type``, ``messages``, etc.
    :returns: Normalised turn dict.
    """
    msg_type = item.get("message_type", "")
    is_user = msg_type == "user_input"

    messages = item.get("messages", [])
    answer_text = ""
    thinking_text = ""
    sources: list[dict[str, Any]] = []
    followups: list[str] = []
    cost_time_ms: int | None = None

    for msg in messages:
        for ev in msg.get("data", []):
            event_name = ev.get("event", "")
            event_data = ev.get("data") or {}

            if event_name == "Think" and isinstance(event_data, dict):
                thinking_text = event_data.get("thinking", "")
            elif event_name == "Answer" and isinstance(event_data, dict):
                answer_text = _strip_cite_tags(event_data.get("summary", ""))
            elif event_name == "Cards" and isinstance(event_data, list):
                for card in event_data:
                    labels = card.get("labels", [])
                    author_name = ""
                    for label in labels:
                        if label.get("type") == "author":
                            author_name = label.get("data", {}).get("name", "")
                            break
                    sources.append(
                        {
                            "title": card.get("title", ""),
                            "url": card.get("url", ""),
                            "abstract": card.get("content_abstract", ""),
                            "author": author_name,
                            "source": card.get("recall_source", ""),
                        }
                    )
            elif event_name == "RecommendQueries" and isinstance(event_data, list):
                for rq in event_data:
                    followups.append(rq.get("intro_word", ""))
            elif event_name == "End" and isinstance(event_data, dict):
                cost_time_ms = event_data.get("cost_time_ms")

    return {
        "type": "user" if is_user else "ai",
        "send_time": item.get("send_time", 0),
        "content": answer_text,
        "thinking": thinking_text,
        "sources": sources,
        "followups": followups,
        "cost_time_ms": cost_time_ms,
    }


def fetch_session_history() -> list[dict[str, Any]]:
    """Fetch the zhida session history list.

    :returns: List of normalised history items.
    """
    resp = session.get(ZHIDA_HISTORY_URL)
    resp.raise_for_status()
    data = resp.json()
    # The API returns a straight JSON array
    if isinstance(data, list):
        return [_parse_history_item(item) for item in data]
    # Defensive: if it ever wraps in a dict, try common keys
    for key in ("data", "list", "items", "sessions"):
        if key in data:
            return [_parse_history_item(item) for item in data[key]]
    return []


def fetch_session_detail(session_id: str) -> list[dict[str, Any]]:
    """Fetch the full conversation for a zhida session.

    :param session_id: The session ID (from history list).
    :returns: List of normalised turn dicts.
    """
    url = ZHIDA_SESSION_URL.format(session_id=session_id)
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    return [_parse_session_turn(item) for item in items]


def chat_stream(
    message: str,
    session_id: str = "",
    chat_model: str = "CM_DEEP_SEEK_R1",
    chat_mode: str = "FAST",
    knowledge_ids: list[str] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Post a chat message and stream SSE events as they arrive.

    Yields structured dicts for each event type:
    ``init``, ``cards``, ``heartbeat``, ``answer_chunk``, ``end``.

    On the ``init`` event the returned ``session_id`` is the newly created
    (or continued) session ID — use it for follow-up messages.

    :param message: The user's question text.
    :param session_id: Session ID to continue, or empty for a new session.
    :param chat_model: Model identifier (see :data:`CHAT_MODELS`).
    :param chat_mode: Chat mode (see :data:`CHAT_MODES`).
    :param knowledge_ids: Knowledge base IDs to search.
    :yields: Event dicts with ``{"event": "<type>", "data": ...}``.
    """
    if knowledge_ids is None:
        knowledge_ids = DEFAULT_KNOWLEDGE_IDS

    payload = {
        "knowledge_ids": knowledge_ids,
        "quiz_type": "QT_CHAT",
        "attachments": [],
        "message_source_type": "text",
        "session_id": session_id or "",
        "zhida_source": "zhida",
        "chat_mode": chat_mode,
        "chat_model": chat_model,
        "message_content": message,
        "push_interval": 50,
    }

    headers = {"Content-Type": "text/event-stream"}

    resp = session.post(
        ZHIDA_COMPLETION_URL,
        data=json.dumps(payload, ensure_ascii=False),
        headers=headers,
        stream=True,
    )
    resp.raise_for_status()

    current_data: str | None = None

    try:
        for raw_line in resp.iter_lines():
            # curl_cffi does not support decode_unicode=True; decode here.
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line

            if not line:
                # Empty line = end of event, flush buffered data
                if current_data is not None:
                    try:
                        parsed = json.loads(current_data)
                        yield _classify_sse_event(parsed)
                    except json.JSONDecodeError:
                        pass
                    current_data = None
                continue

            if line.startswith("data:"):
                raw = line[5:].strip()
                if current_data is None:
                    current_data = raw
                else:
                    current_data += raw
            elif line.startswith(":"):
                # SSE comment — heartbeat/keep-alive
                continue

        # Flush any remaining data
        if current_data is not None:
            try:
                parsed = json.loads(current_data)
                yield _classify_sse_event(parsed)
            except json.JSONDecodeError:
                pass
    finally:
        resp.close()


def _classify_sse_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Classify a raw SSE data dict into a typed event.

    :param raw: Raw JSON dict from an SSE ``data:`` line.
    :returns: Typed event dict with ``event`` key.
    """
    event_name = raw.get("event", "")

    if event_name == "Init":
        return {
            "event": "init",
            "data": {
                "session_id": raw.get("data", {}).get("session_id", ""),
                "message_id": raw.get("data", {}).get("message_id", ""),
            },
        }
    elif event_name == "Cards":
        return {
            "event": "cards",
            "data": _parse_sse_cards(raw.get("data", [])),
        }
    elif event_name == "HeartBeat":
        return {
            "event": "heartbeat",
            "data": raw.get("data", {}),
        }
    elif event_name == "Answer":
        answer_data = raw.get("data", {})
        return {
            "event": "answer_chunk",
            "data": {
                "summary": answer_data.get("summary", ""),
                "delta": bool(answer_data.get("delta", False)),
                "status": answer_data.get("status", 0),
                "cite_dict": answer_data.get("cite_dict") or {},
            },
        }
    elif event_name == "End":
        return {
            "event": "end",
            "data": raw.get("data", {}),
        }
    elif event_name == "Think":
        think_data = raw.get("data", {})
        return {
            "event": "think_chunk",
            "data": {
                "thinking": think_data.get("thinking", ""),
            },
        }
    elif event_name == "RecommendQueries":
        return {
            "event": "followups",
            "data": raw.get("data", []),
        }
    else:
        return {"event": event_name.lower(), "data": raw.get("data", {})}


def _parse_sse_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract key fields from Cards event data.

    :param cards: Raw cards list from the SSE stream.
    :returns: List of normalised card dicts.
    """
    parsed = []
    for card in cards:
        labels = card.get("labels", [])
        author_name = ""
        for label in labels:
            if label.get("type") == "author":
                author_name = label.get("data", {}).get("name", "")
                break
        parsed.append(
            {
                "title": card.get("title", ""),
                "url": card.get("url", ""),
                "abstract": card.get("content_abstract", ""),
                "author": author_name,
                "source": card.get("recall_source", ""),
            }
        )
    return parsed


def chat_complete(
    message: str,
    session_id: str = "",
    chat_model: str = "CM_DEEP_SEEK_R1",
    chat_mode: str = "FAST",
    knowledge_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Post a chat message and return the complete, non-streamed response.

    :param message: The user's question text.
    :param session_id: Session ID to continue, or empty for a new session.
    :param chat_model: Model identifier.
    :param chat_mode: Chat mode.
    :param knowledge_ids: Knowledge base IDs.
    :returns: Full response dict with ``session_id``, ``answer``, ``sources``,
              ``followups``.
    """
    result: dict[str, Any] = {
        "session_id": session_id,
        "answer": "",
        "thinking": "",
        "sources": [],
        "followups": [],
    }

    for event in chat_stream(
        message=message,
        session_id=session_id,
        chat_model=chat_model,
        chat_mode=chat_mode,
        knowledge_ids=knowledge_ids,
    ):
        etype = event["event"]
        if etype == "init":
            result["session_id"] = event["data"]["session_id"]
            result["message_id"] = event["data"]["message_id"]
        elif etype == "think_chunk":
            result["thinking"] += event["data"]["thinking"]
        elif etype == "cards":
            result["sources"] = event["data"]
        elif etype == "answer_chunk":
            result["answer"] += event["data"]["summary"]
        elif etype == "followups":
            result["followups"] = event["data"]

    return result

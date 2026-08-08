import re
from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler

MEMBER_API = "https://www.zhihu.com/api/v4/members/{token}"


def follow(user_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/members/{user_id}/followers")

    data = resp.json()
    if resp.status_code == 403 and "error" in data.keys():
        raise PermissionError(f"Failed to follow: {data['error']['message']}")

    return data


def unfollow(user_id: str) -> dict[str, Any]:
    resp = session.delete(f"https://www.zhihu.com/api/v4/members/{user_id}/followers")
    return resp.json()


def block(user_id: str) -> None:
    session.post(f"https://www.zhihu.com/api/v4/members/{user_id}/actions/block")


def unblock(user_id: str) -> None:
    session.delete(f"https://www.zhihu.com/api/v4/members/{user_id}/actions/block")


# ── member profile ──────────────────────────────────────────────────────────

# ``include`` fields requested from the member profile API. ``gender``, the
# counts and the relational flags are only returned when explicitly listed.
_MEMBER_PROFILE_INCLUDE = (
    "gender,headline,description,educations,business,locations,employments,"
    "is_following,is_followed,follower_count,following_count,answer_count,"
    "articles_count,pins_count,question_count,voteup_count,thanked_count,"
    "cover_url,ip_info"
)


def fetch_member_profile(url_token: str) -> dict[str, Any] | None:
    """Fetch a member's public profile info (via the v4 members API).

    :param url_token: The user's url_token (e.g. ``"zhangsan"``).
    :returns: A flat dict of profile fields, or ``None`` on failure.
    """
    url = f"https://www.zhihu.com/api/v4/members/{url_token}?include={_MEMBER_PROFILE_INCLUDE}"
    try:
        resp = session.get(url)
        resp.raise_for_status()
        user = resp.json()
    except Exception:
        return None

    def _topic_names(items: Any) -> list[str]:
        """Collapse a list of {school|company: {name, ...}} into plain names."""
        if not isinstance(items, list):
            return []
        return [(t.get("school") or t.get("company") or {}).get("name", "") for t in items if isinstance(t, dict)]

    return {
        "id": user.get("id", ""),
        "name": user.get("name", ""),
        "url_token": user.get("url_token", url_token),
        "headline": user.get("headline", ""),
        "avatar_url": user.get("avatar_url", ""),
        "gender": user.get("gender", -1),
        "follower_count": user.get("follower_count", 0),
        "following_count": user.get("following_count", 0),
        "answer_count": user.get("answer_count", 0),
        "articles_count": user.get("articles_count", 0),
        "pins_count": user.get("pins_count", 0),
        "question_count": user.get("question_count", 0),
        "voteup_count": user.get("voteup_count", 0),
        "thanked_count": user.get("thanked_count", 0),
        "description": user.get("description", ""),
        # extra fields only available from the API
        "is_following": user.get("is_following", False),
        "is_followed": user.get("is_followed", False),
        "cover_url": user.get("cover_url", ""),
        "ip_info": user.get("ip_info", ""),
        "business": (user.get("business") or {}).get("name", ""),
        "educations": _topic_names(user.get("educations")),
        "employments": _topic_names(user.get("employments")),
        "locations": _topic_names(user.get("locations")),
    }


# ── answer list ─────────────────────────────────────────────────────────────


def _parse_member_answer(item: dict[str, Any]) -> dict[str, Any]:
    question = item.get("question", {})
    return {
        "type": "answer",
        "id": item.get("id", ""),
        "question_id": question.get("id", ""),
        "title": question.get("title", ""),
        "excerpt": item.get("excerpt", ""),
        "url": f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{item.get('id', '')}",
        "created_time": fmt_time(item.get("created_time")),
        "updated_time": fmt_time(item.get("updated_time")),
        "voteup_count": item.get("voteup_count", 0),
        "comment_count": item.get("comment_count", 0),
        "is_copyable": item.get("is_copyable", True),
    }


def _parse_answer_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_member_answer(item)


def fetch_member_answers(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a member's answers list."""
    url = (
        f"{MEMBER_API.format(token=url_token)}/answers"
        f"?include=data%5B%2A%5D.excerpt%2Cdata%5B%2A%5D.voteup_count"
        f"%2Cdata%5B%2A%5D.comment_count%2Cdata%5B%2A%5D.is_copyable"
        f"&offset=0&limit={limit}&sort_by=created"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_answer_list):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── article list ────────────────────────────────────────────────────────────


def _parse_member_article(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "article",
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "excerpt": item.get("excerpt", ""),
        "url": (item.get("url") or f"https://zhuanlan.zhihu.com/p/{item.get('id', '')}").replace("http://", "https://"),
        "created_time": fmt_time(item.get("created")),
        "updated_time": fmt_time(item.get("updated")),
        "voteup_count": item.get("voteup_count", 0),
        "comment_count": item.get("comment_count", 0),
    }


def _parse_article_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_member_article(item)


def fetch_member_articles(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a member's articles list."""
    url = (
        f"{MEMBER_API.format(token=url_token)}/articles"
        f"?include=data%5B%2A%5D.excerpt%2Cdata%5B%2A%5D.voteup_count"
        f"%2Cdata%5B%2A%5D.comment_count"
        f"&offset=0&limit={limit}&sort_by=created"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_article_list):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── pin list ────────────────────────────────────────────────────────────────


def _parse_member_pin(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content", "")
    if isinstance(content, list):
        content = "\n".join(block.get("content", "") if isinstance(block, dict) else str(block) for block in content)

    clean_title = re.sub(r"<[^>]+>", "", item.get("excerpt", "") or content or "").strip()[:100]

    return {
        "type": "pin",
        "id": item.get("id", ""),
        "title": clean_title,
        "excerpt": item.get("excerpt", ""),
        "content_text": content,
        "url": f"https://www.zhihu.com/pin/{item.get('id', '')}",
        "created_time": fmt_time(item.get("created")),
        "voteup_count": item.get("voteup_count", 0),
        "comment_count": item.get("comment_count", 0),
    }


def _parse_pin_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_member_pin(item)


def fetch_member_pins(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a member's pins (想法) list."""
    url = (
        f"{MEMBER_API.format(token=url_token)}/pins"
        f"?include=data%5B%2A%5D.excerpt%2Cdata%5B%2A%5D.content"
        f"%2Cdata%5B%2A%5D.voteup_count%2Cdata%5B%2A%5D.comment_count"
        f"&offset=0&limit={limit}&sort_by=created"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_pin_list):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── question list ───────────────────────────────────────────────────────────


def _parse_member_question(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "question",
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "excerpt": item.get("excerpt", ""),
        "url": f"https://www.zhihu.com/question/{item.get('id', '')}",
        "created_time": fmt_time(item.get("created")),
        "answer_count": item.get("answer_count", 0),
        "follower_count": item.get("follower_count", 0),
        "comment_count": item.get("comment_count", 0),
    }


def _parse_question_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_member_question(item)


def fetch_member_questions(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch questions a member has asked.

    Returns an empty list if the endpoint is unavailable.
    """
    url = f"{MEMBER_API.format(token=url_token)}/questions?include=data%5B%2A%5D.excerpt&offset=0&limit={limit}"
    items: list[dict[str, Any]] = []
    try:
        for item in stream_handler(url, _parse_question_list):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
    except Exception:
        pass
    return items


# ── activity feed ──────────────────────────────────────────────────────────

# Verbs that represent content creation (not votes, follows, etc.)
_CREATION_VERBS = frozenset(
    {
        "MEMBER_CREATE_PIN",
        "MEMBER_ANSWER_QUESTION",
        "MEMBER_CREATE_ARTICLE",
        "MEMBER_ASK_QUESTION",
        "MEMBER_CREATE_QUESTION",
    }
)


def _parse_activity_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a raw v3 moments/activities item into a structured dict.

    The v3 API returns items with ``verb``, ``action_text``, ``target``, ``actor``,
    and ``created_time``.  This normalizes them into a flat dict suitable for both
    display and JSON output.
    """
    verb = raw.get("verb", "")
    target = raw.get("target", {})
    actor = raw.get("actor", {})

    # Determine target type from verb or target structure
    target_type = ""
    if target.get("type") == "pin":
        target_type = "pin"
    elif target.get("type") == "answer":
        target_type = "answer"
    elif target.get("type") == "article":
        target_type = "article"
    elif target.get("type") == "question":
        target_type = "question"

    # Extract title (strip HTML tags for clean display)
    title = target.get("title", "") or target.get("excerpt_title", "")
    if not title and target.get("question"):
        title = target["question"].get("title", "")
    if isinstance(title, str):
        title = re.sub(r"<[^>]+>", "", title).strip()

    # Build URL (strip query params like ?native=0)
    url = target.get("url", "")
    if url and url.startswith("https://api.zhihu.com/"):
        # Convert API URLs to web URLs
        url = url.replace("https://api.zhihu.com/answers/", "https://www.zhihu.com/question/")
        if target_type == "answer" and target.get("question", {}).get("id"):
            url = f"https://www.zhihu.com/question/{target['question']['id']}/answer/{target.get('id', '')}"
        elif target_type == "article":
            url = f"https://zhuanlan.zhihu.com/p/{target.get('id', '')}"
        elif target_type == "pin":
            url = f"https://www.zhihu.com/pin/{target.get('id', '')}"
        elif target_type == "question":
            url = f"https://www.zhihu.com/question/{target.get('id', '')}"
    # Strip query params from web URLs
    if url and "?" in url:
        url = url.split("?")[0]

    # Excerpt
    excerpt = target.get("excerpt", "") or target.get("excerpt_new", "")
    if isinstance(excerpt, str):
        excerpt = re.sub(r"<[^>]+>", "", excerpt).strip()[:200]

    return {
        "id": raw.get("id", ""),
        "verb": verb,
        "action_text": raw.get("action_text", ""),
        "is_creation": verb in _CREATION_VERBS,
        "is_sticky": raw.get("is_sticky", False),
        "target_type": target_type,
        "target_id": target.get("id", ""),
        "title": title,
        "url": url,
        "excerpt": excerpt,
        "voteup_count": target.get("voteup_count", 0),
        "comment_count": target.get("comment_count", 0),
        "favorite_count": target.get("favlists_count", target.get("favorite_count", 0)),
        "created_time": fmt_time(raw.get("created_time")),
        "created_ts": raw.get("created_time", 0),
        # Actor info
        "actor_name": actor.get("name", ""),
        "actor_url_token": actor.get("url_token", ""),
    }


def _parse_activity_list(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_activity_item(item)


def fetch_member_activities(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
    creations_only: bool = False,
) -> list[dict[str, Any]]:
    """Fetch a member's activity feed from the v3 moments API.

    :param url_token: The user's url_token.
    :param limit: Page size for each API request.
    :param max_items: Maximum total items to return (None = follow pagination).
    :param creations_only: If True, only return content-creation activities
        (answers, pins, articles, questions), filtering out votes, follows, etc.
    """
    url = f"https://www.zhihu.com/api/v3/moments/{url_token}/activities?limit={limit}&desktop=True"
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_activity_list):
        if creations_only and not item["is_creation"]:
            continue
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def get_my_url_token():
    try:
        resp = session.get("https://www.zhihu.com/api/v4/me")
        if resp.status_code == 200:
            me = resp.json()
            return me.get("url_token") or me.get("urlToken")
    except Exception:
        pass
    return None

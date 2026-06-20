import re
from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session
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


def fetch_member_profile(url_token: str) -> dict[str, Any] | None:
    """Fetch a member's public profile info (via HTML js-initialData)."""
    try:
        entities = get_page_state(fetch_page_html(f"https://www.zhihu.com/people/{url_token}"))
    except Exception:
        return None

    users = entities.get("users", {})
    # Find the user entry by url_token (keys can be id or url_token)
    user_data = users.get(url_token)
    if user_data is None:
        for v in users.values():
            if isinstance(v, dict) and v.get("urlToken") == url_token:
                user_data = v
                break
    if user_data is None:
        return None

    return {
        "id": user_data.get("id", ""),
        "name": user_data.get("name", ""),
        "url_token": user_data.get("urlToken", url_token),
        "headline": user_data.get("headline", ""),
        "avatar_url": (user_data.get("avatarUrlTemplate") or "").replace("{size}", "xl"),
        "gender": user_data.get("gender", -1),
        "follower_count": user_data.get("followerCount", 0),
        "following_count": user_data.get("followingCount", 0),
        "answer_count": user_data.get("answerCount", 0),
        "articles_count": user_data.get("articlesCount", 0),
        "pins_count": user_data.get("pinsCount", 0),
        "question_count": user_data.get("questionCount", 0),
        "voteup_count": user_data.get("voteupCount", 0),
        "thanked_count": user_data.get("thankedCount", 0),
        "description": user_data.get("description", ""),
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
    url = f"{MEMBER_API.format(token=url_token)}/answers?include=data%5B%2A%5D.excerpt&offset=0&limit={limit}&sort_by=created"
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
    url = f"{MEMBER_API.format(token=url_token)}/articles?include=data%5B%2A%5D.excerpt&offset=0&limit={limit}&sort_by=created"
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
    url = f"{MEMBER_API.format(token=url_token)}/pins?include=data%5B%2A%5D.excerpt%2Ccontent&offset=0&limit={limit}&sort_by=created"
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


def get_my_url_token():
    try:
        resp = session.get("https://www.zhihu.com/api/v4/me")
        if resp.status_code == 200:
            me = resp.json()
            return me.get("url_token") or me.get("urlToken")
    except Exception:
        pass
    return None

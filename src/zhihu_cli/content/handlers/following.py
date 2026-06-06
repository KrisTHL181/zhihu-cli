"""Fetch Zhihu following lists (followees, topics, questions, columns, collections)."""

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.waterfall import stream_handler

MEMBER_API = "https://www.zhihu.com/api/v4/members/{token}"
TOPIC_FOLLOWING_API = "https://www.zhihu.com/api/v5.1/topics/{token}/following_topics_contributions"


# ── followees (关注的用户) ────────────────────────────────────────────────────


def _parse_followee(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "user",
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "url_token": item.get("url_token", ""),
        "headline": item.get("headline", ""),
        "avatar_url": (item.get("avatar_url_template") or "").replace("{size}", "xl"),
        "gender": item.get("gender", -1),
        "follower_count": item.get("follower_count", 0),
        "answer_count": item.get("answer_count", 0),
        "articles_count": item.get("articles_count", 0),
        "is_following": item.get("is_following", False),
        "is_followed": item.get("is_followed", False),
        "url": f"https://www.zhihu.com/people/{item.get('url_token', '')}",
    }


def _parse_followees(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_followee(item)


def fetch_followees(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    url = (
        f"{MEMBER_API.format(token=url_token)}/followees"
        "?include=data%5B*%5D.answer_count%2Carticles_count%2Cgender"
        "%2Cfollower_count%2Cis_followed%2Cis_following"
        "%2Cbadge%5B%3F(type%3Dbest_answerer)%5D.topics"
        f"&offset=0&limit={limit}"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_followees):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── followers (关注者/粉丝) ────────────────────────────────────────────────────


def fetch_followers(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a user's followers (people who follow them)."""
    url = (
        f"{MEMBER_API.format(token=url_token)}/followers"
        "?include=data%5B*%5D.answer_count%2Carticles_count%2Cgender"
        "%2Cfollower_count%2Cis_followed%2Cis_following"
        "%2Cbadge%5B%3F(type%3Dbest_answerer)%5D.topics"
        f"&offset=0&limit={limit}"
    )
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_followees):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── following topics (关注的话题) ──────────────────────────────────────────────


def _parse_following_topic(item: dict[str, Any]) -> dict[str, Any]:
    topic = item.get("topic", {})
    return {
        "type": "topic",
        "id": topic.get("id", ""),
        "name": topic.get("name", ""),
        "introduction": topic.get("introduction", ""),
        "excerpt": topic.get("excerpt", ""),
        "followers_count": topic.get("followers_count", 0),
        "questions_count": topic.get("questions_count", 0),
        "discuss_count": topic.get("discuss_count", 0),
        "is_following": topic.get("is_following", True),
        "url": f"https://www.zhihu.com/topic/{topic.get('id', '')}",
        "avatar_url": topic.get("avatar_url", ""),
        "contributions_count": item.get("contributions_count", 0),
    }


def _parse_following_topics(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_following_topic(item)


def fetch_following_topics(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    url = f"{TOPIC_FOLLOWING_API.format(token=url_token)}?include=data%5B*%5D.topic.introduction&offset=0&limit={limit}"
    items: list[dict[str, Any]] = []
    try:
        for item in stream_handler(url, _parse_following_topics):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
    except Exception:
        pass
    return items


# ── following questions (关注的问题) ───────────────────────────────────────────


def _parse_following_question(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "question",
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "excerpt": item.get("excerpt", ""),
        "answer_count": item.get("answer_count", 0),
        "follower_count": item.get("follower_count", 0),
        "comment_count": item.get("comment_count", 0),
        "created_time": fmt_time(item.get("created")),
        "updated_time": fmt_time(item.get("updated_time")),
        "url": f"https://www.zhihu.com/question/{item.get('id', '')}",
    }


def _parse_following_questions(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_following_question(item)


def fetch_following_questions(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    url = f"{MEMBER_API.format(token=url_token)}/following-questions?offset=0&limit={limit}"
    items: list[dict[str, Any]] = []
    try:
        for item in stream_handler(url, _parse_following_questions):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
    except Exception:
        pass
    return items


# ── following columns (关注的专栏) ─────────────────────────────────────────────


def _parse_following_column(item: dict[str, Any]) -> dict[str, Any]:
    col = item.get("column", item)
    return {
        "type": "column",
        "id": col.get("id", ""),
        "title": col.get("title", ""),
        "description": col.get("description", ""),
        "excerpt": col.get("excerpt", ""),
        "followers_count": col.get("followers", 0),
        "articles_count": col.get("articles_count", 0) or col.get("posts_count", 0),
        "is_following": col.get("is_following", True),
        "url": f"https://zhuanlan.zhihu.com/{col.get('id', '')}",
        "creator": col.get("creator", {}).get("name", ""),
        "avatar_url": (col.get("image_url") or col.get("avatar_url", "")),
    }


def _parse_following_columns(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_following_column(item)


def fetch_following_columns(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    url = f"{MEMBER_API.format(token=url_token)}/following-columns?offset=0&limit={limit}"
    items: list[dict[str, Any]] = []
    try:
        for item in stream_handler(url, _parse_following_columns):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
    except Exception:
        pass
    return items


# ── following collections (关注的收藏夹) ──────────────────────────────────────


def _parse_following_collection(item: dict[str, Any]) -> dict[str, Any]:
    creator = item.get("creator", {})
    return {
        "type": "collection",
        "id": str(item.get("id", "")),
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "answer_count": item.get("answer_count", 0),
        "follower_count": item.get("follower_count", 0),
        "comment_count": item.get("comment_count", 0),
        "is_following": item.get("is_following", True),
        "is_public": item.get("is_public", True),
        "creator_name": creator.get("name", ""),
        "creator_url_token": creator.get("url_token", ""),
        "created_time": fmt_time(item.get("created_time")),
        "updated_time": fmt_time(item.get("updated_time")),
        "url": f"https://www.zhihu.com/collection/{item.get('id', '')}",
    }


def _parse_following_collections(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        yield _parse_following_collection(item)


def fetch_following_collections(
    url_token: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    url = (
        f"{MEMBER_API.format(token=url_token)}/following-favlists"
        "?include=data%5B*%5D.updated_time%2Canswer_count%2Cfollower_count"
        "%2Ccreator%2Cdescription%2Cis_following%2Ccomment_count%2Ccreated_time"
        f"&offset=0&limit={limit}"
    )
    items: list[dict[str, Any]] = []
    try:
        for item in stream_handler(url, _parse_following_collections):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
    except Exception:
        pass
    return items

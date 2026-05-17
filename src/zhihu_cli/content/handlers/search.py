"""Search Zhihu via the search_v3 API."""

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from zhihu_cli.content.handlers.feed import _parse_article_target as feed_parse_article
from zhihu_cli.content.handlers.feed import _parse_author
from zhihu_cli.content.handlers.question import parse_question_metadata
from zhihu_cli.content.handlers.waterfall import stream_handler

SEARCH_URL = "https://www.zhihu.com/api/v4/search_v3"


def replace_em(text: str) -> str:
    """Replace <em> tags with terminal color codes."""
    text = re.sub(r"<em>", "\033[1;31m", text)
    return re.sub(r"</em>", "\033[0m", text)


def _parse_question(item: dict[str, Any]) -> dict[str, Any]:
    """Adapt search API question object → parse_question_metadata."""
    obj = item.get("object", {})
    adapted = {
        "id": obj.get("id", ""),
        "title": obj.get("title", ""),
        "url": f"https://www.zhihu.com/question/{obj.get('id', '')}",
        "created": obj.get("created_time", 0),
        "updatedTime": obj.get("updated_time", 0),
        "answerCount": obj.get("answer_count", 0),
        "commentCount": obj.get("comment_count", 0),
        "visitCount": obj.get("visits_count", 0),
        "followerCount": obj.get("follower_count", 0),
        "author": {},
    }
    result = parse_question_metadata(adapted)
    result["type"] = "question"
    result["description"] = obj.get("description", "")
    return result


def _parse_article(item: dict[str, Any]) -> dict[str, Any]:
    """Adapt search API article object → feed._parse_article_target."""
    obj = item.get("object", {})
    adapted = {
        "id": obj.get("id", ""),
        "title": obj.get("title", ""),
        "excerpt": obj.get("excerpt", ""),
        "url": obj.get("url", ""),
        "author": obj.get("author", {}),
        "created": obj.get("created_time", 0),
        "updated": obj.get("updated_time", 0),
        "voteup_count": obj.get("voteup_count", 0),
        "comment_count": obj.get("comment_count", 0),
    }
    return feed_parse_article(adapted)


def _parse_user(item: dict[str, Any]) -> dict[str, Any]:
    """Adapt search API user object → _parse_author + extra fields."""
    obj = item.get("object", {})
    gender = {-1: "Unknown", 0: "Female", 1: "Male"}.get(obj.get("gender"), "Unknown")
    result = _parse_author(obj)
    result.update(
        {
            "type": "user",
            "id": obj.get("id", ""),
            "url": f"https://www.zhihu.com/people/{obj.get('url_token', '')}",
            "gender": gender,
            "follower_count": obj.get("follower_count", 0),
            "answer_count": obj.get("answer_count", 0),
            "articles_count": obj.get("articles_count", 0),
            "voteup_count": obj.get("voteup_count", 0),
            "is_following": obj.get("is_following", False),
        }
    )
    return result


def _parse_topic(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a topic search result. No existing parser for topics."""
    obj = item.get("object", {})
    return {
        "type": "topic",
        "id": obj.get("id", ""),
        "name": obj.get("name", ""),
        "introduction": obj.get("introduction", ""),
        "excerpt": obj.get("excerpt", ""),
        "url": f"https://www.zhihu.com/topic/{obj.get('id', '')}",
        "avatar_url": obj.get("avatar_url", ""),
        "questions_count": obj.get("questions_count", 0),
        "followers_count": obj.get("followers_count", 0),
        "visit_count": obj.get("visit_count", 0),
        "is_following": obj.get("is_following", False),
        "topic_type": obj.get("topic_type", ""),
    }


def _make_parser(item_parser):
    """Build a search result parser from an item-level parser."""

    def parser(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for item in data.get("data", []):
            yield item_parser(item)

    return parser


def _build_search(
    query: str,
    search_type: str,
    item_parser,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Shared search: build URL, stream pages, parse items, return list."""
    encoded_query = quote(query)
    url = f"{SEARCH_URL}?t={search_type}&q={encoded_query}&offset=0&limit={limit}&search_source=Normal"
    parser = _make_parser(item_parser)
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, parser, delay=1.0):
        if item.get("name"):
            item["name"] = replace_em(item["name"])
        if item.get("title"):
            item["title"] = replace_em(item["title"])
        if item.get("excerpt"):
            item["excerpt"] = replace_em(item["excerpt"])
        if item.get("description"):
            item["description"] = replace_em(item["description"])
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def search_questions(
    query: str,
    limit: int = 20,
    max_items: int | None = 20,
) -> list[dict[str, Any]]:
    """Search Zhihu questions."""
    return _build_search(query, "question", _parse_question, limit, max_items)


def search_articles(
    query: str,
    limit: int = 20,
    max_items: int | None = 20,
) -> list[dict[str, Any]]:
    """Search Zhihu articles via general search filtered by article type."""
    encoded_query = quote(query)
    url = f"{SEARCH_URL}?t=general&q={encoded_query}&offset=0&limit={limit}&search_source=Normal"
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _make_parser(_parse_article), delay=1.0):
        if item.get("target_type") != "article":
            continue
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def search_users(
    query: str,
    limit: int = 20,
    max_items: int | None = 20,
) -> list[dict[str, Any]]:
    """Search Zhihu users."""
    return _build_search(query, "people", _parse_user, limit, max_items)


def search_topics(
    query: str,
    limit: int = 20,
    max_items: int | None = 20,
) -> list[dict[str, Any]]:
    """Search Zhihu topics."""
    return _build_search(query, "topic", _parse_topic, limit, max_items)

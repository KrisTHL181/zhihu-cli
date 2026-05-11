"""Zhihu feeds (推荐/关注) handler."""

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.waterfall import stream_handler

RECOMMEND_URL = "https://www.zhihu.com/api/v3/feed/topstory/recommend?limit=20&desktop=true"
FOLLOW_URL = "https://www.zhihu.com/api/v3/moments?limit=20&desktop=true"


def _parse_author(author: dict[str, Any] | None) -> dict[str, Any]:
    if not author:
        return {"name": "匿名用户", "url_token": "", "headline": ""}
    return {
        "name": author.get("name", "匿名用户"),
        "url_token": author.get("url_token", ""),
        "headline": author.get("headline", ""),
        "avatar_url": author.get("avatar_url", ""),
    }


def _parse_answer_target(target: dict[str, Any]) -> dict[str, Any]:
    question = target.get("question", {})
    return {
        "target_type": "answer",
        "id": target.get("id", ""),
        "question_id": question.get("id", ""),
        "question_title": question.get("title", ""),
        "title": question.get("title", ""),
        "excerpt": target.get("excerpt", ""),
        "content_html": target.get("content", ""),
        "url": target.get("url", ""),
        "author": _parse_author(target.get("author")),
        "created_time": fmt_time(target.get("created_time")),
        "updated_time": fmt_time(target.get("updated_time")),
        "voteup_count": target.get("voteup_count", 0),
        "comment_count": target.get("comment_count", 0),
        "thanks_count": target.get("thanks_count", 0),
        "visited_count": target.get("visited_count", 0),
        "favorite_count": target.get("favorite_count", 0),
    }


def _parse_article_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_type": "article",
        "id": target.get("id", ""),
        "title": target.get("title", "无标题"),
        "excerpt": target.get("excerpt", ""),
        "content_html": target.get("content", ""),
        "url": target.get("url", ""),
        "author": _parse_author(target.get("author")),
        "created_time": fmt_time(target.get("created")),
        "updated_time": fmt_time(target.get("updated")),
        "voteup_count": target.get("voteup_count", 0),
        "comment_count": target.get("comment_count", 0),
    }


def _parse_pin_target(target: dict[str, Any]) -> dict[str, Any]:
    content = target.get("content", "")
    if isinstance(content, list):
        content = "\n".join(block.get("content", "") for block in content)
    return {
        "target_type": "pin",
        "id": target.get("id", ""),
        "excerpt": target.get("excerpt", ""),
        "content_html": content,
        "url": target.get("url", ""),
        "author": _parse_author(target.get("author")),
        "created_time": fmt_time(target.get("created")),
        "updated_time": fmt_time(target.get("updated_time")),
        "voteup_count": target.get("voteup_count", 0),
        "comment_count": target.get("comment_count", 0),
    }


def _parse_question_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_type": "question",
        "id": target.get("id", ""),
        "title": target.get("title", ""),
        "excerpt": target.get("excerpt", ""),
        "detail_html": target.get("detail", ""),
        "url": target.get("url", ""),
        "author": _parse_author(target.get("author")),
        "created_time": fmt_time(target.get("created")),
        "answer_count": target.get("answer_count", 0),
        "comment_count": target.get("comment_count", 0),
        "follower_count": target.get("follower_count", 0),
    }


def parse_feed_item(item: dict[str, Any]) -> dict[str, Any] | None:
    target = item.get("target", {})
    ttype = target.get("type", "")

    if not ttype and not target:
        return None

    verb = item.get("verb", "")
    parsed = {
        "verb": verb,
        "feed_id": item.get("id", ""),
    }

    if ttype == "answer":
        parsed.update(_parse_answer_target(target))
    elif ttype == "article":
        parsed.update(_parse_article_target(target))
    elif ttype == "pin":
        parsed.update(_parse_pin_target(target))
    elif ttype == "question":
        parsed.update(_parse_question_target(target))
    else:
        parsed["target_type"] = ttype
        parsed["target"] = target

    return parsed


def _parse_feed_items(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in data.get("data", []):
        parsed = parse_feed_item(item)
        if parsed is not None:
            yield parsed


def stream_recommend_feed(limit: int = 20) -> Iterable[dict[str, Any]]:
    url = f"https://www.zhihu.com/api/v3/feed/topstory/recommend?limit={limit}&desktop=true"
    return stream_handler(url, _parse_feed_items)


def stream_follow_feed(limit: int = 20) -> Iterable[dict[str, Any]]:
    url = f"https://www.zhihu.com/api/v3/moments?limit={limit}&desktop=true"
    return stream_handler(url, _parse_feed_items)


def fetch_feed(feed_type: str = "recommend", limit: int = 20, max_items: int = 0) -> list[dict[str, Any]]:
    stream = stream_recommend_feed(limit) if feed_type == "recommend" else stream_follow_feed(limit)
    items = []
    for item in stream:
        items.append(item)
        if max_items and len(items) >= max_items:
            break
    return items


def fetch_feed_with_markdown(feed_type: str = "recommend", limit: int = 20, max_items: int = 0) -> list[dict[str, Any]]:
    from zhihu_cli.content.utils.html2markdown import converter

    items = fetch_feed(feed_type, limit, max_items)
    for item in items:
        html = item.pop("content_html", "")
        if html and isinstance(html, str):
            item["content_markdown"] = converter.convert(html)
    return items


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Zhihu feed streamer")
    parser.add_argument("--type", choices=["recommend", "follow"], default="recommend", help="Feed type")
    parser.add_argument("--limit", type=int, default=20, help="Items per page")
    parser.add_argument("--max", type=int, default=0, dest="max_items", help="Max total items (0=unlimited)")
    parser.add_argument("--output", "-o", type=str, default="", help="Output JSON file")
    parser.add_argument("--markdown", action="store_true", help="Convert HTML content to Markdown")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print items while fetching")
    args = parser.parse_args()

    fetch_fn = fetch_feed_with_markdown if args.markdown else fetch_feed
    items = fetch_fn(args.type, args.limit, args.max_items or 0)

    for item in items:
        if args.verbose:
            title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
            author = item.get("author", {}).get("name", "未知")
            print(f"[{item['target_type']}] {title[:100]}")
            print(f"  author={author}  votes={item.get('voteup_count', 0)}  verb={item['verb']}")
            print()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(items)} items to {args.output}")
    else:
        print(f"Fetched {len(items)} items (use --output to save, --verbose to print)")

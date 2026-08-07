import re
from typing import Any
from urllib.parse import urlparse

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_json
from zhihu_cli.content.utils.html2markdown import converter

ARTICLE_API_URL = "https://www.zhihu.com/api/v4/articles/{article_id}"
_ARTICLE_PATH_RE = re.compile(r"/(?:p|articles)/(\d+)/?$")


def extract_article_id(article_url: str) -> str:
    """Extract a numeric article ID from a Zhihu article URL or raw ID."""
    candidate = article_url.strip()
    if candidate.isdigit():
        return candidate

    match = _ARTICLE_PATH_RE.search(urlparse(candidate).path)
    if match:
        return match.group(1)

    raise ValueError(f"Could not extract a Zhihu article ID from {article_url!r}")


def fetch_article_item(article_url: str) -> dict[str, Any]:
    """Fetch raw article data from the JSON API."""
    article_id = extract_article_id(article_url)
    api_url = ARTICLE_API_URL.format(article_id=article_id)

    item = fetch_json(api_url)
    if "content" not in item:
        raise ValueError("Article API response does not contain content")
    return item


def parse_article_metadata(item: dict[str, Any]) -> dict[str, Any]:
    article_id = item.get("id", "")
    title = item.get("title", "untitled")
    excerpt = item.get("excerpt", "")
    content_preview = excerpt or (item.get("content", "")[:200] if item.get("content") else "")

    # Stats
    voteup_count = item.get("voteup_count", item.get("voteupCount", 0))
    comment_count = item.get("comment_count", item.get("commentCount", 0))
    favlists_count = item.get("favlists_count", item.get("favlistsCount", 0))

    # Timestamps
    created = item.get("created", 0)
    updated = item.get("updated", 0)

    # Author info
    author = item.get("author", {})
    author_name = author.get("name", "unknown")

    # Article URL
    url = item.get("url", "")
    if article_id and (not url or urlparse(url).netloc == "api.zhihu.com"):
        url = f"https://zhuanlan.zhihu.com/p/{article_id}"

    return {
        "id": article_id,
        "title": title,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {"voteup_count": voteup_count, "comment_count": comment_count, "favlists_count": favlists_count},
        "author": {
            "name": author_name,
            "headline": author.get("headline", ""),
        },
        "comment_permission": item.get("comment_permission", ""),
    }


def scrape_article(article_url: str) -> tuple[dict[str, Any], str]:
    item_data = fetch_article_item(article_url)
    return parse_article_metadata(item_data), converter.convert(item_data["content"])

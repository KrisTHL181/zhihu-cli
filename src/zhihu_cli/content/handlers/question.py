import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_json, fetch_page_html, get_page_state, session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import converter

ANSWER_FEEDS_URL = (
    "https://api.zhihu.com/questions/{question_id}/feeds"
    "?include=content,big_card_summary,media_detail,reaction_instruction,is_author,is_thanked,"
    "voting,is_favorited,label_info,content_text_length,reactions"
    "&order=default&show_detail=1"
)

QUESTION_API_URL = (
    "https://api.zhihu.com/questions/{question_id}"
    "?include=detail,answer_count,comment_count,follower_count,author,"
    "voteup_count,voting,can_vote,visit_count,relationship"
)
_QUESTION_PATH_RE = re.compile(r"/(?:question|questions)/(\d+)/?$")


def extract_question_id(question_url: str) -> str:
    """Extract a numeric question ID from a Zhihu question URL or raw ID."""
    candidate = question_url.strip()
    if candidate.isdigit():
        return candidate

    match = _QUESTION_PATH_RE.search(urlparse(candidate).path)
    if match:
        return match.group(1)

    raise ValueError(f"Could not extract a Zhihu question ID from {question_url!r}")


def parse_question_metadata(item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("author", {})
    question_id = item.get("id", "")
    url = item.get("url", "")
    if question_id and (not url or urlparse(url).netloc == "api.zhihu.com"):
        url = f"https://www.zhihu.com/question/{question_id}"

    return {
        "id": question_id,
        "title": item.get("title", ""),
        "url": url,
        "created_time": fmt_time(item.get("created", 0)),
        "updated_time": fmt_time(item.get("updated_time", item.get("updatedTime", 0))),
        "answer_count": item.get("answer_count", item.get("answerCount", 0)),
        "comment_count": item.get("comment_count", item.get("commentCount", 0)),
        "visit_count": item.get("visit_count", item.get("visitCount", 0)),
        "follower_count": item.get("follower_count", item.get("followerCount", 0)),
        "voteup_count": item.get("voteup_count", item.get("voteupCount", 0)),
        "author": {
            "name": author.get("name", "anonymous"),
            "headline": author.get("headline", ""),
        },
    }


def fetch_question_item(question_url: str) -> dict[str, Any]:
    """Fetch raw question data from the JSON API."""
    question_id = extract_question_id(question_url)
    api_url = QUESTION_API_URL.format(question_id=question_id)

    item = fetch_json(api_url)
    if not isinstance(item, dict):
        raise ValueError("Question API returned a non-object response")
    if "error" in item:
        raise ValueError(f"Question API returned an error: {item.get('error')}")
    if "title" not in item:
        raise ValueError("Question API response does not contain question data")
    return item


def scrape_question_data(question_url: str) -> tuple[dict[str, Any], str]:
    """Fetch a question's metadata and detail (as Markdown)."""
    item_data = fetch_question_item(question_url)
    return parse_question_metadata(item_data), converter.convert(item_data.get("detail") or "")


def scrape_answers(
    question_data: dict[str, Any],
    raw: bool = False,
    limit: int = 5,
    max_items: int | None = None,
) -> Iterable[dict[str, Any]]:
    """Yield answers for a question, one dict per answer.

    :param question_data: dict with at least an ``"id"`` key (the question ID).
    :param raw: when True, return raw HTML in the ``"content"`` field
        instead of converting to Markdown.
    :param limit: number of answers requested per API page.
    :param max_items: optional cap on the total number of answers yielded
        (stops pagination early when reached).
    """
    url = ANSWER_FEEDS_URL.replace("{question_id}", str(question_data["id"]))

    def parse_ans(data):
        for item in data.get("data", []):
            # /feeds endpoint wraps answers in a "target" field
            ans = item.get("target", item)
            if not isinstance(ans, dict) or not ans.get("id"):
                continue
            content = ans.get("content", "")
            if not raw:
                content = converter.convert(content)
            yield {
                "author": ans.get("author", {}).get("name", "anonymous"),
                "id": str(ans.get("id", ans.get("url", "/unknown").split("/")[-1])),
                "vote": ans.get("voteup_count", 0),
                "comment": ans.get("comment_count", 0),
                "favorite": ans.get("favlists_count", 0),
                "created_time": int(ans.get("created_time", 0) or 0),
                "content": content,
            }

    return stream_handler(url, parse_ans, limit=limit, max_items=max_items)


def upvote_answer(answer_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "up"})
    return resp.json()


def neutral_answer(answer_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "neutral"})
    return resp.json()


def downvote_answer(answer_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "down"})
    return resp.json()


def thank_answer(answer_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()


def unthank_answer(answer_id: str) -> dict[str, Any]:
    resp = session.delete(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()


def upvote_question(question_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters/up")
    return resp.json()


def unvote_question(question_id: str) -> dict[str, Any]:
    resp = session.delete(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters")
    return resp.json()


def downvote_question(question_id: str) -> dict[str, Any]:  # undocumented endpoint!
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters/down")
    return resp.json()


def follow_question(question_id: str) -> dict[str, Any]:
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/followers")
    return resp.json()


def unfollow_question(question_id: str) -> dict[str, Any]:
    resp = session.delete(f"https://www.zhihu.com/api/v4/questions/{question_id}/followers")
    return resp.json()


def scrape_answer_page(answer_url: str, raw: bool = False) -> tuple[dict[str, Any], str]:
    """Scrape full content from a single answer page URL.

    :param answer_url: full URL to a Zhihu answer page.
    :param raw: when True, return raw HTML instead of converting to Markdown.
    :returns: (metadata, content) — content is Markdown by default, raw HTML if ``raw=True``.
    """
    entities = get_page_state(fetch_page_html(answer_url))

    answers = entities.get("answers", {})
    if not answers:
        raise ValueError(f"No answer data found in {answer_url}")
    answer_data = next(iter(answers.values()))

    question_data = {}
    questions = entities.get("questions", {})
    question_title = "untitled"
    if questions:
        question_data = next(iter(questions.values()))
        question_title = question_data.get("title", "untitled")

    users = entities.get("users", {})
    author_ref = answer_data.get("author", "")
    author_name = "unknown"
    if author_ref:
        if isinstance(author_ref, str) and author_ref in users:
            author_name = users[author_ref].get("name", "unknown")
        elif isinstance(author_ref, dict):
            author_name = author_ref.get("name", "unknown")

    # Resolve created date from entity timestamps
    created_ts = answer_data.get("created_time") or answer_data.get("created") or answer_data.get("createdTime")
    if not created_ts:
        created_ts = question_data.get("created")
    created_date = "unknown"
    if created_ts:
        try:
            if isinstance(created_ts, (int, float)) and created_ts > 1e12:
                created_ts = created_ts / 1000
            created_date = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass

    metadata = {
        "id": str(answer_data.get("id", "")),
        "title": question_title,
        "author": author_name,
        "vote": answer_data.get("voteupCount", 0),
        "comment": answer_data.get("commentCount", 0),
        "favorite": answer_data.get("favlistsCount", 0),
        "created": created_date,
    }

    content_html = answer_data.get("content", "")
    content = content_html if raw else converter.convert(content_html)

    return metadata, content

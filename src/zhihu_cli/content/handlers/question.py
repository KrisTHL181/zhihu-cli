from collections.abc import Generator
from datetime import datetime
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import converter

NEXT_URL_API = "https://www.zhihu.com/api/v4/questions/{question_id}/feeds?include=data%5B%2A%5D.is_normal%2Ccontent%2Cvoteup_count%2Ccomment_count%2Cfavlists_count%2Ccreated_time%2Cauthor.name%2Cauthor.follower_count&limit=5&offset=0&order=default&platform=desktop"


def parse_question_metadata(item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("author", {})

    return {
        "id": item["id"],
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "created_time": fmt_time(item.get("created", 0)),
        "updated_time": fmt_time(item.get("updatedTime", 0)),
        "answer_count": item.get("answerCount", 0),
        "comment_count": item.get("commentCount", 0),
        "visit_count": item.get("visitCount", 0),
        "follower_count": item.get("followerCount", 0),
        "author": {
            "name": author.get("name", "anonymous"),
            "headline": author.get("headline", ""),
        },
    }


def scrape_question_data(question_url: str) -> tuple[dict[str, Any], str]:
    entities = get_page_state(fetch_page_html(question_url))
    item = entities.get("questions", {})
    if not item:
        raise ValueError(f"No {item} data found in entities")

    item_data = next(iter(item.values()))
    return parse_question_metadata(item_data), converter.convert(item_data.get("detail"))


def scrape_answers(question_data: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    url = NEXT_URL_API.replace("{question_id}", question_data["id"])

    def parse_ans(data):
        for item in data.get("data", []):
            # /feeds endpoint wraps answers in a "target" field
            ans = item.get("target", item)
            yield {
                "author": ans.get("author", {}).get("name", "anonymous"),
                "id": str(ans.get("id", ans.get("url", "/unknown").split("/")[-1])),
                "vote": ans.get("voteup_count", 0),
                "comment": ans.get("comment_count", 0),
                "favorite": ans.get("favlists_count", 0),
                "created_time": ans.get("created_time", 0),
                "content": converter.convert(ans.get("content", "")),
            }

    return stream_handler(url, parse_ans)


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


def scrape_answer_page(answer_url: str) -> tuple[dict[str, Any], str]:
    """Scrape full content from a single answer page URL.

    Returns (metadata, markdown_content).
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
    markdown = converter.convert(content_html)

    return metadata, markdown

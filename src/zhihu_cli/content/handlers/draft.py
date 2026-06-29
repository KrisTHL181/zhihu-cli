"""Draft history listing and draft-to-markdown conversion."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from zhihu_cli.content.handlers import fmt_time, get_type_and_id
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import calculate_text_length, converter

DRAFT_HISTORIES_URL = "https://www.zhihu.com/api/v4/draft-histories"
DRAFT_URL = "https://www.zhihu.com/api/v4/draft-history"
ARTICLE_DRAFT_URL = "https://zhuanlan.zhihu.com/api/articles/{article_id}/draft"
ANSWER_DRAFT_URL = "https://www.zhihu.com/api/v4/questions/{question_id}/draft"

ANSWER_DRAFTS_URL = "https://www.zhihu.com/api/v4/answer-drafts"
ARTICLE_DRAFTS_URL = "https://www.zhihu.com/api/v4/articles/my_drafts"
PIN_DRAFTS_URL = "https://www.zhihu.com/api/v4/content/drafts"

ANSWER_DRAFT_SETTINGS: dict[str, Any] = {
    "reshipment_settings": "allowed",
    "columns": None,
    "comment_permission": "all",
    "can_reward": False,
    "tagline": "",
    "disclaimer_status": "close",
    "disclaimer_type": "none",
    "commercial_report_info": {"is_report": False},
    "table_of_contents_enabled": False,
    "thank_inviter_status": "close",
    "thank_inviter": "",
}


def _parse_draft_page(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract draft items from a single page JSON body."""
    return data.get("data", [])


def list_drafts(object_type: str, object_id: str, limit: int = 10) -> list[dict]:
    """List draft histories for a question/answer/article.

    :param object_type: ``"question"``, ``"answer"``, or ``"article"``.
    :param object_id: The Zhihu object ID.
    :param limit: Maximum number of drafts to return (fetches multiple pages if needed).
    :returns: List of draft metadata dicts, newest first.
    """
    page_size = min(limit, 20)
    url = (
        f"{DRAFT_HISTORIES_URL}?"
        f"{urlencode({'object_type': object_type, 'object_id': object_id, 'limit': page_size, 'offset': 0})}"
    )
    drafts: list[dict] = []
    for draft in stream_handler(url, _parse_draft_page, delay=0.3):
        drafts.append(draft)
        if len(drafts) >= limit:
            break
    return drafts


def list_answer_drafts(limit: int = 20) -> list[dict]:
    """List all answer drafts for the current user.

    :param limit: Maximum number of drafts to return.
    :returns: List of draft metadata dicts, newest first.
    """
    page_size = min(limit, 20)
    url = f"{ANSWER_DRAFTS_URL}?{urlencode({'offset': 0, 'limit': page_size, 'include': 'data[*].schedule'})}"
    drafts: list[dict] = []
    for draft in stream_handler(url, _parse_draft_page, delay=0.3):
        drafts.append(draft)
        if len(drafts) >= limit:
            break
    return drafts


def list_article_drafts(limit: int = 20) -> list[dict]:
    """List all article drafts for the current user.

    :param limit: Maximum number of drafts to return.
    :returns: List of draft metadata dicts, newest first.
    """
    page_size = min(limit, 20)
    url = f"{ARTICLE_DRAFTS_URL}?{urlencode({'offset': 0, 'limit': page_size, 'include': 'data[*].schedule'})}"
    drafts: list[dict] = []
    for draft in stream_handler(url, _parse_draft_page, delay=0.3):
        drafts.append(draft)
        if len(drafts) >= limit:
            break
    return drafts


def list_pin_drafts(limit: int = 20) -> list[dict]:
    """List all pin drafts for the current user.

    :param limit: Maximum number of drafts to return.
    :returns: List of draft metadata dicts, newest first.
    """
    page_size = min(limit, 20)
    url = f"{PIN_DRAFTS_URL}?{urlencode({'action': 'pin', 'offset': 0, 'limit': page_size, 'include': 'data[*].schedule'})}"
    drafts: list[dict] = []
    for draft in stream_handler(url, _parse_draft_page, delay=0.3):
        drafts.append(draft)
        if len(drafts) >= limit:
            break
    return drafts


def get_draft(draft_id: str, version_type: str = "current") -> dict:
    """Get a specific draft by ID."""
    url = f"{DRAFT_URL}?{urlencode({'version_type': version_type, 'id': draft_id})}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def draft_to_markdown(url: str, draft_index: int = 0) -> tuple[dict, str]:
    """Convert a draft of a Zhihu item to Markdown.

    Given a Zhihu question/answer URL, fetches the specified draft and
    returns (metadata, markdown).

    :param url: Zhihu URL (question, answer, or article).
    :param draft_index: 0-based index into the draft history (0 = latest,
       1 = previous, etc.). Defaults to 0.
    :returns: ``(metadata_dict, markdown_string)``.
    :raises ValueError: If the URL cannot be parsed, no drafts are found,
        *draft_index* is out of range, or the draft has no content.
    """
    object_type, object_id = get_type_and_id(url)
    if not object_type or not object_id:
        raise ValueError(f"Cannot parse Zhihu URL: {url}")

    type_map = {"questions": "question", "answers": "answer", "articles": "article"}
    api_type = type_map.get(object_type)
    if not api_type:
        raise ValueError(f"Drafts not supported for type: {object_type}")

    # For answers, extract answer_id from composite "question_id/answer_id"
    if object_type == "answers" and "/" in object_id:
        object_id = object_id.split("/")[1]

    drafts = list_drafts(api_type, object_id, limit=draft_index + 1)
    if not drafts:
        raise ValueError(f"No drafts found for: {url}")
    if draft_index >= len(drafts):
        raise ValueError(
            f"Draft index {draft_index} out of range (found {len(drafts)} draft{'s' if len(drafts) > 1 else ''})"
        )

    target = drafts[draft_index]
    draft_detail = get_draft(target["id"], target.get("version_type", "current"))

    html_content = draft_detail.get("draft", {}).get("content", "")
    if not html_content:
        raise ValueError("Draft has no content")

    metadata = {
        "id": draft_detail["id"],
        "created_time": fmt_time(draft_detail.get("created_at", 0)),
        "updated_time": fmt_time(draft_detail.get("updated_at", 0)),
        "version_type": draft_detail.get("version_type", "unknown"),
        "url": url,
        "index": draft_index,
        "total_drafts": len(drafts),
    }

    return metadata, converter.convert(html_content)


def upload_draft(content_type: str, object_id: str, html_content: str) -> dict[str, Any]:
    """Upload a draft to Zhihu.

    :param content_type: ``"article"`` or ``"answer"``.
    :param object_id: Article ID (for articles) or Question ID (for answers).
    :param html_content: HTML content to upload as the draft body.
    :returns: API response dict.
    :raises ValueError: If *content_type* is unrecognised.
    :raises requests.HTTPError: If the API call fails.
    """
    new_length = calculate_text_length(html_content)

    if content_type == "article":
        drafts = list_drafts("article", object_id)
        if drafts:
            old_draft = get_draft(drafts[0]["id"], drafts[0].get("version_type", "current")).get("draft", {})
            # Prefer server-provided content_words over client-side computation
            old_length = old_draft.get("content_words")
            if old_length is None:
                old_html = old_draft.get("content", "")
                old_length = calculate_text_length(old_html)
            delta_time = new_length - old_length
        else:
            delta_time = new_length

        resp = session.patch(
            ARTICLE_DRAFT_URL.format(article_id=object_id),
            json={
                "content": html_content,
                "table_of_contents": False,
                "delta_time": delta_time,
                "can_reward": False,
            },
        )
        resp.raise_for_status()
        return resp.json()

    elif content_type == "answer":
        drafts = list_drafts("question", object_id)
        if drafts:
            old_draft = get_draft(drafts[0]["id"], drafts[0].get("version_type", "current")).get("draft", {})
            # Prefer server-provided content_words over client-side computation
            old_length = old_draft.get("content_words")
            if old_length is None:
                old_html = old_draft.get("content", "")
                old_length = calculate_text_length(old_html)
            delta_time = new_length - old_length

            url = ANSWER_DRAFT_URL.format(question_id=object_id)
            resp = session.put(
                url,
                json={
                    "content": html_content,
                    "delta_time": delta_time,
                    "draft_type": "normal",
                    "attachment": None,
                    "settings": ANSWER_DRAFT_SETTINGS,
                },
            )
        else:
            delta_time = new_length

            url = ANSWER_DRAFT_URL.format(question_id=object_id)
            resp = session.post(
                url,
                json={
                    "content": html_content,
                    "delta_time": delta_time,
                    "draft_type": "normal",
                    "settings": ANSWER_DRAFT_SETTINGS,
                },
            )
        resp.raise_for_status()
        return resp.json()

    else:
        raise ValueError(f"Unsupported draft content type: {content_type}")

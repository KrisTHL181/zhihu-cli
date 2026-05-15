"""Draft history listing and draft-to-markdown conversion."""

from urllib.parse import urlencode, urlparse

from zhihu_cli.content.handlers import fmt_time, get_type_and_id
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.html2markdown import converter
from zhihu_cli.content.utils.zse import ZSECipher

DRAFT_HISTORIES_URL = "https://www.zhihu.com/api/v4/draft-histories"
DRAFT_URL = "https://www.zhihu.com/api/v4/draft-history"


def _zse_sign(url: str) -> dict[str, str]:
    """Generate x-zse signing headers for a Zhihu API request."""
    parsed = urlparse(url)
    path_and_query = parsed.path
    if parsed.query:
        path_and_query += "?" + parsed.query

    cipher = ZSECipher()
    signature = cipher.encrypt(path_and_query)

    return {
        "x-zse-93": "101_3_3.0",
        "x-zse-96": f"2.0_{signature}",
    }


def list_drafts(object_type: str, object_id: str) -> list[dict]:
    """List draft histories for a question/answer/article."""
    url = f"{DRAFT_HISTORIES_URL}?{urlencode({'object_type': object_type, 'object_id': object_id})}"
    resp = session.get(url, headers=_zse_sign(url))
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_draft(draft_id: str, version_type: str = "current") -> dict:
    """Get a specific draft by ID."""
    url = f"{DRAFT_URL}?{urlencode({'version_type': version_type, 'id': draft_id})}"
    resp = session.get(url, headers=_zse_sign(url))
    resp.raise_for_status()
    return resp.json()


def draft_to_markdown(url: str) -> tuple[dict, str]:
    """Convert the latest draft of a Zhihu item to Markdown.

    Given a Zhihu question/answer URL, fetches the latest draft (current
    version) and returns (metadata, markdown).
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

    drafts = list_drafts(api_type, object_id)
    if not drafts:
        raise ValueError(f"No drafts found for: {url}")

    latest = drafts[0]
    draft_detail = get_draft(latest["id"], latest.get("version_type", "current"))

    html_content = draft_detail.get("draft", {}).get("content", "")
    if not html_content:
        raise ValueError("Draft has no content")

    metadata = {
        "id": draft_detail["id"],
        "created_time": fmt_time(draft_detail.get("created_at", 0)),
        "updated_time": fmt_time(draft_detail.get("updated_at", 0)),
        "version_type": draft_detail.get("version_type", "unknown"),
        "url": url,
    }

    return metadata, converter.convert(html_content)

import re
from datetime import datetime
from pathlib import Path

USER_AGENT_FILE = Path.home() / ".zhihu-cli" / "user-agent"


def get_data_dir() -> Path:
    """Return ~/.zhihu-cli/ data directory, creating it if needed."""
    data_dir = Path.home() / ".zhihu-cli"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_user_agent() -> str | None:
    """Return the configured User-Agent from ~/.zhihu-cli/user-agent, or None."""
    if USER_AGENT_FILE.exists():
        ua = USER_AGENT_FILE.read_text(encoding="utf-8").strip()
        if ua:
            return ua
    return None


def set_user_agent(ua: str | None) -> None:
    """Set (or clear) the User-Agent in ~/.zhihu-cli/user-agent."""
    if ua is None:
        USER_AGENT_FILE.unlink(missing_ok=True)
    else:
        get_data_dir()  # ensure directory exists
        USER_AGENT_FILE.write_text(ua.strip(), encoding="utf-8")


ZHIHU_ARTICLE_PATTERN = r"https?://zhuanlan\.zhihu\.com/p/(\d+)"
ZHIHU_QUESTION_PATTERN = r"https?://(?:www\.)?zhihu\.com/question/(\d+)"
ZHIHU_QUESTION_WITH_ANSWER_PATTERN = r"https?://(?:www\.)?zhihu\.com/question/(\d+)(?:/answer/(\d+))?"
ZHIHU_ANSWER_SHORT_PATTERN = r"https?://(?:www\.)?zhihu\.com/answer/(\d+)"
ZHIHU_PIN_PATTERN = r"https?://(?:www\.)?zhihu\.com/pin/([^/?#]+)"
ZHIHU_ZVIDEO_PATTERN = r"https?://(?:www\.)?zhihu\.com/zvideo/(\d+)"
ZHIHU_COLLECTION_PATTERN = r"https?://(?:www\.)?zhihu\.com/collection/(\d+)"


def get_type_and_id(url: str) -> tuple[str | None, str | None]:
    """
    Returns (type, id).
    type: 'articles', 'questions', 'answers', 'pins', 'zvideos', or None.
    """
    match = re.search(ZHIHU_ARTICLE_PATTERN, url)
    if match:
        return ("articles", match.group(1))

    match = re.search(ZHIHU_QUESTION_WITH_ANSWER_PATTERN, url)
    if match and match.group(2):
        return ("answers", f"{match.group(1)}/{match.group(2)}")

    match = re.search(ZHIHU_ANSWER_SHORT_PATTERN, url)
    if match:
        # Short answer URL (e.g. https://www.zhihu.com/answer/123456) —
        # no question ID available; downstream callers use _resolve_answer_id()
        # which handles the bare answer ID just fine.
        return ("answers", match.group(1))

    match = re.search(ZHIHU_QUESTION_PATTERN, url)
    if match:
        return ("questions", match.group(1))

    match = re.search(ZHIHU_PIN_PATTERN, url)
    if match:
        return ("pins", match.group(1))

    match = re.search(ZHIHU_ZVIDEO_PATTERN, url)
    if match:
        return ("zvideos", match.group(1))

    match = re.search(ZHIHU_COLLECTION_PATTERN, url)
    if match:
        return ("collections", match.group(1))

    return (None, None)


def fmt_time(ts: int | float | None) -> str:
    if ts:
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)
    return "unknown time"

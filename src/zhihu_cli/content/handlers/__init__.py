import re
from datetime import datetime
from pathlib import Path


def get_data_dir() -> Path:
    """Return ~/.zhihu-cli/ data directory, creating it if needed."""
    data_dir = Path.home() / ".zhihu-cli"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


ZHIHU_ARTICLE_PATTERN = r"https?://zhuanlan\.zhihu\.com/p/(\d+)"
ZHIHU_QUESTION_PATTERN = r"https?://(?:www\.)?zhihu\.com/question/(\d+)"
ZHIHU_QUESTION_WITH_ANSWER_PATTERN = r"https?://(?:www\.)?zhihu\.com/question/(\d+)(?:/answer/(\d+))?"
ZHIHU_PIN_PATTERN = r"https?://(?:www\.)?zhihu\.com/pin/([^/?#]+)"


def get_type_and_id(url: str) -> tuple[str | None, str | None]:
    """
    返回 (type, id)
    type: 'articles', 'questions', 'answers', 'pins' 或 None
    """
    match = re.search(ZHIHU_ARTICLE_PATTERN, url)
    if match:
        return ("articles", match.group(1))

    match = re.search(ZHIHU_QUESTION_WITH_ANSWER_PATTERN, url)
    if match and match.group(2):
        return ("answers", f"{match.group(1)}/{match.group(2)}")

    match = re.search(ZHIHU_QUESTION_PATTERN, url)
    if match:
        return ("questions", match.group(1))

    match = re.search(ZHIHU_PIN_PATTERN, url)
    if match:
        return ("pins", match.group(1))

    return (None, None)


def fmt_time(ts: int | float | None) -> str:
    if ts:
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)
    return "未知时间"

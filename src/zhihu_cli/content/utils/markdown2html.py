import uuid
from typing import Literal

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils import generate_trace_context

PARSE_API = "https://zhuanlan.zhihu.com/editor/paste/parse"


def markdown2html(markdown: str, scene: Literal["article", "answer"]) -> str:
    parsed = session.post(
        PARSE_API,
        json={
            "task_id": f"{uuid.uuid4()}",
            "content": markdown,
            "content_type": "markdown",
            "html": markdown,
            "scene": scene,
            "trace_id": "".join([str(x) for x in generate_trace_context()]),
        },
    )

    parsed.raise_for_status()
    return parsed.json().get("data", {}).get("parsed_content", "").rstrip("<br>")


def rich2html(rich: str, scene: Literal["article", "answer"]) -> str:
    parsed = session.post(
        PARSE_API,
        json={
            "task_id": f"{uuid.uuid4()}",
            "content": rich,
            "content_type": "html",
            "html": rich,
            "scene": scene,
            "trace_id": "".join([str(x) for x in generate_trace_context()]),
        },
    )

    parsed.raise_for_status()
    return parsed.json().get("data", {}).get("parsed_content", "").rstrip("<br>")

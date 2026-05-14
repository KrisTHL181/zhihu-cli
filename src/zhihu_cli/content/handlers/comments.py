from collections.abc import Iterable, Iterator
from typing import Any

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils import markdown2html
from zhihu_cli.content.utils.html2markdown import converter

COMMENT_API: dict[str, str] = {
    "answers": "https://www.zhihu.com/api/v4/comment_v5/answers/{item_id}/comment",
    "articles": "https://www.zhihu.com/api/v4/comment_v5/articles/{item_id}/comment",
    "pins": "https://www.zhihu.com/api/v4/comment_v5/pins/{item_id}/comment",
    "questions": "https://www.zhihu.com/api/v4/comment_v5/questions/{item_id}/comment",
}

_URL_SEGMENT: dict[str, str] = {
    "answer": "answers",
    "answers": "answers",
    "article": "articles",
    "articles": "articles",
    "pin": "pins",
    "pins": "pins",
    "question": "questions",
    "questions": "questions",
}


def _convert_content(content: str) -> str:
    converted = converter.convert(content)
    converted = converted.replace("\n\n\n\n\n", "\n\t")
    return converted if converted.strip() else content


def fetch_child_comments(parent_comment: dict[str, Any], seen_ids: set[str] | None = None) -> Iterable[dict[str, Any]]:
    if seen_ids is None:
        seen_ids = set()

    child_comment_count = parent_comment.get("child_comment_count", 0)
    if child_comment_count == 0:
        return []

    child_offset = parent_comment.get("child_comment_next_offset")
    # offset 为 None 表示 API 未提供，尝试从 0 开始；
    # offset 为 0 是有效值，不能用 `not child_offset` 误判
    if child_offset is None:
        child_offset = 0

    base_url = f"https://www.zhihu.com/api/v4/comment_v5/comment/{parent_comment['id']}/child_comment"
    initial_url = f"{base_url}?limit=20&offset={child_offset}"

    def child_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for child in data.get("data", []):
            cid = child.get("id")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            yield {
                "author": child.get("author", {}).get("name", "匿名用户"),
                "like_count": child.get("like_count", 0),
                "dislike_count": child.get("dislike_count", 0),
                "content": _convert_content(child.get("content", "")),
                "id": cid,
            }

    return stream_handler(initial_url, child_parser)


def fetch_root_comments(item_type: str, item_id: str) -> Iterable[dict[str, Any]]:
    segment = _URL_SEGMENT.get(item_type, item_type)
    initial_url = (
        f"https://www.zhihu.com/api/v4/comment_v5/{segment}/{item_id}/root_comment?order_by=score&limit=20&offset="
    )

    def root_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for comment in data.get("data", []):
            root: dict[str, Any] = {
                "author": comment.get("author", {}).get("name", "匿名用户"),
                "like_count": comment.get("like_count", 0),
                "dislike_count": comment.get("dislike_count", 0),
                "content": _convert_content(comment.get("content", "")),
                "id": comment.get("id"),
                "child_comments": [],
            }

            seen_ids: set[str] = set()
            inline_children = comment.get("child_comments", [])
            for child in inline_children:
                cid = child.get("id")
                if cid and cid in seen_ids:
                    continue
                if cid:
                    seen_ids.add(cid)
                root["child_comments"].append(
                    {
                        "author": child.get("author", {}).get("name", "匿名用户"),
                        "like_count": child.get("like_count", 0),
                        "dislike_count": child.get("dislike_count", 0),
                        "content": _convert_content(child.get("content", "")),
                        "id": cid,
                    }
                )

            # 翻页获取剩余的 child comments（带去重）
            if comment.get("child_comment_count", 0) >= 1:
                root["child_comments"].extend(fetch_child_comments(comment, seen_ids))

            yield root

    return stream_handler(initial_url, root_parser)


def print_comments(item_type: str, item_id: str) -> None:
    comment_id = 1
    for comment in fetch_root_comments(item_type, item_id):
        print(
            f"\n[{comment_id}] 作者: {comment['author']} | 赞: {comment['like_count']} | 踩: {comment['dislike_count']}"
        )
        print("-" * 20)
        print(comment["content"])
        if comment["child_comments"]:
            print("\n  ↳ 子评论:")
            for child in comment["child_comments"]:
                print(f"    - 作者: {child['author']} | 赞: {child['like_count']} | 踩: {child['dislike_count']}")
                print(f"      {child['content']}\n")
        print("-" * 20)
        comment_id += 1


def comment_item(item_type: str, item_id: str, content: str) -> dict[str, Any]:
    segment = _URL_SEGMENT.get(item_type, item_type)
    if segment not in COMMENT_API:
        raise ValueError(f"Invalid item_type: '{item_type}'. Supported types are: {list(COMMENT_API.keys())}")

    api = COMMENT_API[segment].replace("{item_id}", item_id)
    content = f"{markdown2html.markdown2html(content, scene='answer')}"

    resp = session.post(api, json={"content": content})
    return resp.json()


def delete_comment(comment_id: str) -> None:
    resp = session.delete(f"https://www.zhihu.com/api/v4/comment_v5/comment/{comment_id}")
    if not resp.json()["success"]:
        raise RuntimeError(f"Failed to delete comment {comment_id}")

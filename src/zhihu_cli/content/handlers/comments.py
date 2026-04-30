from collections.abc import Iterable, Iterator
from typing import Any

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils import markdown2html
from zhihu_cli.content.utils.html2markdown import converter

COMMENT_API: dict[str, str] = {
    "answer": "https://www.zhihu.com/api/v4/comment_v5/answers/{item_id}/comment",
    "article": "https://www.zhihu.com/api/v4/comment_v5/articles/{item_id}/comment",
    "pin": "https://www.zhihu.com/api/v4/comment_v5/pins/{item_id}/comment",
    "question": "https://www.zhihu.com/api/v4/comment_v5/questions/{item_id}/comment",
}


def fetch_child_comments(parent_comment: dict[str, Any]) -> Iterable[dict[str, Any]]:
    child_offset = parent_comment.get("child_comment_next_offset")
    if not child_offset:
        return []

    base_url = f"https://www.zhihu.com/api/v4/comment_v5/comment/{parent_comment['id']}/child_comment"
    initial_url = f"{base_url}?limit=20&offset={child_offset}"

    def child_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for child in data.get("data", []):
            yield {
                "author": child.get("author", {}).get("name", "匿名用户"),
                "like_count": child.get("like_count", 0),
                "dislike_count": child.get("dislike_count", 0),
                "content": converter.convert(child.get("content", "")),
                "id": child.get("id"),
            }

    return stream_handler(initial_url, child_parser)


def fetch_root_comments(item_type: str, item_id: str) -> Iterable[dict[str, Any]]:
    initial_url = f"https://www.zhihu.com/api/v4/comment_v5/{item_type}/{item_id}/root_comment?order_by=score&limit=20"

    def root_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for comment in data.get("data", []):
            root: dict[str, Any] = {
                "author": comment.get("author", {}).get("name", "匿名用户"),
                "like_count": comment.get("like_count", 0),
                "dislike_count": comment.get("dislike_count", 0),
                "content": converter.convert(comment.get("content", "")),
                "id": comment.get("id"),
                "child_comments": [],
            }

            for child in comment.get("child_comments", []):
                root["child_comments"].append(
                    {
                        "author": child.get("author", {}).get("name", "匿名用户"),
                        "like_count": child.get("like_count", 0),
                        "dislike_count": child.get("dislike_count", 0),
                        "content": converter.convert(child.get("content", "")),
                    }
                )

            root["child_comments"].extend(fetch_child_comments(comment))

            yield root

    return stream_handler(initial_url, root_parser)


def print_comments(item_type: str, item_id: str) -> None:
    """打印所有评论（带格式）"""
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
    if item_type not in COMMENT_API:
        raise ValueError(f"Invalid item_type: '{item_type}'. Supported types are: {list(COMMENT_API.keys())}")

    api = COMMENT_API[item_type].replace("{item_id}", item_id)
    content = f"{markdown2html.markdown2html(content, scene='answer')}"

    resp = session.post(api, json={"content": content})
    return resp.json()


def delete_comment(comment_id: str) -> None:
    resp = session.delete(f"https://www.zhihu.com/api/v4/comment_v5/comment/{comment_id}")
    if not resp.json()["success"]:
        raise RuntimeError(f"Failed to delete comment {comment_id}")

from collections.abc import Iterable, Iterator
from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils import markdown2html
from zhihu_cli.content.utils.html2markdown import converter
from zhihu_cli.output import divider, echo, f_dim, f_green, f_meta, f_name, f_num, item_index

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


def _build_child_dict(child: dict[str, Any]) -> dict[str, Any]:
    """Extract standard fields from a raw API child comment dict.

    Includes ``reply_comment_id`` (for threading) and recursively
    extracts any inline ``child_comments`` (grandchildren) the API
    may bundle with the child.

    :param child: Raw child comment dict from the Zhihu API.
    :returns: Standardised child comment dict.
    """
    cid = child.get("id")
    result: dict[str, Any] = {
        "author": child.get("author", {}).get("name", "anonymous"),
        "like_count": child.get("like_count", 0),
        "dislike_count": child.get("dislike_count", 0),
        "content": _convert_content(child.get("content", "")),
        "created_time": child.get("created_time"),
        "id": cid,
        "reply_comment_id": child.get("reply_comment_id", ""),
        "child_comments": [],
    }

    # Recursively extract inline grandchildren (replies-to-replies)
    for gc in child.get("child_comments", []):
        result["child_comments"].append(_build_child_dict(gc))

    return result


def _build_thread_tree(children: list[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    """Build a threaded comment tree from a flat list of children.

    Uses ``reply_comment_id`` to nest replies under the comment they
    reply to.  Children that reply directly to *root_id* become
    top-level; replies to other children are nested under their
    parent so that the display can show conversation threading.

    :param children: Flat list of child comment dicts (each must have
        ``id`` and ``reply_comment_id``).
    :param root_id: The root comment ID — replies targeting this ID
        are treated as top-level children.
    :returns: Threaded tree of children (each node may contain nested
        ``child_comments``).
    """
    by_id: dict[str, dict[str, Any]] = {}
    for c in children:
        cid = c.get("id")
        if cid:
            # If a child already exists by this ID (inline dup), keep the
            # first one and skip the duplicate.
            if cid not in by_id:
                by_id[cid] = c

    threaded_roots: list[dict[str, Any]] = []

    for c in children:
        reply_to = c.get("reply_comment_id", "")
        if reply_to and reply_to != root_id and reply_to in by_id:
            parent = by_id[reply_to]
            # Merge into parentʼs existing child_comments (may already have
            # inline grandchildren from the API response).
            parent.setdefault("child_comments", []).append(c)
        else:
            threaded_roots.append(c)

    return threaded_roots


def fetch_child_comments(parent_comment: dict[str, Any], seen_ids: set[str] | None = None) -> Iterable[dict[str, Any]]:
    if seen_ids is None:
        seen_ids = set()

    child_comment_count = parent_comment.get("child_comment_count", 0)
    if child_comment_count == 0:
        return []

    # Always start from offset=0 — the APIʼs child_comment_next_offset is a
    # cursor (e.g. "1781270884_11506206037_0") that follows the last inline
    # child, not a numeric offset.  Using it would skip children that exist
    # between offset 0 and the cursor but were not included in the inline
    # child_comments array.  Rely on seen_ids dedup for the overlap.
    parent_id = parent_comment["id"]
    initial_url = f"https://www.zhihu.com/api/v4/comment_v5/comment/{parent_id}/child_comment?limit=20&offset=0"

    def child_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for child in data.get("data", []):
            cid = child.get("id")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            yield _build_child_dict(child)

    return stream_handler(initial_url, child_parser)


def fetch_root_comments(item_type: str, item_id: str) -> Iterable[dict[str, Any]]:
    segment = _URL_SEGMENT.get(item_type, item_type)
    initial_url = (
        f"https://www.zhihu.com/api/v4/comment_v5/{segment}/{item_id}/root_comment?order_by=score&limit=20&offset="
    )

    def root_parser(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for comment in data.get("data", []):
            root_id = comment.get("id")
            root: dict[str, Any] = {
                "author": comment.get("author", {}).get("name", "anonymous"),
                "like_count": comment.get("like_count", 0),
                "dislike_count": comment.get("dislike_count", 0),
                "content": _convert_content(comment.get("content", "")),
                "id": root_id,
                "created_time": comment.get("created_time"),
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
                root["child_comments"].append(_build_child_dict(child))

            # Paginate to fetch remaining child comments (with dedup)
            if comment.get("child_comment_count", 0) >= 1:
                root["child_comments"].extend(fetch_child_comments(comment, seen_ids))

            # Build threaded tree from flat children
            root["child_comments"] = _build_thread_tree(root["child_comments"], root_id)

            yield root

    return stream_handler(initial_url, root_parser)


def fetch_comments(item_type: str, item_id: str) -> list[dict[str, Any]]:
    """Return comment tree as a list of dicts (for JSON output)."""
    return list(fetch_root_comments(item_type, item_id))


def _print_child_tree(children: list[dict[str, Any]], indent: int = 0) -> None:
    """Recursively print a threaded child comment subtree.

    :param children: List of child comment dicts at this level.
    :param indent: Nesting depth (0 = direct reply to root comment).
    """
    for child in children:
        pad = "    " * indent
        # Use "- " for top-level replies, "↳ " for nested ones
        leader = "- " if indent == 0 else "↳ "
        child_header = (
            f"{pad}    {leader}{f_name(child['author'])} "
            f"{f_meta('| ID:')} {f_dim(child['id'])} "
            f"{f_meta('| Likes:')} {f_num(child['like_count'])} "
            f"{f_meta('| Dislikes:')} {f_num(child['dislike_count'])} "
            f"{f_meta('| Created:')} {f_meta(fmt_time(child['created_time']))}"
        )
        echo(child_header)
        content_pad = pad + "      "
        echo(f"{content_pad}{child['content']}\n")

        grandchildren = child.get("child_comments", [])
        if grandchildren:
            _print_child_tree(grandchildren, indent + 1)


def print_comments(
    item_type: str | None = None,
    item_id: str | None = None,
    *,
    comments: list[dict] | None = None,
) -> None:
    """Display a comment tree.

    Either provide *(item_type, item_id)* to fetch comments, or pass a
    pre-fetched *comments* list directly.

    :param item_type: Resource type (answers, articles, pins, questions).
    :param item_id: Resource ID.
    :param comments: Pre-fetched list of comment dicts (as returned by
        :func:`fetch_root_comments` or :func:`fetch_comments`).
    :raises ValueError: If neither *(item_type, item_id)* nor *comments* is supplied.
    """
    if comments is None:
        if item_type is None or item_id is None:
            raise ValueError("Either (item_type, item_id) or comments must be provided")
        comments = list(fetch_root_comments(item_type, item_id))

    for cid, comment in enumerate(comments, 1):
        header = (
            f"\n{item_index(cid)} "
            f"{f_name(comment['author'])} "
            f"{f_meta('| ID:')} {f_dim(comment['id'])} "
            f"{f_meta('| Likes:')} {f_num(comment['like_count'])} "
            f"{f_meta('| Dislikes:')} {f_num(comment['dislike_count'])} "
            f"{f_meta('| Created:')} {f_meta(fmt_time(comment['created_time']))}"
        )
        echo(header)
        divider("-", 20)
        echo(comment["content"])
        if comment["child_comments"]:
            echo(f"\n  {f_green('↳ Replies:')}")
            _print_child_tree(comment["child_comments"], indent=0)
        divider("-", 20)


def comment_item(item_type: str, item_id: str, content: str, reply_comment_id: str | None = None) -> dict[str, Any]:
    """Post a root comment or reply to an existing comment on an item.

    :param item_type: One of ``"answers"``, ``"articles"``, ``"pins"``, ``"questions"``.
    :param item_id: The resource ID.
    :param content: Comment body (markdown, auto-converted to HTML).
    :param reply_comment_id: When set, this becomes a reply to the specified
        comment rather than a root comment.
    :returns: API response dict.
    :raises ValueError: If *item_type* is unsupported.
    """
    segment = _URL_SEGMENT.get(item_type, item_type)
    if segment not in COMMENT_API:
        raise ValueError(f"Invalid item_type: '{item_type}'. Supported types are: {list(COMMENT_API.keys())}")

    api = COMMENT_API[segment].replace("{item_id}", item_id)
    content_html = markdown2html.markdown2html(content, scene="answer")

    if reply_comment_id:
        payload: dict[str, Any] = {
            "comment_id": "",
            "content": content_html,
            "extra_params": "",
            "has_img": False,
            "reply_comment_id": reply_comment_id,
            "score": 0,
            "selected_settings": [],
            "segment": None,
            "sticker_type": None,
            "unfriendly_check": "strict",
        }
    else:
        payload = {"content": content_html}

    resp = session.post(api, json=payload)
    return resp.json()


def delete_comment(comment_id: str) -> None:
    resp = session.delete(f"https://www.zhihu.com/api/v4/comment_v5/comment/{comment_id}")
    if not resp.json()["success"]:
        raise RuntimeError(f"Failed to delete comment {comment_id}")

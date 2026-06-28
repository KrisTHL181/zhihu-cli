"""Fetch segment comments (句子评论 / sentence annotations) for a Zhihu answer.

The segment-comment API returns comments anchored to specific text ranges
within the answer body, along with per-segment metadata (highlighted text,
position, reaction counts).

This is a mobile-app API endpoint.  The global session (``requests.py``)
injects ``x-app-version`` and ``x-app-za`` headers automatically —
without them the API returns ``data: null``.  See
:func:`zhihu_cli.content.handlers.cache_manager.CacheManager.get_app_za`
and :func:`zhihu_cli.content.handlers.cache_manager.CacheManager.get_app_version`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers.waterfall import stream_handler

SEGMENT_COMMENT_API = (
    "https://api.zhihu.com/comment_v5/answers/{answer_id}/segment_comment?order_by=score&limit=20&offset="
)


def fetch_segment_comments(answer_id: str) -> Iterable[dict[str, Any]]:
    """Fetch segment comments for an answer, enriched with per-segment context.

    :param answer_id: The answer ID (url_token / numeric ID).
    :yields: Dicts with keys from the comment plus ``segment_text``,
        ``segment_is_removed``, ``segment_reaction``, and
        ``segment_position``.
    """
    initial_url = SEGMENT_COMMENT_API.format(answer_id=answer_id)

    # We capture segment_infos from the first data page (theyʼre repeated
    # on subsequent pages, but the first page is authoritative).
    _segment_infos: dict[str, dict[str, Any]] = {}

    def parser(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
        nonlocal _segment_infos

        seg_infos = data.get("segment_infos", {})
        if seg_infos:
            _segment_infos.update(seg_infos)

        for comment in data.get("data", []):
            author = comment.get("author", {})
            seg_info = _segment_infos.get(comment.get("resource_id", ""), {})
            segment_text = seg_info.get("content", "")
            segment_is_removed = seg_info.get("is_removed", False)
            segment_position = seg_info.get("position", {})
            segment_reaction = seg_info.get("reaction", {})

            yield {
                "id": comment.get("id"),
                "type": comment.get("type"),
                "resource_type": comment.get("resource_type"),
                "resource_id": comment.get("resource_id"),
                "content": comment.get("content", ""),
                "score": comment.get("score", 0),
                "created_time": comment.get("created_time"),
                "like_count": comment.get("like_count", 0),
                "dislike_count": comment.get("dislike_count", 0),
                "is_author": comment.get("is_author", False),
                "collapsed": comment.get("collapsed", False),
                "reviewing": comment.get("reviewing", False),
                "is_delete": comment.get("is_delete", False),
                "child_comment_count": comment.get("child_comment_count", 0),
                "child_comments": comment.get("child_comments", []),
                "author": {
                    "id": author.get("id", ""),
                    "url_token": author.get("url_token", ""),
                    "name": author.get("name", "anonymous"),
                    "avatar_url": author.get("avatar_url", ""),
                    "headline": author.get("headline", ""),
                    "gender": author.get("gender", 0),
                    "is_org": author.get("is_org", False),
                    "type": author.get("type", "people"),
                },
                "comment_tag": comment.get("comment_tag", []),
                "segment_text": segment_text,
                "segment_is_removed": segment_is_removed,
                "segment_position": segment_position,
                "segment_reaction": segment_reaction,
            }

    return stream_handler(initial_url, parser)

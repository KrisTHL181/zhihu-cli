"""Zhihu 众裁 (community moderation / agora) API handlers.

众裁 is Zhihu's community-driven content moderation system. Users review
reported comments and vote on whether they should be removed.

Endpoints:
- GET  /appview/court/discussion                      — SSR page with initialData
- GET  /api/v4/agora/me                               — juror status & stats
- GET  /api/v4/agora/discussions/{id}/reviews          — list review cases
- GET  /api/v4/agora/discussions/{id}/comment-detail    — reported comment detail
- POST /api/v4/agora/discussions/{id}/votes            — cast a vote
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session
from zhihu_cli.content.handlers.waterfall import stream_handler

AGORA_BASE = "https://www.zhihu.com/api/v4/agora"
COURT_PAGE = "https://www.zhihu.com/appview/court/discussion"


# ── court page scraping ────────────────────────────────────────────────────


def _parse_discussion_entity(disc: dict[str, Any]) -> dict[str, Any]:
    """Parse a discussion entity from js-initialData into a clean dict."""
    case = disc.get("case", {})
    content = case.get("content", {})
    comment = content.get("comment", {})
    report_info = case.get("reportInfo", {})

    # Extract origin info
    origin_url = content.get("originUrl", "")
    origin_title = content.get("originTitle", "")

    return {
        "id": disc.get("id", ""),
        "type": disc.get("type", "discussion"),
        "status": disc.get("status", ""),
        "my_vote": disc.get("relationship", {}).get("vote", ""),
        # Case info
        "case_id": case.get("id", ""),
        "case_status": case.get("status", ""),
        # Reported comment
        "comment": {
            "id": comment.get("id", 0),
            "content": comment.get("content", ""),
            "url": comment.get("url", ""),
            "created_time": comment.get("createdTime", 0),
            "vote_count": comment.get("voteCount", 0),
            "resource_type": comment.get("resourceType", ""),
            "is_author": comment.get("isAuthor", False),
            "is_delete": comment.get("isDelete", False),
        },
        # Origin (where the comment was posted)
        "origin_url": origin_url,
        "origin_title": origin_title,
        # Report info
        "report_reason": report_info.get("reason", ""),
        "report_note": report_info.get("note", ""),
        "report_name": report_info.get("name", ""),
        "reported_user": report_info.get("reportedName", ""),
        "raw": disc,
    }


def fetch_court_page(discussion_id: str | None = None) -> dict[str, Any]:
    """Fetch the court page and extract initialData.

    Fetches https://www.zhihu.com/appview/court/discussion (the 开始众裁 page),
    extracts the SSR js-initialData, and returns the current discussion.

    If discussion_id is provided, fetches that specific discussion's page instead:
    https://www.zhihu.com/appview/court/discussion/{id}

    Returns a dict with:
      - current_discussion: parsed discussion or None
      - juror_info: juror stats
      - banners: banner list
      - discussion_id: current discussion ID (even if entity not in initialData)
    """
    if discussion_id:
        url = f"{COURT_PAGE}/{discussion_id}?redirect_from_main=1"
    else:
        url = f"{COURT_PAGE}?redirect_from_main=1"

    html_text = fetch_page_html(url)

    court = get_page_state(html_text, "court")

    # Current discussion ID
    current_disc = court.get("currentDiscussion", {})
    disc_id = current_disc.get("id", "") if current_disc else ""

    # Juror info
    juror_info = court.get("jurorInfo", {}) or {}

    # Parse discussion entity if present
    discussion = None
    entities = court.get("entities", {})
    discussions = entities.get("discussions", {})
    comments = entities.get("comments", {})

    if disc_id and disc_id in discussions:
        discussion = _parse_discussion_entity(discussions[disc_id])

        # Enrich with comment author info from entities.comments
        comment_id = str(discussion["comment"]["id"])
        if comment_id in comments:
            comment_entity = comments[comment_id]
            author = comment_entity.get("author", {})
            member = author.get("member", {})
            discussion["comment"]["author"] = {
                "url_token": member.get("urlToken", ""),
                "name": member.get("name", ""),
                "headline": member.get("headline", ""),
                "avatar_url": member.get("avatarUrl", ""),
            }

    # Also get comment entity IDs from reportComments
    report_comments = court.get("reportComments", {})
    report_comment_data = report_comments.get(disc_id, {})

    return {
        "discussion_id": disc_id,
        "current_discussion": discussion,
        "juror_info": {
            "is_juror": juror_info.get("isJuror", False),
            "vote_count": juror_info.get("voteCount", 0),
            "review_count": juror_info.get("reviewCount", 0),
            "review_liked_count": juror_info.get("reviewLikedCount", 0),
            "today_jury_count": juror_info.get("todayJuryCount", 0),
            "week_vote_count": juror_info.get("weekVoteCount", 0),
            "week_review_count": juror_info.get("weekReviewCount", 0),
            "week_review_liked_count": juror_info.get("weekReviewLikedCount", 0),
            "max_day_jury_count": juror_info.get("maxDayJuryCount", 0),
        },
        "banners": court.get("banners", []),
        "report_comment": {
            "resource_id": report_comment_data.get("resourceId", ""),
            "reported_comment_id": report_comment_data.get("reportedCommentId", 0),
        },
        "raw": court,
    }


# ── my juror status ────────────────────────────────────────────────────────


def fetch_agora_me() -> dict[str, Any]:
    """Fetch current user's agora (众裁) juror status and stats.

    Returns:
        Dict with is_juror flag and juror_info containing vote_count,
        review_count, review_liked_count, today_jury_count, week stats,
        and max_day_jury_count.
    """
    url = f"{AGORA_BASE}/me"
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()
    juror = data.get("juror_info", {}) or {}
    return {
        "is_juror": data.get("is_juror", False),
        "juror_info": {
            "vote_count": juror.get("vote_count", 0),
            "review_count": juror.get("review_count", 0),
            "review_liked_count": juror.get("review_liked_count", 0),
            "today_jury_count": juror.get("today_jury_count", 0),
            "week_vote_count": juror.get("week_vote_count", 0),
            "week_review_count": juror.get("week_review_count", 0),
            "week_review_liked_count": juror.get("week_review_liked_count", 0),
            "max_day_jury_count": juror.get("max_day_jury_count", 0),
        },
        "raw": data,
    }


# ── review list ────────────────────────────────────────────────────────────


def _parse_review_item(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single review item from the agora reviews API response."""
    return {
        "id": item.get("id", ""),
        "discussion_id": item.get("discussion_id", ""),
        "resource_id": item.get("resource_id", ""),
        "resource_type": item.get("resource_type", ""),
        "comment_id": item.get("comment_id", ""),
        "comment_content": item.get("comment_content", ""),
        "comment_author": item.get("comment_author", {}),
        "reason": item.get("reason", ""),
        "status": item.get("status", ""),
        "created_time": item.get("created_time", 0),
        "vote_count": item.get("vote_count", 0),
        "affirmative_count": item.get("affirmative_count", 0),
        "dissenting_count": item.get("dissenting_count", 0),
        "my_vote": item.get("my_vote", ""),
        "raw": item,
    }


def _parse_reviews(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Parse the paginated reviews API response, yielding parsed review items."""
    for item in data.get("data", []):
        yield _parse_review_item(item)


def fetch_reviews(
    discussion_id: str,
    limit: int = 20,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch review cases for an agora discussion (paginated).

    Args:
        discussion_id: The agora discussion ID.
        limit: Number of items per page.
        max_items: Maximum total items to fetch (None = fetch all).

    Returns:
        List of parsed review dicts.
    """
    url = f"{AGORA_BASE}/discussions/{discussion_id}/reviews?limit={limit}&offset=0"
    items: list[dict[str, Any]] = []
    for item in stream_handler(url, _parse_reviews):
        items.append(item)
        if max_items is not None and len(items) >= max_items:
            break
    return items


# ── comment detail ─────────────────────────────────────────────────────────


def _parse_comment_detail(data: dict[str, Any]) -> dict[str, Any]:
    """Parse the comment-detail API response."""
    root = data.get("root_comment", {})
    author = root.get("author", {})
    member = author.get("member", {})

    return {
        "resource_id": data.get("resource_id", ""),
        "reported_comment_id": data.get("reported_comment_id", 0),
        "comment": {
            "id": root.get("id", 0),
            "type": root.get("type", ""),
            "url": root.get("url", ""),
            "content": root.get("content", ""),
            "featured": root.get("featured", False),
            "collapsed": root.get("collapsed", False),
            "is_author": root.get("is_author", False),
            "is_delete": root.get("is_delete", False),
            "created_time": root.get("created_time", 0),
            "resource_type": root.get("resource_type", ""),
            "vote_count": root.get("vote_count", 0),
            "voting": root.get("voting", False),
            "disliked": root.get("disliked", False),
            "child_comment_count": root.get("child_comment_count", 0),
            "is_parent_author": root.get("is_parent_author", False),
            "author": {
                "id": member.get("id", ""),
                "url_token": member.get("url_token", ""),
                "name": member.get("name", ""),
                "avatar_url": member.get("avatar_url", ""),
                "headline": member.get("headline", ""),
                "gender": member.get("gender", -1),
                "is_org": member.get("is_org", False),
                "user_type": member.get("user_type", ""),
            },
        },
        "child_comments": data.get("child_comments", []),
        "raw": data,
    }


def fetch_comment_detail(discussion_id: str) -> dict[str, Any]:
    """Fetch the reported comment detail for an agora discussion.

    Args:
        discussion_id: The agora discussion ID.

    Returns:
        Parsed dict with resource_id, reported_comment_id, comment info,
        and child comments.
    """
    url = f"{AGORA_BASE}/discussions/{discussion_id}/comment-detail"
    resp = session.get(url)
    resp.raise_for_status()
    return _parse_comment_detail(resp.json())


# ── voting ─────────────────────────────────────────────────────────────────

VALID_VOTES = ("affirmative", "abstain", "dissenting")

VOTE_LABELS: dict[str, str] = {
    "affirmative": "赞同 (agree — the comment should be removed)",
    "abstain": "弃权 (abstain)",
    "dissenting": "反对 (dissent — the comment should stay)",
}


def vote_discussion(discussion_id: str, vote: str) -> dict[str, Any]:
    """Cast a vote on an agora discussion (众裁投票).

    Args:
        discussion_id: The agora discussion ID.
        vote: One of 'affirmative', 'abstain', or 'dissenting'.

    Returns:
        Vote result dict with affirmative_count, dissenting_count,
        blind_test_wrong, blind_test_today_wrong_count.

    Raises:
        ValueError: If vote is not one of the valid values.
    """
    if vote not in VALID_VOTES:
        raise ValueError(f"Invalid vote '{vote}'. Must be one of: {', '.join(VALID_VOTES)}")

    url = f"{AGORA_BASE}/discussions/{discussion_id}/votes"
    body = {"vote": vote, "review": ""}
    resp = session.post(url, json=body)
    resp.raise_for_status()
    result = resp.json()
    return {
        "affirmative_count": result.get("affirmative_count", 0),
        "dissenting_count": result.get("dissenting_count", 0),
        "blind_test_wrong": result.get("blind_test_wrong", False),
        "blind_test_today_wrong_count": result.get("blind_test_today_wrong_count", 0),
        "vote": vote,
        "raw": result,
    }

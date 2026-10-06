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

import base64
import io
from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
from typing import Any

from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import (
    ZhihuLinkConverter,
    converter,
    image_src,
    replace_with_text,
)
from zhihu_cli.content.utils.llm_config import DEFAULT_MAX_IMAGE_SIDE

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


# ── comment images ─────────────────────────────────────────────────────────

#: Class Zhihu puts on the anchor that wraps a picture inside a comment.
COMMENT_IMG_CLASS = "comment_img"
#: Stands in for an embedded picture in the Markdown handed to an LLM.
COMMENT_IMAGE_PLACEHOLDER = "[图片]"
#: Upper bound on how many images a single comment contributes to an LLM call.
MAX_COMMENT_IMAGES = 4
#: Images larger than this are skipped rather than ballooning the prompt.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_IMAGE_MIME_BY_EXT: tuple[tuple[str, str], ...] = (
    (".png", "image/png"),
    (".gif", "image/gif"),
    (".webp", "image/webp"),
    (".bmp", "image/bmp"),
    (".jpeg", "image/jpeg"),
    (".jpg", "image/jpeg"),
)


@dataclass(frozen=True)
class CommentImage:
    """A comment picture held in memory as raw bytes.

    :param url: The CDN URL the bytes were downloaded from.
    :param data: The image bytes as received.
    :param mime: Best-guess MIME type for *data*.
    """

    url: str
    data: bytes
    mime: str

    def to_data_url(self) -> str:
        """Render the image as a base64 ``data:`` URL.

        :returns: A data URL suitable for a multimodal LLM message.
        """
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


_lxml_html_mod = None
_pil_image_mod = None


def _lxml_html():
    """Import and cache the ``lxml.html`` module on first use.

    ``lxml`` is a heavy C-extension dependency, so it is pulled in lazily
    rather than at module import time.

    :returns: The ``lxml.html`` module.
    """
    global _lxml_html_mod
    if _lxml_html_mod is None:
        from lxml import html as _lxml_html_mod
    return _lxml_html_mod


def _pil_image():
    """Import and cache the ``PIL.Image`` module on first use.

    ``Pillow`` is a heavy C-extension dependency used only when an image has
    to be re-encoded, so it is pulled in lazily rather than at module import
    time — this module is on the ``zhihu`` CLI startup path.

    :returns: The ``PIL.Image`` module.
    """
    global _pil_image_mod
    if _pil_image_mod is None:
        from PIL import Image as _pil_image_mod
    return _pil_image_mod


def _comment_html_root(content_html: str):
    """Parse a comment body into an lxml tree, or return None if unparseable.

    :param content_html: Raw ``content`` field of a comment.
    :returns: Root element, or None when there is no markup to walk.
    """
    if not content_html:
        return None
    text = content_html
    if "<" not in text and "&lt;" in text:
        # Some endpoints hand back an escaped body; recover the real markup.
        text = unescape(text)
    if "<" not in text:
        return None
    try:
        return _lxml_html().fromstring(text)
    except Exception:
        return None


def comment_to_markdown(content_html: str, *, image_placeholder: str = COMMENT_IMAGE_PLACEHOLDER) -> str:
    """Render a comment body as Markdown, collapsing embedded pictures.

    Every picture becomes *image_placeholder* instead of a Markdown image or
    link. A consumer that receives the picture itself finds the CDN URL
    redundant, and one that does not gains nothing from a bare URL — while
    the placeholder still marks where the picture sat and how many there are.

    :param content_html: Raw ``content`` field of a comment.
    :param image_placeholder: Text substituted for each embedded picture.
    :returns: Markdown text, or the raw body when it cannot be parsed.
    """
    root = _comment_html_root(content_html)
    if root is None:
        return content_html

    lxml_html = _lxml_html()
    # Wrap the fragment so it always has a parent: ``replace_with_text`` is a
    # no-op on a parentless element, and a comment that is nothing but an
    # image parses with that image as the root.
    wrapper = lxml_html.Element("div")
    wrapper.append(root)

    # Snapshot first: replacing an anchor detaches the <img> nested inside it.
    for element in list(wrapper.iter()):
        if element is wrapper or element.getparent() is None:
            continue  # the wrapper, or already removed by an earlier replacement
        kind = _image_kind(element)
        if kind == "picture":
            replace_with_text(element, image_placeholder)
        elif kind == "emoticon":
            # Keep the name: ``[doge]`` is a sentiment cue a moderator reads.
            replace_with_text(element, element.get("alt") or image_placeholder)

    converted = converter.convert(lxml_html.tostring(wrapper, encoding="unicode"))
    return converted.strip() or content_html


def _image_kind(element) -> str | None:
    """Classify an image-bearing element as a picture, emoticon, or formula.

    Zhihu marks three unrelated things up with image elements, and only a
    real picture is worth downloading or collapsing to a placeholder:
    emoticons carry their name in ``alt``, and formulas are left for the
    Markdown converter to render as LaTeX.

    :param element: An lxml element from a comment body.
    :returns: ``"picture"``, ``"emoticon"``, ``"formula"``, or None.
    """
    if not isinstance(element.tag, str):
        return None
    tag = element.tag.lower()
    classes = (element.get("class") or "").split()
    if tag == "a":
        return "picture" if COMMENT_IMG_CLASS in classes else None
    if tag != "img":
        return None
    if element.get("eeimg"):
        return "formula"
    alt = (element.get("alt") or "").strip()
    # Zhihu's emoticon class, plus a fallback for markup that omits it: an
    # alt of the form "[doge]" is an emoticon name, not a picture caption.
    if "emoticon" in classes or (alt.startswith("[") and alt.endswith("]")):
        return "emoticon"
    return "picture"


def extract_comment_image_urls(content_html: str) -> list[str]:
    """Extract the image URLs embedded in a comment body.

    Zhihu wraps comment pictures in an anchor rather than an ``<img>`` tag::

        <a href="https://pic2.zhimg.com/v2-xxx_qhd.jpeg" class="comment_img">查看图片</a>

    Plain ``<img>`` tags are picked up too, resolving the real URL through
    :func:`~zhihu_cli.content.utils.html2markdown.image_src` so lazy-loaded
    images yield the picture rather than its placeholder.

    :param content_html: Raw ``content`` field of a comment.
    :returns: Absolute image URLs in document order, de-duplicated.
    """
    root = _comment_html_root(content_html)
    if root is None:
        return []

    urls: list[str] = []
    for element in root.iter():
        if _image_kind(element) != "picture":
            continue  # skip emoticons and formulas
        if element.tag.lower() == "a":
            # Zhihu wraps comment pictures in an anchor rather than an <img>,
            # so the Markdown converter renders them as links; pick up the
            # href here instead.
            url = element.get("href", "")
        else:
            url = image_src(element)
        url = ZhihuLinkConverter.normalize_link(url.strip())
        if url.startswith("http") and url not in urls:
            urls.append(url)
    return urls


def _guess_image_mime(url: str, content_type: str) -> str:
    """Pick an image MIME type, preferring the response header over the URL suffix."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime.startswith("image/"):
        return mime
    path = url.split("?")[0].lower()
    for ext, guess in _IMAGE_MIME_BY_EXT:
        if path.endswith(ext):
            return guess
    return "image/jpeg"


def fetch_comment_images(
    urls: Iterable[str],
    *,
    max_images: int = MAX_COMMENT_IMAGES,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> list[CommentImage]:
    """Download comment images, keeping their bytes and guessed MIME type.

    Images go through the shared authenticated :data:`session`, so the Zhihu
    CDN sees the same cookies and headers as every other request. Downloads
    that fail, come back empty, or exceed *max_bytes* are skipped.

    :param urls: Image URLs, typically from :func:`extract_comment_image_urls`.
    :param max_images: Maximum number of images to fetch.
    :param max_bytes: Skip any image larger than this many bytes.
    :returns: Downloaded images ready to attach to a message.
    """
    images: list[CommentImage] = []
    for url in list(urls)[:max_images]:
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            body = resp.content
        except Exception:
            continue
        if not body or len(body) > max_bytes:
            continue
        mime = _guess_image_mime(url, resp.headers.get("content-type", ""))
        images.append(CommentImage(url=url, data=body, mime=mime))
    return images


def transcode_for_llm(
    image: CommentImage,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    max_side: int = DEFAULT_MAX_IMAGE_SIDE,
) -> CommentImage | None:
    """Re-encode an image in memory so a multimodal LLM will accept it.

    Two things trip the model up, and both are repaired here. The Zhihu CDN
    sometimes answers with something that is not a decodable picture — an
    anti-hotlink HTML page, say — while the URL suffix still claims
    ``image/jpeg``; and comment pictures are often screenshots of a whole
    thread, tall enough to blow past the model's per-side limit (see
    :data:`zhihu_cli.content.utils.llm_config.DEFAULT_MAX_IMAGE_SIDE`). The
    bytes are decoded with Pillow, re-encoded in a format the model accepts,
    and scaled down if any side is still too long.

    The work happens entirely in memory: no temporary file, no download
    directory, no disk write.

    Pictures carrying transparency become PNG; everything else (including
    CMYK and palette images) becomes JPEG. An animated GIF contributes its
    first frame only.

    :param image: An image as downloaded by :func:`fetch_comment_images`.
    :param max_bytes: Give up when the re-encoded result exceeds this size.
    :param max_side: Scale down until the longest side fits this many pixels.
    :returns: A new :class:`CommentImage` with the same URL and the re-encoded
        bytes, or None when the input cannot be decoded or encoded, or when
        Pillow itself is unavailable.
    """
    try:
        pil_image = _pil_image()
        with pil_image.open(io.BytesIO(image.data)) as opened:
            has_alpha = opened.mode in ("RGBA", "LA") or (opened.mode == "P" and "transparency" in opened.info)
            reencoded = opened.convert("RGBA" if has_alpha else "RGB")
        mime, fmt, save_kwargs = ("image/png", "PNG", {}) if has_alpha else ("image/jpeg", "JPEG", {"quality": 90})
        width, height = reencoded.size
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / longest
            reencoded = reencoded.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                pil_image.LANCZOS,
            )
        buffer = io.BytesIO()
        reencoded.save(buffer, format=fmt, **save_kwargs)
        data = buffer.getvalue()
    except Exception:
        return None
    if len(data) > max_bytes:
        return None
    return CommentImage(url=image.url, data=data, mime=mime)


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

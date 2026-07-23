"""Yanxuan (盐选) novel / premium content reader.

Fetches paginated text segments from Zhihu's next-content-render API
and assembles them for terminal reading.

Also supports parsing paid-column section pages
(``/market/paid_column/{id}/section/{id}``) via SSR state extraction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler

NEXT_CONTENT_RENDER = "https://api.zhihu.com/next-content-render"

#: Matches paid-column section URLs:
#: ``https://www.zhihu.com/market/paid_column/<column_id>/section/<section_id>``
PAID_COLUMN_SECTION_RE = re.compile(r"https?://(?:www\.)?zhihu\.com/market/paid_column/(\d+)/section/(\d+)")


def extract_url_token(input_str: str) -> str:
    """Extract answer ID (url_token) from a Zhihu URL or raw ID string.

    Supports:
    - Raw answer ID: ``"2541691985"``
    - Composite ID:   ``"question_id/2541691985"``
    - Full URL:       ``"https://www.zhihu.com/question/xxx/answer/2541691985"``
    - Paid column:    ``"https://www.zhihu.com/market/paid_column/<id>/section/<id>"``
    """
    # If it looks like a URL, parse it
    if input_str.startswith("http://") or input_str.startswith("https://"):
        # Paid column section URL
        pc_match = PAID_COLUMN_SECTION_RE.search(input_str)
        if pc_match:
            # Use section_id as the url_token
            return pc_match.group(2)

        # /question/<qid>/answer/<aid>
        m = re.search(r"/answer/(\d+)", input_str)
        if m:
            return m.group(1)
        # Try to extract from path as fallback
        parsed = urlparse(input_str)
        qs = parse_qs(parsed.query)
        if "url_token" in qs:
            return qs["url_token"][0]
        # Last resort: last numeric segment
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[-1].isdigit():
            return parts[-1]
        raise ValueError(f"Cannot extract answer ID from URL: {input_str}")

    # Composite ID like "question_id/answer_id"
    if "/" in input_str:
        return input_str.split("/")[1]

    return input_str


def is_paid_column_section_url(url: str) -> bool:
    """Check if *url* is a paid-column section page URL."""
    return bool(PAID_COLUMN_SECTION_RE.search(url))


def _extract_card_meta(card_segment: dict[str, Any]) -> dict[str, str]:
    """Extract title and brand info from a card-type segment."""
    card = card_segment.get("card", {})
    extra_raw = card.get("extra_info", "{}")
    try:
        extra = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
    except (json.JSONDecodeError, TypeError):
        return {}

    meta: dict[str, str] = {}
    # Brand name (e.g. "盐言故事")
    za = extra.get("za", {})
    brand = za.get("brand_type", "")
    if brand:
        meta["brand"] = brand

    # Title from vip_head_line
    vip = extra.get("vip_head_line", {})
    # big_text is the brand label (e.g. "盐言故事"), text is the story name
    brand_label = vip.get("big_text", "") or ""
    if brand_label and not meta.get("brand"):
        meta["brand"] = brand_label

    story_name = vip.get("text", "")
    if story_name:
        meta["title"] = story_name

    # Copyright
    copyright_ = extra.get("copyright", "")
    if copyright_:
        meta["copyright"] = copyright_

    return meta


def _segment_text(seg: dict[str, Any]) -> str:
    """Extract text content from any segment type."""
    seg_type = seg.get("type", "paragraph")

    if seg_type == "card":
        # Card is metadata, not content — return empty
        return ""

    block = seg.get(seg_type, {})
    if isinstance(block, dict):
        return block.get("text", "")
    return ""


def _segment_marks(seg: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract marks from any segment type."""
    seg_type = seg.get("type", "paragraph")
    block = seg.get(seg_type, {})
    if isinstance(block, dict):
        return block.get("marks", [])
    return []


# ── SSR page parsing (paid column sections) ──────────────────────────────────


def _extract_ssr_state(html: str) -> dict[str, Any]:
    """Extract the SSR Redux state JSON from a manuscript/paid-column page.

    The state is embedded as a flat JSON object inside the HTML.  We locate
    it by scanning for the ``__connectedAutoFetch`` key which is unique to
    the manuscript SSR payload.

    :param html: Raw HTML of the manuscript page.
    :returns: Parsed SSR state dict.
    :raises ValueError: If the SSR state cannot be found or parsed.
    """
    pos = html.find("__connectedAutoFetch")
    if pos < 0:
        raise ValueError("Cannot find SSR state marker '__connectedAutoFetch' in page HTML")

    # Walk backwards to find the opening brace of the outermost JSON object
    depth = 0
    start = pos
    for i in range(pos, -1, -1):
        if html[i] == "}":
            depth += 1
        elif html[i] == "{":
            depth -= 1
            if depth < 0:
                start = i
                break

    # Walk forwards to find the matching closing brace
    depth = 0
    end = pos
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    state_json = html[start:end]
    try:
        return json.loads(state_json)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse SSR state JSON: {e}") from e


def _parse_manuscript_from_ssr(state: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Parse manuscript content from a paid-column section SSR state.

    Extracts metadata (title, author, brand) and segment list from the
    ``__connectedAutoFetch.manuscript.data.manuscriptData`` subtree.

    .. note::

       The manuscript text is **font-scrambled** (a custom substitution
       cipher where glyphs are swapped via an obfuscated font file).
       Descrambling requires decoding the ``font.base64`` / ``pFont.base64``
       fields — this is not yet implemented.  Returned segments contain
       the scrambled text as-is.

    :param state: Parsed SSR state dict.
    :returns: Tuple of ``(meta, segments)`` — same shape as
              :func:`fetch_yanxuan_segments`.
    """
    ms = state["__connectedAutoFetch"]["manuscript"]
    md = ms["data"]["manuscriptData"]

    # ── metadata ──────────────────────────────────────────────────────────
    pc = md.get("paid_column", {})
    authors = pc.get("authors", [])
    author_name = authors[0].get("name", "") if authors else ""

    meta: dict[str, str] = {
        "title": md.get("recommend_title") or md.get("title", ""),
        "brand": "盐选专栏",
        "column_title": pc.get("title", ""),
    }
    if author_name:
        meta["author"] = author_name
    section_type = md.get("section_type", "")
    if section_type:
        meta["section_type"] = section_type
    meta["like_count"] = str(md.get("like_count", 0))

    # ── segments ──────────────────────────────────────────────────────────
    segments: list[dict[str, Any]] = []
    ptags = md.get("pTagList", [])

    for i, p_html in enumerate(ptags):
        # Strip HTML tags to get the (possibly scrambled) plain text
        text = re.sub(r"<[^>]+>", "", p_html).strip()
        # Skip the vip yxcode watermark paragraph
        if text.startswith("备案号:") or text.startswith("备案号："):
            continue
        if text:
            segments.append(
                {
                    "id": str(i),
                    "type": "paragraph",
                    "text": text,
                    "marks": [],
                }
            )

    return meta, segments


def _fetch_paid_column_section_page(url: str) -> str:
    """Fetch the full HTML of a paid-column section page.

    Uses the global :data:`session` so authentication headers and ZSE
    signing are applied automatically.

    :param url: The full paid-column section URL.
    :returns: Raw HTML string.
    :raises requests.HTTPError: If the page cannot be fetched.
    """
    resp = session.get(url, headers={"User-Agent": session.headers.get("User-Agent", "")})
    resp.raise_for_status()
    return resp.text


def fetch_paid_column_section_segments(
    url: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Fetch and parse a paid-column section page into segments.

    This is the SSR-based equivalent of :func:`fetch_yanxuan_segments`
    for URLs matching ``/market/paid_column/*/section/*``.  It fetches
    the page HTML, extracts the SSR Redux state, and parses the embedded
    manuscript data.

    :param url: Full URL of the paid-column section page.
    :returns: Tuple of ``(meta, segments)`` with the same shape as
              :func:`fetch_yanxuan_segments`.
    :raises ValueError: If the URL does not match the expected pattern.
    :raises requests.HTTPError: If the page fetch fails.
    """
    match = PAID_COLUMN_SECTION_RE.search(url)
    if not match:
        raise ValueError(f"URL does not match paid column section pattern: {url}")

    html = _fetch_paid_column_section_page(url)
    state = _extract_ssr_state(html)
    return _parse_manuscript_from_ssr(state)


def fetch_yanxuan_segments(
    url_token: str,
    offset: int = 0,
    content_type: str = "answer",
    max_segments: int | None = None,
    max_pages: int | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Fetch segments from the next-content-render API.

    Args:
        url_token: The answer ID (url_token) for the yanxuan content.
        offset: Starting segment offset (default 0 = from beginning).
        content_type: Content type (default ``"answer"``).
        max_segments: Maximum total segments to return (optional).
        max_pages: Maximum number of API pages to fetch (optional).

    Returns:
        Tuple of ``(meta, segments)`` where *meta* is a dict with title/brand
        info from the header card, and *segments* is a list of dicts each
        with keys: id, type, text, marks.
    """
    meta: dict[str, str] = {}
    initial_url = (
        f"{NEXT_CONTENT_RENDER}?{urlencode({'offset': offset, 'url_token': url_token, 'content_type': content_type})}"
    )

    # Track pagination state in closures
    state: dict[str, int] = {"next_offset": offset, "pages": 0}

    def parse_segments(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for seg in data.get("segments", []):
            seg_type = seg.get("type", "paragraph")
            # Extract card metadata on first encounter
            if seg_type == "card" and not meta:
                meta.update(_extract_card_meta(seg))
            yield {
                "id": seg.get("id", ""),
                "type": seg_type,
                "text": _segment_text(seg),
                "marks": _segment_marks(seg),
            }

    def extract_next(data: dict[str, Any]) -> str | None:
        # paging is a JSON-encoded string — parse it
        paging_raw = data.get("paging", "{}")
        paging = json.loads(paging_raw) if isinstance(paging_raw, str) else paging_raw
        if paging.get("is_end", False):
            return None
        state["pages"] += 1
        if max_pages is not None and state["pages"] >= max_pages:
            return None
        # Advance offset by number of segments in this page
        state["next_offset"] += len(data.get("segments", []))
        return (
            f"{NEXT_CONTENT_RENDER}?"
            f"{urlencode({'offset': state['next_offset'], 'url_token': url_token, 'content_type': content_type})}"
        )

    all_segments: list[dict[str, Any]] = []
    for seg in stream_handler(initial_url, parse_segments, extract_next):
        all_segments.append(seg)
        if max_segments is not None and len(all_segments) >= max_segments:
            break

    return meta, all_segments


def segments_to_text(segments: list[dict[str, Any]]) -> str:
    """Convert segments to plain text with paragraph breaks.

    Entity marks are rendered inline as ``[word](url)`` style references.
    Card segments are skipped. Blockquote segments are rendered with ``>`` prefix.
    """
    lines: list[str] = []
    for seg in segments:
        seg_type = seg.get("type", "paragraph")
        if seg_type == "card":
            continue

        text = seg.get("text", "")
        marks = seg.get("marks", [])

        if marks:
            # Apply marks in reverse order so indices stay valid
            for mark in sorted(marks, key=lambda m: m.get("start_index", 0), reverse=True):
                start = mark.get("start_index", 0)
                end = mark.get("end_index", 0)
                ew = mark.get("entity_word", {})
                word = ew.get("word", text[start:end])
                url = ew.get("url", "")
                if url:
                    replacement = f"[{word}]({url})"
                else:
                    replacement = word
                text = text[:start] + replacement + text[end:]

        if seg_type == "blockquote":
            # Prefix each line with "> " for markdown blockquote rendering
            quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in text.split("\n"))
            lines.append(quoted)
        else:
            lines.append(text)

    return "\n\n".join(lines)


def segments_to_json(segments: list[dict[str, Any]]) -> str:
    """Dump segments as formatted JSON."""
    return json.dumps(segments, ensure_ascii=False, indent=2)

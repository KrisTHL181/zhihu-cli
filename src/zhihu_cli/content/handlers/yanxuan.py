"""Yanxuan (盐选) novel / premium content reader.

Fetches paginated text segments from Zhihu's next-content-render API
and assembles them for terminal reading.

Also supports parsing paid-column section pages
(``/market/paid_column/{id}/section/{id}``) via SSR state extraction.

Paid-column sections ship the body with a per-response glyph-substitution
cipher; :func:`_parse_manuscript_from_ssr` removes it with the subset font
that came with the response.  See
:mod:`zhihu_cli.content.utils.yanxuan_font`.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
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


#: Matches one ``<p>…</p>`` paragraph of the SSR ``manuscript`` field.
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)

#: Matches any HTML tag left inside a paragraph once it has been extracted.
_TAG_RE = re.compile(r"<[^>]+>")

#: The trailing watermark paragraph of every manuscript body.
_WATERMARK_PREFIX = "备案号"


def _paragraphs_from_manuscript(manuscript: str, p_tag_list: list[str]) -> list[str]:
    """Build the body paragraphs of a manuscript, in reading order.

    The SSR payload carries the body twice: ``manuscript`` as one HTML blob with
    one ``<p>`` per paragraph, and ``pTagList`` as a list of paragraph fragments.
    ``manuscript`` is the one that is actually populated, so it wins, and
    ``pTagList`` is only a fallback.  The trailing ``备案号:`` watermark
    paragraph is dropped.

    :param manuscript: Value of the ``manuscript`` field.
    :param p_tag_list: Value of the ``pTagList`` field.
    :returns: Paragraph plain texts, watermark excluded.
    """
    if manuscript:
        fragments = _PARAGRAPH_RE.findall(manuscript)
    else:
        fragments = list(p_tag_list)

    paragraphs: list[str] = []
    for fragment in fragments:
        text = html.unescape(_TAG_RE.sub("", fragment)).strip()
        if text:
            paragraphs.append(text)

    while paragraphs and paragraphs[-1].startswith(_WATERMARK_PREFIX):
        paragraphs.pop()
    return paragraphs


def _decode_manuscript(paragraphs: list[str], font_base64: str, bold_base64: str) -> list[str]:
    """Undo the per-response glyph-substitution cipher on *paragraphs*.

    The subset font that draws the substituted glyphs travels with the response,
    so it is the only thing that can decode them — see
    :mod:`zhihu_cli.content.utils.yanxuan_font` for the mechanism.  The regular
    font carries the body; the bold one is the fallback when it is absent.

    :param paragraphs: Body paragraphs, still scrambled.
    :param font_base64: Value of the ``font.base64`` field.
    :param bold_base64: Value of the ``font.bold_base64`` field.
    :returns: The paragraphs with substituted characters resolved.  When no font
        is shipped the paragraphs come back untouched.
    """
    payload = font_base64 or bold_base64
    if not payload:
        return paragraphs

    # Imported here rather than at module scope: the decoder pulls in Pillow.
    from zhihu_cli.content.utils.yanxuan_font import decode_manuscript

    return [decode_manuscript(paragraph, payload) for paragraph in paragraphs]


@dataclass(frozen=True)
class DecodeOutcome:
    """Which decoder produced the manuscript body, and how it went.

    :param backend: ``"local"`` or ``"server"``.
    :param warning: User-facing message when the server path was attempted and
        failed, otherwise ``None``.
    """

    backend: str
    warning: str | None


def _parse_manuscript_from_ssr(
    state: dict[str, Any], *, server_decode: bool = False
) -> tuple[dict[str, str], list[dict[str, Any]], DecodeOutcome]:
    """Parse manuscript content from a paid-column section SSR state.

    Extracts metadata (title, author, brand) and the paragraph list from the
    ``__connectedAutoFetch.manuscript.data.manuscriptData`` subtree, undoing the
    font-substitution cipher that scrambles the body — see
    :mod:`zhihu_cli.content.utils.yanxuan_font`.

    .. note::

       The obfuscation is an anti-scraping layer, not a paywall: the full text
       is delivered (``authentication_result`` is true and ``truncate_ratio`` is
       1), so decoding the shipped glyphs recovers the manuscript as published.

    :param state: Parsed SSR state dict.
    :param server_decode: When true, try Zhihu's own decode endpoint first and
        fall back to the local glyph decoder on any failure.
    :returns: Tuple of ``(meta, segments, outcome)``.  *meta* and *segments*
              have the same shape as :func:`fetch_yanxuan_segments`; *outcome*
              reports which decoder was used.
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
    font = md.get("font") or {}
    paragraphs = _paragraphs_from_manuscript(md.get("manuscript") or "", md.get("pTagList") or [])

    # The server path needs only ``font.type``, so it is a sibling of
    # ``_decode_manuscript`` rather than a mode of it — that keeps the
    # "no font payload -> return untouched" guard from swallowing it.
    outcome = DecodeOutcome("local", None)
    if server_decode:
        try:
            from zhihu_cli.content.utils.yanxuan_server_decode import decode_texts

            paragraphs = decode_texts(paragraphs, font.get("type") or "")
            outcome = DecodeOutcome("server", None)
        except Exception as exc:  # broad on purpose; BaseException still propagates
            outcome = DecodeOutcome("local", f"--server-decode failed ({exc}); used the local glyph decoder.")

    # ``paragraphs`` is only rebound on full success, so a failed server decode
    # can never yield empty or partial text.
    if outcome.backend != "server":
        paragraphs = _decode_manuscript(paragraphs, font.get("base64") or "", font.get("bold_base64") or "")

    segments: list[dict[str, Any]] = [
        {
            "id": str(i),
            "type": "paragraph",
            "text": text,
            "marks": [],
        }
        for i, text in enumerate(paragraphs)
    ]

    return meta, segments, outcome


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
    url: str, *, server_decode: bool = False
) -> tuple[dict[str, str], list[dict[str, Any]], DecodeOutcome]:
    """Fetch and parse a paid-column section page into segments.

    This is the SSR-based equivalent of :func:`fetch_yanxuan_segments`
    for URLs matching ``/market/paid_column/*/section/*``.  It fetches
    the page HTML, extracts the SSR Redux state, and parses the embedded
    manuscript data.

    :param url: Full URL of the paid-column section page.
    :param server_decode: When true, decode the body with Zhihu's own endpoint
        instead of the local glyph table, falling back on any failure.
    :returns: Tuple of ``(meta, segments, outcome)`` — *meta* and *segments*
              have the same shape as :func:`fetch_yanxuan_segments`, and
              *outcome* reports which decoder produced the body.
    :raises ValueError: If the URL does not match the expected pattern.
    :raises requests.HTTPError: If the page fetch fails.
    """
    match = PAID_COLUMN_SECTION_RE.search(url)
    if not match:
        raise ValueError(f"URL does not match paid column section pattern: {url}")

    html = _fetch_paid_column_section_page(url)
    state = _extract_ssr_state(html)
    return _parse_manuscript_from_ssr(state, server_decode=server_decode)


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

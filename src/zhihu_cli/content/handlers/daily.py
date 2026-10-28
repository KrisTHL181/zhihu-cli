"""Zhihu Daily (知乎日报) handler.

The daily paper lives on ``news-at.zhihu.com`` — a different host from the rest
of Zhihu.  Its API needs no login, no cookies, and no ZSE signature, so the
module sends only an API-version header, the usual configured User-Agent, and
— when ``ZHIHU_DAILY_TOKEN`` is set — a bearer token.  In particular it does not
send the Android app's device identifiers
(``x-uuid``, ``x-device``, ``za``), which are per-installation values rather
than API parameters.  That is also why it gets a session of its own instead of
the package-wide :data:`zhihu_cli.content.handlers.requests.session` singleton,
which signs requests to ``www.zhihu.com`` / ``api.zhihu.com`` and attaches the
active profile's cookies — neither of which belongs on a news-at.zhihu.com
request.

Everything in this module is stdlib-only at import time.  ``curl_cffi`` is
imported when the HTTP session is built and ``lxml`` when a story body is
parsed, so ``import zhihu_cli.content.handlers.daily`` never pays for either.

API version
-----------
``DAILY_API`` uses ``/api/4`` deliberately.  Listing endpoints
(``stories/latest``, ``stories/before/<date>``) answer anonymously on v2, v3 and
v4 only: v2 keeps the legacy schema (a ``news`` array with
``display_date``/``is_today``), v3 and v4 are byte-identical and use the modern
``stories`` schema, v5 and v6 report "client not released", and v7 answers 401
("login required") without a Bearer token/cookie.  Story detail
(``news/<id>``) additionally works on v5-v7 but breaks from v8 on with the same
"client not released" error.  v4 is therefore the only version that is stable
anonymously for both listing and story.  Note that ``news/latest`` and
``news/before/<date>`` are aliases of the two ``stories/`` paths, and that the
story detail path is ``news/<id>`` — ``stories/<id>`` is a 404.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

DAILY_API = "https://news-at.zhihu.com/api/4"

#: The API version is the only non-UA header the endpoint actually needs.
_BASE_HEADERS = {"x-api-version": "4"}

_EMPTY_HEADING_RE = re.compile(r"#{1,6}\s*")
_DATE_SEPARATOR_RE = re.compile(r"[-/\s]")

_session: Any = None
_session_lock = threading.Lock()


def _user_agent() -> str:
    """Return the configured User-Agent, or the project's desktop preset.

    ``get_user_agent()`` is the runtime override written by
    ``zhihu config user-agent``; ``DESKTOP_USER_AGENT`` is the same preset the
    login and cookie-import paths fall back to.  The import of ``auth_login``
    is deferred because that module imports ``curl_cffi`` at module level.

    :returns: A non-empty User-Agent string.
    """
    from zhihu_cli.content.handlers import get_user_agent
    from zhihu_cli.content.handlers.auth_login import DESKTOP_USER_AGENT

    return get_user_agent() or DESKTOP_USER_AGENT


def _get_session() -> Any:
    """Return the module's shared :class:`curl_cffi.requests.Session`.

    The session is built on first use under a lock, so concurrent callers share
    one connection pool.  It is deliberately independent of the package-wide
    session: that one adds ZSE signing and the active profile's cookies, which
    are meant for ``*.zhihu.com`` and must not leak onto ``news-at.zhihu.com``.

    :returns: The shared ``curl_cffi`` session.
    """
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                from curl_cffi import requests as curl_requests

                _session = curl_requests.Session(impersonate="chrome")
    return _session


def _get(path: str, *, missing: str | None = None) -> dict[str, Any]:
    """GET ``DAILY_API/<path>`` and return the decoded JSON object.

    :param path: Path below :data:`DAILY_API`, without a leading slash.
    :param missing: Message for a 404; a generic one is built when omitted.
    :returns: The response body as a dict.
    :raises ValueError: On a 404, or when the body is not valid JSON.
    """
    url = f"{DAILY_API}/{path}"
    headers = {"user-agent": _user_agent(), **_BASE_HEADERS}
    token = os.environ.get("ZHIHU_DAILY_TOKEN", "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"

    response = _get_session().get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        raise ValueError(missing or f"Zhihu Daily resource not found: {path}")
    response.raise_for_status()
    try:
        return response.json()
    except Exception as exc:
        raise ValueError(f"Invalid JSON from Zhihu Daily: {path}") from exc


def fetch_latest() -> dict[str, Any]:
    """Fetch today's Zhihu Daily issue.

    :returns: The raw envelope, with ``date``, ``stories`` and ``top_stories``.
    :raises ValueError: When the endpoint answers 404 or invalid JSON.
    """
    return _get("stories/latest")


def fetch_before(issue_date: str) -> dict[str, Any]:
    """Fetch the issue published strictly before *issue_date*.

    ``stories/before/<YYYYMMDD>`` returns the previous day's issue — asking for
    ``20261005`` yields the issue dated ``20261004``, not that day's issue.

    :param issue_date: ``YYYYMMDD``, ``YYYY-MM-DD`` or ``YYYY/MM/DD``.
    :returns: The raw envelope, with ``date`` and ``stories``.
    :raises ValueError: When the date is malformed or not a real calendar date,
        or when no issue exists before it.
    """
    normalized = _DATE_SEPARATOR_RE.sub("", issue_date.strip())
    if not re.fullmatch(r"\d{8}", normalized):
        raise ValueError(f"Invalid date {issue_date!r}: expected YYYYMMDD, YYYY-MM-DD, or YYYY/MM/DD")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError:
        raise ValueError(f"Invalid date {issue_date!r}: not a real calendar date") from None

    return _get(
        f"stories/before/{normalized}",
        missing=f"No Zhihu Daily issue before {normalized}",
    )


def fetch_story(story_id: int | str) -> dict[str, Any]:
    """Fetch one Zhihu Daily story.

    The story body comes from ``news/<id>``; ``stories/<id>`` does not exist.

    :param story_id: A positive numeric story ID.
    :returns: The raw story object, including its HTML ``body``.
    :raises ValueError: When the ID is not a positive integer or the story does
        not exist.
    """
    sid = str(story_id).strip()
    if not re.fullmatch(r"\d+", sid) or int(sid) <= 0:
        raise ValueError(f"Invalid Zhihu Daily story ID: {story_id!r}")

    return _get(f"news/{sid}", missing=f"Zhihu Daily story not found: {sid}")


def _extract_author(body: str) -> str:
    """Return the author name embedded in a story body.

    Daily story HTML carries ``<span class="author">游旭东，</span>`` — with a
    trailing Chinese comma.  Any parse failure degrades to an empty string.

    :param body: The story's HTML body.
    :returns: The trimmed author name, or ``""``.
    """
    if not body:
        return ""
    try:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(body)
        nodes = tree.xpath('//span[@class="author"]/text()')
    except Exception:
        return ""
    if not nodes:
        return ""
    return nodes[0].strip().rstrip("，,").strip()


def _story_date(story: dict[str, Any]) -> str:
    """Return the story's publish date as ``YYYY-MM-DD``.

    The story object carries ``publish_time`` (and usually ``create_time``) as
    Unix timestamps.  When neither is usable — the field is absent on some
    payloads — today's date is returned so the metadata stays well-formed.

    :param story: A raw story object.
    :returns: An ISO date string.
    """
    for key in ("publish_time", "create_time"):
        timestamp = story.get(key)
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            try:
                return datetime.fromtimestamp(timestamp).date().isoformat()
            except (OSError, OverflowError, ValueError):
                continue
    return date.today().isoformat()


def story_metadata(story: dict[str, Any]) -> dict[str, Any]:
    """Build the metadata block for a story.

    :param story: A raw story object from :func:`fetch_story`.
    :returns: A dict with ``title``, ``author``, ``created``, ``story_id``,
        ``url`` and — when the payload has one — ``image``.
    """
    story_id = story.get("id", "")
    url = story.get("url") or (f"https://daily.zhihu.com/story/{story_id}" if story_id else "")
    metadata: dict[str, Any] = {
        "title": story.get("title") or "untitled",
        "author": _extract_author(story.get("body", "") or ""),
        "created": _story_date(story),
        "story_id": story_id,
        "url": url,
    }
    image = story.get("image")
    if image:
        metadata["image"] = image
    return metadata


def story_markdown(story: dict[str, Any]) -> str:
    """Convert a story's HTML body to Markdown.

    ``PageToMarkdown`` leaves an empty ``##`` line behind for Daily's empty
    ``<h2 class="question-title">``; those lines are dropped and the resulting
    runs of blank lines collapsed.

    :param story: A raw story object from :func:`fetch_story`.
    :returns: The story body as Markdown (empty when the payload has no body).
    """
    body = story.get("body", "") or ""
    if not body:
        return ""

    from zhihu_cli.content.utils.html2markdown import PageToMarkdown

    markdown = PageToMarkdown(skip_empty=True).convert(body, story.get("url", ""))
    lines = [line for line in markdown.splitlines() if not _EMPTY_HEADING_RE.fullmatch(line.strip())]
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return markdown.strip()


def scrape_story(story_id: int | str) -> tuple[dict[str, Any], str]:
    """Fetch a story and return ``(metadata, markdown)``.

    :param story_id: A positive numeric story ID.
    :returns: The metadata dict from :func:`story_metadata` and the Markdown
        from :func:`story_markdown`.
    :raises ValueError: When the ID is invalid or the story does not exist.
    """
    story = fetch_story(story_id)
    return story_metadata(story), story_markdown(story)

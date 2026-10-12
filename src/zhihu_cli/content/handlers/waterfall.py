from __future__ import annotations

import json
import warnings
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.wait import wait

PAGE_SIZE: int = 20
"""Items requested per API page.

Page size is an API-level detail, not a user-facing knob: the CLI exposes only
``-n/--max`` (a cap on the *total* number of items).  This module owns the
value so every handler paginates identically.
"""


def _set_query_param(url: str, key: str, value: Any) -> str:
    """Return ``url`` with the query parameter ``key`` set to ``value``.

    Any existing value for ``key`` is replaced.  Blank query values are
    preserved (e.g. a trailing ``offset=``).
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    query.append((key, str(value)))
    return urlunparse(parts._replace(query=urlencode(query)))


def should_suppress_incomplete_warning() -> bool:
    """Check if the environment variable to suppress incomplete stream warnings is set."""
    import os

    env = os.getenv("ZHIHU_CLI_SUPPRESS_INCOMPLETE_WARNING", "0") == "1"
    return env or (Path.home() / ".zhihu-cli" / "suppress-incomplete-warning").exists()


class _StreamStats:
    """Mutable bookkeeping shared between :class:`PageStream` and its page loop."""

    __slots__ = ("totals", "truncated", "yielded")

    def __init__(self) -> None:
        self.totals: int | None = None
        self.yielded = 0
        self.truncated = False


class PageStream(Iterator[Any]):
    """Iterable returned by :func:`stream_handler`, carrying pagination metadata.

    :ivar totals: Total number of items reported by the API, or ``None`` when the
        API never reported a (non-zero) total.
    :ivar yielded: Number of items produced so far.
    :ivar truncated: Whether pagination stopped early because ``max_items`` was
        reached.
    """

    def __init__(self, source: Iterator[Any], stats: _StreamStats) -> None:
        self._source = source
        self._stats = stats
        self._exhausted = False

    def __iter__(self) -> PageStream:
        return self

    def __next__(self) -> Any:
        if self._exhausted:
            raise StopIteration
        try:
            return next(self._source)
        except StopIteration:
            self._exhausted = True
            # ── natural end of stream — check completeness ──────────────────
            # Skip the warning when pagination was intentionally capped by
            # ``max_items``.  A consumer that abandons the stream never gets
            # here at all, so it never sees the warning either.
            totals = self._stats.totals
            if totals is not None and self._stats.yielded < totals and not self._stats.truncated:
                missing = totals - self._stats.yielded
                if not should_suppress_incomplete_warning():
                    warnings.warn(
                        f"API reported {totals} total items but only {self._stats.yielded} were returned "
                        f"(missing {missing}).",
                        stacklevel=2,
                    )
            raise

    @property
    def totals(self) -> int | None:
        """Total number of items reported by the API (``None`` when unknown)."""
        return self._stats.totals

    @property
    def yielded(self) -> int:
        """Number of items produced so far."""
        return self._stats.yielded

    @property
    def truncated(self) -> bool:
        """Whether the stream stopped early because ``max_items`` was reached."""
        return self._stats.truncated


def _paginate(
    initial_url: str,
    parser: Callable[[dict[str, Any]], Iterable[Any]],
    extract_next: Callable[[dict[str, Any]], str | None] | None,
    delay: float,
    limit: int | None,
    max_items: int | None,
    skip_app_headers: bool,
    stats: _StreamStats,
) -> Iterator[Any]:
    """Drive the pagination loop, yielding parsed items and updating ``stats``."""
    current_url = initial_url
    if limit is not None:
        current_url = _set_query_param(current_url, "limit", limit)

    api_totals = 0
    yielded_count = 0
    stopped_by_limit = False

    while current_url and (max_items is None or yielded_count < max_items):
        resp = session.get(current_url, skip_app_headers=skip_app_headers)
        resp.raise_for_status()
        data = resp.json()

        paging_raw = data.get("paging", {})
        # Some endpoints (e.g. next-content-render) return paging as a
        # JSON-encoded string rather than a dict — normalise it here.
        if isinstance(paging_raw, str):
            try:
                paging = json.loads(paging_raw)
            except (json.JSONDecodeError, TypeError):
                paging = {}
        else:
            paging = paging_raw
        # Capture totals from the first page that reports a non-zero value.
        if api_totals == 0:
            api_totals = paging.get("totals", 0) or 0
            stats.totals = api_totals or None

        for item in parser(data):
            yielded_count += 1
            stats.yielded = yielded_count
            yield item
            if max_items is not None and yielded_count >= max_items:
                stopped_by_limit = True
                stats.truncated = True
                break

        if stopped_by_limit:
            break

        if extract_next:
            current_url = extract_next(data)
        else:
            if paging.get("is_end", True):
                current_url = None
            else:
                current_url = paging.get("next")

        if current_url:
            current_url = current_url.replace("http://", "https://")

        wait(delay)


def stream_handler(
    initial_url: str,
    parser: Callable[[dict[str, Any]], Iterable[Any]],
    extract_next: Callable[[dict[str, Any]], str | None] | None = None,
    delay: float = 1.0,
    limit: int | None = None,
    max_items: int | None = None,
    skip_app_headers: bool = False,
) -> PageStream:
    """Paginate a Zhihu API endpoint, yielding parsed items one by one.

    :param initial_url: The first page URL (includes ``offset=0``).
    :param parser: Called on each page's JSON body; must yield zero or more
        parsed items per page.
    :param extract_next: Optional custom pagination resolver.  When omitted,
        ``paging.next`` / ``paging.is_end`` is used.
    :param limit: Optional per-page item count.  When provided, it is injected
        into the request URL's ``limit`` query parameter (overriding any
        existing value), so callers need not build it into the base URL.
    :param max_items: Optional cap on the total number of items yielded.  When
        reached, pagination stops early without issuing the incomplete-stream
        warning.
    :param skip_app_headers: When :data:`True`, omit the ``x-app-za`` /
        ``x-app-version`` app-identity headers.  Needed for endpoints
        (e.g. the articles list) that return 500 when ``x-app-za`` is
        present.  Works through both the daemon proxy and direct session.
    :returns: A :class:`PageStream` — iterable over the parsed items, and
        carrying ``totals`` / ``yielded`` / ``truncated`` pagination metadata.
    """
    stats = _StreamStats()
    source = _paginate(
        initial_url,
        parser,
        extract_next,
        delay,
        limit,
        max_items,
        skip_app_headers,
        stats,
    )
    return PageStream(source, stats)

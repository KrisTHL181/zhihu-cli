"""zhihu-cli: a general-purpose Zhihu client for the terminal and for Python.

The public library surface is :mod:`zhihu_cli.api` — ``fetch_*`` / ``stream_*`` /
``scrape_*`` helpers plus :func:`use_profile` for picking an account — re-exported
here. :mod:`zhihu_cli.errors` defines the exception types they raise, and is the
only eagerly imported submodule (it has no third-party dependencies).

Every other re-export resolves lazily. Importing them eagerly would drag
:mod:`zhihu_cli.content` — and with it ``curl_cffi`` and ``lxml`` — into every
``import zhihu_cli``, which costs roughly a second of startup on every CLI
invocation. Submodule imports are unaffected either way.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from zhihu_cli import _import_trim  # noqa: F401  # must precede zhihu_cli.content

# Errors are plain class definitions with no third-party imports, so binding them
# for real beats a lazy lookup: ``except ZhihuError`` then costs nothing extra.
from zhihu_cli.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ZhihuError,
    ZhihuHTTPError,
)

if TYPE_CHECKING:
    # The names must exist for type checkers even though, at runtime, they are
    # resolved through __getattr__ (PEP 562) rather than bound here.
    from zhihu_cli.api import (
        direct_sessions,
        draft_to_markdown,
        fetch_article_item,
        fetch_child_comments,
        fetch_comments,
        fetch_daily_before,
        fetch_daily_latest,
        fetch_daily_story,
        fetch_feed,
        fetch_followees,
        fetch_followers,
        fetch_following_collections,
        fetch_following_columns,
        fetch_following_questions,
        fetch_following_topics,
        fetch_hot_list,
        fetch_member_activities,
        fetch_member_answers,
        fetch_member_articles,
        fetch_member_pins,
        fetch_member_profile,
        fetch_member_questions,
        fetch_notifications,
        fetch_question_log,
        fetch_read_history,
        fetch_root_comments,
        fetch_segment_comments,
        fetch_upvoters,
        fetch_yanxuan_segments,
        get_collection_meta,
        get_draft,
        get_inbox,
        get_item_stats,
        get_my_url_token,
        get_rate_limit,
        list_collection_contents,
        list_collections,
        list_drafts,
        rate_limit,
        scrape_answer_page,
        scrape_answers,
        scrape_article,
        scrape_daily_story,
        scrape_pin,
        scrape_question_data,
        scrape_zvideo,
        set_rate_limit,
        stream_follow_feed,
        stream_read_history,
        stream_recommend_feed,
        use_direct_session,
        use_profile,
    )
    from zhihu_cli.content import (
        ContentDownloader,
        PageToMarkdown,
        cache_manager,
        extract_config_from_curl,
        extract_metadata_from_html,
        sanitize_filename,
    )

__all__ = [
    # content-to-Markdown helpers, defined in zhihu_cli.content
    "ContentDownloader",
    "PageToMarkdown",
    "cache_manager",
    "extract_config_from_curl",
    "extract_metadata_from_html",
    "sanitize_filename",
    # exception types, bound for real above
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "ZhihuError",
    "ZhihuHTTPError",
    # the public library API, defined in zhihu_cli.api
    "fetch_hot_list",
    "scrape_article",
    "fetch_article_item",
    "scrape_question_data",
    "scrape_answers",
    "scrape_answer_page",
    "scrape_pin",
    "scrape_zvideo",
    "fetch_comments",
    "fetch_root_comments",
    "fetch_child_comments",
    "fetch_segment_comments",
    "fetch_upvoters",
    "fetch_question_log",
    "fetch_followees",
    "fetch_followers",
    "fetch_following_topics",
    "fetch_following_questions",
    "fetch_following_columns",
    "fetch_following_collections",
    "fetch_member_profile",
    "fetch_member_answers",
    "fetch_member_articles",
    "fetch_member_pins",
    "fetch_member_questions",
    "fetch_member_activities",
    "fetch_yanxuan_segments",
    "fetch_daily_latest",
    "fetch_daily_before",
    "fetch_daily_story",
    "scrape_daily_story",
    "get_item_stats",
    "fetch_notifications",
    "fetch_read_history",
    "stream_read_history",
    "fetch_feed",
    "stream_recommend_feed",
    "stream_follow_feed",
    "get_inbox",
    "get_my_url_token",
    "list_collections",
    "get_collection_meta",
    "list_collection_contents",
    "list_drafts",
    "get_draft",
    "draft_to_markdown",
    "use_profile",
    # request pacing and direct-session pinning, defined in zhihu_cli.api
    "set_rate_limit",
    "get_rate_limit",
    "rate_limit",
    "use_direct_session",
    "direct_sessions",
]

#: Exceptions, bound at import time by the imports above: they never take the
#: lazy path.  Everything else falls into one of the two buckets below.
_EAGER_EXPORTS = frozenset(("AuthError", "NotFoundError", "RateLimitError", "ZhihuError", "ZhihuHTTPError"))

#: Content-to-Markdown helpers, which live in :mod:`zhihu_cli.content`.  The
#: remaining names all come from :mod:`zhihu_cli.api`, so adding an export is a
#: one-line change to :data:`__all__`.
_CONTENT_EXPORTS = frozenset(
    (
        "ContentDownloader",
        "PageToMarkdown",
        "cache_manager",
        "extract_config_from_curl",
        "extract_metadata_from_html",
        "sanitize_filename",
    )
)

#: Public re-export → the submodule that actually defines it.
_LAZY_EXPORTS: dict[str, str] = {
    **dict.fromkeys(_CONTENT_EXPORTS, "zhihu_cli.content"),
    **dict.fromkeys(
        (name for name in __all__ if name not in _CONTENT_EXPORTS and name not in _EAGER_EXPORTS),
        "zhihu_cli.api",
    ),
}


def __getattr__(name: str) -> object:
    """Resolve a lazy re-export on first access (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # cache so later lookups skip this path
    return value


def __dir__() -> list[str]:
    """Report the lazy re-exports alongside the module's real attributes."""
    return sorted({*globals(), *__all__})

"""Public Python API for zhihu-cli.

This is the supported surface for using the package as a library::

    from zhihu_cli import fetch_hot_list, scrape_article, use_profile

    for item in fetch_hot_list(limit=10):
        print(item["title"])

    metadata, markdown = scrape_article("https://zhuanlan.zhihu.com/p/123456")

The names here are thin wrappers over the handler modules in
:mod:`zhihu_cli.content.handlers`.  Three things they give you that the handlers
do not:

* **A stable import path.** Handler modules are internal and move; this module
  is the promise.
* **One exception type.** Everything raised out of here is a
  :class:`~zhihu_cli.errors.ZhihuError` subclass, the causes of which are
  documented on :func:`_normalize`.  The handlers keep raising their own
  built-in types, so the CLI is unaffected.
* **Profile pinning.** :func:`use_profile` selects an account for a block,
  without touching the process-wide ``active_profile``.

Two conventions worth knowing:

* **Calls block.** There is no async variant — these functions do network I/O on
  the calling thread.  The ``stream_*`` functions return a lazy iterable, so
  their requests happen while you iterate.
* **Wrapper arguments are forwarded.** Each wrapper takes ``*args, **kwargs``
  and passes them to its handler unchanged, rather than restating the signature
  and drifting from it.  The handler named in the docstring documents the
  parameters — ``zhihu_cli.content.handlers.hot.fetch_hot_list`` for
  :func:`fetch_hot_list`, and so on.

The handlers are imported inside the wrapper bodies.  Importing one at module
level would pull :mod:`~zhihu_cli.content.handlers._session_core` — and with it
``curl_cffi`` and ``lxml`` — into ``import zhihu_cli``, which the package
deliberately keeps free of them.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from zhihu_cli.content.handlers.risk_control import ZhihuGateError
from zhihu_cli.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ZhihuError,
    ZhihuHTTPError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = [
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "ZhihuError",
    "ZhihuHTTPError",
    # ── public data — no login required ────────────────────────────────────
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
    "get_item_stats",
    # ── require a logged-in profile ────────────────────────────────────────
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
    # ── profile selection ──────────────────────────────────────────────────
    "use_profile",
    # ── request pacing ─────────────────────────────────────────────────────
    "set_rate_limit",
    "get_rate_limit",
    "rate_limit",
    "use_direct_session",
    "direct_sessions",
]

# ── plumbing ───────────────────────────────────────────────────────────────

#: Handler module name → the imported module, so a repeated call costs a dict
#: lookup instead of a second import.
_HANDLERS: dict[str, Any] = {}

#: Substrings that mark a :class:`RuntimeError` as risk control rather than, say,
#: a failed send.  The direct sessions raise one for both.
_RISK_CONTROL_MARKERS = ("403", "risk-control", "risk control", "captcha")


def _resolve(module_name: str, func_name: str) -> Any:
    """Import the handler module lazily and return one of its functions.

    :param module_name: Module under ``zhihu_cli.content.handlers``.
    :param func_name: Function within it.
    :returns: The handler function.
    """
    module = _HANDLERS.get(module_name)
    if module is None:
        module = importlib.import_module(f"zhihu_cli.content.handlers.{module_name}")
        _HANDLERS[module_name] = module
    return getattr(module, func_name)


def _message(exc: BaseException) -> str:
    """Return a non-empty message for *exc*."""
    return str(exc) or type(exc).__name__


def _is_http_error(exc: BaseException) -> bool:
    """Return True for curl_cffi's and the daemon's HTTP error classes.

    Matched by name rather than by ``isinstance``: importing ``curl_cffi`` costs
    ~265 ms, and the objects that raise these errors exist precisely so the
    daemon path never imports it.  Paying that on the error path would be the
    worst possible place.

    :param exc: The exception to classify.
    :returns: Whether it represents an HTTP-level error.
    """
    cls = type(exc)
    if cls.__name__ == "DaemonHTTPError":  # zhihu_cli.daemon.proxy raises this
        return True
    module = getattr(cls, "__module__", "") or ""
    return module.startswith("curl_cffi") and cls.__name__.endswith("HTTPError")


def _normalize(exc: BaseException) -> ZhihuError:
    """Translate a handler exception into the public error contract.

    :param exc: Whatever a handler raised.
    :returns: The equivalent :class:`~zhihu_cli.errors.ZhihuError`.  The original
        stays reachable as ``__cause__``.

    Mapping:

    * already a :class:`ZhihuError` → returned unchanged
    * :class:`~zhihu_cli.content.handlers.risk_control.ZhihuGateError` with the
      sign-in or account-verification gate → :class:`AuthError`; with the
      ``__zse_ck`` or risk-control gate → :class:`RateLimitError`
    * ``HTTPError`` / ``DaemonHTTPError`` with 403 → :class:`RateLimitError`
    * the same with 404 → :class:`NotFoundError`
    * any other ``HTTPError`` → :class:`ZhihuHTTPError`
    * ``PermissionError`` → :class:`AuthError`
    * ``RuntimeError`` mentioning risk control, HTTP 403, or a captcha →
      :class:`RateLimitError`; any other → :class:`ZhihuError`
    * ``ValueError`` — an unresolvable URL or a response missing its payload →
      :class:`NotFoundError`
    """
    if isinstance(exc, ZhihuError):
        return exc
    if isinstance(exc, ZhihuGateError):
        if exc.gate in ("unhuman", "signin"):
            return AuthError(_message(exc))
        return RateLimitError(_message(exc))

    status = getattr(getattr(exc, "response", None), "status_code", None)
    if _is_http_error(exc):
        if status == 403:
            return RateLimitError(_message(exc))
        if status == 404:
            return NotFoundError(_message(exc))
        return ZhihuHTTPError(_message(exc))
    if isinstance(exc, PermissionError):
        return AuthError(_message(exc))
    if isinstance(exc, RuntimeError):
        if any(marker in _message(exc).lower() for marker in _RISK_CONTROL_MARKERS):
            return RateLimitError(_message(exc))
        return ZhihuError(_message(exc))
    if isinstance(exc, ValueError):
        return NotFoundError(_message(exc))
    return ZhihuError(_message(exc))


class _StreamProxy:
    """Wrap a lazy handler stream so iteration raises the documented errors.

    :func:`_call` normalises what a handler raises, but a stream only issues its
    requests while the caller iterates — by then that ``try`` has returned.
    Attribute access is forwarded, so the pagination metadata a
    :class:`~zhihu_cli.content.handlers.waterfall.PageStream` carries
    (``totals``, ``yielded``, ``truncated``) stays reachable.
    """

    __slots__ = ("_source",)

    def __init__(self, source: Iterable[Any]) -> None:
        self._source = source

    def __iter__(self) -> Iterator[Any]:
        try:
            yield from self._source
        except ZhihuError:
            raise
        except Exception as exc:
            raise _normalize(exc) from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} → {self._source!r}>"


def _call(module_name: str, func_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a handler function, translating its errors to :class:`ZhihuError`.

    :param module_name: Module under ``zhihu_cli.content.handlers``.
    :param func_name: Function within it.
    :param args: Positional arguments for the handler.
    :param kwargs: Keyword arguments for the handler.
    :returns: The handler's return value; a lazy iterable is wrapped in a
        :class:`_StreamProxy` so errors raised during iteration are translated
        too.
    :raises ZhihuError: For any failure, per :func:`_normalize`.
    """
    func = _resolve(module_name, func_name)
    try:
        result = func(*args, **kwargs)
    except ZhihuError:
        raise
    except Exception as exc:
        raise _normalize(exc) from exc

    eager = (str, bytes, bytearray, list, tuple, dict, set, frozenset)
    if isinstance(result, Iterable) and not isinstance(result, eager):
        return _StreamProxy(result)
    return result


# ── hot list ───────────────────────────────────────────────────────────────


def fetch_hot_list(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the Zhihu hot list — :func:`zhihu_cli.content.handlers.hot.fetch_hot_list`."""
    return _call("hot", "fetch_hot_list", *args, **kwargs)


# ── articles and answers ───────────────────────────────────────────────────


def scrape_article(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, markdown)`` for an article — ``handlers.article.scrape_article``."""
    return _call("article", "scrape_article", *args, **kwargs)


def fetch_article_item(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return an article's raw API item — ``handlers.article.fetch_article_item``."""
    return _call("article", "fetch_article_item", *args, **kwargs)


def scrape_question_data(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, markdown)`` for a question — ``handlers.question.scrape_question_data``."""
    return _call("question", "scrape_question_data", *args, **kwargs)


def scrape_answers(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate a question's answers — :func:`zhihu_cli.content.handlers.question.scrape_answers`."""
    return _call("question", "scrape_answers", *args, **kwargs)


def scrape_answer_page(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, markdown)`` for one answer — ``handlers.question.scrape_answer_page``."""
    return _call("question", "scrape_answer_page", *args, **kwargs)


# ── pins and videos ────────────────────────────────────────────────────────


def scrape_pin(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, markdown)`` for a pin — :func:`zhihu_cli.content.handlers.pin.scrape_pin`."""
    return _call("pin", "scrape_pin", *args, **kwargs)


def scrape_zvideo(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, markdown)`` for a zvideo — ``handlers.zvideo.scrape_zvideo``."""
    return _call("zvideo", "scrape_zvideo", *args, **kwargs)


# ── comments, upvoters, and edit history ───────────────────────────────────


def fetch_comments(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return an item's comments — :func:`zhihu_cli.content.handlers.comments.fetch_comments`."""
    return _call("comments", "fetch_comments", *args, **kwargs)


def fetch_root_comments(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate an item's root comments — ``handlers.comments.fetch_root_comments``."""
    return _call("comments", "fetch_root_comments", *args, **kwargs)


def fetch_child_comments(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate the replies to one comment — ``handlers.comments.fetch_child_comments``."""
    return _call("comments", "fetch_child_comments", *args, **kwargs)


def fetch_segment_comments(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate an answer's segment comments — ``handlers.segment_comments.fetch_segment_comments``."""
    return _call("segment_comments", "fetch_segment_comments", *args, **kwargs)


def fetch_upvoters(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the members who upvoted an item — ``handlers.upvoter.fetch_upvoters``."""
    return _call("upvoter", "fetch_upvoters", *args, **kwargs)


def fetch_question_log(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a question's edit log — ``handlers.question_log.fetch_question_log``."""
    return _call("question_log", "fetch_question_log", *args, **kwargs)


# ── members: profiles, relationships, content ──────────────────────────────


def fetch_followees(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the members a member follows — ``handlers.following.fetch_followees``."""
    return _call("following", "fetch_followees", *args, **kwargs)


def fetch_followers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's followers — ``handlers.following.fetch_followers``."""
    return _call("following", "fetch_followers", *args, **kwargs)


def fetch_following_topics(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the topics a member follows — ``handlers.following.fetch_following_topics``."""
    return _call("following", "fetch_following_topics", *args, **kwargs)


def fetch_following_questions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the questions a member follows — ``handlers.following.fetch_following_questions``."""
    return _call("following", "fetch_following_questions", *args, **kwargs)


def fetch_following_columns(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the columns a member follows — ``handlers.following.fetch_following_columns``."""
    return _call("following", "fetch_following_columns", *args, **kwargs)


def fetch_following_collections(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the collections a member follows — ``handlers.following.fetch_following_collections``."""
    return _call("following", "fetch_following_collections", *args, **kwargs)


def fetch_member_profile(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Return a member's profile, or ``None`` — ``handlers.people.fetch_member_profile``."""
    return _call("people", "fetch_member_profile", *args, **kwargs)


def fetch_member_answers(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's answers — ``handlers.people.fetch_member_answers``."""
    return _call("people", "fetch_member_answers", *args, **kwargs)


def fetch_member_articles(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's articles — ``handlers.people.fetch_member_articles``."""
    return _call("people", "fetch_member_articles", *args, **kwargs)


def fetch_member_pins(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's pins — ``handlers.people.fetch_member_pins``."""
    return _call("people", "fetch_member_pins", *args, **kwargs)


def fetch_member_questions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the questions a member asked — ``handlers.people.fetch_member_questions``."""
    return _call("people", "fetch_member_questions", *args, **kwargs)


def fetch_member_activities(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's activity feed — ``handlers.people.fetch_member_activities``."""
    return _call("people", "fetch_member_activities", *args, **kwargs)


def get_my_url_token(*args: Any, **kwargs: Any) -> str:
    """Return the logged-in member's ``url_token`` — ``handlers.people.get_my_url_token``."""
    return _call("people", "get_my_url_token", *args, **kwargs)


# ── premium content and engagement stats ───────────────────────────────────


def fetch_yanxuan_segments(*args: Any, **kwargs: Any) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Return ``(meta, segments)`` for Yanxuan content — ``handlers.yanxuan.fetch_yanxuan_segments``."""
    return _call("yanxuan", "fetch_yanxuan_segments", *args, **kwargs)


def get_item_stats(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return engagement stats for a post — :func:`zhihu_cli.content.handlers.stats.get_item_stats`."""
    return _call("stats", "get_item_stats", *args, **kwargs)


# ── logged-in: notifications, history, feeds ───────────────────────────────


def fetch_notifications(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the logged-in account's notifications — ``handlers.notifications.fetch_notifications``."""
    return _call("notifications", "fetch_notifications", *args, **kwargs)


def fetch_read_history(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the logged-in account's read history — ``handlers.read_history.fetch_read_history``."""
    return _call("read_history", "fetch_read_history", *args, **kwargs)


def stream_read_history(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate the read history lazily — ``handlers.read_history.stream_read_history``."""
    return _call("read_history", "stream_read_history", *args, **kwargs)


def fetch_feed(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the recommend or follow feed — :func:`zhihu_cli.content.handlers.feed.fetch_feed`."""
    return _call("feed", "fetch_feed", *args, **kwargs)


def stream_recommend_feed(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate the recommend feed lazily — ``handlers.feed.stream_recommend_feed``."""
    return _call("feed", "stream_recommend_feed", *args, **kwargs)


def stream_follow_feed(*args: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
    """Iterate the follow feed lazily — :func:`zhihu_cli.content.handlers.feed.stream_follow_feed`."""
    return _call("feed", "stream_follow_feed", *args, **kwargs)


# ── logged-in: chat, collections, drafts ───────────────────────────────────


def get_inbox(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
    """Return ``(threads, total)`` for the chat inbox — ``handlers.chat.get_inbox``."""
    return _call("chat", "get_inbox", *args, **kwargs)


def list_collections(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return a member's collections — :func:`zhihu_cli.content.handlers.collection.list_collections`."""
    return _call("collection", "list_collections", *args, **kwargs)


def get_collection_meta(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return one collection's metadata — ``handlers.collection.get_collection_meta``."""
    return _call("collection", "get_collection_meta", *args, **kwargs)


def list_collection_contents(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Return the items inside a collection — ``handlers.collection.list_collection_contents``."""
    return _call("collection", "list_collection_contents", *args, **kwargs)


def list_drafts(*args: Any, **kwargs: Any) -> list[dict[Any, Any]]:
    """Return the drafts of one specific object — :func:`zhihu_cli.content.handlers.draft.list_drafts`."""
    return _call("draft", "list_drafts", *args, **kwargs)


def get_draft(*args: Any, **kwargs: Any) -> dict[Any, Any]:
    """Return one draft version — :func:`zhihu_cli.content.handlers.draft.get_draft`."""
    return _call("draft", "get_draft", *args, **kwargs)


def draft_to_markdown(*args: Any, **kwargs: Any) -> tuple[dict[Any, Any], str]:
    """Return ``(metadata, markdown)`` for a draft — ``handlers.draft.draft_to_markdown``."""
    return _call("draft", "draft_to_markdown", *args, **kwargs)


# ── profile selection ──────────────────────────────────────────────────────


@contextmanager
def use_profile(name: str) -> Iterator[None]:
    """Talk to Zhihu as the profile *name* for the duration of the block::

        from zhihu_cli import fetch_notifications, use_profile

        with use_profile("alt"):
            for thread in fetch_notifications():
                print(thread)

    Pinning is per-thread, so concurrent threads may each pin a different profile
    without rewriting the shared ``active_profile`` file or reloading the
    process-wide daemon session.  Naming the active profile is free: the existing
    session is reused.

    Two costs, both because the daemon holds one session for one profile: a
    non-active profile bypasses the daemon, paying a direct session (the
    ``_session_core`` import plus a TLS/QUIC handshake) on first use and caching
    it per profile from then on; and those cached sessions are never closed.
    Captcha handling is one process-wide gate, so it is *not* isolated per
    profile — see the README's limitations section.

    The pin is per thread and is not inherited: a worker started *inside* the
    block uses the process-wide session, so pin the profile inside the worker
    instead.  See
    :func:`~zhihu_cli.content.handlers.requests.use_profile` for the details.

    :param name: Name of an existing profile (``zhihu profile list``).
    :raises AuthError: If no such profile exists.
    """
    from zhihu_cli.content.handlers.requests import use_profile as _use_profile

    try:
        with _use_profile(name):
            yield
    except ZhihuError:
        raise
    except Exception as exc:
        raise _normalize(exc) from exc


# ── request pacing ─────────────────────────────────────────────────────────


def set_rate_limit(qps: float) -> None:
    """Set this process's request rate limit (requests/second).

    ``0`` disables limiting.  This is the per-invocation knob: it affects
    requests made by this process only.  Use ``zhihu config qps set`` for a
    persisted, account-wide limit that a running daemon enforces.

    :param qps: Requests per second; non-positive values mean unlimited.
    """
    from zhihu_cli.content.handlers.requests import set_rate_limit as _set

    _set(qps)


def get_rate_limit() -> float:
    """Return this process's current request rate limit (``0.0`` = unlimited)."""
    from zhihu_cli.content.utils.rate_limit import limiter

    return limiter.qps


@contextmanager
def rate_limit(qps: float) -> Iterator[None]:
    """Apply *qps* for the duration of a block, then restore the previous value::

        from zhihu_cli import rate_limit, fetch_hot_list

        with rate_limit(1):
            fetch_hot_list()

    Restoring in ``finally`` matters for long-lived library use: a raised
    request error must not leave the process permanently throttled at an
    unexpected rate.

    :param qps: Requests per second; ``<= 0`` disables limiting within the
        block.
    """
    previous = get_rate_limit()
    set_rate_limit(qps)
    try:
        yield
    finally:
        set_rate_limit(previous)


@contextmanager
def use_direct_session(profile: str | None = None) -> Iterator[Any]:
    """Pin a direct (non-daemon) session for the duration of a block::

        from zhihu_cli import use_direct_session

        with use_direct_session("alt") as session:
            # session.request(...) sends as that profile, bypassing the daemon
            ...

    The session is closed on exit.  This is the supported alternative to
    reaching into the handlers' private ``_ctx_session`` ContextVar when a
    caller needs the session object itself; see
    :func:`zhihu_cli.content.handlers.requests.use_direct_session`.

    :param profile: Profile whose headers to use, or ``None`` for the active
        profile.
    """
    from zhihu_cli.content.handlers.requests import use_direct_session as _use_direct

    with _use_direct(profile) as session_:
        yield session_


@contextmanager
def direct_sessions(
    profile: str | None = None,
    *,
    captcha_handler: str | None = None,
    request_timeout: float | None = None,
    configure: Iterable[Callable[[Any], None]] = (),
) -> Iterator[Any]:
    """Use one direct, non-daemon session per thread for the duration of a block::

        from concurrent.futures import ThreadPoolExecutor
        from zhihu_cli import direct_sessions

        with direct_sessions(captcha_handler="ignore", request_timeout=30.0) as ds:
            with ThreadPoolExecutor(max_workers=8, initializer=ds.initializer) as pool:
                list(pool.map(work, items))
        # every thread's session is closed here

    This is the supported way to make a worker pool bypass the daemon.  The
    process-wide daemon holds one session behind one lock, and a curl_cffi
    session must not be shared across threads; inside the block every thread
    that resolves the process session builds and caches its own instead.

    The mode is process-wide, so workers created anywhere while the block is
    active participate — unlike a context variable, which
    ``ThreadPoolExecutor`` workers do not inherit.  All sessions for a profile
    share one cookie jar, so a CAPTCHA resolved by one worker updates the
    cookies every other worker sends.

    Keep the block alive for the whole pool lifetime: sessions are closed at
    block exit, not when a worker thread ends (there is no reliable hook for
    the latter).  Scopes nest; only the outermost exit closes sessions.

    :param profile: Profile whose headers every session uses, or ``None`` for
        the active profile.  An enclosing :func:`use_profile` overrides this.
    :param captcha_handler: ``"auto"``, ``"prompt"``, or ``"ignore"``.  ``None``
        keeps the default (``"auto"``).
    :param request_timeout: Default request timeout in seconds for every
        session.  ``None`` keeps curl_cffi's default.
    :param configure: Callables run with each new session before use, e.g. to
        set extra headers.  They run once per thread session.
    :returns: A handle exposing ``initializer`` (for
        ``ThreadPoolExecutor``), ``current()``, and ``close_all()``.
    :raises RuntimeError: From ``current()``/``initializer`` if called after the
        block has exited.
    """
    from zhihu_cli.content.handlers.requests import direct_sessions as _direct_sessions

    with _direct_sessions(
        profile,
        captcha_handler=captcha_handler,
        request_timeout=request_timeout,
        configure=configure,
    ) as handle:
        yield handle

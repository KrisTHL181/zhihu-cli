"""HTTP session management — lightweight entry point.

This module provides a lazy :data:`session` singleton.  The module-level
``session`` name is a :class:`_LazySession` *proxy*, so submodules that do
``from zhihu_cli.content.handlers.requests import session`` at import time
stay cheap.  On first attribute access the proxy connects to the background
daemon (→ lightweight
:class:`~zhihu_cli.daemon.proxy.DaemonProxySession`, no curl_cffi needed).
If the daemon is unavailable it falls back to importing the heavy
:mod:`_session_core` module and building a direct :class:`ZhihuSession`.

All heavy imports (curl_cffi, lxml) are deferred to :mod:`_session_core`,
which is loaded *only* when a direct session is built or when calling
``fetch_page_html`` / ``get_page_state``.

:func:`use_profile` pins a session for one profile in a :class:`ContextVar`, so a
call inside the ``with`` block talks to that profile while every other thread
keeps the process-wide session.  See its docstring for why it does not go through
:func:`reload_session`.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

# ── lightweight (stdlib-only) imports ──────────────────────────────────────
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.utils import rate_limit
from zhihu_cli.errors import AuthError

if TYPE_CHECKING:
    from collections.abc import Iterator

# ── lazy re-exports ────────────────────────────────────────────────────────
#
# Module-level ``from requests import <name>`` triggers ``__getattr__``,
# so names that are frequently imported at module level are defined as
# real *wrapper* functions below — the heavy import only fires when the
# function is *called*, not when it is *imported*.
#
# Rarely-imported names (ZhihuSession, get_browser, …) stay in
# _LAZY_EXPORTS and resolve via __getattr__.

_LAZY_EXPORTS: dict[str, str] = {
    "ZSE93": "_session_core",
    "get_browser": "_session_core",
    "ZhihuSession": "_session_core",
    "CurlHttpVersion": "_session_core",
    "_resolve_user_agent": "_session_core",
    "_build_direct_session": "_session_core",
    "_is_captcha_endpoint": "_session_core",
    "_print_captcha_warning": "_session_core",
}

# ── module-level __getattr__ ───────────────────────────────────────────────


def __getattr__(name: str) -> Any:
    # ``session`` and ``requests`` are real module attributes (a
    # _LazySession proxy), so they never reach this function.
    if name == "headers":
        # Inside ``use_profile`` the mirror below belongs to a different
        # profile, so report the pinned session's own headers instead.
        ctx = _ctx_session.get()
        if ctx is not None:
            return ctx.headers
        _get_session()  # ensure headers is populated
        return _headers

    # ── lazy re-exports from _session_core ──────────────────────────
    if name in _LAZY_EXPORTS:
        mod_name = _LAZY_EXPORTS[name]
        import importlib

        mod = importlib.import_module(f"zhihu_cli.content.handlers.{mod_name}")
        return getattr(mod, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── real wrapper functions (import is cheap, call triggers heavy load) ────
#
# These are defined as actual module-level functions so that ``from
# requests import fetch_page_html`` does NOT trigger __getattr__ →
# _session_core import.  The heavy import happens inside the function
# body on first call.


def fetch_page_html(url: str) -> str:
    """Fetch a Zhihu page as HTML (uses the global session)."""
    url = url.replace("http://", "https://")
    response = _get_session().get(url)
    if response.status_code == 403 and "zh-zse-ck" in response.text:
        raise RuntimeError(f"Zhihu returned an HTML zh-zse-ck risk-control challenge for {url}")
    response.raise_for_status()
    return response.text


def fetch_json(url: str) -> Any:
    """Fetch a URL and decode its JSON response."""
    response = _get_session().get(url)
    response.raise_for_status()
    return response.json()


def get_page_state(html_text: str, key: str = "entities") -> dict[str, Any]:
    """Extract ``js-initialData`` from a Zhihu SSR page."""
    from zhihu_cli.content.handlers._session_core import get_page_state as _impl

    return _impl(html_text, key)


# ── lazy session state ─────────────────────────────────────────────────────

_session: Any = None
_headers: dict[str, str] = {}

#: Guards the ``_session`` / ``_headers`` globals.  A plain (non-reentrant)
#: lock is enough: :func:`_build_session` never calls back into
#: :func:`_get_session` — it talks to the daemon or to ``_session_core``
#: directly — so there is no re-entry path that could self-deadlock.
_session_lock = threading.Lock()

# ── per-context profile sessions (:func:`use_profile`) ─────────────────────
#
# Deliberately a ContextVar rather than ``switch_profile()`` + ``reload_session()``:
# ``CacheManager.switch_profile`` writes the ``active_profile`` *file*, i.e. state
# every other process on this machine reads, and on the daemon path a reload is
# pushed into the shared long-running daemon, replacing the session its other
# clients are using.  A ContextVar is per-thread, so two threads (or the threads
# of the out-of-tree consumer that uses this package as a library) can each pin
# their own profile without observing or disturbing the other's.

#: Session pinned by the enclosing :func:`use_profile`; ``None`` means "use the
#: process-wide session".  Note that a value of ``None`` *is* meaningful inside
#: ``use_profile`` — it re-pins the global session for a nested block — which is
#: why :func:`_get_session` reads it rather than checking a separate flag.
_ctx_session: ContextVar[Any | None] = ContextVar("zhihu_profile_session", default=None)

#: Profile name behind :data:`_ctx_session`, for the code paths that build their
#: own session instead of asking :func:`_get_session` for one.
_ctx_profile: ContextVar[str | None] = ContextVar("zhihu_profile_name", default=None)

#: Profile name → its direct session, so each profile pays the direct-session
#: build cost (heavy import, then the TLS/QUIC handshake) once per process.
_profile_session_cache: dict[str, Any] = {}
_profile_session_lock = threading.Lock()


class _LazySession:
    """Transparent proxy that builds the real session on first attribute use.

    Roughly 26 modules do ``from zhihu_cli.content.handlers.requests import
    session`` at import time.  Resolving that name eagerly would pull
    :mod:`_session_core` (curl_cffi + lxml, ~150 ms) into the startup path of
    every command — including ``--help`` and shell completion.  Binding this
    proxy instead keeps the import free.

    The real object is re-resolved on *every* access rather than cached, so a
    later :func:`reload_session` is picked up even by modules that imported
    ``session`` before the reload.  ``__slots__`` is empty on purpose: with no
    instance dict there is no ``self._obj`` lookup that could recurse back into
    ``__getattr__``.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_get_session(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(_get_session(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(_get_session(), name)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} → {_get_session()!r}>"


session: Any = _LazySession()
requests: Any = session


def _get_session() -> Any:
    """Return the session for the current context, creating it on first call.

    Thread-safe via double-checked locking.  Building the session performs
    network I/O (a daemon ping, a session reload) and creating a second one
    would leave the process with two competing sessions, so the build must
    happen exactly once.

    The unsynchronised fast path matters: :data:`session` is a
    :class:`_LazySession` proxy that re-resolves through here on *every*
    attribute access, so the common case must stay a plain attribute read.

    Inside ``with use_profile(...)`` the pinned session short-circuits both
    paths — the global singleton is neither read nor built.
    """
    global _session, _headers

    ctx = _ctx_session.get()
    if ctx is not None:
        return ctx

    if _session is not None:
        return _session

    with _session_lock:
        if _session is not None:
            return _session

        _session = _build_session()
        if _session is not None:
            try:
                _headers = dict(_session.headers)
            except Exception:
                pass
        return _session


def _build_session() -> Any:
    """Try daemon proxy first; fall back to direct :class:`ZhihuSession`.

    The daemon path is extremely fast (~1 ms) because it only uses stdlib
    (socket, json).  The direct path triggers a full import of
    :mod:`_session_core` (curl_cffi + lxml, ~200 ms).
    """
    # Resolve the persistent/env limit before any request can be sent.  This
    # is a no-op after ``main`` pinned an explicit ``--qps``/``ZHIHU_QPS``.
    rate_limit.configure_from_config()

    # ── check daemon config ──────────────────────────────────────────
    try:
        config = cache_manager.get_config()
        daemon_enabled = config.get("daemon", {}).get("enabled", True)
    except Exception:
        daemon_enabled = True

    if daemon_enabled:
        proxy: Any = None
        try:
            from zhihu_cli.daemon.proxy import DaemonProxySession

            proxy = DaemonProxySession()
            proxy._test_connection()

            # Mirror current profile headers
            hdrs = cache_manager.load_headers()
            proxy.headers = dict(hdrs)
            if hdrs.get("User-Agent"):
                proxy.headers["User-Agent"] = hdrs["User-Agent"]
            proxy._init_cookie_cache()

            # Ensure daemon session matches active profile
            proxy._send_reload()

            # Give the daemon the *persistent* cap, so an account-wide limit
            # survives across client processes.  Deliberately not the client's
            # explicit ``--qps``: that option is documented as local to this
            # invocation and must not rewrite the shared daemon's setting.
            proxy._send_config("qps", cache_manager.get_qps())

            _inject_app_headers(proxy)
            return proxy
        except Exception:
            # Close socket if we connected but a later step failed
            if proxy is not None:
                try:
                    proxy.close()
                except Exception:
                    pass
            # Fall through to direct session

    # ── direct session (lazy heavy import) ───────────────────────────
    from zhihu_cli.content.handlers._session_core import _build_direct_session

    session_ = _build_direct_session()
    _inject_app_headers(session_)
    return session_


def _inject_app_headers(sess: Any) -> None:
    """Add ``x-app-version`` and ``x-app-za`` headers to *sess*.

    These headers are required by mobile-api endpoints
    (e.g. ``segment_comment``) to identify the request as coming from the
    Android app.  Without them those endpoints return ``data: null``.

    The values are loaded from config so users can update the version
    string and tune the device fingerprint.
    """
    sess.headers["x-app-version"] = cache_manager.get_app_version()
    sess.headers["x-app-za"] = cache_manager.get_app_za()


def reload_session() -> None:
    """Reload the global session with headers from the active profile.

    When proxying through the daemon, sends a reload message so the
    daemon rebuilds its session from the new profileʼs headers.  For a
    direct session, rebuilds the session in-process.

    Serialised by :data:`_session_lock` so two threads reloading at once
    cannot leave the ``_session``/``_headers`` pair inconsistent, or build two
    sessions where one was meant.  The lock is held across the rebuild's I/O
    on purpose — that is exactly the work that must not be duplicated.
    """
    global _session, _headers

    try:
        from zhihu_cli.daemon.proxy import DaemonProxySession
    except ImportError:
        DaemonProxySession = None  # type: ignore[assignment]

    with _session_lock:
        if DaemonProxySession is not None and isinstance(_session, DaemonProxySession):
            # Update local headers mirror
            hdrs = cache_manager.load_headers()
            _headers = dict(hdrs)
            _session.headers = dict(hdrs)
            if hdrs.get("User-Agent"):
                _session.headers["User-Agent"] = hdrs["User-Agent"]
            _session._init_cookie_cache()

            _session._send_reload()
            _session._send_config("captcha_handler", _session.captcha_handler)
            # Re-push the persisted rate limit: ``config qps set`` already does
            # so, but a reload is the natural point to re-sync a daemon that
            # was restarted or missed the earlier message.
            _session._send_config("qps", cache_manager.get_qps())
        else:
            # Direct reload: rebuild the session
            sess = _build_session()
            _session = sess
            _inject_app_headers(sess)
            try:
                _headers = dict(sess.headers)
            except Exception:
                pass


def _context_profile() -> str | None:
    """Return the profile pinned by the enclosing :func:`use_profile`.

    Code that builds its own session instead of going through
    :func:`_get_session` — the daemon proxy's direct fallbacks — must pass this
    to ``_build_direct_session`` so it honours the enclosing ``with`` block.

    :returns: The pinned profile name, or ``None`` when the current context uses
        the process-wide session, which is the case for every plain CLI run.
    """
    return _ctx_profile.get()


def _profile_session(name: str) -> Any:
    """Return a cached direct session for *name*, building it on first use.

    :param name: An existing profile.
    :returns: The session, cached for the life of the process.
    :raises AuthError: If no such profile exists.  Reported here rather than
        silently returning an anonymous session, which would look like a
        logged-out user to the caller.
    """
    sess = _profile_session_cache.get(name)
    if sess is not None:
        return sess

    with _profile_session_lock:
        sess = _profile_session_cache.get(name)
        if sess is None:
            if name not in cache_manager.list_profiles():
                raise AuthError(f"Profile '{name}' does not exist")
            # Deliberately the direct factory, not ``_build_session``: the daemon
            # holds one session for one profile, so a second profile cannot be
            # proxied through it.
            from zhihu_cli.content.handlers._session_core import _build_direct_session

            sess = _build_direct_session(name)
            _inject_app_headers(sess)
            _profile_session_cache[name] = sess
        return sess


@contextmanager
def use_profile(name: str) -> Iterator[None]:
    """Talk to Zhihu as the profile *name* for the duration of the block.

    Intended for library use::

        from zhihu_cli import fetch_hot_list, use_profile

        with use_profile("alt"):
            print(fetch_hot_list())

    Pinning is per-thread (:class:`~contextvars.ContextVar`), so several threads
    may each pin a different profile concurrently — none of them rewrites the
    ``active_profile`` file or reloads the shared daemon, which is what
    :func:`reload_session` would do.  Nested blocks work too, and the innermost
    wins.  When *name* is already the active profile the process-wide session is
    reused as-is: no second session is built and requests keep going through the
    daemon.

    Two costs to be aware of, both a consequence of the daemon holding a single
    session:

    * A non-active profile bypasses the daemon, so its first use pays for a
      direct session (the ``_session_core`` import plus a TLS/QUIC handshake).
      It is then cached per profile for the life of the process.
    * Those cached sessions are never closed — their number is bounded by the
      number of profiles, but a long-lived process touching many profiles
      accumulates them.

    The pin lives in a fresh context per thread, so it does **not** cross a
    thread boundary: a worker started *inside* the block sees no pin and uses the
    process-wide session.  Pin the profile inside the worker — in a thread pool,
    wrap the task — or the work silently runs as the active profile.

    Captcha handling is likewise *not* isolated per profile: the gate that
    coordinates it is one process-wide object, so a challenge resolved for one
    profile also releases threads pinned to another.

    :param name: Name of an existing profile.
    :raises AuthError: If no such profile exists.
    """
    active = cache_manager.get_active_profile()
    if active is not None and name == active:
        # The process-wide session already *is* this profile.  ``None`` also
        # says "global session" to a nested block, which is exactly what this
        # profile should get there too.
        sess: Any | None = None
    else:
        sess = _profile_session(name)

    session_token = _ctx_session.set(sess)
    profile_token = _ctx_profile.set(name if sess is not None else None)
    try:
        yield
    finally:
        _ctx_session.reset(session_token)
        _ctx_profile.reset(profile_token)


# ── rate limiting (per-invocation and account-wide) ────────────────────────
#
# The limiter itself lives in :mod:`zhihu_cli.content.utils.rate_limit`; this
# module owns only the integration points so callers (``main``, ``config``,
# and the public API) never need to know about process globals.


def set_rate_limit(qps: float) -> None:
    """Apply a per-invocation request rate limit to this process.

    This is the ``--qps`` / ``ZHIHU_QPS`` path: it configures the in-process
    limiter and pins it so building a session later does not replace it with
    the persisted value.  It deliberately does **not** contact the daemon —
    ``--qps`` is documented as local to the invocation, and pushing it would
    silently tighten every other client on the machine.  Use
    ``zhihu config qps set`` for the account-wide, daemon-enforced cap.

    :param qps: Requests per second; ``<= 0`` disables limiting.
    """
    rate_limit.set_explicit_qps(qps)


def push_rate_limit_to_daemon(qps: float | None = None) -> None:
    """Best-effort push of a rate limit to a running daemon.

    Called after ``config qps set``/``clear`` so a long-lived daemon picks up
    the new persistent value immediately instead of only at its next start.
    There is no daemon most of the time, and that is a normal outcome, not an
    error: every failure (no socket, connection reset, protocol mismatch) is
    swallowed.  The value is already persisted in ``config.json``, so a later
    daemon start or ``reload_session`` will apply it anyway.

    Reuses the process-wide session when it is already a
    :class:`DaemonProxySession`; otherwise it opens an ephemeral one.  The
    ephemeral connection is closed on every path so a one-off config command
    does not leave a socket behind.

    :param qps: Value to send.  ``None`` means "read the persisted value",
        which is what the config setter wants after writing it.
    """
    value = cache_manager.get_qps() if qps is None else qps

    try:
        from zhihu_cli.daemon.proxy import DaemonProxySession
    except ImportError:
        return

    sess = _session
    ephemeral = not isinstance(sess, DaemonProxySession)
    if ephemeral:
        try:
            sess = DaemonProxySession()
            sess._test_connection()
        except Exception:
            return

    try:
        sess._send_config("qps", value)
    except Exception:
        # Includes connection errors ``_send_config`` itself does not catch.
        pass
    finally:
        if ephemeral:
            try:
                sess.close()
            except Exception:
                pass


@contextmanager
def use_direct_session(profile: str | None = None) -> Iterator[Any]:
    """Pin a *direct* :class:`ZhihuSession` for the duration of the block.

    This is the supported replacement for reaching into the private
    ``_ctx_session`` ContextVar from an out-of-tree consumer.  It is for the
    cases that genuinely cannot use the daemon: a second profile, requests
    the daemon protocol cannot relay, or a caller that needs the session
    object itself.

    Differences from :func:`use_profile` are intentional:

    * it always builds a direct session, even for the active profile — the
      daemon path cannot be forced through a ContextVar pin when the pinned
      value would be the proxy's own global;
    * the session is closed on exit rather than cached, because callers that
      need pinning are usually one-shot and the (curl_cffi) session is
      expensive to leak;
    * the qps configuration is resolved here too, so a library caller that
      never went through :func:`_build_session` still gets the persisted cap.

    The pin is a :class:`~contextvars.ContextVar`, so it is thread-local and
    restored exactly (nested blocks work).  As with :func:`use_profile`, it
    does not cross a thread boundary: start workers inside the block.

    :param profile: Profile whose headers to use, or ``None`` for the active
        profile.
    :returns: A context manager yielding the direct session.
    """
    from zhihu_cli.content.handlers._session_core import _build_direct_session

    sess = _build_direct_session(profile)
    _inject_app_headers(sess)
    rate_limit.configure_from_config()
    token = _ctx_session.set(sess)
    try:
        yield sess
    finally:
        _ctx_session.reset(token)
        try:
            sess.close()
        except Exception:
            pass

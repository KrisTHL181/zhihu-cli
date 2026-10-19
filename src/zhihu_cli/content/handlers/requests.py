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

import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from http.cookiejar import CookieJar
from typing import TYPE_CHECKING, Any

# ── lightweight (stdlib-only) imports ──────────────────────────────────────
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.utils import rate_limit
from zhihu_cli.errors import AuthError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

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
        if _direct_depth:
            return _current_thread_session().headers
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
    response = _check_auth_expired(_get_session().get(url))
    if response.status_code == 403 and "zh-zse-ck" in response.text:
        raise RuntimeError(f"Zhihu returned an HTML zh-zse-ck risk-control challenge for {url}")
    response.raise_for_status()
    return response.text


def _zhihu_error_detail(response: Any) -> str | None:
    """Format Zhihu's ``{"error": {...}}`` envelope for a failed response.

    Zhihu reports API failures as JSON such as
    ``{"error": {"code": 4041, "name": "ResourceNotFoundException",
    "message": "…"}}``.  The direct session's ``raise_for_status`` discards
    that body, leaving only a bare ``HTTP Error 404:`` — this helper recovers
    the server's own wording.

    :param response: The failed response; its JSON body is inspected.
    :returns: A string like ``ResourceNotFoundException (code 4041): …``, or
        ``None`` when the body is absent or is not an error envelope.
    """
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None

    name = str(error.get("name") or "").strip()
    message = str(error.get("message") or "").strip()
    code = error.get("code")
    if not name and not message and code is None:
        return None

    detail = name or "HTTP error"
    if code is not None:
        detail += f" (code {code})"
    if message:
        detail += f": {message}"
    return detail


def fetch_json(url: str) -> Any:
    """Fetch a URL and decode its JSON response.

    When Zhihu returns a non-2xx error envelope, its ``code``/``name``/
    ``message`` is folded into the raised HTTP error, so the caller sees why
    the request failed instead of only the status code.

    :param url: Target URL.
    :returns: The decoded JSON payload.
    :raises: The session's HTTP error for a non-2xx response.
    """
    response = _check_auth_expired(_get_session().get(url))
    try:
        response.raise_for_status()
    except Exception as exc:
        detail = _zhihu_error_detail(response)
        if not detail:
            raise
        base = str(exc).strip()
        body = getattr(response, "text", "").strip()
        if body and body in base:
            # The daemon path already appends the raw body — drop it so the
            # parsed detail replaces it rather than being repeated.
            base = base.split(body, 1)[0]
        base = base.rstrip(": ")
        message = f"{base}: {detail}" if base else detail
        try:
            enriched = type(exc)(message, response=response)
        except TypeError:
            enriched = type(exc)(message)
        raise enriched from None
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

# ── process-wide direct-per-thread scope (:func:`direct_sessions`) ──────────
#
# Deliberately a plain module-level depth counter rather than a ContextVar:
# ``ThreadPoolExecutor`` workers do not inherit the submitter's contextvars, so
# a ContextVar would make workers created anywhere inside the block silently
# fall back to the daemon.  Every thread that calls :func:`_get_session` while
# the depth is non-zero gets its own direct session.
#
# The lock is leaf-level: it guards the counters/registries below and is never
# held across session construction, ``configure`` callbacks, or network I/O.
_direct_lock = threading.RLock()

#: Nesting depth.  ``> 0`` means "direct per-thread sessions are active".
_direct_depth: int = 0

#: Bumped by :func:`reload_session` and :func:`_close_direct_sessions`; a thread
#: holding a session built for an older generation rebuilds on its next use.
_direct_generation: int = 0

#: Enclosing scope options; the innermost scope's entry wins.
_direct_options_stack: list[dict[str, Any]] = []

#: Per-thread cache, keyed by ``(generation, profile)``.  Created lazily so
#: merely importing this module does not allocate a thread-local.
_direct_thread_state = threading.local()

#: Sessions built inside any active scope, so :meth:`DirectSessions.close_all`
#: can find every thread's session.  Guarded by :data:`_live_direct_lock`.
_live_direct_sessions: set[Any] = set()
_live_direct_lock = threading.Lock()

#: One cookie jar per profile key (``None`` = active profile), shared by every
#: thread's session so a CAPTCHA resolved by one worker updates the cookies all
#: the others send.  ``http.cookiejar.CookieJar`` has an internal lock, making
#: concurrent use safe.
_direct_cookie_jars: dict[str | None, CookieJar] = {}

# ── expired-login detection ────────────────────────────────────────────────
#
# A stale ``z_c0`` ticket makes Zhihu answer 401 with one of these markers
# instead of the anonymous response.  Anonymous requests get a different body
# (code 101 / "request headers malformed"), so seeing a marker means "this
# profile was logged in once and no longer is" — worth a one-line hint, since
# the raw traceback ends in a bare ``HTTP Error 401`` that says nothing.

#: Response bodies that mean the saved login ticket is no longer valid.
_AUTH_EXPIRED_MARKERS = ("ERR_TICKET_NOT_EXIST", "ZERR_NO_AUTH_TOKEN")

#: HTTP verbs whose response is inspected for an expired login.  ``get`` et al.
#: are wrapped rather than ``request`` alone because curl_cffi's ``get`` calls
#: the concrete session's ``request`` directly, bypassing this proxy.
_AUTH_CHECKED_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "request"})

#: Profiles already reported, so one broken login prints one hint rather than
#: one per request.
_auth_warned_profiles: set[str] = set()
_auth_warn_lock = threading.Lock()


def _warn_auth_expired() -> None:
    """Print (once per profile) that Zhihu rejected the saved login."""
    profile = _context_profile() or cache_manager.get_active_profile()
    key = profile or ""
    with _auth_warn_lock:
        if key in _auth_warned_profiles:
            return
        _auth_warned_profiles.add(key)

    label = f"profile '{profile}'" if profile else "the active profile"
    print(file=sys.stderr)
    print(f"  ⚠️  Zhihu rejected the saved login for {label} (HTTP 401).", file=sys.stderr)
    print("      Run 'zhihu auth login' to sign in again, or 'zhihu auth import' to", file=sys.stderr)
    print("      reuse a browser session. 'zhihu profile switch <name>' uses another profile.", file=sys.stderr)
    print(file=sys.stderr)


def _check_auth_expired(response: Any) -> Any:
    """Warn when *response* shows the saved login ticket is no longer valid.

    :param response: A response object (curl_cffi or daemon proxy).
    :returns: *response*, unchanged, so this can wrap a call in place.
    """
    if getattr(response, "status_code", None) != 401:
        return response
    try:
        body = response.text
    except Exception:  # noqa: BLE001 - streaming responses have no ``.text``
        return response
    if isinstance(body, str) and any(marker in body for marker in _AUTH_EXPIRED_MARKERS):
        _warn_auth_expired()
    return response


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
        attr = getattr(_get_session(), name)
        if name in _AUTH_CHECKED_VERBS:

            def checked(*args: Any, **kwargs: Any) -> Any:
                return _check_auth_expired(attr(*args, **kwargs))

            return checked
        return attr

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

    Inside ``with direct_sessions(...)`` every thread gets its own lazily-built
    direct session; the scope's options config profile, captcha handling, and
    timeout.  The daemon is never consulted in that mode.
    """
    global _session, _headers

    ctx = _ctx_session.get()
    if ctx is not None:
        return ctx

    if _direct_depth:
        return _current_thread_session()

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


def _direct_options() -> dict[str, Any]:
    """Return the innermost direct-session scope's options (empty when none)."""
    return _direct_options_stack[-1] if _direct_options_stack else {}


def _thread_sessions() -> dict[tuple[int, str | None], Any]:
    """Return (creating on first use) this thread's direct-session cache."""
    sessions = getattr(_direct_thread_state, "sessions", None)
    if sessions is None:
        sessions = {}
        _direct_thread_state.sessions = sessions  # type: ignore[attr-defined]
    return sessions


def _direct_cookie_jar(profile: str | None) -> CookieJar:
    """Return the shared jar for *profile*, creating it on first use.

    Creation is a bare object allocation, so taking :data:`_direct_lock` here is
    leaf-level and safe.  The jar itself is not seeded until a session is built
    from it (see ``_session_core._build_direct_session``).

    :param profile: Profile name, or ``None`` for the active profile.
    :returns: The process-wide jar shared by every session for that profile.
    """
    jar = _direct_cookie_jars.get(profile)
    if jar is not None:
        return jar
    with _direct_lock:
        jar = _direct_cookie_jars.get(profile)
        if jar is None:
            jar = CookieJar()
            _direct_cookie_jars[profile] = jar
    return jar


def _current_thread_session() -> Any:
    """Return this thread's direct session for the active scope, building it once.

    The cache key includes :data:`_direct_generation`, so a
    :func:`reload_session` (or :meth:`DirectSessions.close_all`) invalidates it:
    the next call closes the thread's stale sessions and builds fresh ones.
    Only this thread's entries are ever closed here — a worker must not close a
    session another worker is still using.

    :returns: A :class:`~zhihu_cli.content.handlers._session_core.ZhihuSession`
        carrying the effective profile's headers and the scope's options.
    :raises AuthError: Never directly; a profile named by ``use_profile`` is
        validated there.
    """
    options = _direct_options()

    profile = _ctx_profile.get()
    if profile is None:
        profile = options.get("profile")

    generation = _direct_generation
    key = (generation, profile)
    sessions = _thread_sessions()

    cached = sessions.get(key)
    if cached is not None:
        return cached

    stale = [stale_key for stale_key in sessions if stale_key[0] != generation]
    for stale_key in stale:
        old = sessions.pop(stale_key, None)
        if old is None:
            continue
        try:
            old.close()
        except Exception:
            pass
        with _live_direct_lock:
            _live_direct_sessions.discard(old)

    # Lazy import is a hard rule: a module-level import would pull curl_cffi and
    # lxml into ``zhihu --help`` and shell completion.
    from zhihu_cli.content.handlers._session_core import _build_direct_session

    sess = _build_direct_session(
        profile,
        cookies=_direct_cookie_jar(profile),
        timeout=options.get("request_timeout"),
    )
    _inject_app_headers(sess)
    captcha_handler = options.get("captcha_handler")
    if captcha_handler is not None:
        sess.captcha_handler = captcha_handler
    for configure in options.get("configure", ()):
        configure(sess)

    sessions[key] = sess
    with _live_direct_lock:
        _live_direct_sessions.add(sess)
    return sess


def _close_direct_sessions() -> None:
    """Close every session registered by the direct-session scopes.

    Idempotent: the registry is snapshotted and cleared before any session is
    closed, so a concurrent or repeated call finds nothing left to do.  The
    generation bump makes each thread rebuild on its next request even though
    its thread-local cache still holds an entry.
    """
    global _direct_generation

    with _live_direct_lock:
        sessions = list(_live_direct_sessions)
        _live_direct_sessions.clear()

    with _direct_lock:
        _direct_generation += 1

    for sess in sessions:
        try:
            sess.close()
        except Exception:
            pass


class DirectSessions:
    """Handle yielded by :func:`direct_sessions`.

    The handle is the only supported way to reach the per-thread sessions
    without going through :func:`_get_session`; it is safe to share across the
    worker threads of the block.
    """

    def initializer(self) -> None:
        """Build the calling thread's session eagerly.

        Intended for ``ThreadPoolExecutor(initializer=ds.initializer)``: it
        removes the first-request latency from every worker and surfaces a
        construction error in the pool rather than inside a task.

        :raises RuntimeError: If the enclosing :func:`direct_sessions` block has
            already exited.
        """
        self.current()

    def current(self) -> Any:
        """Return the calling thread's session for the active scope.

        :returns: This thread's direct session, built on first call.
        :raises RuntimeError: If no :func:`direct_sessions` scope is active in
            this process.
        """
        if not _direct_depth:
            raise RuntimeError("direct_sessions() is not active")
        return _current_thread_session()

    def close_all(self) -> None:
        """Close every session built in the scope.  Safe to call more than once."""
        _close_direct_sessions()


@contextmanager
def direct_sessions(
    profile: str | None = None,
    *,
    captcha_handler: str | None = None,
    request_timeout: float | None = None,
    configure: Iterable[Callable[[Any], None]] = (),
) -> Iterator[DirectSessions]:
    """Use one direct session per thread instead of the process-wide session.

    This is the supported replacement for out-of-tree scrapers that called the
    private ``_build_direct_session`` and wrote the private ``_ctx_session``
    ContextVar from each worker.  Inside the block every thread that resolves
    :func:`_get_session` — including workers created by a
    ``ThreadPoolExecutor``, which do not inherit the submitter's contextvars —
    lazily builds and caches its own :class:`ZhihuSession`.  The daemon is never
    contacted: each session talks to Zhihu directly, so requests from different
    threads run concurrently instead of being serialised behind the daemon's
    single lock::

        from concurrent.futures import ThreadPoolExecutor
        from zhihu_cli import direct_sessions

        with direct_sessions(captcha_handler="ignore", request_timeout=30.0) as ds:
            with ThreadPoolExecutor(max_workers=8, initializer=ds.initializer) as pool:
                ...

    Every session for a profile shares one cookie jar, so a CAPTCHA resolved by
    one worker updates the cookies all the others send.

    The scope is process-wide and nestable.  Only the outermost exit closes
    sessions; a nested block contributes its own options while it is active.
    Exit order matters in two ways:

    * The block must outlive the worker pool.  Shut the pool down *inside* the
      ``with``; a task that starts afterwards falls back to the process-wide
      session.
    * Sessions are closed when the block exits, **not** when a worker thread
      ends — Python offers no reliable hook for the latter.  A long-lived pool
      that keeps running after the block therefore holds closed sessions; keep
      the pool's lifetime inside the scope.

    :param profile: Profile whose headers every session uses, or ``None`` for
        the active profile.  An enclosing :func:`use_profile` overrides this.
    :param captcha_handler: ``"auto"``, ``"prompt"``, or ``"ignore"``.  ``None``
        keeps :class:`ZhihuSession`'s default (``"auto"``).
    :param request_timeout: Default request timeout in seconds for every
        session.  ``None`` keeps curl_cffi's default.
    :param configure: Callables run with each new session before it is handed
        out, e.g. to set extra headers.  They run once per thread session.
    :returns: A :class:`DirectSessions` handle providing the pool initializer and
        :meth:`DirectSessions.close_all`.
    """
    global _direct_depth

    options: dict[str, Any] = {
        "profile": profile,
        "captcha_handler": captcha_handler,
        "request_timeout": request_timeout,
        "configure": tuple(configure),
    }

    with _direct_lock:
        outermost = _direct_depth == 0
        # Options first: a thread that observes a non-zero depth must always
        # find the options that go with it.
        _direct_options_stack.append(options)
        _direct_depth += 1

    if outermost:
        # Direct sessions bypass ``_build_session``, the usual place this is
        # resolved, so a library caller still gets the persisted qps cap.
        rate_limit.configure_from_config()

    handle = DirectSessions()
    try:
        yield handle
    finally:
        # Decrement *before* closing: once the depth is back to zero no thread
        # can enter ``_current_thread_session`` through ``_get_session``, so
        # the registry snapshot below cannot miss a just-created session.
        with _direct_lock:
            _direct_depth -= 1
            _direct_options_stack.pop()

        if outermost:
            handle.close_all()


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
    global _direct_generation, _session, _headers

    if _direct_depth:
        # Scratch the per-thread sessions instead of rebuilding the global one:
        # there is no daemon in this mode and no single session to replace.
        hdrs = cache_manager.load_headers()
        with _direct_lock:
            _direct_generation += 1
            _headers = dict(hdrs)
        return

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
            _inject_app_headers(_session)
            _session._init_cookie_cache()
            # If the proxy already fell back to a direct session (the daemon
            # went away earlier in this process), that session still carries
            # the *old* profile — drop it so the next request rebuilds from
            # the headers loaded above.
            _session._reset_direct_session()

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

    Inside :func:`direct_sessions` the pin is even cheaper: only the profile
    *name* is pinned (``_ctx_session`` stays ``None``), and each thread builds
    and caches its own session for it, sharing that profile's cookie jar.  The
    per-profile session cache above is not used in that mode.

    :param name: Name of an existing profile.
    :raises AuthError: If no such profile exists.
    """
    if _direct_depth:
        if name not in cache_manager.list_profiles():
            raise AuthError(f"Profile '{name}' does not exist")
        session_token = _ctx_session.set(None)
        profile_token = _ctx_profile.set(name)
        try:
            yield
        finally:
            _ctx_session.reset(session_token)
            _ctx_profile.reset(profile_token)
        return

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

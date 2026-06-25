"""HTTP session management — lightweight entry point.

This module provides a lazy :data:`session` singleton.  On first access
it attempts to connect to the background daemon (→ lightweight
:class:`~zhihu_cli.daemon.proxy.DaemonProxySession`, no curl_cffi needed).
If the daemon is unavailable it falls back to importing the heavy
:mod:`_session_core` module and building a direct :class:`ZhihuSession`.

All heavy imports (curl_cffi, lxml) are deferred to :mod:`_session_core`,
which is loaded *only* when a direct session is built or when calling
``fetch_page_html`` / ``get_page_state``.
"""

from __future__ import annotations

from typing import Any

# ── lightweight (stdlib-only) imports ──────────────────────────────────────
from zhihu_cli.content.handlers.cache_manager import cache_manager

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
    # ── lazy session singleton ──────────────────────────────────────
    if name in ("session", "requests"):
        return _get_session()
    if name == "headers":
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
    return _get_session().get(url).text


def get_page_state(html_text: str, key: str = "entities") -> dict[str, Any]:
    """Extract ``js-initialData`` from a Zhihu SSR page."""
    from zhihu_cli.content.handlers._session_core import get_page_state as _impl

    return _impl(html_text, key)


# ── lazy session state ─────────────────────────────────────────────────────

_session: Any = None
_headers: dict[str, str] = {}


def _get_session() -> Any:
    """Return the singleton session, creating it on first call."""
    global _session, _headers
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

            # Ensure daemon session matches active profile
            proxy._send_reload()

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

    return _build_direct_session()


def reload_session() -> None:
    """Reload the global session with headers from the active profile.

    When proxying through the daemon, sends a reload message so the
    daemon rebuilds its session from the new profileʼs headers.  For a
    direct session, rebuilds the session in-process.
    """
    global _session, _headers

    try:
        from zhihu_cli.daemon.proxy import DaemonProxySession
    except ImportError:
        DaemonProxySession = None  # type: ignore[assignment]

    if DaemonProxySession is not None and isinstance(_session, DaemonProxySession):
        # Update local headers mirror
        hdrs = cache_manager.load_headers()
        _headers = dict(hdrs)
        _session.headers = dict(hdrs)
        if hdrs.get("User-Agent"):
            _session.headers["User-Agent"] = hdrs["User-Agent"]

        _session._send_reload()
        _session._send_config("captcha_handler", _session.captcha_handler)
    else:
        # Direct reload: rebuild the session
        sess = _build_session()
        _session = sess
        try:
            _headers = dict(sess.headers)
        except Exception:
            pass

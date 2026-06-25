"""Client-side proxy session that talks to the daemon over a Unix socket.

:class:`DaemonProxySession` is a drop-in replacement for
:class:`~zhihu_cli.content.handlers.requests.ZhihuSession`.  It has the
same ``get`` / ``post`` / ``put`` / ``delete`` / ``request`` API, plus
``headers``, ``cookies``, and ``captcha_handler`` attributes.

When the daemon is not running, attempting to use the proxy raises
:class:`DaemonNotRunningError`.  The session factory in ``requests.py``
catches this and falls back to a direct ``ZhihuSession``.
"""

from __future__ import annotations

import base64
import json
import socket
from pathlib import Path
from typing import Any

from zhihu_cli.daemon.protocol import (
    MSG_CAPTCHA_REQUIRED,
    MSG_CAPTCHA_RESOLVED,
    MSG_ERROR,
    MSG_HTTP_RESPONSE,
    MSG_PING,
    MSG_PONG,
    MSG_RELOAD_SESSION,
    MSG_SET_CONFIG,
    DaemonConnectionError,
    DaemonNotRunningError,
    DaemonProtocolError,
    DaemonRequestError,
    build_http_request,
    make_msg_id,
    recv_message,
    send_message,
)

SOCKET_PATH = Path.home() / ".zhihu-cli" / "daemon.sock"
CONNECT_TIMEOUT = 2.0
RW_TIMEOUT = 60.0

# kwargs keys that are safe to serialise over the wire.
# Excluded: stream (handled client-side), hooks, auth (may contain
# callables), and other curl_cffi internals.
_WIRE_KWARGS = frozenset(
    {
        "headers",
        "params",
        "json",
        "data",
        "timeout",
        "cookies",
        "files",
        "allow_redirects",
        "verify",
        "max_redirects",
        "proxies",
    }
)


class DaemonProxyResponse:
    """A response object that duck-types :class:`curl_cffi.requests.Response`.

    Only the attributes actually accessed by the zhihu-cli handler code
    are provided (see the exploration report for the full audit).
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.status_code: int = data.get("status_code", 0)
        self._headers: dict[str, str] = data.get("headers", {})
        self._body_b64: str = data.get("body_base64", "")
        self._body_bytes: bytes = base64.b64decode(self._body_b64) if self._body_b64 else b""
        self.elapsed: float = data.get("elapsed", 0.0)
        self.url: str = data.get("url", "")
        self.reason: str = "OK" if 200 <= self.status_code < 300 else "Error"
        self.encoding: str = "utf-8"

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    @property
    def text(self) -> str:
        """Decode the body as UTF-8 (Zhihu APIs are always UTF-8)."""
        return self._body_bytes.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body_bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self, **kwargs: Any) -> Any:
        """Parse the body as JSON."""
        return json.loads(self._body_bytes, **kwargs)

    def raise_for_status(self) -> None:
        """Raise :class:`HTTPError` if the status code is >= 400."""
        if self.status_code >= 400:
            from curl_cffi.requests.exceptions import HTTPError

            raise HTTPError(f"{self.status_code} {self.reason}", response=self)

    def iter_content(self, chunk_size: int = 8192) -> Any:
        """Yield the body in chunks (for streaming compatibility).

        The daemon always returns the full body at once; this generator
        yields a single chunk.  Only used when ``stream=True`` fallback
        is not triggered (i.e. for very small responses).
        """
        yield self._body_bytes

    def close(self) -> None:
        """No-op for interface compatibility."""

    def __repr__(self) -> str:
        return f"<DaemonProxyResponse [{self.status_code}]>"

    def __bool__(self) -> bool:
        return True  # Always truthy (match requests.Response behaviour)


class DaemonProxySession:
    """Drop-in replacement for :class:`ZhihuSession` that proxies requests
    through the background daemon.

    Maintains a single persistent Unix-socket connection to the daemon for
    the lifetime of the CLI process.  All HTTP requests, control messages,
    and pings reuse the same connection — the daemon server handles
    multiple request/response pairs per connection natively.

    Usage::

        proxy = DaemonProxySession()
        proxy._test_connection()          # raises DaemonNotRunningError if unavailable
        resp = proxy.get("https://www.zhihu.com/api/v4/me")
        print(resp.json())
    """

    def __init__(self) -> None:
        self._captcha_handler: str = "auto"
        self.headers: dict[str, str] = {}
        self.cookies: _CookieProxy = _CookieProxy(self)
        self._sock: socket.socket | None = None

    # ── persistent connection management ──────────────────────────────

    def _get_sock(self) -> socket.socket:
        """Return the persistent socket, creating it on first call.

        :raises DaemonNotRunningError: if the daemon is unavailable.
        """
        if self._sock is not None:
            return self._sock
        self._sock = self._connect()
        return self._sock

    def _connect(self) -> socket.socket:
        """Open a fresh connection to the daemon socket.

        :raises DaemonNotRunningError: if the daemon is unavailable.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(str(SOCKET_PATH))
            sock.settimeout(RW_TIMEOUT)
            return sock
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            sock.close()
            raise DaemonNotRunningError(f"Daemon not available: {e}") from e

    def _send_recv(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Send *msg* and return the response, reusing the persistent socket.

        On the first call the socket is lazily connected.  If the socket
        has been closed by the peer (e.g. daemon restart), reconnects
        once and retries.
        """
        sock = self._get_sock()
        try:
            send_message(sock, msg)
            return recv_message(sock)
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            # Socket died — reconnect once and retry
            self._close_sock()
            sock = self._connect()
            self._sock = sock
            try:
                send_message(sock, msg)
                return recv_message(sock)
            except Exception:
                self._close_sock()
                raise DaemonConnectionError(f"IPC error after reconnect: {e}") from e

    def _close_sock(self) -> None:
        """Close and clear the persistent socket."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _sync_cookies(self, cookies: dict[str, str]) -> None:
        """Merge *cookies* from a daemon response into the local Cookie header.

        This keeps ``session.cookies.get("d_c0")`` and friends in sync
        with the daemon's actual cookie jar.
        """
        if not cookies:
            return
        cookie_header = self.headers.get("Cookie", "")
        parts: dict[str, str] = {}
        if isinstance(cookie_header, str):
            for part in cookie_header.split("; "):
                if "=" in part:
                    k, _, v = part.partition("=")
                    parts[k] = v
        parts.update(cookies)
        self.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in parts.items())

    def close(self) -> None:
        """Close the persistent daemon connection."""
        self._close_sock()

    def _test_connection(self) -> None:
        """Ping the daemon to verify it is reachable.

        :raises DaemonNotRunningError: if the daemon is not responding.
        """
        try:
            resp = self._send_recv({"id": make_msg_id(), "type": MSG_PING})
            if resp.get("type") != MSG_PONG:
                raise DaemonNotRunningError("Unexpected ping response")
        except (DaemonNotRunningError, ConnectionError, OSError):
            raise
        except json.JSONDecodeError as e:
            raise DaemonNotRunningError(f"Invalid daemon response: {e}") from e

    # ── control messages ───────────────────────────────────────────────

    def _send_reload(self) -> None:
        """Instruct the daemon to reload its session from the current profile."""
        try:
            self._send_recv({"id": make_msg_id(), "type": MSG_RELOAD_SESSION})
        except DaemonNotRunningError:
            pass

    def _send_config(self, key: str, value: str) -> None:
        """Propagate a configuration change to the daemon (best-effort)."""
        try:
            self._send_recv(
                {
                    "id": make_msg_id(),
                    "type": MSG_SET_CONFIG,
                    "key": key,
                    "value": value,
                }
            )
        except DaemonNotRunningError:
            pass

    # ── captcha_handler property ───────────────────────────────────────

    @property
    def captcha_handler(self) -> str:
        return self._captcha_handler

    @captcha_handler.setter
    def captcha_handler(self, value: str) -> None:
        self._captcha_handler = value
        self._send_config("captcha_handler", value)

    # ── HTTP verb shortcuts ────────────────────────────────────────────

    def get(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> DaemonProxyResponse:
        return self.request("OPTIONS", url, **kwargs)

    # ── main request method ────────────────────────────────────────────

    def request(self, method: str, url: str, **kwargs: Any) -> DaemonProxyResponse:
        """Execute an HTTP request through the daemon.

        :param method: HTTP method (GET, POST, PUT, DELETE, …).
        :param url: Full URL to request.
        :param kwargs: Same keyword arguments accepted by
            :meth:`curl_cffi.requests.Session.request`.
        :returns: A :class:`DaemonProxyResponse`.
        :raises DaemonNotRunningError: if the daemon is unavailable.
        :raises DaemonConnectionError: if the IPC connection fails.
        """
        # ── streaming fallback ──
        if kwargs.get("stream", False):
            streamless = {k: v for k, v in kwargs.items() if k != "stream"}
            return self._fallback_direct(method, url, **streamless)

        wire_kwargs = self._serialize_kwargs(kwargs)
        msg = build_http_request(method, url, wire_kwargs)

        try:
            resp_data = self._send_recv(msg)
        except DaemonNotRunningError:
            raise
        except (ConnectionError, OSError, json.JSONDecodeError) as e:
            raise DaemonConnectionError(f"IPC error: {e}") from e

        return self._process_response(resp_data, method, url, kwargs)

    # ── internal helpers ───────────────────────────────────────────────

    def _serialize_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Filter *kwargs* to only the keys safe for wire transport."""
        return {k: v for k, v in kwargs.items() if k in _WIRE_KWARGS}

    def _process_response(
        self,
        resp_data: dict[str, Any],
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
    ) -> DaemonProxyResponse:
        resp_type = resp_data.get("type")

        if resp_type == MSG_HTTP_RESPONSE:
            # Sync cookies from daemon back to the local headers mirror so
            # `session.cookies.get("d_c0")` reflects the latest state.
            self._sync_cookies(resp_data.get("cookies", {}))
            return DaemonProxyResponse(resp_data)

        if resp_type == MSG_CAPTCHA_REQUIRED:
            return self._handle_captcha_via_daemon(resp_data, method, url, original_kwargs)

        if resp_type == MSG_ERROR:
            error_type = resp_data.get("error_type", "DaemonRequestError")
            message = resp_data.get("message", "Unknown daemon error")
            raise DaemonRequestError(error_type, message)

        raise DaemonProtocolError(f"Unexpected response type: {resp_type}")

    def _fallback_direct(self, method: str, url: str, **kwargs: Any) -> DaemonProxyResponse:
        """Create a temporary direct :class:`ZhihuSession` for streaming
        requests (video downloads, etc.).
        """
        from zhihu_cli.content.handlers._session_core import _build_direct_session

        direct = _build_direct_session()
        try:
            resp = direct.request(method, url, **kwargs)
            body = getattr(resp, "content", b"") or b""
            return DaemonProxyResponse(
                {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body_base64": base64.b64encode(body).decode("ascii") if body else "",
                    "elapsed": getattr(resp, "elapsed", 0.0),
                    "url": str(getattr(resp, "url", "")),
                }
            )
        finally:
            try:
                direct.close()
            except Exception:
                pass

    def _handle_captcha_via_daemon(
        self,
        captcha_data: dict[str, Any],
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
    ) -> DaemonProxyResponse:
        """Handle a captcha that the daemon detected.

        Interactive resolution happens in the CLI process (which has a
        TTY).  After resolution, a ``captcha_resolved`` message is sent
        to the daemon so it can harvest cookies and retry.
        """
        captcha_error = captcha_data.get("captcha_error", {})
        pending = captcha_data.get("pending_request", {})

        if self._captcha_handler == "ignore":
            return _synthetic_403(captcha_error, url)

        if self._captcha_handler == "prompt":
            from zhihu_cli.content.handlers._session_core import _print_captcha_warning

            _print_captcha_warning(captcha_error)
            return _synthetic_403(captcha_error, url)

        # "auto" mode: interactive resolution
        from zhihu_cli.content.handlers._session_core import _build_direct_session
        from zhihu_cli.content.handlers.captcha import handle_captcha

        # Use a temporary direct session for captcha cookie harvesting
        direct = _build_direct_session()
        try:
            result = handle_captcha(direct, captcha_error)
            if result == "resolved":
                # Build the unhuman URL for the daemon to visit
                unhuman_url = captcha_error.get("redirect", "")
                if unhuman_url.startswith("/"):
                    unhuman_url = "https://www.zhihu.com" + unhuman_url

                # Send resolved message with retry info
                resolve_msg = {
                    "id": make_msg_id(),
                    "type": MSG_CAPTCHA_RESOLVED,
                    "unhuman_url": unhuman_url,
                    "retry_request": {
                        "method": pending.get("method", method.upper()),
                        "url": pending.get("url", url),
                        "kwargs": pending.get("kwargs", self._serialize_kwargs(original_kwargs)),
                    },
                }
                resp_data = self._send_recv(resolve_msg)
                if resp_data.get("type") == MSG_HTTP_RESPONSE:
                    return DaemonProxyResponse(resp_data)
                raise DaemonRequestError(
                    resp_data.get("error_type", "CaptchaError"),
                    resp_data.get("message", "Captcha resolution failed"),
                )

            # "skipped" or "cancelled" — return synthetic 403
            return _synthetic_403(captcha_error, url)
        finally:
            try:
                direct.close()
            except Exception:
                pass


class _CookieProxy:
    """Minimal cookie jar proxy that reads from the daemon session.

    Only ``cookies.get(name)`` is used by the codebase (for ``d_c0``
    extraction in ``ZhihuSession._get_dc0()`` and auth login helpers).
    """

    def __init__(self, session: DaemonProxySession) -> None:
        self._session = session

    def get(self, name: str, default: str = "") -> str:
        """Return a cookie value by name.

        Reads from the local headers mirror (``Cookie`` header).
        """
        cookie_header = self._session.headers.get("Cookie", "")
        if isinstance(cookie_header, str):
            for part in cookie_header.split("; "):
                if part.startswith(name + "="):
                    return part[len(name) + 1 :]
        return default

    def get_dict(self) -> dict[str, str]:
        """Return all cookies as a dict (used by auth login)."""
        result: dict[str, str] = {}
        cookie_header = self._session.headers.get("Cookie", "")
        if isinstance(cookie_header, str):
            for part in cookie_header.split("; "):
                if "=" in part:
                    key, _, val = part.partition("=")
                    result[key] = val
        return result

    def set(self, name: str, value: str) -> None:
        """Set a cookie value in the local headers mirror.

        Note: this does NOT propagate to the daemon.  For persistent
        cookie changes, use ``zhihu auth`` or ``reload_session()``.
        """
        cookie_header = self._session.headers.get("Cookie", "")
        parts = [p for p in cookie_header.split("; ") if p and not p.startswith(name + "=")]
        if value:
            parts.append(f"{name}={value}")
        self._session.headers["Cookie"] = "; ".join(parts)


def _synthetic_403(captcha_error: dict[str, Any], url: str) -> DaemonProxyResponse:
    """Build a synthetic 403 response representing a captcha block."""
    body = json.dumps(
        {
            "error": {
                "code": captcha_error.get("code", 40352),
                "message": captcha_error.get("message", "Risk control triggered"),
            }
        }
    ).encode()
    return DaemonProxyResponse(
        {
            "status_code": 403,
            "headers": {"Content-Type": "application/json"},
            "body_base64": base64.b64encode(body).decode("ascii"),
            "elapsed": 0.0,
            "url": url,
        }
    )

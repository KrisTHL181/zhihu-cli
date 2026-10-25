"""Client-side proxy session that talks to the daemon over a Unix socket.

:class:`DaemonProxySession` is a drop-in replacement for
:class:`~zhihu_cli.content.handlers.requests.ZhihuSession`.  It has the
same ``get`` / ``post`` / ``put`` / ``delete`` / ``request`` API, plus
``headers``, ``cookies``, and ``captcha_handler`` attributes.

When the daemon is not running at *build* time, attempting to use the proxy
raises :class:`DaemonNotRunningError`.  The session factory in ``requests.py``
catches this and falls back to a direct ``ZhihuSession``.

A session built while the daemon *was* running has to survive the daemon
leaving later (idle timeout, ``zhihu daemon stop``, a crash).  A long-lived
client — a library consumer rather than a one-shot CLI invocation — never
rebuilds its session, so a lost daemon used to fail every subsequent request
forever.  The proxy now heals itself: the first connection failure flips
:attr:`DaemonProxySession._daemon_down` and the request (plus every later one)
runs on a persistent direct session instead.  The switch is sticky for the
life of the process; the direct session is fully functional, and re-probing a
daemon that went away would only add a failed connect to every request.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.captcha_gate import captcha_gate, resolve_captcha
from zhihu_cli.content.utils import rate_limit
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
    DaemonError,
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


class DaemonHTTPError(Exception):
    """Raised for a >= 400 response relayed by the daemon.

    Stands in for :class:`curl_cffi.requests.exceptions.HTTPError`.  The
    daemon exists so that the CLI process never imports curl_cffi — which
    still costs ~265 ms even after ``zhihu_cli`` trims its optional
    HTML-to-Markdown dependencies.  Importing it here purely to raise gave
    that cost straight back on any error response, which is exactly the
    case the daemon is meant to avoid.

    Not a subclass of curl_cffi's ``HTTPError``, so ``except
    curl_cffi.requests.HTTPError`` does not catch it — catch
    :class:`DaemonHTTPError` or the broader :class:`Exception` instead.
    The message is richer than curl_cffi's (which is only
    ``"HTTP Error <code>: <reason>"``): it includes the truncated body so
    Zhihu's HTML error pages are visible.

    :param message: Error message, including status code, reason, and body.
    :param response: The offending response object, for parity with
        ``curl_cffi.requests.exceptions.HTTPError.response``.
    """

    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


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
        "skip_app_headers",
    }
)


class _StreamResponse:
    """Wraps a real curl_cffi streaming response for ``stream=True`` requests.

    Created by :meth:`DaemonProxySession._stream_direct` when a caller
    passes ``stream=True`` — the daemon protocol cannot relay real-time
    chunks, so we bypass the daemon and create a temporary direct session.

    Provides the subset of :class:`curl_cffi.requests.Response` that
    streaming callers need: :meth:`iter_lines`, :meth:`raise_for_status`,
    ``status_code``, ``headers``, and :meth:`close` (which also closes
    the underlying session).
    """

    def __init__(self, resp: Any, session: Any) -> None:
        self._resp = resp
        self._session = session
        self.status_code: int = resp.status_code
        self.headers: dict[str, str] = dict(resp.headers)
        self.url: str = str(getattr(resp, "url", ""))
        self.reason: str = getattr(resp, "reason", "OK")

    def iter_lines(self, decode_unicode: bool = False) -> Any:
        """Delegate to the real curl_cffi response's :meth:`iter_lines`."""
        return self._resp.iter_lines(decode_unicode=decode_unicode)

    def raise_for_status(self) -> None:
        """Delegate to the wrapped response's own :meth:`raise_for_status`.

        Unlike :class:`DaemonProxyResponse`, this class wraps a *real*
        curl_cffi response (streaming always bypasses the daemon), so
        curl_cffi is already imported and raising its native
        :class:`~curl_cffi.requests.exceptions.HTTPError` is free — and
        matches what a caller would see with the daemon stopped.

        (Previously this built its own message from ``self.text``, which
        this class does not define — any >= 400 stream raised
        :class:`AttributeError` instead.)
        """
        self._resp.raise_for_status()

    def close(self) -> None:
        """Close the underlying response and the temporary direct session."""
        try:
            self._resp.close()
        except Exception:
            pass
        try:
            self._session.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"<_StreamResponse [{self.status_code}]>"


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
        """Raise :class:`DaemonHTTPError` if the status code is >= 400.

        The exception message includes the server reply body (truncated)
        so callers see *why* the request failed, not just the status code.
        """
        if self.status_code >= 400:
            # DaemonProxyResponse stores the body as bytes (_body_bytes);
            # use the decoded ``text`` property so real bodies (e.g. Zhihu's
            # HTML 500 page) surface instead of a misleading "(empty body)".
            body = self.text.strip()
            if not body:
                body = "(empty body)"
            if len(body) > 500:
                body = body[:500] + "…"
            raise DaemonHTTPError(f"{self.status_code} {self.reason}: {body}", response=self)

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


def _build_context_session() -> Any:
    """Build a direct session honouring an enclosing ``use_profile()``.

    The streaming and daemon-failure paths build their own session rather than
    asking :func:`~zhihu_cli.content.handlers.requests._get_session` for one, so
    they must read the pinned profile themselves — otherwise a
    ``with use_profile("alt"): download_video(...)`` would silently authenticate
    as the *active* profile.

    Both imports stay inside the body on purpose: this module's import graph is
    deliberately light (the daemon exists so the CLI never imports
    ``_session_core``), and a module-level import would undo that.

    :returns: A direct :class:`ZhihuSession` for the pinned profile, or for the
        active one when nothing is pinned.
    """
    from zhihu_cli.content.handlers._session_core import _build_direct_session
    from zhihu_cli.content.handlers.requests import _context_profile, _inject_app_headers

    sess = _build_direct_session(_context_profile())
    # Mirror what ``_build_session`` puts on the daemon proxy: without
    # ``x-app-version`` / ``x-app-za`` some mobile-api endpoints answer with
    # ``data: null``.
    _inject_app_headers(sess)
    return sess


class DaemonProxySession:
    """Drop-in replacement for :class:`ZhihuSession` that proxies requests
    through the background daemon.

    Maintains a single persistent Unix-socket connection to the daemon for
    the lifetime of the CLI process.  All HTTP requests, control messages,
    and pings reuse the same connection — the daemon server handles
    multiple request/response pairs per connection natively.

    If the daemon exits after the connection was established, the proxy
    degrades in place: see :meth:`_degrade_to_direct`.

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
        self._lock: threading.Lock = threading.Lock()
        # Internal cookie cache — avoids O(n) string-splitting of the
        # Cookie header on every response.  Kept in sync with the daemon's
        # actual cookie jar via _sync_cookies().
        self._cookie_dict: dict[str, str] = {}

        # ── direct fallback after the daemon goes away ──
        # A long-lived process keeps its first session for its whole life, so
        # nothing rebuilds it when the daemon exits mid-run.  The first
        # connection failure sets ``_daemon_down``; every request from then on
        # (including the one that failed) goes through ``_direct`` instead.
        # Sticky on purpose: the direct session is fully functional, and
        # re-probing a daemon that is gone would add a failed connect to every
        # request.
        self._daemon_down: bool = False
        self._direct: Any = None
        self._direct_lock: threading.Lock = threading.Lock()

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

        The entire send+recv pair is protected by a lock so that
        concurrent callers (multi-threaded use of zhihu_cli as a library)
        do not interleave messages on the shared socket, which would
        cause "Message too large" and other framing errors.
        """
        with self._lock:
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
        """Merge *cookies* from a daemon response into the local cookie cache.

        Uses an internal ``_cookie_dict`` to avoid O(n) string-splitting of
        the ``Cookie`` header on every response.  The header is only rebuilt
        when a cookie value actually changes.
        """
        if not cookies:
            return
        changed = False
        for k, v in cookies.items():
            if self._cookie_dict.get(k) != v:
                self._cookie_dict[k] = v
                changed = True
        if changed:
            self.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookie_dict.items())

    def _init_cookie_cache(self) -> None:
        """Populate ``_cookie_dict`` from the initial ``Cookie`` header.

        Called once after setting ``self.headers`` from the profile, so
        subsequent ``_sync_cookies`` calls have a baseline to diff against.
        """
        cookie_header = self.headers.get("Cookie", "")
        if not isinstance(cookie_header, str) or not cookie_header:
            return
        for part in cookie_header.split("; "):
            if "=" in part:
                k, _, v = part.partition("=")
                self._cookie_dict[k] = v

    def close(self) -> None:
        """Close the persistent daemon connection and any direct fallback."""
        self._close_sock()
        self._reset_direct_session()

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
        """Instruct the daemon to reload its session from the current profile.

        Best-effort: called by :func:`requests.reload_session`, which must
        succeed even when the daemon has gone away (it then falls back to the
        direct session on the next request).
        """
        if self._daemon_down:
            return
        try:
            self._send_recv({"id": make_msg_id(), "type": MSG_RELOAD_SESSION})
        except (DaemonError, OSError, ValueError):
            pass

    def _send_config(self, key: str, value: Any) -> None:
        """Propagate a configuration change to the daemon (best-effort)."""
        if self._daemon_down:
            return
        try:
            self._send_recv(
                {
                    "id": make_msg_id(),
                    "type": MSG_SET_CONFIG,
                    "key": key,
                    "value": value,
                }
            )
        except (DaemonError, OSError, ValueError):
            pass

    # ── captcha_handler property ───────────────────────────────────────

    @property
    def captcha_handler(self) -> str:
        return self._captcha_handler

    @captcha_handler.setter
    def captcha_handler(self, value: str) -> None:
        self._captcha_handler = value
        self._send_config("captcha_handler", value)
        # Keep the direct fallback (when one already exists) in the same mode.
        direct = self._direct
        if direct is not None:
            direct.captcha_handler = value

    # ── HTTP verb shortcuts ────────────────────────────────────────────

    def get(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        return self.request("OPTIONS", url, **kwargs)

    # ── main request method ────────────────────────────────────────────

    def request(self, method: str, url: str, **kwargs: Any) -> DaemonProxyResponse | _StreamResponse:
        """Execute an HTTP request through the daemon, or directly once it is gone.

        :param method: HTTP method (GET, POST, PUT, DELETE, …).
        :param url: Full URL to request.
        :param kwargs: Same keyword arguments accepted by
            :meth:`curl_cffi.requests.Session.request`, plus:

            ``skip_app_headers`` (:class:`bool`)
                When :data:`True`, suppress ``x-app-version`` and ``x-app-za``
                headers for this request.  The daemon does not inject these
                headers (only the direct session does), so this is a no-op
                — accepted for interface parity with
                :class:`~zhihu_cli.content.handlers._session_core.ZhihuSession`.
        :returns: A :class:`DaemonProxyResponse`.
        :raises DaemonNotRunningError: only from :meth:`_test_connection`; a
            request that cannot reach the daemon transparently falls back to a
            direct session instead of raising.
        :raises DaemonConnectionError: never for connection failures — they are
            the fallback trigger.  Kept in the contract for callers that catch
            it defensively.
        """
        # ``skip_app_headers`` stays in kwargs — it lives in ``_WIRE_KWARGS``
        # so ``_serialize_kwargs`` forwards it and the daemon strips the
        # app-identity headers for this request.

        # ── streaming fallback ──
        # The daemon protocol cannot relay real-time chunks, so we bypass
        # the daemon entirely and create a temporary direct session for
        # streaming requests (SSE chat, video downloads, etc.).
        # NOTE: we keep stream=True in the kwargs — the direct session
        # needs it to enable curl_cffi's internal queue/curl handle.
        if kwargs.get("stream", False):
            return self._stream_direct(method, url, **kwargs)

        # ── already degraded ──
        # The daemon went away earlier in this process's life.  Skip the
        # socket (and the gate/limiter, which the direct session applies
        # itself) and serve the request directly.
        if self._daemon_down:
            return self._fallback_direct(method, url, **kwargs)

        # Park here — deliberately *before* ``_send_recv`` takes the socket
        # lock — while another thread resolves a captcha.  A waiter holding
        # that lock would deadlock the resolver, which needs the socket to
        # send ``captcha_resolved``.  The wait is bounded, so a timed-out
        # thread simply sends and gets the daemon's 403.
        captcha_gate.acquire()
        # Client-side pacing, after the gate for the same reason as in
        # ``ZhihuSession.request``: threads parked on a captcha must not
        # consume slots they will not use.  This is the same process-global
        # limiter the direct paths use — never a second one.  When the daemon
        # is running and also limits (its shared session is the server-side
        # chokepoint), absolute slot reservation makes the stacked waits
        # ``max`` rather than a sum.
        rate_limit.limiter.acquire()

        msg = self._build_request_msg(method, url, kwargs)

        # Read the gate's resolution counter immediately before the send —
        # deliberately not at request entry.  A resolution that completed
        # *before* this send already applies to the request, so a captcha
        # reply on it is a genuine new challenge and must still prompt; only
        # one that completes *after* the send makes the reply stale.
        epoch_at_send = captcha_gate.epoch

        try:
            resp_data = self._send_recv(msg)
        except (DaemonError, ConnectionError, OSError, ValueError) as exc:
            # Covers DaemonNotRunningError / DaemonConnectionError as well as
            # raw socket errors and framing/JSON errors that escape
            # ``_send_recv``.  A framing error also leaves the persistent
            # socket desynchronised, which is another reason to drop it.
            return self._degrade_to_direct(method, url, kwargs, exc)

        return self._process_response(resp_data, method, url, kwargs, epoch_at_send=epoch_at_send)

    # ── internal helpers ───────────────────────────────────────────────

    def _build_request_msg(self, method: str, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Build the ``http_request`` wire message for *kwargs*.

        Factored out of :meth:`request` so the post-captcha retry can re-send
        the caller's original request without duplicating the serialisation.

        :param method: HTTP method.
        :param url: Full request URL.
        :param kwargs: Caller kwargs, filtered by :meth:`_serialize_kwargs`.
        :returns: The message dict to hand to :meth:`_send_recv`.
        """
        return build_http_request(method, url, self._serialize_kwargs(kwargs))

    def _serialize_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Filter *kwargs* to only the keys safe for wire transport.

        Bytes values (e.g. ``data`` for image uploads) are base64-encoded
        under a ``data_base64`` key so they survive JSON serialisation.
        """
        wire: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k not in _WIRE_KWARGS:
                continue
            if k == "data" and isinstance(v, bytes):
                wire["data_base64"] = base64.b64encode(v).decode("ascii")
            else:
                wire[k] = v
        return wire

    def _process_response(
        self,
        resp_data: dict[str, Any],
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
        *,
        epoch_at_send: int | None = None,
        allow_captcha_handling: bool = True,
    ) -> DaemonProxyResponse:
        """Turn one daemon reply into a response, handling captchas on the way.

        :param resp_data: The decoded daemon message.
        :param method: HTTP method of the request that produced it.
        :param url: URL of the request that produced it.
        :param original_kwargs: Caller kwargs of that request.
        :param epoch_at_send: Value of :attr:`CaptchaGate.epoch` read
            immediately before that request was sent, so a captcha reply can
            be recognised as stale (a resolution completed while it was in
            flight).  ``None`` — the default — means the caller has no such
            timestamp, which disables only the stale-403 shortcut; the reply is
            then handled exactly as before it existed.
        :param allow_captcha_handling: When ``False``, a ``captcha_required``
            reply is turned into a synthetic 403 instead of re-entering the
            resolution flow.  The post-gate retry sets this so a *second*
            captcha cannot send the thread back around the wait loop.
        :returns: The response.
        :raises DaemonRequestError: if the daemon reported an error.
        :raises DaemonProtocolError: on an unexpected message type.
        """
        resp_type = resp_data.get("type")

        if resp_type == MSG_HTTP_RESPONSE:
            # Sync cookies from daemon back to the local headers mirror so
            # `session.cookies.get("d_c0")` reflects the latest state.
            self._sync_cookies(resp_data.get("cookies", {}))
            return DaemonProxyResponse(resp_data)

        if resp_type == MSG_CAPTCHA_REQUIRED:
            if not allow_captcha_handling:
                return _synthetic_403(resp_data.get("captcha_error", {}), url)
            return self._handle_captcha_via_daemon(resp_data, method, url, original_kwargs, epoch_at_send=epoch_at_send)

        if resp_type == MSG_ERROR:
            error_type = resp_data.get("error_type", "DaemonRequestError")
            message = resp_data.get("message", "Unknown daemon error")
            raise DaemonRequestError(error_type, message)

        raise DaemonProtocolError(f"Unexpected response type: {resp_type}")

    def _get_direct_session(self) -> Any:
        """Return the persistent direct fallback session, building it once.

        Created on first use after the daemon is lost and then reused, so the
        long-lived client keeps one TLS/QUIC connection and one cookie jar
        instead of paying for a handshake per request.  The mirror headers the
        daemon path kept up to date (including a rotated ``d_c0``) are copied
        over so the fallback starts from the same identity.

        :returns: The cached :class:`ZhihuSession` for the pinned (or active)
            profile.
        """
        direct = self._direct
        if direct is not None:
            return direct

        with self._direct_lock:
            if self._direct is None:
                direct = _build_context_session()
                try:
                    direct.headers.update(self.headers)
                except Exception:
                    pass
                direct.captcha_handler = self._captcha_handler
                self._direct = direct
            return self._direct

    def _reset_direct_session(self) -> None:
        """Close and forget the direct fallback (for profile reloads/shutdown).

        The next request after a profile switch builds a fresh session from
        the new profile's headers, mirroring what the daemon does on
        ``reload_session``.
        """
        with self._direct_lock:
            direct, self._direct = self._direct, None
        if direct is not None:
            try:
                direct.close()
            except Exception:
                pass

    def _degrade_to_direct(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any],
        exc: BaseException,
    ) -> DaemonProxyResponse:
        """Flip to direct mode and run the request that lost the daemon.

        Called from :meth:`request` (and the post-captcha re-sends) after a
        connection-level failure.  ``_daemon_down`` is sticky: subsequent
        requests skip the socket entirely, because a daemon that has gone away
        will not accept a reconnect.

        :param method: HTTP method of the failed request.
        :param url: URL of the failed request.
        :param kwargs: Caller kwargs of the failed request.
        :param exc: The connection error, reported once so the degradation is
            visible instead of silently changing transport.
        :returns: The direct response, shaped like a daemon reply.
        """
        with self._direct_lock:
            first_failure = not self._daemon_down
            self._daemon_down = True
        self._close_sock()
        if first_failure:
            print(
                f"  ⚠️  Lost the zhihu daemon ({exc}); falling back to direct requests for this process.",
                file=sys.stderr,
            )
        return self._fallback_direct(method, url, **kwargs)

    def _fallback_direct(self, method: str, url: str, **kwargs: Any) -> DaemonProxyResponse:
        """Run a non-streaming request through the persistent direct session.

        Used once the daemon is gone (see :meth:`_degrade_to_direct`).  The
        real response is re-wrapped as a :class:`DaemonProxyResponse` so
        callers cannot tell which transport served it; cookies set by the
        direct session are merged into the local mirror for the same reason
        the daemon path does it.
        """
        direct = self._get_direct_session()
        resp = direct.request(method, url, **kwargs)
        try:
            self._sync_cookies(dict(resp.cookies.get_dict()))
        except Exception:
            pass
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

    def _stream_direct(self, method: str, url: str, **kwargs: Any) -> _StreamResponse:
        """Create a temporary direct :class:`ZhihuSession` for a streaming
        request (SSE chat, video downloads, etc.).

        Returns a :class:`_StreamResponse` that wraps the real curl_cffi
        response — callers use ``iter_lines()`` to consume the stream and
        MUST call ``.close()`` when done to release the underlying session.
        """
        direct = _build_context_session()
        resp = direct.request(method, url, **kwargs)
        return _StreamResponse(resp, direct)

    def _handle_captcha_via_daemon(
        self,
        captcha_data: dict[str, Any],
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
        *,
        epoch_at_send: int | None = None,
    ) -> DaemonProxyResponse:
        """Handle a captcha that the daemon detected.

        Interactive resolution happens in the CLI process (which has a
        TTY).  After resolution, a ``captcha_resolved`` message is sent
        to the daemon so it can harvest cookies and retry.

        The :data:`~zhihu_cli.content.handlers.captcha_gate.captcha_gate`
        coordinates threads: the first thread to see the challenge becomes the
        resolver, the rest park in the gate and re-send their own stale 403
        once it clears.  A thread that never parked — its reply came back
        after a resolution from someone else already finished — spots the
        same staleness through :attr:`CaptchaGate.epoch` and re-sends too,
        instead of becoming a second resolver.  A resolver is *not* required
        to succeed — giving up still releases the gate, and everyone falls
        back to the 403.

        :param captcha_data: The ``captcha_required`` message.
        :param method: HTTP method of the blocked request.
        :param url: URL of the blocked request.
        :param original_kwargs: Caller kwargs of the blocked request, used to
            re-send it after the gate clears.
        :param epoch_at_send: :attr:`CaptchaGate.epoch` as read immediately
            before the blocked request was sent.  ``None`` means unknown, which
            skips the stale-403 shortcut but leaves every other branch
            unchanged.
        :returns: The retried response, or a synthetic 403 when the challenge
            was skipped, cancelled, timed out, or nothing can prompt.
        """
        captcha_error = captcha_data.get("captcha_error", {})
        pending = captcha_data.get("pending_request", {})

        if self._captcha_handler == "ignore":
            return _synthetic_403(captcha_error, url)

        if captcha_error.get("need_login"):
            # A login gate is not a captcha: never open the unhuman page or
            # wait for input.  Report it once and let the caller's gate
            # detection produce the actionable error.
            from zhihu_cli.content.handlers._session_core import _print_non_interactive_captcha_warning

            _print_non_interactive_captcha_warning(captcha_error)
            return _synthetic_403(captcha_error, url)

        if self._captcha_handler == "prompt":
            from zhihu_cli.content.handlers._session_core import _print_captcha_warning

            _print_captcha_warning(captcha_error)
            return _synthetic_403(captcha_error, url)

        # "auto" mode: interactive resolution.  ``_can_prompt_user`` is the
        # same guard the direct path uses — without a terminal, prompting
        # would block forever, so degrade to returning the 403.
        from zhihu_cli.content.handlers._session_core import (
            _can_prompt_user,
            _print_non_interactive_captcha_warning,
        )

        if not _can_prompt_user():
            _print_non_interactive_captcha_warning(captcha_error)
            return _synthetic_403(captcha_error, url)

        if captcha_gate.is_resolver():
            # Re-entrant: this reply reached a thread that is already running
            # the resolution flow — never prompt twice.
            return _synthetic_403(captcha_error, url)

        if epoch_at_send is not None and captcha_gate.epoch != epoch_at_send:
            # A resolution completed while our request was in flight, so this
            # reply was produced before it and is stale.  We never parked at
            # the gate, so the wait branch below cannot see us: without this
            # the thread would become a *second* resolver and prompt the user
            # for a challenge that was just answered.
            return self._resend_after_captcha(method, url, original_kwargs)

        if captcha_gate.try_begin():
            try:
                return self._resolve_captcha_interactively(captcha_error, pending, method, url, original_kwargs)
            finally:
                captcha_gate.finish()

        if captcha_gate.acquire():
            # Another thread resolved the captcha while we waited: our own
            # request already came back as a stale 403, so re-send it once.
            return self._resend_after_captcha(method, url, original_kwargs)

        # Gate timed out: hand back the 403 rather than hang forever.
        return _synthetic_403(captcha_error, url)

    def _resend_after_captcha(
        self,
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
    ) -> DaemonProxyResponse:
        """Re-send a request whose earlier captcha reply the gate made stale.

        ``allow_captcha_handling=False`` is the non-recursion guard: a second
        captcha on the retry yields a synthetic 403 instead of re-entering the
        wait loop.  If the daemon disappeared while the challenge was being
        resolved, degrade to a direct request rather than raising — the
        caller still gets its answer.

        :param method: HTTP method of the blocked request.
        :param url: URL of the blocked request.
        :param original_kwargs: Caller kwargs of the blocked request.
        :returns: The retried response.
        """
        resend_epoch = captcha_gate.epoch
        try:
            resp_data = self._send_recv(self._build_request_msg(method, url, original_kwargs))
        except (DaemonError, ConnectionError, OSError, ValueError) as exc:
            return self._degrade_to_direct(method, url, original_kwargs, exc)
        return self._process_response(
            resp_data,
            method,
            url,
            original_kwargs,
            epoch_at_send=resend_epoch,
            allow_captcha_handling=False,
        )

    def _resolve_captcha_interactively(
        self,
        captcha_error: dict[str, Any],
        pending: dict[str, Any],
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
    ) -> DaemonProxyResponse:
        """Run the interactive flow and tell the daemon to retry.

        Called on the resolver thread with the gate held; the caller is
        responsible for :meth:`CaptchaGate.finish`.

        :param captcha_error: The error dict from the daemon.
        :param pending: ``pending_request`` from the ``captcha_required`` message.
        :param method: HTTP method of the blocked request (fallback).
        :param url: URL of the blocked request (fallback).
        :param original_kwargs: Caller kwargs of the blocked request (fallback).
        :returns: The daemon's response to the retried request, or a synthetic
            403 when the user skipped/cancelled the challenge.
        :raises DaemonRequestError: if the daemon's retry failed.
        """
        from zhihu_cli.content.handlers._session_core import _build_direct_session

        # Use a temporary direct session for captcha cookie harvesting
        direct = _build_direct_session()
        try:
            result = resolve_captcha(direct, captcha_error)
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
    Uses the session's ``_cookie_dict`` cache for O(1) lookups instead of
    O(n) string-scanning of the ``Cookie`` header.
    """

    def __init__(self, session: DaemonProxySession) -> None:
        self._session = session

    def get(self, name: str, default: str = "") -> str:
        """Return a cookie value by name (O(1) from internal cache)."""
        return self._session._cookie_dict.get(name, default)

    def get_dict(self) -> dict[str, str]:
        """Return all cookies as a dict (used by auth login)."""
        return dict(self._session._cookie_dict)

    def set(self, name: str, value: str) -> None:
        """Set a cookie value in the local cache and header mirror.

        Note: this does NOT propagate to the daemon.  For persistent
        cookie changes, use ``zhihu auth`` or ``reload_session()``.
        """
        if value:
            self._session._cookie_dict[name] = value
        else:
            self._session._cookie_dict.pop(name, None)
        self._session.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._session._cookie_dict.items())


def _synthetic_403(captcha_error: dict[str, Any], url: str) -> DaemonProxyResponse:
    """Build a synthetic 403 response representing a captcha or login block.

    ``need_login`` and ``redirect`` are preserved so the caller's gate
    detection can distinguish a login gate from a captcha after the daemon
    has declined to resolve it.
    """
    error = {
        "code": captcha_error.get("code", 40352),
        "message": captcha_error.get("message", "Risk control triggered"),
    }
    if captcha_error.get("need_login"):
        error["need_login"] = True
    if captcha_error.get("redirect"):
        error["redirect"] = captcha_error["redirect"]
    body = json.dumps({"error": error}).encode()
    return DaemonProxyResponse(
        {
            "status_code": 403,
            "headers": {"Content-Type": "application/json"},
            "body_base64": base64.b64encode(body).decode("ascii"),
            "elapsed": 0.0,
            "url": url,
        }
    )

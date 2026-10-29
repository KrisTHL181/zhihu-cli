"""Daemon server: local socket listener that holds persistent ZhihuSessions.

The server keeps one long-lived :class:`ZhihuSession` (with QUIC/HTTP3) per
profile name and accepts JSON-RPC-style messages over a local socket — a Unix
domain socket on POSIX, or loopback TCP with a per-start token on Windows.
Each client message may carry a ``profile`` field; the server selects (and, if
needed, lazily builds) that profile's session and never falls back to another
profile's credentials.  HTTP requests are executed synchronously in a
thread-pool to keep the asyncio event loop responsive.

Captcha handling
----------------
The daemon runs its session with ``captcha_handler = "ignore"`` to avoid
blocking on interactive prompts.  When a captcha is detected, the daemon
returns a ``captcha_required`` message to the client (CLI), which handles
resolution interactively.  The client then sends ``captcha_resolved`` so
the daemon can harvest cookies and retry.

Logging
-------
Errors are written to stderr, which is redirected to
``~/.zhihu-cli/daemon.log`` by ``start_daemon()``.  This avoids importing
the logging module (keeping startup fast) while still producing
diagnosable output when the daemon misbehaves.
"""

from __future__ import annotations

import asyncio
import base64
import faulthandler
import hashlib
import hmac
import json
import os
import signal
import sys
import threading
import time
import traceback
from typing import Any

from curl_cffi import CurlHttpVersion

from zhihu_cli.content.handlers._session_core import (
    ZhihuSession,
    _is_captcha_endpoint,
    _resolve_user_agent,
    get_browser,
)
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha
from zhihu_cli.content.utils import rate_limit
from zhihu_cli.daemon import _platform, transport
from zhihu_cli.daemon.protocol import (
    MSG_AUTH,
    MSG_CAPTCHA_REQUIRED,
    MSG_CAPTCHA_RESOLVED,
    MSG_HTTP_REQUEST,
    MSG_PING,
    MSG_RELOAD_SESSION,
    MSG_SET_CONFIG,
    MSG_SHUTDOWN,
    build_error,
    build_http_response,
    build_pong,
    make_msg_id,
    read_message,
    write_message,
)
from zhihu_cli.paths import zhihu_home

# ── stderr-based logging (output -> daemon.log) ──────────────────────────


def _log(msg: str, *, exc: bool = False) -> None:
    """Write a timestamped message to stderr (captured by daemon.log).

    :param msg: Human-readable message.
    :param exc: If :data:`True`, append the current exception traceback.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[daemon {ts}] {msg}", file=sys.stderr, flush=True)
    if exc:
        traceback.print_exc(file=sys.stderr)


def _profile_label(profile: str | None) -> str:
    """Return a log-safe label for *profile*.

    ``None`` is the key older clients (which do not send the field) use; it
    resolves to the daemon's active profile, exactly as before per-profile
    sessions existed.

    :param profile: Profile name, or ``None``.
    :returns: The name, or ``"<active>"`` for ``None``.
    """
    return profile if profile is not None else "<active>"


# Default timeout for requests that don't specify one (prevents a single
# hung request from blocking the shared session indefinitely).
_DEFAULT_REQUEST_TIMEOUT = 30.0


class DaemonServer:
    """Async Unix socket server that proxies HTTP requests through a persistent
    :class:`ZhihuSession`.
    """

    SOCKET_PATH = zhihu_home() / "daemon.sock"

    def __init__(self, idle_timeout: int = 300) -> None:
        self.idle_timeout = idle_timeout
        #: TCP auth token for this start; ``None`` on a Unix socket.
        self._token: str | None = None
        #: Human-readable bound endpoint, for the start log (never the token).
        self._endpoint: str = ""
        #: One session per profile name.  ``None`` is the key used by clients
        #: that do not pin a profile (older versions), and resolves to the
        #: active profile at build time — the pre-isolation behavior.
        self._sessions: dict[str | None, ZhihuSession] = {}
        #: Fingerprint of the headers/UA/app-header triple each session was
        #: built from, so a reload can be a no-op when nothing changed.
        self._fingerprints: dict[str | None, str] = {}
        #: Per-profile captcha mode, applied whenever that session is (re)built.
        self._captcha_modes: dict[str | None, str] = {}
        #: Single global lock protecting both registry mutations and the whole
        #: HTTP exchange (preserves the previous serialisation).  It is a plain
        #: ``threading.Lock`` — never re-acquire it.
        self._session_lock = threading.Lock()
        self._last_activity = time.monotonic()
        #: Requests currently being processed.  The main loop is a single
        #: asyncio thread, so no lock is needed; the idle checker uses this
        #: to avoid shutting down in the middle of a request.
        self._in_flight = 0
        self._requests_handled = 0
        self._start_time = time.monotonic()
        self._shutdown_event = asyncio.Event()
        #: Why the server is stopping.  Written once by
        #: :meth:`_request_shutdown` and reported in the exit log, so
        #: ``daemon.log`` distinguishes an idle timeout from
        #: ``zhihu daemon stop`` and from a signal.  ``None`` means nothing
        #: initiated a shutdown (an abnormal exit).
        self._shutdown_reason: str | None = None

    # ── public entry point ──────────────────────────────────────────────

    def run(self) -> None:
        """Start the server (blocking).  Sets up signal handlers and enters
        the asyncio event loop.

        Any exception during initialisation or the event loop is logged to
        stderr (→ daemon.log) and triggers cleanup so stale state/socket
        files don't block a subsequent restart.
        """
        # A fatal signal (SIGSEGV/SIGABRT/…) otherwise kills the process
        # without leaving any closing line in daemon.log, which makes a crash
        # indistinguishable from an uncatchable kill.  This dumps a traceback
        # to the same file first.  An uncatchable kill cannot be observed
        # from inside the process; its signature is simply a log with no
        # exit line.
        faulthandler.enable(file=sys.stderr)
        failed = False
        try:
            # Pre-build the active profile under its own name so the common
            # (unpinned) case is warm at first request.
            self._init_session(cache_manager.get_active_profile())
            self._cleanup_stale_socket()
            asyncio.run(self._async_main())
        except KeyboardInterrupt:
            _log(f"Daemon shutting down (PID {os.getpid()}): KeyboardInterrupt")
        except Exception:
            _log("Daemon terminated by unhandled exception", exc=True)
            failed = True
        finally:
            self.cleanup()
        if failed:
            raise SystemExit(1)

    def _request_shutdown(self, reason: str) -> None:
        """Record *reason* (first one wins) and wake the main loop.

        Every shutdown trigger goes through here so the exit log names the
        actual cause.  The first reason is kept: a ``zhihu daemon stop``
        that is followed by ``stop_daemon()``'s SIGTERM fallback while the
        process is exiting must not have its cause overwritten.

        :param reason: Human-readable cause, e.g. ``"idle timeout (300s)"``.
        """
        if self._shutdown_reason is None:
            self._shutdown_reason = reason
            _log(f"Shutdown initiated: {reason}")
        self._shutdown_event.set()

    # ── initialization ──────────────────────────────────────────────────

    def _session_fingerprint(self, headers: dict[str, str], ua: str) -> str:
        """Return a stable digest of the identity a session would be built from.

        Reloads compare this instead of rebuilding unconditionally, so the
        per-client ``reload_session`` that every CLI build sends no longer
        tears down the daemon's session when nothing changed.

        :param headers: Profile headers as loaded from the cache.
        :param ua: Resolved User-Agent (may be empty).
        :returns: Hex SHA-256 over headers, UA, and the app-identity headers.
        """
        payload = json.dumps(
            [
                sorted(headers.items()),
                ua,
                cache_manager.get_app_version(),
                cache_manager.get_app_za(),
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_session_locked(self, profile: str | None) -> None:
        """Build and install the :class:`ZhihuSession` for *profile*.

        The caller must hold :attr:`_session_lock`; this method never acquires
        it (the lock is not reentrant).  Only the previous session for the same
        profile key is closed — other profiles are untouched.

        :param profile: Profile name, or ``None`` for the active/legacy key.
        """
        hdrs = cache_manager.load_headers(profile)
        ua = _resolve_user_agent(hdrs)
        sess = ZhihuSession(
            impersonate=get_browser(ua),
            http_version=CurlHttpVersion.V3,
        )
        sess.headers.update(hdrs)
        if ua:
            sess.headers["User-Agent"] = ua
        # Inject the mobile-app identity headers (mirrors
        # requests._inject_app_headers for the direct session).  Without
        # ``x-app-za`` the server treats requests as a generic web client
        # and returns a reduced feed (e.g. excludes paid / 盐选 answers).
        sess.headers["x-app-version"] = cache_manager.get_app_version()
        sess.headers["x-app-za"] = cache_manager.get_app_za()
        # Daemon never handles captcha interactively (no TTY) unless a client
        # pinned another mode through SET_CONFIG.  Captcha detection is done
        # manually in _handle_http_request().
        sess.captcha_handler = self._captcha_modes.get(profile, "ignore")

        old = self._sessions.get(profile)
        self._sessions[profile] = sess
        self._fingerprints[profile] = self._session_fingerprint(hdrs, ua)
        if old is not None and old is not sess:
            try:
                old.close()
            except Exception:
                pass
        # The daemon is the account-wide chokepoint: every client that goes
        # through it shares these sessions' ``request``, so pacing it here
        # limits the account, not one process.  Deliberately the *persistent*
        # config only — the daemon must not inherit ``ZHIHU_QPS`` from whichever
        # client happened to start it.  A client's ``--qps`` stays client-local;
        # only ``zhihu config qps set`` reaches this process, via SET_CONFIG.
        rate_limit.limiter.configure(cache_manager.get_qps())
        _log(f"Session rebuilt for profile '{_profile_label(profile)}'")

    def _reload_session_locked(self, profile: str | None) -> None:
        """Reload *profile*'s session only when its identity changed.

        The caller must hold :attr:`_session_lock`.  Reuse is the common case:
        every CLI invocation asks for a reload, but the profile's headers are
        unchanged, so rebuilding would only drop a warm QUIC connection.

        :param profile: Profile name, or ``None`` for the active/legacy key.
        """
        hdrs = cache_manager.load_headers(profile)
        ua = _resolve_user_agent(hdrs)
        fingerprint = self._session_fingerprint(hdrs, ua)
        if profile in self._sessions and self._fingerprints.get(profile) == fingerprint:
            _log(f"Session reused for profile '{_profile_label(profile)}'")
            return
        self._build_session_locked(profile)

    def _init_session(self, profile: str | None = None) -> None:
        """Build (or rebuild) *profile*'s session from its headers.

        Used at startup and for config rebuilds; acquires the lock itself.

        :param profile: Profile name, or ``None`` for the active/legacy key.
        """
        with self._session_lock:
            self._build_session_locked(profile)

    def _reload_session(self, profile: str | None) -> None:
        """Reload *profile*'s session; thread-pool entry point.

        :param profile: Profile name, or ``None`` for the active/legacy key.
        """
        with self._session_lock:
            self._reload_session_locked(profile)

    def _rebuild_all_sessions(self) -> None:
        """Rebuild every cached profile's session; thread-pool entry point.

        Used when a global setting (the User-Agent override) changes and every
        cached profile's fingerprint therefore no longer matches.
        """
        with self._session_lock:
            for profile in list(self._sessions):
                self._build_session_locked(profile)

    @classmethod
    def _cleanup_stale_socket(cls) -> None:
        """Remove leftovers from a previous run (state, legacy pid/socket).

        ``Path.unlink(missing_ok=True)`` only touches the filesystem, so
        the legacy socket path is safe to remove on every platform — no
        socket object is ever created here.
        """
        transport.remove_state()
        transport.LEGACY_PID_FILE.unlink(missing_ok=True)
        cls.SOCKET_PATH.unlink(missing_ok=True)

    @classmethod
    def cleanup(cls) -> None:
        """Remove state, legacy pid file, and socket on shutdown."""
        transport.remove_state()
        transport.LEGACY_PID_FILE.unlink(missing_ok=True)
        cls.SOCKET_PATH.unlink(missing_ok=True)

    # ── async server loop ───────────────────────────────────────────────

    async def _async_main(self) -> None:
        loop = asyncio.get_running_loop()

        _platform.register_signal_handlers(loop, self._handle_signal)

        kind = transport.transport_kind()
        # Process identity for PID-reuse detection; it does not depend on
        # the transport, so read it once for the state dict below.
        start_time = _platform.process_start_time()
        server, fields = await _platform.bind_listener(self._handle_connection, self.SOCKET_PATH, kind)
        self._token = fields.get("token")
        self._endpoint = fields.get("path") or f"{fields['host']}:{fields['port']}"
        state = {
            "version": 1,
            "pid": os.getpid(),
            "started_at": time.time(),
            "start_time": start_time,
            **fields,
        }

        # Publish the endpoint only after the listener is up, so a client
        # never reads a state file pointing at a socket that does not accept
        # connections yet.
        transport.write_state(state)
        _log(f"Daemon started (PID {os.getpid()}), listening on {self._endpoint}, idle timeout={self.idle_timeout}s")

        # Idle-timeout background task
        idle_task = asyncio.create_task(self._idle_checker())

        async with server:
            await self._shutdown_event.wait()

        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass

        _log(f"Daemon process exiting (PID {os.getpid()}), reason: {self._shutdown_reason or 'unknown'}")

    def _handle_signal(self, signum: int) -> None:
        """Stop on SIGTERM/SIGINT, naming the signal in the shutdown log."""
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        self._request_shutdown(f"signal {name}")

    # ── connection handling ─────────────────────────────────────────────

    async def _authenticate(self, reader: asyncio.StreamReader) -> bool:
        """Consume and validate the one-frame TCP token handshake.

        Rejected clients are logged once, at a level that does not dump a
        traceback (a peer that simply cannot authenticate is not a server
        bug), and the caller closes the connection.

        :param reader: The connection's stream reader.
        :returns: :data:`True` if the frame carried the expected token.
        """
        try:
            msg = await asyncio.wait_for(read_message(reader), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError, ValueError) as exc:
            _log(f"Rejected daemon client: no valid auth frame ({type(exc).__name__})")
            return False
        if not isinstance(msg, dict):
            _log("Rejected daemon client: auth frame is not an object")
            return False
        if msg.get("type") != MSG_AUTH or not hmac.compare_digest(str(msg.get("token", "")), self._token or ""):
            _log("Rejected daemon client: auth token mismatch")
            return False
        return True

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process one client connection.  A single connection may carry
        multiple request/response pairs (persistent connection).
        """
        try:
            # TCP transport authenticates the peer before any message
            # dispatch; a Unix socket is already protected by filesystem
            # permissions and has no token.
            if self._token is not None and not await self._authenticate(reader):
                return
            while not self._shutdown_event.is_set():
                msg = await read_message(reader)
                # Count the request as activity as soon as it arrives, not
                # only once the response is out: a request that takes longer
                # than the idle budget must not let the idle checker fire.
                self._last_activity = time.monotonic()
                self._in_flight += 1
                try:
                    msg_id = msg.get("id", make_msg_id())
                    try:
                        response = await self._process_message(msg)
                    except Exception:
                        _log(f"Unhandled exception processing message {msg_id}", exc=True)
                        response = build_error(
                            msg_id,
                            "InternalError",
                            "Daemon encountered an unexpected error.  Check daemon.log for details.",
                        )
                    write_message(writer, response)
                    await writer.drain()
                finally:
                    self._in_flight -= 1
                self._last_activity = time.monotonic()
                self._requests_handled += 1
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass  # Client disconnected — normal
        except ValueError as exc:
            _log(f"Malformed message frame: {exc}")
        except Exception:
            _log("Unhandled exception in connection handler", exc=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── message dispatch ────────────────────────────────────────────────

    async def _process_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        msg_type = msg.get("type", "")
        msg_id = msg.get("id", make_msg_id())

        handlers = {
            MSG_PING: self._handle_ping,
            MSG_SHUTDOWN: self._handle_shutdown,
            MSG_RELOAD_SESSION: self._handle_reload_session,
            MSG_SET_CONFIG: self._handle_set_config,
            MSG_HTTP_REQUEST: self._handle_http_request,
            MSG_CAPTCHA_RESOLVED: self._handle_captcha_resolved,
        }

        handler = handlers.get(msg_type)
        if handler is None:
            return build_error(msg_id, "UnknownMessage", f"Unknown message type: {msg_type}")
        return await handler(msg_id, msg)

    # ── individual message handlers ─────────────────────────────────────

    async def _handle_ping(self, msg_id: str, _msg: dict[str, Any]) -> dict[str, Any]:
        return build_pong(
            msg_id,
            uptime_seconds=time.monotonic() - self._start_time,
            requests_handled=self._requests_handled,
        )

    async def _handle_shutdown(self, msg_id: str, _msg: dict[str, Any]) -> dict[str, Any]:
        self._request_shutdown("shutdown request from client (zhihu daemon stop)")
        return build_pong(msg_id)

    async def _handle_reload_session(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Reload the session named by the message's ``profile`` field.

        The rebuild is fingerprint-idempotent: unchanged headers reuse the
        existing session.  Runs in a thread-pool worker so that acquiring
        ``_session_lock`` never blocks the asyncio event loop — even when a
        long-running HTTP request already holds the lock.
        """
        profile = msg.get("profile")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._reload_session, profile)
            return build_pong(msg_id)
        except Exception as e:
            _log("Session reload failed", exc=True)
            return build_error(msg_id, type(e).__name__, str(e))

    async def _handle_set_config(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Propagate a live configuration change to the daemon session.

        All session mutations are dispatched to a thread-pool worker so
        that ``_session_lock`` contention never stalls the event loop.
        The ``user_agent`` key rebuilds every cached profile and must NOT
        be called inside ``_session_lock`` (``threading.Lock`` is
        non-reentrant — doing so would deadlock).

        ``captcha_handler`` applies to the message's ``profile`` (the
        per-profile mode is remembered for future builds); the other keys are
        process-global.

        ``qps`` is the account-wide rate limit: it configures the same
        process-global limiter that :meth:`ZhihuSession.request` calls, so it
        needs no session mutation and thus no ``_session_lock``.  Clients send
        it here only for the *persistent* value (``zhihu config qps set`` /
        ``clear``); a per-invocation ``--qps`` stays local to that client.
        """
        key = msg.get("key")
        value = msg.get("value")
        profile = msg.get("profile")
        loop = asyncio.get_running_loop()

        if key == "captcha_handler":
            try:
                await loop.run_in_executor(None, self._do_set_captcha_handler, profile, value)
                return build_pong(msg_id)
            except Exception as e:
                _log(f"Setting config captcha_handler failed for profile {_profile_label(profile)!r}", exc=True)
                return build_error(msg_id, type(e).__name__, str(e))
        elif key == "user_agent":
            try:
                await loop.run_in_executor(None, self._rebuild_all_sessions)
                return build_pong(msg_id)
            except Exception as e:
                _log("Session rebuild for user_agent failed", exc=True)
                return build_error(msg_id, type(e).__name__, str(e))
        elif key == "qps":
            # ``configure`` clamps non-finite/negative values to unlimited,
            # but a value that is not a number at all is a client bug worth
            # reporting rather than silently interpreting as "off".
            try:
                qps = float(value)
            except (TypeError, ValueError):
                return build_error(msg_id, "InvalidConfig", f"Invalid qps value: {value!r}")
            rate_limit.limiter.configure(qps)
            return build_pong(msg_id)
        else:
            return build_error(msg_id, "UnknownConfig", f"Unknown config key: {key}")

    def _do_set_captcha_handler(self, profile: str | None, value: Any) -> None:
        """Set the captcha_handler mode for *profile* (thread-pool worker).

        The mode is remembered even when that profile has no session yet, so a
        later lazy build applies it.

        :param profile: Profile name, or ``None`` for the active/legacy key.
        :param value: The new mode; non-strings are ignored.
        """
        if not isinstance(value, str):
            return
        with self._session_lock:
            self._captcha_modes[profile] = value
            sess = self._sessions.get(profile)
            if sess is not None:
                sess.captcha_handler = value

    async def _handle_http_request(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method", "GET")
        url = msg.get("url", "")
        kwargs = msg.get("kwargs", {})
        profile = msg.get("profile")

        loop = asyncio.get_running_loop()

        # Execute the synchronous request in a thread.  Wrap in try/except
        # so network errors don't propagate to the connection handler and
        # silently kill the client socket (client would hang until timeout).
        try:
            resp, cookies = await loop.run_in_executor(None, self._do_request, method, url, kwargs, profile)
        except Exception as e:
            _log(f"HTTP request failed: {method} {url}: {e}", exc=True)
            return build_error(msg_id, type(e).__name__, str(e))

        # Check for captcha after the request (skip captcha endpoints to
        # avoid re-entrant handling — same guard as ZhihuSession.request()).
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if not _is_captcha_endpoint(parsed.path):
            captcha_error = detect_captcha(resp)
            if captcha_error is not None:
                return {
                    "id": msg_id,
                    "type": MSG_CAPTCHA_REQUIRED,
                    "captcha_error": captcha_error,
                    # Save request details so the client can retry after resolution
                    "pending_request": {
                        "method": method,
                        "url": url,
                        "kwargs": kwargs,
                    },
                }

        return self._build_response(msg_id, resp, cookies)

    async def _handle_captcha_resolved(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        """After the CLI resolves the captcha, harvest cookies via the
        unhuman URL and retry the original request.
        """
        unhuman_url = msg.get("unhuman_url", "")
        retry = msg.get("retry_request", {})
        retry_method = retry.get("method", "GET")
        retry_url = retry.get("url", "")
        retry_kwargs = retry.get("kwargs", {})
        profile = msg.get("profile")

        loop = asyncio.get_running_loop()

        # Never retry under a different account: an unknown profile is an
        # error rather than a silent fallback to the active one.
        if profile is not None and profile not in cache_manager.list_profiles():
            return build_error(msg_id, "UnknownProfile", f"Profile {profile!r} does not exist")

        try:
            # Visit unhuman URL to harvest post-captcha cookies
            if unhuman_url:
                await loop.run_in_executor(None, self._do_request, "GET", unhuman_url, {"timeout": 30.0}, profile)

            # Retry the original request
            resp, cookies = await loop.run_in_executor(
                None, self._do_request, retry_method, retry_url, retry_kwargs, profile
            )
            return self._build_response(msg_id, resp, cookies)
        except Exception as e:
            _log(f"captcha retry failed: {retry_method} {retry_url}", exc=True)
            return build_error(msg_id, type(e).__name__, str(e))

    # ── session-level request execution ─────────────────────────────────

    def _do_request(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any],
        profile: str | None = None,
    ) -> tuple[Any, dict[str, str]]:
        """Execute an HTTP request with the session for *profile*.

        All access to the session registry is protected by ``_session_lock``.
        The session is selected strictly by *profile* — a missing one is built
        lazily for that profile, never borrowed from another.  A default timeout
        is applied when the caller does not specify one to prevent a single hung
        request from blocking all concurrent requests (and the idle checker)
        indefinitely.

        :param profile: Profile name from the request message, or ``None`` for
            the active/legacy key.
        :returns: ``(response, cookies_dict)`` — cookies are read while the
            lock is still held, avoiding a second lock acquisition just to
            harvest auth tokens.
        """
        # Strip timeout=None (curl_cffi's Session.request accepts it but
        # uses a sentinel internally; passing None explicitly can cause
        # issues on some versions).
        safe_kwargs = {k: v for k, v in kwargs.items() if not (k == "timeout" and v is None)}

        # The articles list endpoint (/members/{token}/articles) returns 500
        # when the x-app-za=OS=Android app-identity header is present.  Honor
        # a request-level skip_app_headers flag by removing the app headers
        # from the shared session for the duration of this request only.
        skip_app = bool(safe_kwargs.pop("skip_app_headers", False))

        # Decode base64-encoded binary body from the wire.
        if "data_base64" in safe_kwargs:
            safe_kwargs["data"] = base64.b64decode(safe_kwargs.pop("data_base64"))

        # Apply a default timeout so one hung request doesn't block the
        # shared session forever.
        if "timeout" not in safe_kwargs:
            safe_kwargs["timeout"] = _DEFAULT_REQUEST_TIMEOUT

        with self._session_lock:
            sess = self._sessions.get(profile)
            if sess is None:
                # Lazy build for this profile only.  Never fall back to another
                # profile's session: that would run the request as the wrong
                # account.
                self._build_session_locked(profile)
                sess = self._sessions[profile]
            saved_version = None
            saved_za = None
            if skip_app:
                # Temporarily drop the app-identity headers (restored below)
                # so endpoints that reject x-app-za (articles list) work.
                saved_version = sess.headers.pop("x-app-version", None)
                saved_za = sess.headers.pop("x-app-za", None)
            try:
                resp = sess.request(method, url, **safe_kwargs)
            finally:
                if saved_version is not None:
                    sess.headers["x-app-version"] = saved_version
                if saved_za is not None:
                    sess.headers["x-app-za"] = saved_za
            # Harvest auth cookies while the lock is still held — this
            # avoids a second lock acquisition in _build_response().
            cookies: dict[str, str] = {}
            for name in ("d_c0", "z_c0", "_xsrf"):
                val = sess.cookies.get(name, "")
                if val:
                    cookies[name] = str(val)
            return resp, cookies

    def _build_response(self, msg_id: str, resp: Any, cookies: dict[str, str] | None = None) -> dict[str, Any]:
        """Serialise a curl_cffi Response into an ``http_response`` message.

        :param cookies: Auth cookies harvested while the session lock was
            held by the caller.  Avoids a second lock acquisition just to
            read cookie values.
        """
        body = getattr(resp, "content", b"") or b""

        # elapsed may be a timedelta or float
        elapsed: Any = getattr(resp, "elapsed", 0.0)
        if not isinstance(elapsed, (int, float)):
            elapsed = getattr(elapsed, "total_seconds", lambda: 0.0)()

        return build_http_response(
            msg_id,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            body=body,
            elapsed=float(elapsed),
            url=str(getattr(resp, "url", "")),
            cookies=cookies or {},
        )

    # ── idle checker ────────────────────────────────────────────────────

    async def _idle_checker(self) -> None:
        """Shut down if the server is idle for longer than *idle_timeout*.

        Set *idle_timeout* to 0 to disable automatic shutdown entirely.
        Checks every 10 seconds so the daemon exits promptly after going
        idle without wasting CPU on busy-polling.  The timeout is suppressed
        while a request is in flight, so a long-running request is never
        killed by the idle clock.
        """
        if self.idle_timeout <= 0:
            return  # Disabled — never auto-exit
        while not self._shutdown_event.is_set():
            await asyncio.sleep(10)
            if self._in_flight:
                continue
            idle_for = time.monotonic() - self._last_activity
            if idle_for > self.idle_timeout:
                self._request_shutdown(f"idle timeout ({self.idle_timeout}s, idle for {idle_for:.0f}s)")
                break


# ── module entry point (``python -m zhihu_cli.daemon.server``) ───────────────


def _main() -> None:
    """Parse CLI args and run the server (entry point for subprocess)."""
    import argparse

    parser = argparse.ArgumentParser(description="zhihu-cli QUIC/HTTP3 daemon")
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=300,
        help="Auto-exit after N seconds of inactivity (default: 300)",
    )
    args = parser.parse_args()

    try:
        server = DaemonServer(idle_timeout=args.idle_timeout)
        server.run()
    except Exception:
        _log("Fatal error in _main()", exc=True)
        sys.exit(1)


if __name__ == "__main__":
    _main()

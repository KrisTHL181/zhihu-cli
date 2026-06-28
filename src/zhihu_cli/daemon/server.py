"""Daemon server: Unix socket listener that holds a persistent ZhihuSession.

The server creates one long-lived :class:`ZhihuSession` (with QUIC/HTTP3)
and accepts JSON-RPC-style messages over a Unix domain socket.  HTTP
requests are executed synchronously in a thread-pool to keep the asyncio
event loop responsive.

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
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
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
from zhihu_cli.daemon.protocol import (
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


# Default timeout for requests that don't specify one (prevents a single
# hung request from blocking the shared session indefinitely).
_DEFAULT_REQUEST_TIMEOUT = 30.0


class DaemonServer:
    """Async Unix socket server that proxies HTTP requests through a persistent
    :class:`ZhihuSession`.
    """

    SOCKET_PATH = Path.home() / ".zhihu-cli" / "daemon.sock"
    PID_FILE = Path.home() / ".zhihu-cli" / "daemon.pid"

    def __init__(self, idle_timeout: int = 300) -> None:
        self.idle_timeout = idle_timeout
        self._session: ZhihuSession | None = None
        self._session_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._requests_handled = 0
        self._start_time = time.monotonic()
        self._shutdown_event = asyncio.Event()

    # ── public entry point ──────────────────────────────────────────────

    def run(self) -> None:
        """Start the server (blocking).  Sets up signal handlers and enters
        the asyncio event loop.

        Any exception during initialisation or the event loop is logged to
        stderr (→ daemon.log) and triggers cleanup so stale PID/socket
        files don't block a subsequent restart.
        """
        try:
            self._init_session()
            self._cleanup_stale_socket()
            asyncio.run(self._async_main())
        except KeyboardInterrupt:
            _log("Received KeyboardInterrupt, shutting down")
        except Exception:
            _log("Daemon terminated by unhandled exception", exc=True)
        finally:
            self.cleanup()

    # ── initialization ──────────────────────────────────────────────────

    def _init_session(self) -> None:
        """Build (or rebuild) the :class:`ZhihuSession` from the currently
        active profile's headers.
        """
        with self._session_lock:
            old = self._session
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass

            hdrs = cache_manager.load_headers()
            ua = _resolve_user_agent(hdrs)
            sess = ZhihuSession(
                impersonate=get_browser(ua),
                http_version=CurlHttpVersion.V3,
            )
            sess.headers.update(hdrs)
            if ua:
                sess.headers["User-Agent"] = ua
            # Daemon never handles captcha interactively (no TTY).
            # Captcha detection is done manually in _handle_http_request().
            sess.captcha_handler = "ignore"

            self._session = sess

    def _write_pid_file(self) -> None:
        self.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.PID_FILE.write_text(str(os.getpid()))

    @classmethod
    def _cleanup_stale_socket(cls) -> None:
        """Remove a leftover socket file from a previous run."""
        cls.SOCKET_PATH.unlink(missing_ok=True)

    @classmethod
    def cleanup(cls) -> None:
        """Remove PID file and socket on shutdown."""
        cls.SOCKET_PATH.unlink(missing_ok=True)
        cls.PID_FILE.unlink(missing_ok=True)

    # ── async server loop ───────────────────────────────────────────────

    async def _async_main(self) -> None:
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except NotImplementedError:
                pass  # Windows / restricted environments

        server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.SOCKET_PATH),
        )
        # Restrict to current user only
        try:
            self.SOCKET_PATH.chmod(0o600)
        except OSError:
            pass

        # Write PID file only after the socket is successfully listening.
        # Doing it here ensures a stale PID file is never left behind if
        # the event loop fails to start.
        self._write_pid_file()
        _log(f"Daemon started (PID {os.getpid()}), listening on {self.SOCKET_PATH}, idle timeout={self.idle_timeout}s")

        # Idle-timeout background task
        idle_task = asyncio.create_task(self._idle_checker())

        async with server:
            await self._shutdown_event.wait()

        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass

        _log("Daemon shutting down normally")

    def _handle_signal(self) -> None:
        self._shutdown_event.set()

    # ── connection handling ─────────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process one client connection.  A single connection may carry
        multiple request/response pairs (persistent connection).
        """
        try:
            while not self._shutdown_event.is_set():
                msg = await read_message(reader)
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
                self._last_activity = time.monotonic()
                self._requests_handled += 1
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass  # Client disconnected — normal
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
        self._shutdown_event.set()
        return build_pong(msg_id)

    async def _handle_reload_session(self, msg_id: str, _msg: dict[str, Any]) -> dict[str, Any]:
        try:
            self._init_session()
            return build_pong(msg_id)
        except Exception as e:
            return build_error(msg_id, type(e).__name__, str(e))

    async def _handle_set_config(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        key = msg.get("key")
        value = msg.get("value")

        with self._session_lock:
            if self._session is None:
                return build_error(msg_id, "InternalError", "Session not initialised")
            if key == "captcha_handler":
                if isinstance(value, str):
                    self._session.captcha_handler = value
                return build_pong(msg_id)
            elif key == "user_agent":
                # Rebuild session to pick up new UA
                self._init_session()
                return build_pong(msg_id)
            else:
                return build_error(msg_id, "UnknownConfig", f"Unknown config key: {key}")

    async def _handle_http_request(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method", "GET")
        url = msg.get("url", "")
        kwargs = msg.get("kwargs", {})

        loop = asyncio.get_running_loop()

        # Execute the synchronous request in a thread.  Wrap in try/except
        # so network errors don't propagate to the connection handler and
        # silently kill the client socket (client would hang until timeout).
        try:
            resp = await loop.run_in_executor(None, self._do_request, method, url, kwargs)
        except Exception as e:
            _log(f"HTTP request failed: {method} {url}: {e}")
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

        return self._build_response(msg_id, resp)

    async def _handle_captcha_resolved(self, msg_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        """After the CLI resolves the captcha, harvest cookies via the
        unhuman URL and retry the original request.
        """
        unhuman_url = msg.get("unhuman_url", "")
        retry = msg.get("retry_request", {})
        retry_method = retry.get("method", "GET")
        retry_url = retry.get("url", "")
        retry_kwargs = retry.get("kwargs", {})

        loop = asyncio.get_running_loop()

        try:
            # Visit unhuman URL to harvest post-captcha cookies
            if unhuman_url:
                await loop.run_in_executor(None, self._do_request, "GET", unhuman_url, {"timeout": 30.0})

            # Retry the original request
            resp = await loop.run_in_executor(None, self._do_request, retry_method, retry_url, retry_kwargs)
            return self._build_response(msg_id, resp)
        except Exception as e:
            return build_error(msg_id, type(e).__name__, str(e))

    # ── session-level request execution ─────────────────────────────────

    def _do_request(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        """Execute an HTTP request with the shared session.

        All access to ``self._session`` is protected by ``_session_lock``.
        A default timeout is applied when the caller does not specify one
        to prevent a single hung request from blocking all concurrent
        requests (and the idle checker) indefinitely.
        """
        # Strip timeout=None (curl_cffi's Session.request accepts it but
        # uses a sentinel internally; passing None explicitly can cause
        # issues on some versions).
        safe_kwargs = {k: v for k, v in kwargs.items() if not (k == "timeout" and v is None)}

        # Apply a default timeout so one hung request doesn't block the
        # shared session forever.
        if "timeout" not in safe_kwargs:
            safe_kwargs["timeout"] = _DEFAULT_REQUEST_TIMEOUT

        with self._session_lock:
            if self._session is None:
                raise RuntimeError("Session not initialised")
            return self._session.request(method, url, **safe_kwargs)

    def _build_response(self, msg_id: str, resp: Any) -> dict[str, Any]:
        """Serialise a curl_cffi Response into an ``http_response`` message."""
        body = getattr(resp, "content", b"") or b""

        # Gather current cookies for the client mirror.
        # Must hold _session_lock to avoid racing with _init_session(),
        # which closes and replaces the session object.
        cookies: dict[str, str] = {}
        try:
            with self._session_lock:
                if self._session is not None:
                    for name in ("d_c0", "z_c0", "_xsrf"):
                        val = self._session.cookies.get(name, "")
                        if val:
                            cookies[name] = str(val)
        except Exception:
            pass

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
            cookies=cookies,
        )

    # ── idle checker ────────────────────────────────────────────────────

    async def _idle_checker(self) -> None:
        """Shut down if the server is idle for longer than *idle_timeout*.

        Set *idle_timeout* to 0 to disable automatic shutdown entirely.
        """
        if self.idle_timeout <= 0:
            return  # Disabled — never auto-exit
        while not self._shutdown_event.is_set():
            await asyncio.sleep(60)
            if time.monotonic() - self._last_activity > self.idle_timeout:
                self._shutdown_event.set()
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

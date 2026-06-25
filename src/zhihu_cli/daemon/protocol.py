"""IPC protocol: length-prefixed JSON framing over Unix stream sockets.

Wire format::

    [4 bytes: uint32 big-endian message length N][N bytes: UTF-8 JSON payload]

All multi-byte integers are network byte order (big-endian).

Message types (``msg["type"]``):

    ``http_request``
        Client → Daemon.  Carry an HTTP request the daemon should execute
        with its real ``ZhihuSession``.

    ``http_response``
        Daemon → Client.  Response body is base64-encoded in ``body_base64``.

    ``error``
        Daemon → Client.  Request failed with a recoverable exception.

    ``ping`` / ``pong``
        Client → Daemon / Daemon → Client.  Health check + stats.

    ``reload_session``
        Client → Daemon.  Instruct the daemon to rebuild its session from
        the current profile headers.

    ``shutdown``
        Client → Daemon.  Graceful shutdown request.

    ``set_config``
        Client → Daemon.  Propagate a config change (e.g. captcha_handler).

    ``captcha_required``
        Daemon → Client.  A captcha was triggered.  The CLI must resolve it
        interactively.

    ``captcha_resolved``
        Client → Daemon.  Captcha was resolved; retry the pending request.
"""

from __future__ import annotations

import base64
import json
import socket
import struct
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio


# ── constants ────────────────────────────────────────────────────────────────

MSG_HTTP_REQUEST = "http_request"
MSG_HTTP_RESPONSE = "http_response"
MSG_ERROR = "error"
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_RELOAD_SESSION = "reload_session"
MSG_SHUTDOWN = "shutdown"
MSG_SET_CONFIG = "set_config"
MSG_CAPTCHA_REQUIRED = "captcha_required"
MSG_CAPTCHA_RESOLVED = "captcha_resolved"

FRAME_HEADER_SIZE = 4  # uint32 big-endian
MAX_MESSAGE_SIZE = 32 * 1024 * 1024  # 32 MiB safety cap


# ── helpers ──────────────────────────────────────────────────────────────────


def make_msg_id() -> str:
    """Return a short unique message identifier for request/response pairing."""
    return "req_" + uuid.uuid4().hex[:12]


def _encode_body(data: bytes) -> str:
    """Base64-encode binary body data for JSON transport."""
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


# ── async I/O (server side) ──────────────────────────────────────────────────


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one length-prefixed JSON message from *reader*.

    :raises asyncio.IncompleteReadError: if the connection closes mid-frame.
    """
    header = await reader.readexactly(FRAME_HEADER_SIZE)
    length = struct.unpack("!I", header)[0]
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload)


def write_message(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    """Encode and write *msg* to *writer* (does NOT drain)."""
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    writer.write(struct.pack("!I", len(data)))
    writer.write(data)


# ── sync I/O (client / lifecycle side) ───────────────────────────────────────


def encode_message(msg: dict[str, Any]) -> bytes:
    """Return the wire-format bytes for *msg*."""
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return struct.pack("!I", len(data)) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly *n* bytes from *sock*, blocking."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed during read")
        buf += chunk
    return buf


def recv_message(sock: socket.socket) -> dict[str, Any]:
    """Read one length-prefixed message from *sock* (blocking)."""
    raw_len = _recv_exact(sock, FRAME_HEADER_SIZE)
    length = struct.unpack("!I", raw_len)[0]
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} bytes")
    payload = _recv_exact(sock, length)
    return json.loads(payload)


def send_message(sock: socket.socket, msg: dict[str, Any]) -> None:
    """Encode and send *msg* over *sock* (blocking)."""
    sock.sendall(encode_message(msg))


# ── message builders ─────────────────────────────────────────────────────────


def build_http_request(
    method: str,
    url: str,
    kwargs: dict[str, Any],
    msg_id: str | None = None,
) -> dict[str, Any]:
    """Build an ``http_request`` message."""
    return {
        "id": msg_id or make_msg_id(),
        "type": MSG_HTTP_REQUEST,
        "method": method.upper(),
        "url": url,
        "kwargs": kwargs,
    }


def build_http_response(
    msg_id: str,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
    *,
    elapsed: float = 0.0,
    url: str = "",
    cookies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an ``http_response`` message."""
    return {
        "id": msg_id,
        "type": MSG_HTTP_RESPONSE,
        "status_code": status_code,
        "headers": headers,
        "body_base64": _encode_body(body),
        "elapsed": elapsed,
        "url": url,
        "cookies": cookies or {},
    }


def build_error(
    msg_id: str,
    error_type: str,
    message: str,
    error_data: Any = None,
) -> dict[str, Any]:
    """Build an ``error`` response message."""
    return {
        "id": msg_id,
        "type": MSG_ERROR,
        "error_type": error_type,
        "message": message,
        "error_data": error_data,
    }


def build_pong(
    msg_id: str,
    *,
    uptime_seconds: float = 0.0,
    requests_handled: int = 0,
) -> dict[str, Any]:
    """Build a ``pong`` response with stats."""
    return {
        "id": msg_id,
        "type": MSG_PONG,
        "uptime_seconds": uptime_seconds,
        "requests_handled": requests_handled,
    }


# ── error classes ────────────────────────────────────────────────────────────


class DaemonError(Exception):
    """Base exception for all daemon-related errors."""


class DaemonNotRunningError(DaemonError):
    """The daemon is not running or the socket is unavailable."""


class DaemonConnectionError(DaemonError):
    """IPC connection failed or was lost mid-request."""


class DaemonProtocolError(DaemonError):
    """Unexpected or malformed response from daemon."""


class DaemonRequestError(DaemonError):
    """The daemon returned an error for a specific request."""

    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        super().__init__(f"[{error_type}] {message}")

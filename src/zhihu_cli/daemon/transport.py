"""Platform-portable daemon transport: Unix domain socket or loopback TCP.

POSIX systems use a Unix domain stream socket at ``~/.zhihu-cli/daemon.sock``.
Windows does not guarantee them, so the daemon binds an ephemeral
loopback TCP port instead and requires a one-frame token handshake
(:data:`~zhihu_cli.daemon.protocol.MSG_AUTH`) before serving any request.

Both client and server learn which transport is in use from a small JSON
state file (``~/.zhihu-cli/daemon.json``) written by the server *after* it
has bound its endpoint.  Client code therefore never branches on the
platform: it reads the state file and connects accordingly.

The module imports only the standard library and
:mod:`zhihu_cli.daemon.protocol`, keeping it light enough for the daemon
proxy's startup path.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from pathlib import Path

from zhihu_cli.daemon import _platform
from zhihu_cli.daemon.protocol import MSG_AUTH, make_msg_id, send_message

STATE_FILE = Path.home() / ".zhihu-cli" / "daemon.json"
UNIX_SOCKET_PATH = Path.home() / ".zhihu-cli" / "daemon.sock"
LEGACY_PID_FILE = Path.home() / ".zhihu-cli" / "daemon.pid"


# ── state file ───────────────────────────────────────────────────────────────


def read_state() -> dict | None:
    """Return the daemon state dict, or :data:`None`.

    A missing, unreadable, malformed, or non-object state file all map to
    :data:`None`, which callers treat as "no daemon".

    :returns: The parsed state, or :data:`None`.
    """
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(state: dict) -> None:
    """Atomically write *state* to :data:`STATE_FILE`.

    The payload is written to a temporary file in the same directory and
    then moved into place with :func:`os.replace`, so a concurrent reader
    never observes a partial file.  Permissions are restricted to the
    owner on a best-effort basis.

    :param state: JSON-serialisable state mapping.
    """
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=STATE_FILE.name + ".", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp_name, STATE_FILE)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def remove_state() -> None:
    """Delete the state file if it exists (idempotent)."""
    STATE_FILE.unlink(missing_ok=True)


# ── transport selection ──────────────────────────────────────────────────────


def transport_kind() -> str:
    """Return the transport the server should bind: ``"unix"`` or ``"tcp"``.

    Windows always uses TCP.  On POSIX, ``ZHIHU_DAEMON_TRANSPORT=tcp``
    forces TCP so both transports can be exercised in tests; it is read at
    call time, not import time.  The platform default comes from
    :func:`zhihu_cli.daemon._platform.default_transport`.

    :returns: ``"tcp"`` or ``"unix"``.
    """
    if os.environ.get("ZHIHU_DAEMON_TRANSPORT") == "tcp":
        return "tcp"
    return _platform.default_transport()


# ── connection ───────────────────────────────────────────────────────────────


def connect(state: dict, connect_timeout: float, read_timeout: float) -> socket.socket:
    """Open a connection described by *state*.

    For ``"unix"`` the state carries ``path``; for ``"tcp"`` it carries
    ``host`` and ``port``.  When the state carries a ``token`` (the TCP
    case), one :data:`~zhihu_cli.daemon.protocol.MSG_AUTH` frame is sent
    immediately.  No response is expected: the server either proceeds to
    serve requests or closes the connection.

    The returned socket has *read_timeout* applied for subsequent I/O.

    :param state: State mapping as returned by :func:`read_state`.
    :param connect_timeout: Timeout for establishing the connection.
    :param read_timeout: Timeout applied after a successful connection.
    :returns: A connected socket.
    :raises OSError: If the connection or the auth frame fails.  The socket
        is closed before the error propagates.
    """
    kind = state.get("transport")
    sock: socket.socket | None = None
    try:
        # The branch follows the transport recorded in the state file, not the platform.
        if kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(connect_timeout)
            sock.connect(state["path"])
        elif kind == "tcp":
            sock = socket.create_connection((state["host"], state["port"]), timeout=connect_timeout)
        else:
            raise OSError(f"Unknown daemon transport: {kind!r}")

        token = state.get("token")
        if token:
            send_message(sock, {"id": make_msg_id(), "type": MSG_AUTH, "token": token})

        sock.settimeout(read_timeout)
        return sock
    except OSError:
        if sock is not None:
            sock.close()
        raise

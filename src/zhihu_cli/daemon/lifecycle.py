"""Daemon process lifecycle management.

Provides helpers for starting, stopping, and checking the status of the
background zhihu-daemon process.  The daemon publishes its endpoint to a
state file at ``~/.zhihu-cli/daemon.json``: a Unix domain socket on POSIX,
or loopback TCP (with a per-start token) on Windows.  Clients connect from
that state file rather than branching on the platform.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from zhihu_cli.daemon import _platform, transport
from zhihu_cli.daemon.protocol import (
    MSG_PING,
    MSG_PONG,
    MSG_SHUTDOWN,
    DaemonNotRunningError,
    make_msg_id,
    recv_message,
    send_message,
)
from zhihu_cli.paths import zhihu_home

DAEMON_LOG_FILE = zhihu_home() / "daemon.log"

_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 3.0


# ── public API ───────────────────────────────────────────────────────────────


def is_running() -> bool:
    """Return :data:`True` if the daemon state names a live daemon process.

    Only the state file and the process identity are consulted; the
    endpoint itself is not probed.  A state file whose process is gone
    (or whose PID has been reused by an unrelated process) is stale and is
    cleaned up.
    """
    state = transport.read_state()
    if state is None:
        return False
    if _state_matches_process(state):
        return True
    cleanup_stale_files()
    return False


def start_daemon(idle_timeout: int = 300, foreground: bool = False) -> int:
    """Start the daemon process.

    :param idle_timeout: Seconds of inactivity before auto-shutdown.
    :param foreground: If :data:`True`, run in the foreground (for debugging).
    :returns: PID of the started daemon.
    :raises RuntimeError: If the daemon is already running or fails to start.
    """
    if is_running():
        raise RuntimeError(f"Daemon already running (PID {_read_pid()})")

    cleanup_stale_files()

    daemon_args = [
        sys.executable,
        "-m",
        "zhihu_cli.daemon.server",
        "--idle-timeout",
        str(idle_timeout),
    ]

    if foreground:
        proc = subprocess.Popen(daemon_args)
        return proc.pid

    # Background: detach from terminal, redirect output to log.
    log_fh = subprocess.DEVNULL
    try:
        log_fh = open(DAEMON_LOG_FILE, "a")
    except OSError:
        pass

    try:
        # Detach from the terminal and redirect output to the log.  Platform
        # differences (start_new_session on POSIX, creationflags on Windows)
        # live in _platform.popen_kwargs().
        proc = subprocess.Popen(
            daemon_args,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **_platform.popen_kwargs(),
        )
    finally:
        if log_fh is not subprocess.DEVNULL:
            log_fh.close()  # type: ignore[union-attr]

    # Wait for the daemon to publish its endpoint in the state file (up to
    # 2 seconds).  The file is written only after the listener is bound, so
    # this replaces polling for the socket path.
    for _ in range(20):
        state = transport.read_state()
        if state is not None and state.get("pid") == proc.pid and _platform.pid_alive(proc.pid):
            return proc.pid
        time.sleep(0.1)

    # Timeout — try to kill the child
    try:
        proc.kill()
    except ProcessLookupError:
        pass  # Process already exited
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Process didn't die — will be raised as RuntimeError below
        pass
    # Read any logged error
    if DAEMON_LOG_FILE.exists():
        tail = _tail_log(DAEMON_LOG_FILE, 20)
        raise RuntimeError(f"Daemon failed to start:\n{tail}")
    raise RuntimeError("Daemon failed to start within 2 seconds")


def stop_daemon(timeout: float = 5.0) -> bool:
    """Gracefully stop the daemon.

    Sends a ``shutdown`` message over the local socket first; then asks the
    process to exit through the platform helper (SIGTERM on POSIX, terminate
    on Windows) and finally force-kills it if it is still alive.

    :param timeout: Seconds to wait for graceful exit.
    :returns: :data:`True` if the daemon was stopped.
    """
    if not is_running():
        return False

    # Read the state exactly once: the PID and start time must describe the
    # same process, and the identity is re-checked before every signal below.
    state = transport.read_state()
    if state is None:
        return False
    pid = state.get("pid")
    if not isinstance(pid, int):
        cleanup_stale_files()
        return False

    # Try IPC shutdown first.  The exception types are caught (not only
    # cleaned up in ``finally``) so the terminal fallbacks below always run
    # even when the daemon vanished between ``is_running()`` and here.
    sock = None
    try:
        try:
            sock = _connect()
            send_message(sock, {"id": make_msg_id(), "type": MSG_SHUTDOWN})
            # Read acknowledgement (pong or error — either is fine)
            recv_message(sock)
            # Give it a moment to shut down
            if pid:
                _wait_for_exit(pid, min(timeout, 3.0))
        except (DaemonNotRunningError, OSError, ValueError):
            pass

        # If still alive, ask politely, then force it.  The identity is
        # re-checked immediately before each signal: between waiting for the
        # daemon to exit and this point its PID could have been reused by an
        # unrelated process, which must never be signalled.
        if _platform.pid_alive(pid) and _state_matches_process(state):
            _platform.terminate(pid, force=False)
            _wait_for_exit(pid, timeout)

        if _platform.pid_alive(pid) and _state_matches_process(state):
            _platform.terminate(pid, force=True)
            _wait_for_exit(pid, 2.0)

        return not _platform.pid_alive(pid) if pid else True
    finally:
        if sock is not None:
            sock.close()
        cleanup_stale_files()


def daemon_status() -> dict:
    """Return daemon status as a dict.

    When running, includes ``pid``, ``uptime_seconds``, and
    ``requests_handled``.  When the daemon does not answer the ping, the
    result also carries ``status: "unresponsive"`` and ``error``.  When not
    running, only ``{"running": False}``.
    """
    if not is_running():
        return {"running": False}

    pid = _read_pid()

    error: str | None = None
    sock = None
    try:
        sock = _connect()
        send_message(sock, {"id": make_msg_id(), "type": MSG_PING})
        pong = recv_message(sock)

        if pong.get("type") == MSG_PONG:
            return {
                "running": True,
                "pid": pid,
                "uptime_seconds": pong.get("uptime_seconds", 0),
                "requests_handled": pong.get("requests_handled", 0),
            }
        error = f"unexpected reply: {pong.get('type')!r}"
    except (DaemonNotRunningError, OSError, ValueError) as exc:
        error = str(exc)
    finally:
        if sock is not None:
            sock.close()

    return {"running": True, "pid": pid, "status": "unresponsive", "error": error}


def cleanup_stale_files() -> None:
    """Remove the state file and legacy pid/socket files (idempotent)."""
    transport.remove_state()
    transport.LEGACY_PID_FILE.unlink(missing_ok=True)
    transport.UNIX_SOCKET_PATH.unlink(missing_ok=True)


# ── internal helpers ─────────────────────────────────────────────────────────


def _connect() -> socket.socket:
    """Connect to the endpoint published in the state file.

    :raises DaemonNotRunningError: if no state exists or the connection fails.
    """
    state = transport.read_state()
    if state is None:
        raise DaemonNotRunningError("Daemon not running (no state file)")
    try:
        return transport.connect(state, _CONNECT_TIMEOUT, _READ_TIMEOUT)
    except OSError as e:
        raise DaemonNotRunningError(f"Daemon not available: {e}") from e


def _state_matches_process(state: dict) -> bool:
    """Return :data:`True` if *state* still names the daemon process.

    A PID alone is not an identity: after the daemon exits, the OS may
    reuse its PID for an unrelated process, so a stale state file plus PID
    reuse would otherwise make the old daemon look alive — and
    :func:`stop_daemon` would signal the wrong process.  The state
    therefore records the process start time, which is re-read and compared
    here.  The check fails closed: if the identity cannot be re-read, the
    state is treated as stale rather than risk killing a reused PID.

    :param state: State mapping as returned by :func:`transport.read_state`.
    :returns: :data:`True` only when the PID is alive and its start time
        matches the recorded one (or the state predates the identity check).
    """
    pid = state.get("pid")
    if not isinstance(pid, int) or not _platform.pid_alive(pid):
        return False
    expected = state.get("start_time")
    if expected is None:
        # Legacy state file, or a platform without process identity —
        # preserve the old PID-only behaviour.
        return True
    current = _platform.process_start_time(pid)
    return current is not None and current == expected


def _read_pid() -> int | None:
    """Read the daemon PID from the state file, or :data:`None`."""
    state = transport.read_state()
    if state is None:
        return None
    pid = state.get("pid")
    return pid if isinstance(pid, int) else None


def _wait_for_exit(pid: int, timeout: float) -> None:
    """Poll until *pid* exits or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _platform.pid_alive(pid):
            return
        time.sleep(0.1)


def _tail_log(path: Path, lines: int) -> str:
    """Return the last *lines* lines of *path*."""
    try:
        text = path.read_text()
        all_lines = text.splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception:
        return "(could not read log)"

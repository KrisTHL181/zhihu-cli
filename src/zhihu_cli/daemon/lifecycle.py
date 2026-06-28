"""Daemon process lifecycle management.

Provides helpers for starting, stopping, and checking the status of the
background zhihu-daemon process.  The daemon communicates over a Unix
domain socket at ``~/.zhihu-cli/daemon.sock`` and writes its PID to
``~/.zhihu-cli/daemon.pid``.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from zhihu_cli.daemon.protocol import (
    MSG_PING,
    MSG_PONG,
    MSG_SHUTDOWN,
    DaemonNotRunningError,
    make_msg_id,
    recv_message,
    send_message,
)

DAEMON_SOCKET = Path.home() / ".zhihu-cli" / "daemon.sock"
DAEMON_PID_FILE = Path.home() / ".zhihu-cli" / "daemon.pid"
DAEMON_LOG_FILE = Path.home() / ".zhihu-cli" / "daemon.log"

_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 3.0


# ── public API ───────────────────────────────────────────────────────────────


def is_running() -> bool:
    """Return :data:`True` if the daemon is alive and accepting connections.

    Checks the PID file validity (signal 0) *and* socket existence.
    """
    if not DAEMON_PID_FILE.exists():
        return False
    if not DAEMON_SOCKET.exists():
        # Stale PID file — clean up
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return False

    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (OSError, ProcessLookupError, ValueError):
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
        proc = subprocess.Popen(
            daemon_args,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if log_fh is not subprocess.DEVNULL:
            log_fh.close()  # type: ignore[union-attr]

    # Wait for the socket to appear (up to 2 seconds)
    for _ in range(20):
        if DAEMON_SOCKET.exists():
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

    Sends a ``shutdown`` message over the socket first; falls back to
    SIGTERM / SIGKILL if the socket is unresponsive.

    :param timeout: Seconds to wait for graceful exit.
    :returns: :data:`True` if the daemon was stopped.
    """
    if not is_running():
        return False

    pid = _read_pid()

    # Try IPC shutdown first
    try:
        sock = _connect()
        send_message(sock, {"id": make_msg_id(), "type": MSG_SHUTDOWN})
        # Read acknowledgement (pong or error — either is fine)
        try:
            recv_message(sock)
        except Exception:
            pass
        sock.close()
        # Give it a moment to shut down
        _wait_for_exit(pid, min(timeout, 3.0))
    except Exception:
        pass

    # If still alive, send SIGTERM
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        _wait_for_exit(pid, timeout)

    # Force kill if still alive
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_for_exit(pid, 2.0)

    cleanup_stale_files()
    return not _pid_alive(pid) if pid else True


def daemon_status() -> dict:
    """Return daemon status as a dict.

    When running, includes ``pid``, ``uptime_seconds``, and
    ``requests_handled``.  When not running, only ``{"running": False}``.
    """
    if not is_running():
        return {"running": False}

    pid = _read_pid()

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
    except Exception:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    return {"running": True, "pid": pid}


def cleanup_stale_files() -> None:
    """Remove PID file and socket if they exist (idempotent)."""
    DAEMON_PID_FILE.unlink(missing_ok=True)
    DAEMON_SOCKET.unlink(missing_ok=True)


# ── internal helpers ─────────────────────────────────────────────────────────


def _connect() -> socket.socket:
    """Connect to the daemon socket.

    :raises DaemonNotRunningError: if connection fails.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(_CONNECT_TIMEOUT)
    try:
        sock.connect(str(DAEMON_SOCKET))
        sock.settimeout(_READ_TIMEOUT)
        return sock
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        sock.close()
        raise DaemonNotRunningError(f"Daemon not available: {e}") from e


def _read_pid() -> int | None:
    """Read the PID from the PID file, or :data:`None`."""
    if not DAEMON_PID_FILE.exists():
        return None
    try:
        return int(DAEMON_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    """Return :data:`True` if a process with *pid* exists."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _wait_for_exit(pid: int, timeout: float) -> None:
    """Poll until *pid* exits or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
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

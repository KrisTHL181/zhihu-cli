"""Platform-specific primitives for the daemon, gathered in one place.

Every ``os.name`` / platform decision in the daemon lives here: the default
transport, the flags used to detach a background daemon process, process
liveness and termination, the process start-time identity token, signal
handler registration, and listener binding.  The rest of the daemon branches
only on the transport recorded in the state file (``~/.zhihu-cli/daemon.json``),
never on the operating system.

This module is imported on the CLI startup path via
:mod:`zhihu_cli.daemon.lifecycle`, so it is stdlib-only: ``asyncio``,
``secrets``, and ``psutil`` are imported inside the functions that need
them.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def default_transport() -> str:
    """Return the transport the daemon should use on this platform.

    :returns: ``"tcp"`` on Windows (Unix domain sockets are not guaranteed
        there), ``"unix"`` everywhere else.
    """
    return "tcp" if os.name == "nt" else "unix"


def popen_kwargs() -> dict[str, Any]:
    """Return the :func:`subprocess.Popen` keyword arguments for a detached daemon.

    Windows has no ``start_new_session``; it detaches the child from the
    console with ``DETACHED_PROCESS`` and gives it its own process group.
    Those ``subprocess`` attributes are accessed only inside the Windows
    branch, because they do not exist on POSIX.

    :returns: Keyword arguments to pass to :class:`subprocess.Popen`.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def pid_alive(pid: int) -> bool:
    """Return :data:`True` if a process with *pid* exists.

    Windows has no safe signal-0 probe, so it goes through
    ``psutil.pid_exists`` (imported lazily — this is a Windows-only
    dependency).  On Windows ``os.kill(pid, 0)`` calls ``TerminateProcess``
    and must never be used.

    :param pid: Process ID to probe.
    :returns: :data:`True` if the process exists.
    """
    if os.name == "nt":
        import psutil

        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except OSError:
        return False


def terminate(pid: int, *, force: bool) -> None:
    """Ask *pid* to exit, force-killing it when *force* is set.

    POSIX uses SIGTERM/SIGKILL; Windows uses ``psutil``'s ``terminate`` /
    ``kill``, which map onto the platform's own process-termination APIs.

    :param pid: Process to signal.
    :param force: Use a hard kill instead of a polite termination request.
    """
    if os.name == "nt":
        import psutil

        try:
            proc = psutil.Process(pid)
            if force:
                proc.kill()
            else:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


def process_start_time(pid: int | None = None) -> int | float | None:
    """Return a start-time identity token for *pid* (default: this process).

    The value is only meaningful on the machine that produced it: it comes
    from that machine's own process table (``/proc/<pid>/stat`` field 22 on
    Linux, ``psutil.Process.create_time()`` on Windows) and is not
    comparable across machines or reboots.  Its purpose is an identity
    check against PID reuse — the daemon records the token next to its PID,
    so a later liveness check can tell the daemon apart from an unrelated
    process that happens to have the same PID.

    :param pid: Process to inspect; defaults to the current process.
    :returns: The start-time token, or :data:`None` when it cannot be read
        (a platform without ``/proc`` such as macOS, a vanished process, or
        a malformed entry).
    """
    if os.name == "nt":
        import psutil

        try:
            return psutil.Process(pid or os.getpid()).create_time()
        except psutil.Error:
            return None
    try:
        text = Path(f"/proc/{pid or 'self'}/stat").read_text(errors="replace")
        # The comm field is wrapped in parentheses and may itself contain
        # spaces and parens, so split after the *last* ``)``; the start time
        # is field 22 overall, i.e. index 19 in the remainder.
        fields = text.rpartition(")")[2].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def register_signal_handlers(loop: Any, callback: Callable[[int], None]) -> None:
    """Register SIGTERM/SIGINT shutdown handlers on *loop* (POSIX only).

    Windows has no equivalent; the daemon there is stopped through the IPC
    shutdown message or ``psutil``.  Restricted POSIX environments may not
    implement ``add_signal_handler``; that is ignored.

    :param loop: The running asyncio event loop.
    :param callback: Called with the signal number when a signal arrives.
    """
    if os.name == "nt":
        return
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, callback, sig)
        except NotImplementedError:
            pass  # Restricted environment


async def bind_listener(handler: Any, socket_path: Path, kind: str) -> tuple[Any, dict[str, Any]]:
    """Bind the daemon listener for transport *kind*.

    ``"unix"`` binds a Unix domain socket at *socket_path* and restricts it
    to the owner; any other value binds an ephemeral loopback TCP port and
    returns a per-start random auth token (any local process could otherwise
    reach the port).

    :param handler: Connection handler passed to ``asyncio.start_*_server``.
    :param socket_path: Unix socket path (ignored for TCP).
    :param kind: Transport to bind, ``"unix"`` or ``"tcp"``.
    :returns: ``(server, fields)`` where *fields* are the state-file
        transport fields (``transport``, plus ``path`` or ``host``/``port``,
        always ``token``).
    """
    import asyncio
    import secrets

    if kind == "unix":
        server = await asyncio.start_unix_server(handler, path=str(socket_path))
        try:
            socket_path.chmod(0o600)
        except OSError:
            pass
        return server, {"transport": "unix", "path": str(socket_path), "token": None}

    token = secrets.token_hex(32)
    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    return server, {"transport": "tcp", "host": "127.0.0.1", "port": port, "token": token}

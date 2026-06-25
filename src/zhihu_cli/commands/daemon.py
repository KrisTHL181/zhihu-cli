"""``zhihu daemon`` CLI command group.

Provides subcommands for managing the background QUIC/HTTP3 daemon::

    zhihu daemon start    # Start the daemon
    zhihu daemon stop     # Graceful shutdown
    zhihu daemon status   # Show uptime and request count
    zhihu daemon restart  # Stop + start
"""

from __future__ import annotations

import click

from zhihu_cli.daemon.lifecycle import (
    daemon_status,
    start_daemon,
    stop_daemon,
)
from zhihu_cli.output import echo, error, f_label, f_num, info, success


def register_daemon(main_group: click.Group) -> None:
    """Register the ``zhihu daemon`` command group on *main_group*."""

    @main_group.group()
    def daemon() -> None:
        """Manage the background QUIC/HTTP3 connection daemon."""

    @daemon.command("start")
    @click.option(
        "--idle-timeout",
        "-t",
        default=300,
        type=int,
        help="Auto-exit after N seconds of inactivity (default: 300).",
    )
    @click.option(
        "--foreground",
        "-f",
        is_flag=True,
        default=False,
        help="Run in the foreground (for debugging).",
    )
    def daemon_start(idle_timeout: int, foreground: bool) -> None:
        """Start the background daemon.

        The daemon maintains persistent QUIC/HTTP3 connections to Zhihu,
        avoiding the overhead of a new TLS+QUIC handshake on every CLI
        invocation.
        """
        try:
            pid = start_daemon(idle_timeout=idle_timeout, foreground=foreground)
            success(f"Daemon started (PID {f_num(pid)}).")
        except RuntimeError as e:
            error(str(e))
            raise SystemExit(1) from e

    @daemon.command("stop")
    def daemon_stop() -> None:
        """Stop the daemon gracefully.

        Sends a shutdown message over the Unix socket; falls back to
        SIGTERM / SIGKILL if the socket is unresponsive.
        """
        if stop_daemon():
            success("Daemon stopped.")
        else:
            info("Daemon was not running.")

    @daemon.command("status")
    def daemon_status_cmd() -> None:
        """Show daemon status and statistics."""
        status = daemon_status()
        if status.get("running"):
            echo(f"  {f_label('Status:')}    running")
            echo(f"  {f_label('PID:')}       {f_num(status['pid'])}")
            uptime = status.get("uptime_seconds", 0)
            echo(f"  {f_label('Uptime:')}    {f_num(f'{uptime:.0f}s')}")
            handled = status.get("requests_handled", 0)
            echo(f"  {f_label('Requests:')}  {f_num(handled)}")
        else:
            info("Daemon is not running.")

    @daemon.command("restart")
    @click.option(
        "--idle-timeout",
        "-t",
        default=300,
        type=int,
        help="Auto-exit after N seconds of inactivity (default: 300).",
    )
    def daemon_restart(idle_timeout: int) -> None:
        """Stop and restart the daemon."""
        stop_daemon()
        try:
            pid = start_daemon(idle_timeout=idle_timeout)
            success(f"Daemon restarted (PID {f_num(pid)}).")
        except RuntimeError as e:
            error(str(e))
            raise SystemExit(1) from e

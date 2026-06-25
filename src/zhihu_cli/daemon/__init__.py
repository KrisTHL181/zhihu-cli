"""QUIC/HTTP3 connection daemon for persistent Zhihu API sessions.

The daemon package provides a long-running background process that maintains
persistent QUIC connections to Zhihu, avoiding the overhead of new TLS+QUIC
handshakes on every CLI invocation.

Public API
----------
- :class:`DaemonProxySession` — drop-in replacement for ``ZhihuSession``
  that proxies requests through the background daemon over a Unix socket
- :func:`is_running`, :func:`start_daemon`, :func:`stop_daemon`,
  :func:`daemon_status` — lifecycle management helpers
"""

from __future__ import annotations

from zhihu_cli.daemon.lifecycle import daemon_status, is_running, start_daemon, stop_daemon
from zhihu_cli.daemon.proxy import DaemonProxyResponse, DaemonProxySession

__all__ = [
    "DaemonProxySession",
    "DaemonProxyResponse",
    "is_running",
    "start_daemon",
    "stop_daemon",
    "daemon_status",
]

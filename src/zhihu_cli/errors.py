"""Exception types raised by the public library API.

Everything that comes out of :mod:`zhihu_cli.api` is a :class:`ZhihuError`, so a
caller can catch that single type instead of the mix the handlers raise
internally — ``ValueError`` for unresolvable URLs and missing response fields,
``RuntimeError`` for risk control and failed sends, ``PermissionError`` for a
rejected follow, and ``curl_cffi.requests.HTTPError`` for everything else.

The CLI does **not** use this module: command modules branch on the built-in
types (``except ValueError`` in ``commands/draft.py``, ``except RuntimeError`` in
``commands/chat.py``, …), so the handlers keep raising them unchanged and the
translation happens at the ``api.py`` boundary.

This module defines classes and nothing else. It imports no third-party package,
so :mod:`zhihu_cli` imports it eagerly without slowing ``import zhihu_cli`` down.
"""

from __future__ import annotations

__all__ = [
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "ZhihuError",
    "ZhihuHTTPError",
]


class ZhihuError(Exception):
    """Base class for every error the public API raises."""


class AuthError(ZhihuError):
    """The operation needs a logged-in profile that is missing or invalid."""


class RateLimitError(ZhihuError):
    """Zhihu's risk control refused the request (HTTP 403, captcha challenge)."""


class NotFoundError(ZhihuError):
    """A URL or ID could not be resolved, or a response was missing its payload."""


class ZhihuHTTPError(ZhihuError):
    """A non-2xx response that is not a risk-control refusal.

    Wraps :class:`curl_cffi.requests.HTTPError` on a direct session and
    :class:`~zhihu_cli.daemon.proxy.DaemonHTTPError` on a daemon-backed one.  The
    original exception stays reachable as ``__cause__``.
    """

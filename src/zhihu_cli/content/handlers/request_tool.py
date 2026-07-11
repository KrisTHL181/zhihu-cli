"""Request tool — send HTTP requests or generate equivalent curl commands.

Provides the backend for ``zhihu tools request``.  Supports both sending
requests through the authenticated ZhihuSession (with automatic ZSE signing
for zhihu.com domains) and generating equivalent curl commands for manual
execution or debugging.
"""

from __future__ import annotations

import hashlib
import json as json_mod
from typing import Any
from urllib.parse import urlparse


def _get_dc0(headers: dict[str, str]) -> str:
    """Extract ``d_c0`` value from the Cookie header in *headers*."""
    cookie = headers.get("Cookie", "")
    for part in cookie.split("; "):
        if part.startswith("d_c0="):
            return part[5:]
    return ""


def _build_sign_source(
    url: str,
    data: str | None,
    headers: dict[str, str],
) -> str | None:
    """Build the ZSE sign source string for *url*, or ``None`` if not a zhihu.com domain.

    :param url: Full request URL.
    :param data: Request body string (JSON or raw — must match what
        :meth:`ZhihuSession._build_sign_source` would see).
    :param headers: Request headers dict (must include ``Cookie`` so
        ``d_c0`` can be extracted).
    :returns: Sign source string for ZSE encryption, or ``None`` if
        the URL is not a zhihu.com domain.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not (hostname == "zhihu.com" or hostname.endswith(".zhihu.com")):
        return None

    path_and_query = parsed.path
    if parsed.query:
        path_and_query += "?" + parsed.query

    dc0 = _get_dc0(headers)
    parts = ["101_3_3.0", path_and_query, dc0]
    if data:
        parts.append(data)

    return "+".join(parts)


def build_curl_command(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | None = None,
) -> str:
    """Build a curl command string for the given request.

    For zhihu.com / api.zhihu.com URLs, computes and includes ZSE signing
    headers (``x-zse-93``, ``x-zse-96``) automatically.  The ``d_c0``
    cookie value is extracted from the Cookie header in *headers*.

    :param url: Target URL.
    :param method: HTTP method (default ``"GET"``).
    :param headers: Headers dict — should include ``Cookie`` from the
        active profile so the curl command carries authentication.
    :param data: Request body string.
    :returns: A curl command string ready for shell execution.
    """
    parts = ["curl"]

    if method.upper() != "GET":
        parts.append(f"-X {method.upper()}")

    # Session / profile headers (Cookie, User-Agent, …)
    if headers:
        for key, value in headers.items():
            if key.lower() in ("accept-encoding",):
                continue
            # Escape single quotes in header values
            escaped_val = value.replace("'", "'\\''")
            parts.append(f"-H '{key}: {escaped_val}'")

    # ZSE signing for zhihu.com URLs
    sign_source = _build_sign_source(url, data, headers or {})
    if sign_source is not None:
        from zhihu_cli.content.utils.zse import ZSECipher

        md5_hash = hashlib.md5(sign_source.encode()).hexdigest()
        cipher = ZSECipher()
        signature = cipher.encrypt(md5_hash)
        parts.append("-H 'x-zse-93: 101_3_3.0'")
        parts.append(f"-H 'x-zse-96: 2.0_{signature}'")

    if data:
        escaped = data.replace("'", "'\\''")
        parts.append(f"-d '{escaped}'")

    parts.append(f"'{url}'")

    return " \\\n  ".join(parts)


def send_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | None = None,
) -> Any:
    """Send an HTTP request through the global :class:`ZhihuSession`.

    For zhihu.com URLs, ZSE signing and captcha handling are applied
    automatically by the session.  For external URLs the request goes
    through without signing.

    :param url: Target URL.
    :param method: HTTP method (default ``"GET"``).
    :param headers: Extra headers to merge with session defaults.
    :param data: Request body — sent as ``json`` if parseable as JSON,
        otherwise sent as raw ``data``.
    :returns: A response object with ``status_code``, ``headers``,
        ``text``, and ``content`` attributes.
    """
    from zhihu_cli.content.handlers.requests import session

    kwargs: dict[str, Any] = {}
    if headers:
        kwargs["headers"] = dict(headers)
    if data:
        try:
            kwargs["json"] = json_mod.loads(data)
        except (json_mod.JSONDecodeError, TypeError, ValueError):
            kwargs["data"] = data.encode("utf-8")

    return session.request(method, url, **kwargs)

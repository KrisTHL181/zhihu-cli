"""Core session classes and helpers that depend on curl_cffi and lxml.

This module is imported *lazily* by ``requests.py`` — only when the daemon
is unavailable and a direct :class:`ZhihuSession` is needed, or when
``fetch_page_html`` / ``get_page_state`` are called.

Keeping these heavy imports (curl_cffi, lxml) out of the ``requests.py``
top-level allows the CLI to start quickly when the background daemon is
running, because it only needs :class:`~zhihu_cli.daemon.proxy.DaemonProxySession`
(which uses nothing but stdlib).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

from curl_cffi import CurlHttpVersion
from curl_cffi import requests as _requests
from lxml import html as lxml_html

from zhihu_cli.content.handlers import get_user_agent
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha, handle_captcha

ZSE93 = "101_3_3.0"


def get_browser(ua: str) -> _requests.BrowserTypeLiteral:
    """Detect browser family from a User-Agent string for curl_cffi TLS impersonation.

    Matching order is deliberate:
    1. Edge first — its UA also contains ``Chrome/``.
    2. Chrome Android — distinguish phone (``Mobile``) vs tablet.
    3. Chrome desktop — ``Chrome/`` without ``Mobile``.
    4. Firefox — ``Firefox/`` + ``Gecko/``.
    5. iOS Safari — ``iPhone/iPad/iPod`` + ``Safari/``.
    6. Desktop Safari — ``Safari/`` + ``Version/``, no ``Mobile``, no ``Chrome/``.

    Tor Browser is intentionally **not** detected — it forges a generic Firefox
    UA on all platforms and actively hides its fingerprint.
    """
    if not ua:
        return "chrome"

    # Edge — contains the Edg/ marker (newer Chromium-based Edge).
    if re.search(r"Edg/", ua):
        return "edge"

    # Chrome on Android — Linux + Android + Chrome/.
    if "Android" in ua and re.search(r"Chrome/", ua):
        return "chrome_android"

    # Chrome desktop — Chrome/ without Edg/, Android, or Mobile.
    if re.search(r"Chrome/", ua) and "Mobile" not in ua:
        return "chrome"

    # Firefox — Firefox/ + Gecko/.
    if re.search(r"Firefox/", ua) and re.search(r"Gecko/", ua):
        return "firefox"

    # iOS Safari — iPhone/iPad/iPod device token + Safari/.
    if re.search(r"(?:iPhone|iPad|iPod)", ua) and re.search(r"Safari/", ua):
        return "safari_ios"

    # Desktop Safari — Safari/ + Version/, no Mobile, no Chrome/.
    if re.search(r"Safari/", ua) and re.search(r"Version/", ua) and "Mobile" not in ua:
        return "safari"

    return "chrome"


class ZhihuSession(_requests.Session):
    """Session that auto-injects x-zse-93 / x-zse-96 signing headers.

    Equivalent to zhihu++'s ``signFetchRequest()``: builds a canonical
    source string from the ZSE version, request path, ``d_c0`` cookie, and
    JSON body (when present), hashes it with MD5, then encrypts the hash
    via the ZSEv4 block cipher.

    Set ``captcha_handler`` to "auto" (interactive), "prompt" (ask first),
    or "ignore" (skip handling) to control risk-control handling.
    """

    CAPTCHA_HANDLER_MODES = ("auto", "prompt", "ignore")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._zse_cipher = None  # lazy init to avoid import-time side effects
        self.captcha_handler: str = "auto"  # "auto", "prompt", or "ignore"

    @property
    def zse_cipher(self):
        if self._zse_cipher is None:
            from zhihu_cli.content.utils.zse import ZSECipher

            self._zse_cipher = ZSECipher()
        return self._zse_cipher

    def _get_dc0(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        if isinstance(cookie_header, str):
            for part in cookie_header.split("; "):
                if part.startswith("d_c0="):
                    return part[5:]
        # curl_cffi Cookies may yield strings (names) when iterated
        try:
            return str(self.cookies.get("d_c0", ""))
        except Exception:
            pass
        return ""

    def _build_sign_source(self, method: str, url: str, kwargs: dict) -> str | None:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        if not (hostname == "zhihu.com" or hostname.endswith(".zhihu.com")):
            return None

        path_and_query = parsed.path
        if parsed.query:
            path_and_query += "?" + parsed.query

        dc0 = self._get_dc0()

        body = None
        if "json" in kwargs and kwargs["json"] is not None:
            body = json.dumps(kwargs["json"], separators=(",", ":"), ensure_ascii=False)
        elif "data" in kwargs:
            data = kwargs["data"]
            if isinstance(data, str):
                try:
                    json.loads(data)
                    body = data
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        parts = [ZSE93, path_and_query, dc0]
        if body is not None:
            parts.append(body)
        return "+".join(parts)

    def request(self, method, url, **kwargs):
        skip_app = kwargs.pop("skip_app_headers", False)

        saved_version = None
        saved_za = None
        if skip_app:
            saved_version = self.headers.pop("x-app-version", None)
            saved_za = self.headers.pop("x-app-za", None)

        try:
            sign_source = self._build_sign_source(method, url, kwargs)
            if sign_source is not None:
                md5_hash = hashlib.md5(sign_source.encode()).hexdigest()
                signature = self.zse_cipher.encrypt(md5_hash)

                zse_headers = {
                    "x-zse-93": ZSE93,
                    "x-zse-96": "2.0_" + signature,
                    "x-requested-with": "fetch",
                }
                user_headers = kwargs.get("headers", {})
                zse_headers.update(user_headers)
                kwargs["headers"] = zse_headers

            resp = super().request(method, url, **kwargs)

            # Post-response captcha / risk-control handling
            if self.captcha_handler != "ignore":
                captcha_error = detect_captcha(resp)
                if captcha_error is not None:
                    # Avoid re-entrant handling for captcha-related endpoints
                    parsed = urlparse(url)
                    path = parsed.path
                    if not _is_captcha_endpoint(path):
                        mode = self.captcha_handler
                        if mode == "auto":
                            result = handle_captcha(self, captcha_error)
                            if result == "resolved":
                                # Retry the original request once after verification
                                resp = super().request(method, url, **kwargs)
                            # For "skipped" or "cancelled", return the original
                            # 403 response so the caller can handle it appropriately.
                        elif mode == "prompt":
                            _print_captcha_warning(captcha_error)

            return resp
        finally:
            if skip_app:
                if saved_version is not None:
                    self.headers["x-app-version"] = saved_version
                if saved_za is not None:
                    self.headers["x-app-za"] = saved_za


def _resolve_user_agent(headers: dict[str, str]) -> str:
    """Return the effective User-Agent: configured override > profile UA > empty."""
    configured = get_user_agent()
    if configured:
        return configured
    return headers.get("User-Agent", "")


def _build_direct_session() -> ZhihuSession:
    """Build a direct :class:`ZhihuSession` from the active profile headers.

    This is the original (non-daemon) session factory.
    """
    hdrs = cache_manager.load_headers()
    ua = _resolve_user_agent(hdrs)
    sess = ZhihuSession(
        impersonate=get_browser(ua),
        http_version=CurlHttpVersion.V3,
    )
    sess.headers.update(hdrs)
    if ua:
        sess.headers["User-Agent"] = ua
    return sess


def get_page_state(html_text: str, key: str = "entities") -> dict[str, Any]:
    """Extract ``js-initialData`` from a Zhihu SSR page.

    :param html_text: Raw HTML of a Zhihu page.
    :param key: Top-level key in ``initialState`` (default: ``"entities"``).
    :returns: The parsed JSON subtree.
    :raises ValueError: If the ``js-initialData`` script tag is missing.
    """
    doc = lxml_html.fromstring(html_text)

    script_tag = doc.find(".//script[@id='js-initialData']")
    if script_tag is None or not script_tag.text:
        raise ValueError("Could not find 'js-initialData' script tag")

    initial_data = json.loads(script_tag.text)
    return initial_data["initialState"][key]


# ── captcha helpers ────────────────────────────────────────────────────────


def _is_captcha_endpoint(path: str) -> bool:
    """Return True if the path belongs to a captcha-related endpoint.

    These endpoints should NOT trigger captcha handling themselves,
    to avoid infinite recursion.
    """
    captcha_paths = (
        "/api/v4/anticrawl/new_captcha_appeal",
        "/antispam/",
        "/account/unhuman",
        "/account/risk_control",
        "/api/v3/oauth/captcha",
    )
    return path.startswith(captcha_paths)


def _print_captcha_warning(captcha_error: dict) -> None:
    """Print a brief warning that captcha was triggered (non-blocking)."""
    redirect = captcha_error.get("redirect", "")
    print(file=sys.stderr)
    print(f"  ⚠️  Zhihu risk control triggered ({captcha_error.get('message', 'unknown')[:60]}...)", file=sys.stderr)
    if redirect:
        print(f"  Verify at: {redirect}", file=sys.stderr)
    print("  Run 'zhihu auth captcha' to resolve, or switch to a different profile.", file=sys.stderr)
    print(file=sys.stderr)

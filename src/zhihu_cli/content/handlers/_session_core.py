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
import threading
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlparse

from curl_cffi import CurlHttpVersion
from curl_cffi import requests as _requests
from lxml import html as lxml_html

from zhihu_cli.content.handlers import get_user_agent
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha
from zhihu_cli.content.handlers.captcha_gate import captcha_gate, resolve_captcha
from zhihu_cli.content.utils import rate_limit

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
        # Serialises the pop→request→restore window over the *shared*
        # ``self.headers`` dict for ``skip_app_headers`` requests (see
        # ``request``); only those callers ever take it.
        self._app_headers_lock = threading.Lock()

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
        """Send a request, waiting at the captcha gate and handling challenges.

        The gate handles multi-threaded use: while any thread is resolving a
        captcha, every other thread parks here instead of sending requests
        Zhihu would only answer with another 403.  The wait is bounded (see
        :mod:`zhihu_cli.content.handlers.captcha_gate`), so a timed-out thread
        still sends and simply sees the original 403.

        :param method: HTTP method.
        :param url: Full request URL.
        :param kwargs: Same keyword arguments as
            :meth:`curl_cffi.requests.Session.request`, plus
            ``skip_app_headers`` (suppress ``x-app-version``/``x-app-za``).
        :returns: The response, retried once when a captcha was resolved.
        """
        # Order matters: the captcha gate can park many threads, so waiting
        # there first keeps the rate limiter's slots from being consumed by
        # threads that are not actually about to send.  This is the single
        # chokepoint for direct sessions, the daemon server's shared session,
        # and every ``_stream_direct`` fallback — no other call site needed.
        captcha_gate.acquire()
        rate_limit.limiter.acquire()
        skip_app = kwargs.pop("skip_app_headers", False)

        saved_version = None
        saved_za = None

        # Taking the app-headers lock for the whole request is the cheap,
        # obviously-correct way to keep two concurrent ``skip_app_headers``
        # requests from interleaving their pop/restore on the shared header
        # dict.  Residual (pre-existing, out of scope) limitation: a request
        # that does *not* skip the app headers can still observe them missing
        # while a skip_app request is in flight.
        with self._app_headers_lock if skip_app else nullcontext():
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

                # Read the gate's resolution counter immediately before the
                # send — deliberately not at request entry.  A resolution that
                # completed *before* this send already applies to the request
                # (post-resolution cookies went out with it), so a 403 on it is
                # a genuine new challenge and must still prompt; only a
                # resolution that completes *after* the send leaves us holding
                # a stale 403.
                epoch_at_send = captcha_gate.epoch

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
                                if not _can_prompt_user():
                                    # ``handle_captcha`` prompts through ``input()``. With no
                                    # terminal to read from it would block forever (or die with
                                    # EOFError), and either way it consumes the 403 so the
                                    # caller can no longer tell "captcha" from "this item is
                                    # unavailable". Degrade to the "ignore" behaviour instead:
                                    # hand the raw 403 back and let the caller decide.
                                    _print_non_interactive_captcha_warning(captcha_error)
                                elif captcha_gate.is_resolver():
                                    # Re-entrant: this request came from the resolver
                                    # thread's own resolution flow — never prompt twice.
                                    pass
                                elif captcha_gate.epoch != epoch_at_send:
                                    # A resolution completed while our request was in
                                    # flight, so this 403 was produced before it and is
                                    # stale.  We never parked at the gate, so the wait
                                    # branch below cannot see us: without this the
                                    # thread would become a *second* resolver and prompt
                                    # the user for a challenge that was just answered.
                                    # Re-send once through ``super().request`` (bypassing
                                    # this block) so a second captcha cannot recurse.
                                    resp = super().request(method, url, **kwargs)
                                elif captcha_gate.try_begin():
                                    try:
                                        result = resolve_captcha(self, captcha_error)
                                    finally:
                                        captcha_gate.finish()
                                    if result == "resolved":
                                        # Retry the original request once after
                                        # verification.  ``super().request`` on purpose:
                                        # the retry must bypass this block rather than
                                        # recurse into it.  For "skipped"/"cancelled"
                                        # the original 403 is returned as-is.
                                        resp = super().request(method, url, **kwargs)
                                elif captcha_gate.acquire():
                                    # Another thread resolved the captcha while we waited
                                    # at the gate: our own in-flight request is already a
                                    # stale 403, so re-send it exactly once.
                                    resp = super().request(method, url, **kwargs)
                                # On gate timeout, fall through and return the original
                                # 403 so the caller can decide what to do.
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


def _build_direct_session(profile_name: str | None = None) -> ZhihuSession:
    """Build a direct :class:`ZhihuSession` from a profile's headers.

    This is the original (non-daemon) session factory.

    :param profile_name: Profile whose headers to load.  ``None`` — the default —
        keeps the historical behaviour of using the *active* profile, so every
        existing caller is unaffected.  Naming a profile builds a session for a
        profile that is not active, which is what
        :func:`zhihu_cli.content.handlers.requests.use_profile` relies on.
    :returns: A session carrying that profile's headers.
    """
    hdrs = cache_manager.load_headers(profile_name)
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


def _can_prompt_user() -> bool:
    """Whether an interactive captcha prompt could actually reach a user.

    Both a readable stdin and a visible stderr are required: ``handle_captcha``
    reads a choice from stdin and prints instructions to stderr. Under cron,
    ``nohup``, or a redirected pipeline neither is a terminal, and prompting
    would block forever instead of telling anyone anything.
    """
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except Exception:  # noqa: BLE001
        return False


_non_interactive_captcha_warned = False
_non_interactive_captcha_lock = threading.Lock()


def _print_non_interactive_captcha_warning(captcha_error: dict) -> None:
    """Warn that a captcha went unhandled because nothing can prompt.

    Printed at most once per process — a captcha window lasts many requests and
    repeating this on every one of them would bury the rest of the output.  The
    flag is claimed under a lock (several worker threads can hit a captcha at
    the same instant); the message itself is printed outside it, so threads
    never contend on I/O.
    """
    global _non_interactive_captcha_warned
    with _non_interactive_captcha_lock:
        if _non_interactive_captcha_warned:
            return
        _non_interactive_captcha_warned = True

    redirect = captcha_error.get("redirect", "")
    print(file=sys.stderr)
    print(
        "  ⚠️  Zhihu risk control triggered, but this process has no terminal to "
        "prompt on.\n"
        "      Leaving the 403 unhandled so the caller can stop and report it.",
        file=sys.stderr,
    )
    if redirect:
        print(f"  Verify at: {redirect}", file=sys.stderr)
    print("  Run 'zhihu auth captcha' to resolve, or switch to a different profile.", file=sys.stderr)
    print(file=sys.stderr)

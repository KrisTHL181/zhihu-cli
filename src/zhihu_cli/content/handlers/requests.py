import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import CurlHttpVersion
from curl_cffi import requests as _requests
from user_agents import parse

from zhihu_cli.content.handlers import get_user_agent
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha, handle_captcha

ZSE93 = "101_3_3.0"


def get_browser(ua: str) -> _requests.BrowserTypeLiteral:
    family = parse(ua).browser.family.lower()
    if family in _requests.impersonate.REAL_TARGET_MAP:
        return family

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


def _resolve_user_agent(headers: dict[str, str]) -> str:
    """Return the effective User-Agent: configured override > profile UA > empty."""
    configured = get_user_agent()
    if configured:
        return configured
    return headers.get("User-Agent", "")


def _build_session() -> ZhihuSession:
    """Create a ZhihuSession with the configured or profile User-Agent."""
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


headers: dict[str, str] = {}
session = requests = _build_session()


def reload_session() -> None:
    """Reload the global session with headers from the active profile."""
    global headers, session, requests
    sess = _build_session()
    headers = dict(sess.headers)
    session = requests = sess


def get_page_entities(url: str) -> dict[str, Any]:
    url = url.replace("http://", "https://")
    resp = session.get(url)

    if resp.status_code == 403:
        raise PermissionError(f"Access denied (403). You might be blocked: {url}")

    soup = BeautifulSoup(resp.text, "html.parser")

    script_tag = soup.find("script", id="js-initialData")
    if not script_tag or script_tag.string is None:
        raise ValueError(f"Could not find 'js-initialData' script tag at {url}")

    initial_data = json.loads(script_tag.string)
    page_data = initial_data["initialState"]["entities"]
    return page_data


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
    print()
    print(f"  ⚠️  Zhihu risk control triggered ({captcha_error.get('message', 'unknown')[:60]}...)")
    if redirect:
        print(f"  Verify at: {redirect}")
    print("  Run 'zhihu auth captcha' to resolve, or switch to a different profile.")
    print()

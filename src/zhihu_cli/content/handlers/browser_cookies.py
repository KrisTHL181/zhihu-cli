"""Read an authenticated Zhihu session out of a locally installed browser's cookie store.

This module has no heavy top-level imports (no ``curl_cffi``, no ``lxml``, no
``browser_cookie3``) so it can be imported from the CLI without paying for the
optional dependency or for the browser-cookie machinery.
"""

from __future__ import annotations

import time
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from http.cookiejar import Cookie, CookieJar

#: CLI name -> ``browser_cookie3`` loader attribute name.
BROWSER_LOADERS: dict[str, str] = {
    "arc": "arc",
    "brave": "brave",
    "chrome": "chrome",
    "chromium": "chromium",
    "edge": "edge",
    "firefox": "firefox",
    "librewolf": "librewolf",
    "opera": "opera",
    "opera-gx": "opera_gx",
    "safari": "safari",
    "vivaldi": "vivaldi",
}

#: Order probed when the user does not pass --browser. Firefox is first
#: because its cookies are plaintext SQLite on Linux, whereas the Chromium
#: family needs a working keyring/SecretService to decrypt.
AUTO_DETECT_ORDER: tuple[str, ...] = (
    "firefox",
    "chrome",
    "chromium",
    "edge",
    "brave",
    "vivaldi",
    "opera",
    "opera-gx",
    "librewolf",
    "arc",
    "safari",
)

DOMAIN = "zhihu.com"
_PIP_HINT = "pip install 'zhihu-toolkit[browser]'"


def _require_browser_cookie3() -> ModuleType:
    """Import the optional ``browser_cookie3`` dependency.

    :returns: The imported ``browser_cookie3`` module.
    :raises RuntimeError: If the optional dependency is not installed.
    """
    try:
        import browser_cookie3
    except ImportError as exc:
        raise RuntimeError(
            f"Reading cookies from a browser requires the optional 'browser' extra: {_PIP_HINT}"
        ) from exc
    return browser_cookie3


def _load_jar(bc3: ModuleType, browser: str, domain: str) -> CookieJar:
    """Load the raw cookie jar for one browser and domain.

    :param bc3: The imported ``browser_cookie3`` module.
    :param browser: A key of :data:`BROWSER_LOADERS`.
    :param domain: The domain to filter cookies by.
    :returns: The browser's cookie jar for ``domain``.
    :raises RuntimeError: If the installed ``browser_cookie3`` has no loader for ``browser``.
    """
    loader_name = BROWSER_LOADERS[browser]
    loader = getattr(bc3, loader_name, None)
    if loader is None:
        raise RuntimeError(f"Installed browser_cookie3 has no '{loader_name}' loader — upgrade it: {_PIP_HINT}")
    return loader(domain_name=domain)


def _filter_expired(cookies: list[Cookie]) -> list[Cookie]:
    """Drop cookies that the browser has already expired.

    Session cookies (``expires is None``) are kept — they are only valid for
    the browser session but are still what the server expects right now.

    :param cookies: The cookies to filter.
    :returns: The cookies that are still valid.
    """
    now = time.time()
    return [cookie for cookie in cookies if cookie.expires is None or cookie.expires > now]


def _dedupe(cookies: list[Cookie]) -> list[Cookie]:
    """Collapse same-name cookies, keeping the most specific domain.

    A browser stores one cookie both for the bare domain and for the
    subdomain it was set on; the server only ever sees the most specific
    match, so ``www.zhihu.com`` must win over ``.zhihu.com``. Sorting
    ascending by (domain length, wildcard flag) is a specificity ordering, so
    the last entry written per cookie name is the most specific one.

    :param cookies: The cookies to deduplicate.
    :returns: One cookie per name.
    """
    ordered = sorted(
        cookies, key=lambda cookie: (len(cookie.domain or ""), bool((cookie.domain or "").startswith(".")))
    )
    by_name: dict[str, Cookie] = {}
    for cookie in ordered:
        by_name[cookie.name] = cookie
    return list(by_name.values())


def read_cookies(browser: str | None = None, domain: str = DOMAIN) -> tuple[str, list[Cookie]]:
    """Read the Zhihu cookies out of a browser's cookie store.

    :param browser: A name from :data:`BROWSER_LOADERS`, or ``None`` to probe
        :data:`AUTO_DETECT_ORDER` and use the first browser holding Zhihu cookies.
    :param domain: The domain to filter cookies by.
    :returns: ``(browser_name_used, cookies)``.
    :raises RuntimeError: If the dependency is missing, the browser name is
        unknown, or no Zhihu cookies could be found.
    """
    if browser is not None:
        browser = browser.strip().lower()
        if browser not in BROWSER_LOADERS:
            raise RuntimeError(f"Unknown browser '{browser}'. Supported: {', '.join(sorted(BROWSER_LOADERS))}.")
        candidates: tuple[str, ...] = (browser,)
    else:
        candidates = AUTO_DETECT_ORDER

    bc3 = _require_browser_cookie3()

    failures: list[str] = []
    for name in candidates:
        try:
            jar = _load_jar(bc3, name, domain)
        except Exception as exc:
            # browser_cookie3 raises assorted ad-hoc exception types (it has no
            # common base class), so catch broadly and just try the next browser.
            failures.append(f"{name}: {exc}")
            continue

        cookies = _dedupe(_filter_expired(list(jar)))
        if cookies:
            return name, cookies

    detail = ""
    if failures:
        detail = "\n" + "\n".join(f"  {line}" for line in failures)

    if browser is not None:
        raise RuntimeError(f"No Zhihu cookies found in {browser}. Is Zhihu logged in there?{detail}")

    message = "No Zhihu cookies found in any supported browser."
    message += detail
    raise RuntimeError(message)


def cookies_to_header(cookies: list[Cookie]) -> str:
    """Render cookies as a ``Cookie`` request header value.

    :param cookies: The cookies to render.
    :returns: The ``name=value`` pairs joined with ``"; "``.
    """
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies)


def collect_headers(browser: str | None = None) -> tuple[str, dict[str, str]]:
    """Build request headers for a session imported from a browser.

    :param browser: A name from :data:`BROWSER_LOADERS`, or ``None`` to auto-detect.
    :returns: ``(browser_name_used, headers)`` where ``headers`` holds the
        ``Cookie`` and ``User-Agent`` keys expected by
        ``cache_manager.save_headers()``.
    :raises RuntimeError: If the dependency is missing, the browser name is
        unknown, or no Zhihu cookies could be found.
    """
    # auth_login imports curl_cffi at module level — keep this import deferred.
    from zhihu_cli.content.handlers import get_user_agent
    from zhihu_cli.content.handlers.auth_login import DESKTOP_USER_AGENT

    browser_name, cookies = read_cookies(browser)
    headers = {
        "Cookie": cookies_to_header(cookies),
        "User-Agent": get_user_agent() or DESKTOP_USER_AGENT,
    }
    return browser_name, headers

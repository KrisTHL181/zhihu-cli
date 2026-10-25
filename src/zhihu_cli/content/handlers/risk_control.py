"""Detect Zhihu's anonymous-access gates and explain them to the caller.

Zhihu stops unauthenticated clients with several independent mechanisms.  This
module recognises them but deliberately does **not** solve any of them:

* the ``__zse_ck`` proof-of-work challenge — HTTP 403 with a ``zh-zse-ck``
  meta tag and a per-request ``zse-ck/v4/*.js`` script;
* the account-verification gate — a redirect to ``/account/unhuman`` (often
  carrying ``need_login=true``) or a rendered "安全验证" page;
* the sign-in redirect — ``/signin?next=…``;
* JSON risk-control refusals — HTTP 403 with error code ``40352`` (captcha)
  or ``40362`` (request blocked).

The module is stdlib-only on purpose: :mod:`requests` imports it at module
level, and that module must stay cheap so the CLI can start without curl_cffi
or lxml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlparse

if TYPE_CHECKING:
    from typing import Any

__all__ = ["ZhihuGateError", "detect_gate"]

#: Marker in the body of the ``__zse_ck`` challenge page.
_ZSE_CK_MARKER = "zh-zse-ck"

#: Title of the rendered account-verification page (served with HTTP 200).
_SECURITY_TITLE = "<title>安全验证 - 知乎</title>"

#: Path prefixes that mean "Zhihu wants a human / a login".
_UNHUMAN_PREFIXES = ("/account/unhuman", "/account/risk_control")
_SIGNIN_PREFIX = "/signin"

#: Error codes that mean risk control rather than a missing resource.
_RISK_CONTROL_CODES = frozenset({40352, 40362})

_GATE_LABELS = {
    "zse_ck": "the __zse_ck proof-of-work challenge",
    "unhuman": "the account verification gate",
    "signin": "the sign-in page",
    "risk_control": "Zhihu risk control",
}


class ZhihuGateError(RuntimeError):
    """An anonymous request was stopped by one of Zhihu's access gates.

    Subclasses :class:`RuntimeError` so existing handler call sites that already
    catch ``RuntimeError`` keep working.

    :param gate: One of ``"zse_ck"``, ``"unhuman"``, ``"signin"``, or
        ``"risk_control"``.
    :param url: The requested URL, or the gate URL when the server supplied one.
    :param detail: Optional server wording (for error-envelope gates).
    """

    def __init__(self, gate: str, url: str, *, detail: str | None = None) -> None:
        self.gate = gate
        self.url = url
        self.detail = detail
        super().__init__(_gate_message(gate, url, detail=detail))


def _gate_message(gate: str, url: str, *, detail: str | None = None) -> str:
    """Build the user-facing explanation for *gate*.

    :param gate: Gate identifier (see :class:`ZhihuGateError`).
    :param url: Requested URL or gate URL.
    :param detail: Optional server wording.
    :returns: A multi-sentence message with the next action to take.
    """
    login_hint = "Run 'zhihu auth login' or 'zhihu auth import' to sign in, or 'zhihu profile switch <name>' to use another profile."
    if gate == "zse_ck":
        return (
            f"Zhihu returned {_GATE_LABELS['zse_ck']} for {url}. "
            "This CLI recognises the challenge but does not solve it. " + login_hint
        )
    if gate == "unhuman":
        parsed = parse_qs(urlparse(url).query)
        need_login = parsed.get("need_login", ["false"])[0].lower() == "true"
        reason = " (need_login=true)" if need_login else ""
        return (
            f"Zhihu redirected to {_GATE_LABELS['unhuman']}{reason}: {url}. "
            "Complete the verification in a browser, then retry. " + login_hint
        )
    if gate == "signin":
        return (
            f"Zhihu redirected {url} to {_GATE_LABELS['signin']}; this resource needs "
            "an authenticated profile. " + login_hint
        )
    suffix = f" Server said: {detail}" if detail else ""
    return (
        f"Zhihu risk control refused {url} (HTTP 403)." + suffix + " "
        "Retry later, switch profiles, or use a different network."
    )


def _safe_text(response: Any) -> str:
    """Return the response body as text, or ``""`` when unavailable."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - streaming/closed responses have no text
        return ""
    return text if isinstance(text, str) else ""


def _safe_json(response: Any) -> dict | None:
    """Return the parsed JSON object, or ``None`` when the body is not one."""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - HTML and empty bodies are expected here
        return None
    return payload if isinstance(payload, dict) else None


def _location(response: Any) -> str:
    """Return the ``Location`` header of *response*, if any."""
    headers = getattr(response, "headers", None) or {}
    try:
        return str(headers.get("location") or headers.get("Location") or "")
    except Exception:  # noqa: BLE001 - exotic header containers
        return ""


def _redirect_urls(response: Any, request_url: str) -> list[str]:
    """Collect every URL a redirect chain passed through.

    Direct ``curl_cffi`` responses expose ``history``; the daemon proxy does
    not, so its final ``url`` and the ``Location`` header cover that case.

    :param response: Any response duck-typing the handler contract.
    :param request_url: The URL that was requested.
    :returns: Absolute URLs in chain order, starting with *request_url*.
    """
    urls = [request_url]
    location = _location(response)
    if location:
        urls.append(urljoin(request_url, location))

    for item in getattr(response, "history", None) or ():
        item_url = getattr(item, "url", "")
        if item_url:
            urls.append(str(item_url))
        item_location = _location(item)
        if item_location:
            urls.append(urljoin(str(item_url or request_url), item_location))

    final = getattr(response, "url", "")
    if final:
        urls.append(str(final))
    return urls


def _gate_for_url(url: str) -> str | None:
    """Classify a URL by path, returning a gate name or ``None``."""
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return None
    if path.startswith(_UNHUMAN_PREFIXES):
        return "unhuman"
    if path == _SIGNIN_PREFIX or path.startswith(_SIGNIN_PREFIX + "/"):
        return "signin"
    return None


def detect_gate(response: Any, request_url: str) -> ZhihuGateError | None:
    """Return the access gate that stopped *response*, if any.

    Detection order is deliberate: the ``__zse_ck`` body marker only appears on
    a 403, so it cannot be confused with the rendered verification page; the
    JSON envelope is checked before redirects because the API reports
    ``40353``/``40362`` without an HTTP redirect.

    :param response: Response from either a direct or daemon-backed session.
    :param request_url: The URL that was requested.
    :returns: A populated :class:`ZhihuGateError`, or ``None`` for a normal
        response.
    """
    status = getattr(response, "status_code", None)
    text = _safe_text(response)

    if status == 403 and _ZSE_CK_MARKER in text:
        return ZhihuGateError("zse_ck", request_url)

    if status == 403:
        payload = _safe_json(response)
        error = payload.get("error") if payload else None
        if isinstance(error, dict):
            redirect = str(error.get("redirect") or request_url)
            message = str(error.get("message") or "") or None
            if error.get("need_login"):
                return ZhihuGateError("unhuman", redirect, detail=message)
            if error.get("code") in _RISK_CONTROL_CODES:
                return ZhihuGateError("risk_control", request_url, detail=message)

    for url in _redirect_urls(response, request_url):
        gate = _gate_for_url(url)
        if gate is not None:
            return ZhihuGateError(gate, url)

    if _SECURITY_TITLE in text:
        return ZhihuGateError("unhuman", request_url)

    return None

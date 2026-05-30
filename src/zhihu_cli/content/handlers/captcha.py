"""Captcha (验证码) and risk-control (风控) handling for Zhihu API requests.

When Zhihu detects suspicious activity, it returns a 403 with error code
40352 and a ``sec_token`` cookie. The client must visit the unhuman page,
complete a verification challenge, and then retry the original request.

This module provides:
- Detection: parse 403 responses for captcha/risk-control errors
- Info: fetch captcha details from the appeal API
- Handling: guide the user through the verification process
"""

from __future__ import annotations

import json
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlparse

CAPTCHA_ERROR_CODE = 40352
APPEAL_URL = "https://www.zhihu.com/api/v4/anticrawl/new_captcha_appeal"
RISK_CONTROL_FALLBACK = "https://www.zhihu.com/account/risk_control/"


def detect_captcha(response: Any) -> dict | None:
    """Check if a response contains a captcha/risk-control error.

    Returns the parsed error dict if found, or None.
    The error dict contains: code, message, redirect, need_login.
    """
    if response.status_code != 403:
        return None

    try:
        data = response.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    error = data.get("error", {})
    if not isinstance(error, dict):
        return None

    if error.get("code") == CAPTCHA_ERROR_CODE or error.get("need_login"):
        return {
            "code": error.get("code"),
            "message": error.get("message", ""),
            "redirect": error.get("redirect") or RISK_CONTROL_FALLBACK,
            "need_login": error.get("need_login", False),
        }

    return None


def parse_redirect(redirect_url: str) -> dict[str, str]:
    """Parse the redirect URL to extract captcha type and session token.

    Example URL:
        /account/unhuman?type=S6E3V1&need_login=true&session=abc123
    Returns:
        {"type": "S6E3V1", "session": "abc123", "need_login": "true"}
    """
    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)
    return {
        "type": query.get("type", ["unknown"])[0],
        "session": query.get("session", [""])[0],
        "need_login": query.get("need_login", ["false"])[0],
    }


def get_captcha_info(session: Any, session_id: str) -> dict[str, Any]:
    """Fetch captcha details from the Zhihu appeal API.

    Returns a dict with:
        block_level: int - severity (0 = none, 1+ = blocked)
        redirect_url: str - more detailed redirect with human-readable message
        img_base64: str - base64-encoded captcha image (empty if no visual captcha)
    """
    url = f"{APPEAL_URL}?session={session_id}"
    resp = session.get(url)
    if resp.status_code != 200:
        return {
            "block_level": -1,
            "redirect_url": "",
            "img_base64": "",
            "error": f"Appeal API returned HTTP {resp.status_code}",
        }
    return resp.json()


def handle_captcha(
    session: Any,
    captcha_error: dict,
    *,
    interactive: bool = True,
    auto_open_browser: bool = True,
) -> str:
    """Handle a captcha/risk-control challenge.

    This is the main entry point. When a request triggers a 403 with
    captcha, call this function to guide the user through verification.

    Parameters:
        session: The ZhihuSession or curl_cffi Session instance.
        captcha_error: The error dict from detect_captcha().
        interactive: If False, raise an error instead of prompting.
        auto_open_browser: If True, try to open the browser automatically.

    Returns:
        "resolved" if the user completed verification.
        "skipped" if the user chose to skip.
        "cancelled" if the user cancelled.

    Raises:
        RuntimeError: If interactive=False or verification fails.
    """
    redirect_url = captcha_error.get("redirect", RISK_CONTROL_FALLBACK)
    params = parse_redirect(redirect_url)
    captcha_type = params["type"]
    session_id = params["session"]

    # Try to get more details
    info_msg = captcha_error.get("message", "Risk control triggered")
    block_level = 0
    try:
        info = get_captcha_info(session, session_id)
        block_level = info.get("block_level", 0)
    except Exception:
        pass

    # Build the full unhuman URL
    unhuman_url = redirect_url
    if unhuman_url.startswith("/"):
        unhuman_url = "https://www.zhihu.com" + unhuman_url

    # Display to user
    _print_captcha_banner(
        captcha_type=captcha_type,
        session_id=session_id,
        unhuman_url=unhuman_url,
        message=info_msg,
        block_level=block_level,
    )

    if not interactive:
        raise RuntimeError(
            f"Captcha required. Open this URL in a browser: {unhuman_url}\nType: {captcha_type}, Session: {session_id}"
        )

    # Try to open browser
    if auto_open_browser:
        try:
            webbrowser.open(unhuman_url)
            print("  📂 Browser opened automatically.")
            print()
        except Exception:
            print("  (Could not open browser automatically.)")
            print()

    # Prompt user
    while True:
        try:
            choice = input("  [O]pen URL in browser / [Enter] when done / [S]kip / [Q]uit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "cancelled"

        if choice == "o":
            print(f"  Opening: {unhuman_url}")
            try:
                webbrowser.open(unhuman_url)
            except Exception:
                print(f"  Please open this URL manually:\n  {unhuman_url}")
        elif choice == "s":
            return "skipped"
        elif choice == "q":
            return "cancelled"
        elif choice == "":
            # After verification, re-visit the unhuman page to harvest cookies
            try:
                session.get(unhuman_url)
            except Exception:
                pass
            return "resolved"
        else:
            print("  Invalid choice. Press Enter after completing verification.")


def _print_captcha_banner(
    captcha_type: str,
    session_id: str,
    unhuman_url: str,
    message: str,
    block_level: int,
) -> None:
    """Display a formatted banner with captcha information."""
    width = 60
    border = "=" * width

    print()
    print(border)
    print("  ⚠️  Zhihu Risk Control / Captcha Triggered")
    print(border)
    print(f"  Captcha type : {captcha_type}")
    print(f"  Session ID   : {session_id[:32]}...")
    print(f"  Block level  : {block_level}")
    if message:
        print(f"  Message      : {message}")
    print()
    print("  To resolve this, you need to complete verification in a browser:")
    print(f"  \033[34m{unhuman_url}\033[0m")
    print()
    print("  After completing the verification in your browser,")
    print("  return here and press Enter to continue.")
    print(border)
    print()

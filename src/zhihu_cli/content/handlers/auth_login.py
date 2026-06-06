"""QR code login for Zhihu. Based on login flow from zhihu-plus-plus."""

import time

from curl_cffi import requests as curl_requests

from zhihu_cli.content.handlers import get_user_agent
from zhihu_cli.content.utils.wait import wait

DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
DESKTOP_SEC_CH_UA = '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'
DESKTOP_SEC_CH_UA_MOBILE = "?0"
DESKTOP_SEC_CH_UA_PLATFORM = '"Windows"'

SIGNIN_URL = "https://www.zhihu.com/signin?next=%2F"
SIGNIN_REFERER = "https://www.zhihu.com/signin"
UDID_URL = "https://www.zhihu.com/udid"
CAPTCHA_URL = "https://www.zhihu.com/api/v3/oauth/captcha/v2?type=captcha_sign_in"
QRCODE_URL = "https://www.zhihu.com/api/v3/account/api/login/qrcode"
ME_URL = "https://www.zhihu.com/api/v4/me"
HOME_URL = "https://www.zhihu.com/"
RISK_CONTROL_FALLBACK = "https://www.zhihu.com/account/risk_control/"

RISK_CONTROL_EXIT = object()


def _desktop_headers(referer: str | None = None) -> dict[str, str]:
    ua = get_user_agent() or DESKTOP_USER_AGENT
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": ua,
        "sec-ch-ua": DESKTOP_SEC_CH_UA,
        "sec-ch-ua-mobile": DESKTOP_SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": DESKTOP_SEC_CH_UA_PLATFORM,
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _login_headers(session: curl_requests.Session, referer: str, *, polling: bool = False) -> dict[str, str]:
    headers = _desktop_headers(referer)
    headers["Origin"] = HOME_URL.rstrip("/")
    headers["x-requested-with"] = "fetch"
    headers["content-type"] = "application/json;charset=UTF-8"
    if polling:
        headers["Accept"] = "*/*"
        headers["sec-fetch-dest"] = "empty"
        headers["sec-fetch-mode"] = "cors"
        headers["sec-fetch-site"] = "same-origin"
        headers["x-zse-93"] = "101_3_3.0"
    xsrf = _cookie_value(session, "_xsrf")
    if xsrf:
        headers["x-xsrftoken"] = xsrf
    return headers


def _cookie_value(session: curl_requests.Session, name: str) -> str | None:
    return session.cookies.get(name)


def _cookies_to_header(session: curl_requests.Session) -> str:
    parts = [f"{k}={v}" for k, v in session.cookies.get_dict().items() if k and v]
    return "; ".join(parts)


def _print_qr(url: str) -> None:
    print("Scan the QR code with the Zhihu App:\n")
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make()
    qr.print_ascii(invert=True)
    print(f"\nOr open this link in browser: {url}\n")


def _detect_risk_control(data: dict) -> str | None:
    """If ``data`` contains a risk-control error, return the redirect URL."""
    error = data.get("error", {})
    if isinstance(error, dict):
        if error.get("code") == 40352 or error.get("need_login"):
            return error.get("redirect") or RISK_CONTROL_FALLBACK
    return None


def _handle_risk_control(session: curl_requests.Session, redirect_url: str) -> None:
    """Prompt the user to complete browser verification, then refresh session cookies."""
    print()
    print("=" * 60)
    print("  ⚠️  Zhihu needs to verify your network environment.")
    print()
    print("  Open this URL in a \033[1mbrowser\033[0m:")
    print(f"  \033[34m{redirect_url}\033[0m")
    print()
    print("  After completing the verification, press Enter to continue...")
    print("=" * 60)

    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise

    # After verification, re-visit risk_control page with the session to pick
    # up any new cookies that the verification set.
    try:
        session.get(redirect_url, headers=_login_headers(session, SIGNIN_REFERER))
    except Exception:
        pass


def _is_login_success(scan_info: dict) -> bool:
    """Return True when scan_info indicates the user successfully logged in."""
    if scan_info.get("user_id"):
        return True
    if scan_info.get("access_token"):
        return True
    if scan_info.get("success"):
        return True
    login_status = str(scan_info.get("loginStatus", "")).upper()
    if login_status in ("CONFIRMED", "LOGIN_SUCCESS", "SUCCESS", "OK", "LOGGED_IN"):
        return True
    return False


def qr_login() -> dict[str, str]:
    """Execute QR code login flow. Returns headers dict suitable for cache_manager.save_headers()."""

    session = curl_requests.Session(impersonate="chrome")

    # Step 1: visit signin page to seed initial cookies (d_c0, _xsrf)
    session.get(SIGNIN_URL, headers=_desktop_headers(SIGNIN_REFERER))

    # Step 2: register device UDID
    try:
        session.post(UDID_URL, json={}, headers=_login_headers(session, SIGNIN_REFERER))
    except Exception:
        pass

    # Step 3: fetch captcha context
    try:
        session.get(CAPTCHA_URL, headers=_login_headers(session, SIGNIN_REFERER))
    except Exception:
        pass

    # Step 4: request QR code
    resp = session.post(QRCODE_URL, json={}, headers=_login_headers(session, SIGNIN_REFERER))
    data = resp.json()

    # Check for risk control before QR code is issued
    risk_url = _detect_risk_control(data)
    if risk_url:
        _handle_risk_control(session, risk_url)
        resp = session.post(QRCODE_URL, json={}, headers=_login_headers(session, SIGNIN_REFERER))
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to request QR code after verification: HTTP {resp.status_code}")
        data = resp.json()

    if resp.status_code != 200:
        raise RuntimeError(f"Failed to request QR code: HTTP {resp.status_code}")

    token = data.get("token") or data.get("qrcode_token")
    link = data.get("link")
    if not token or not link:
        raise RuntimeError(f"QR code response missing token/link: {data}")

    # Step 5: display QR code
    _print_qr(link)

    # Step 6: poll scan_info
    expires_at = data.get("expires_at", 0)
    if 0 < expires_at < 10_000_000_000:
        expires_at *= 1000
    if not expires_at or expires_at <= 0:
        expires_at = int(time.time() * 1000) + 120_000
    deadline = expires_at / 1000.0

    print("Waiting for scan...")
    scanned_reported = False
    risk_control_count = 0

    while time.time() < deadline:
        try:
            resp = session.get(
                f"{QRCODE_URL}/{token}/scan_info",
                headers=_login_headers(session, SIGNIN_URL, polling=True),
            )

            scan_info = resp.json() if resp.text else {}

            risk_url = _detect_risk_control(scan_info)
            if risk_url:
                risk_control_count += 1
                if risk_control_count <= 3:
                    _handle_risk_control(session, risk_url)
                    # Re-request a fresh QR code after verification
                    resp = session.post(QRCODE_URL, json={}, headers=_login_headers(session, SIGNIN_REFERER))
                    data = resp.json()
                    token = data.get("token") or data.get("qrcode_token")
                    link = data.get("link")
                    if token and link:
                        _print_qr(link)
                        scanned_reported = False
                        expires_at_raw = data.get("expires_at", 0)
                        if 0 < expires_at_raw < 10_000_000_000:
                            expires_at_raw *= 1000
                        deadline = (expires_at_raw / 1000.0) if expires_at_raw else (time.time() + 120)
                        print("Waiting for scan...")
                    continue
                else:
                    raise RuntimeError(
                        "Too many risk-control challenges. Zhihu is throttling this network. "
                        "Try again later or use 'zhihu auth paste' instead."
                    )

            zc0 = scan_info.get("zC0") or scan_info.get("z_c0")
            if zc0 and not _cookie_value(session, "z_c0"):
                session.cookies.set("z_c0", zc0, domain=".zhihu.com")

            if scan_info.get("status") == 1 and not scanned_reported:
                print("Scanned! Please confirm login in the Zhihu App...")
                scanned_reported = True

            if _is_login_success(scan_info):
                if not _cookie_value(session, "z_c0"):
                    try:
                        session.get(ME_URL, headers=_login_headers(session, SIGNIN_URL, polling=True))
                    except Exception:
                        pass
                if _cookie_value(session, "z_c0") or scan_info.get("userId"):
                    break

        except KeyboardInterrupt:
            print("\nCancelled.")
            return {}
        except Exception:
            pass

        wait(1.0)

    if not _cookie_value(session, "z_c0"):
        raise RuntimeError("QR code expired or login was not completed. Please try again.")

    # Step 7: verify login and get username
    resp = session.get(ME_URL)
    if resp.status_code == 200:
        me = resp.json()
        username = me.get("name", "unknown")
        print(f"Login successful! Welcome, {username}.")

        headers = {
            "Cookie": _cookies_to_header(session),
            "User-Agent": get_user_agent() or DESKTOP_USER_AGENT,
        }
        return headers

    raise RuntimeError(f"Login verification failed: HTTP {resp.status_code}")

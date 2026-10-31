"""Auth command group — manage authentication (cURL headers / QR login / captcha)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha, get_captcha_info, handle_captcha, parse_redirect
from zhihu_cli.content.handlers.requests import reload_session, session
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_green,
    f_label,
    f_name,
    f_num,
    info,
    print_json,
    success,
    warning,
)

#: A cURL header flag: ``-H '...'`` / ``--header '...'``, quoted or bare.
_CURL_HEADER_RE = re.compile(r"(?:^|\s)(?:-H|--header)(?:[=\s]+)(?P<value>'[^']*'|\"[^\"]*\"|\S+)")

#: Characters allowed in an HTTP field name (RFC 9110 ``token``).
_FIELD_NAME_RE = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")

#: Header names without a ``-`` that must be read as headers, not as cookies.
_KNOWN_HEADER_NAMES = frozenset(
    {
        "accept",
        "accept-charset",
        "accept-encoding",
        "accept-language",
        "authorization",
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "dnt",
        "expect",
        "host",
        "origin",
        "pragma",
        "referer",
        "te",
        "upgrade-insecure-requests",
        "user-agent",
        "via",
        "x-api-version",
        "x-app-version",
        "x-app-za",
        "x-requested-with",
        "x-zse-83",
        "x-zse-93",
        "x-zse-96",
    }
)

#: cURL emits these itself; storing them would pin stale values.
_SKIP_HEADER_NAMES = frozenset({"accept-encoding", "content-length"})

#: Headers whose spelling the rest of the codebase reads case-sensitively.
_CANONICAL_HEADER_NAMES = {"cookie": "Cookie", "user-agent": "User-Agent"}


def _unquote_curl_value(value: str) -> str:
    """Strip the matching quotes cURL wraps a header value in.

    :param value: A raw ``-H`` value as captured from the pasted command.
    :returns: The value without surrounding single or double quotes.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _split_auth_line(raw: str) -> tuple[str, str] | None:
    """Split one pasted line into a ``(name, value)`` pair.

    Accepts both ``Key: Value`` and ``Key=Value``.  cURL flags, the command's
    own URL, and anything that is not a well-formed field name are rejected.

    :param raw: A cURL ``-H`` value or one line of pasted text.
    :returns: ``(name, value)``, or ``None`` when the line is not a pair.
    """
    raw = raw.strip()
    if not raw or raw.startswith("-"):
        # Blank lines and cURL flags such as ``--compressed`` or ``-X POST``.
        return None
    if re.match(r"(?i)^\$?\s*(?:curl\b|https?://|www\.)", raw):
        # The cURL command itself and bare URLs are not headers.
        return None

    cuts = [pos for pos in (raw.find(":"), raw.find("=")) if pos > 0]
    if not cuts:
        return None

    cut = min(cuts)
    name = raw[:cut].strip()
    value = raw[cut + 1 :].strip()
    if not value or not _FIELD_NAME_RE.fullmatch(name):
        return None
    return name, value


def _is_header_name(name: str) -> bool:
    """Tell a request-header name from a bare cookie name.

    :param name: The part before the first ``:`` or ``=``.
    :returns: ``True`` for hyphens (``x-zse-96``) and well-known headers
        (``Cookie``); ``False`` for cookie names such as ``z_c0``.
    """
    return "-" in name or name.lower() in _KNOWN_HEADER_NAMES


def _cookie_segments(raw: str) -> list[str]:
    """Split a cookie string into ``name=value`` segments.

    ``;`` and ``&`` are both accepted as separators, and quotes/padding are
    dropped, so a string copied from DevTools, a URL query, or several lines
    normalizes to the ``"; "`` form Zhihu expects.

    :param raw: A cookie string or ``name=value`` fragment.
    :returns: The non-empty segments.
    """
    return [segment.strip() for segment in re.split(r"[;&]", raw) if segment.strip()]


def _parse_pasted_headers(text: str) -> dict[str, str]:
    """Parse pasted credentials into a header dictionary.

    Understands the three shapes ``zhihu auth paste`` accepts:

    * a cURL command with ``-H 'Key: Value'`` / ``-H 'Key=Value'``;
    * ``Key: Value`` or ``Key=Value`` lines, one header per line;
    * a bare cookie string (``z_c0=...; _xsrf=...``), on one line or across
      several lines, which becomes the ``Cookie`` header.

    Keys that are not header-shaped are folded into ``Cookie`` rather than
    stored as headers of their own.

    :param text: The text pasted on stdin.
    :returns: Header name -> value; empty when nothing could be parsed.
    """
    curl_values = list(_CURL_HEADER_RE.finditer(text))
    lines = [_unquote_curl_value(match.group("value")) for match in curl_values] if curl_values else text.splitlines()

    headers: dict[str, str] = {}
    cookies: list[str] = []
    for line in lines:
        pair = _split_auth_line(line)
        if pair is None:
            continue
        name, value = pair
        lowered = name.lower()
        if lowered in _SKIP_HEADER_NAMES:
            continue
        if lowered == "cookie":
            cookies.extend(_cookie_segments(value))
        elif _is_header_name(name):
            headers[_CANONICAL_HEADER_NAMES.get(lowered, name)] = value
        else:
            cookies.extend(_cookie_segments(f"{name}={value}"))

    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    return headers


def register_auth(main_group) -> None:
    """Register the auth command group on a Click group."""

    @main_group.group()
    def auth() -> None:
        """Manage authentication (cURL headers)."""

    @auth.command("paste")
    @click.option("--profile", "-p", "profile_name", default=None, help="Save to a named profile")
    def auth_paste(profile_name: str | None) -> None:
        """Paste credentials to cache headers.

        Accepts any of these, one per line or all at once:

        \033[2m1. A cURL command copied from browser DevTools\033[0m
        \033[2m   (F12 → Network → right-click → Copy → Copy as cURL)\033[0m
        \033[2m2. Key: Value or Key=Value header lines\033[0m
        \033[2m3. A bare cookie string, e.g. z_c0=...; _xsrf=...\033[0m

        Press Ctrl+D when done. Use --profile to save to a named profile
        (e.g. work, personal).
        """
        if profile_name and profile_name.startswith("_"):
            error("Profile names starting with '_' are reserved for internal use.")
            raise SystemExit(1)

        echo("Paste cURL, 'Key: Value' / 'Key=Value' lines, or a cookie string (Ctrl+D to finish):")
        try:
            pasted = sys.stdin.read()
        except EOFError:
            pasted = ""

        if not pasted.strip():
            error("No input detected.")
            raise SystemExit(1)

        headers = _parse_pasted_headers(pasted)
        if not headers:
            error(
                "Could not parse any headers. Paste a cURL command, 'Key: Value' / 'Key=Value' lines, "
                "or a cookie string."
            )
            raise SystemExit(1)

        cache_manager.save_headers(headers, profile_name=profile_name)
        active = cache_manager.get_active_profile() or "default"
        success(f"Saved {len(headers)} headers to profile '{active}'")

    @auth.command("login")
    @click.option("--profile", "-p", "profile_name", default=None, help="Save to a named profile")
    def auth_login(profile_name: str | None) -> None:
        """Login to Zhihu via QR code. Scan with the Zhihu App to authenticate.

        Displays a QR code in the terminal. Open the Zhihu App, go to
        \033[2mMy → Settings → Scan\033[0m, and scan the code to log in.

        This generates fresh cookies and saves them as a profile, so you
        don't need to manually paste cURL headers.
        """
        if profile_name and profile_name.startswith("_"):
            error("Profile names starting with '_' are reserved for internal use.")
            raise SystemExit(1)

        from zhihu_cli.content.handlers.auth_login import qr_login

        info("Starting QR code login...")
        try:
            headers = qr_login()
        except RuntimeError as e:
            error(f"{e}")
            raise SystemExit(1)

        if not headers:
            error("Login failed.")
            raise SystemExit(1)

        cache_manager.save_headers(headers, profile_name=profile_name)
        active = cache_manager.get_active_profile() or "default"
        success(f"Saved credentials to profile '{active}'")
        reload_session()

    @auth.command("import")
    @click.option(
        "--browser", "-b", "browser_name", default=None, help="Browser to read cookies from (default: auto-detect)"
    )
    @click.option("--profile", "-p", "profile_name", default=None, help="Save to a named profile")
    @click.option("--no-verify", is_flag=True, default=False, help="Skip the login check after importing")
    def auth_import(browser_name: str | None, profile_name: str | None, no_verify: bool) -> None:
        """Import the Zhihu session from a local browser's cookie store.

        Reads the cookies straight out of a browser you are already logged in
        to — no DevTools copy-paste like \033[2mauth paste\033[0m and no QR scan like
        \033[2mauth login\033[0m. Log in to Zhihu once in the browser, then run this.

        Requires the optional extra: \033[2mpip install 'zhihu-toolkit[browser]'\033[0m

        \033[2mExamples:\033[0m
          zhihu auth import                      # auto-detect the browser
          zhihu auth import -b firefox           # read Firefox specifically
          zhihu auth import -p work -b chrome    # save as a named profile
        """
        if profile_name:
            if profile_name.startswith("_"):
                error("Profile names starting with '_' are reserved for internal use.")
                raise SystemExit(1)
            if Path(profile_name).name != profile_name:
                error("Profile names must be a plain name (no path separators).")
                raise SystemExit(1)

        from zhihu_cli.content.handlers.browser_cookies import collect_headers

        # Snapshot the current state so a failed login check can be rolled back.
        # The legacy headers.json counts as pre-existing state: save_headers()
        # migrates it into a profile, so the migrated profile is not ours to delete.
        previous_headers = cache_manager.load_headers()
        previous_active = cache_manager.get_active_profile()
        pre_existing_profiles = set(cache_manager.list_profiles())
        legacy_headers_existed = cache_manager.header_file.exists()

        # When the import targets a named profile that already exists, remember what
        # it held — a failed import must not cost the user those credentials.
        previous_target: dict[str, str] | None = None
        if profile_name and profile_name in pre_existing_profiles:
            target_file = cache_manager.profiles_dir / f"{profile_name}.json"
            if target_file.exists():
                previous_target = json.loads(target_file.read_text())

        try:
            browser, headers = collect_headers(browser_name)
        except RuntimeError as e:
            error(f"{e}")
            raise SystemExit(1)

        cache_manager.save_headers(headers, profile_name=profile_name)
        reload_session()

        saved = cache_manager.get_active_profile() or "default"
        cookie_header = headers["Cookie"]
        count = len(cookie_header.split("; ")) if cookie_header else 0
        noun = "cookie" if count == 1 else "cookies"

        if no_verify:
            success(f"Imported {count} {noun} from {browser} into profile '{saved}' (not verified).")
            return

        # Verify the imported session actually works before keeping it.
        username = None
        verified = False
        resp = session.get("https://www.zhihu.com/api/v4/me", timeout=10)
        if resp.status_code == 200:
            me = resp.json()
            username = me.get("name")
            verified = True

        if verified:
            as_user = f" as {f_name(username)}" if username else ""
            success(f"Imported {count} {noun} from {browser} into profile '{saved}'{as_user}.")
            return

        # Roll back to the pre-import state. Whatever credentials were in use before
        # always come back; a profile this command created is removed.
        restored_target = previous_active or saved
        migrated = legacy_headers_existed and previous_active is None and profile_name is None
        created = saved not in pre_existing_profiles and not migrated
        notes: list[str] = []

        try:
            if profile_name:
                # The import landed in the named profile, replacing whatever it held.
                if previous_target is not None:
                    cache_manager.save_headers(previous_target, profile_name=profile_name)
                    notes.append(f"profile '{profile_name}' was restored")
                elif created:
                    cache_manager.delete_profile(profile_name)
                    notes.append(f"the new profile '{profile_name}' was removed")
                if previous_active and previous_active != profile_name:
                    cache_manager.switch_profile(previous_active)
                    notes.append(f"profile '{previous_active}' is active again")
            elif previous_active or legacy_headers_existed:
                cache_manager.save_headers(previous_headers or {}, profile_name=restored_target)
                notes.append("the previous credentials were restored")
            elif created:
                cache_manager.delete_profile(saved)
                notes.append("the imported credentials were discarded")
            else:
                notes.append(f"profile '{saved}' was left holding the imported credentials")
        except Exception as exc:
            notes.append(f"rollback failed: {exc}")

        try:
            reload_session()
        except Exception as exc:
            notes.append(f"session reload failed: {exc}")

        error(
            f"The Zhihu session in {browser} is stale or not logged in "
            f"({'; '.join(notes)}). Log in to Zhihu in {browser} and retry, or run 'zhihu auth login'."
        )
        raise SystemExit(1)

    @auth.command("status")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def auth_status(output_json: bool) -> None:
        """Show authentication status and active profile."""
        active = cache_manager.get_active_profile()
        profiles = [p for p in cache_manager.list_profiles() if not p.startswith("_")]
        headers = cache_manager.load_headers()
        has_cookie = "cookie" in {k.lower() for k in headers} if headers else False

        # Fetch authenticated user info from /api/v4/me
        username = None
        uid = None
        user_id = None
        url_token = None
        if has_cookie:
            try:
                resp = session.get("https://www.zhihu.com/api/v4/me", timeout=10)
                if resp.status_code == 200:
                    me = resp.json()
                    username = me.get("name")
                    user_id = me.get("id")
                    url_token = me.get("url_token")
                    uid = me.get("uid")
            except Exception:
                pass  # Silently ignore — user info is a best-effort bonus

        if output_json:
            print_json(
                {
                    "active_profile": active,
                    "profiles": profiles,
                    "headers_count": len(headers),
                    "has_cookie": has_cookie,
                    "username": username,
                    "user_id": user_id,
                    "url_token": url_token,
                    "uid": uid,
                }
            )
            return

        if active:
            echo(f"  {f_label('Active profile:')} {f_bold(active)}")
        else:
            info("No active profile set.")

        if profiles:
            echo(f"  {f_label('Saved profiles:')} {', '.join(profiles)}")

        if headers:
            echo(f"  {f_label('Headers:')} {f_num(len(headers))} cached")
            if has_cookie:
                echo(f"  {f_label('Cookie:')} {f_green('present')}")
                if username:
                    echo(f"  {f_label('Username:')} {f_name(username)}")
                    if user_id:
                        echo(f"  {f_label('User ID:')} {f_num(user_id)}")
                    if url_token:
                        echo(f"  {f_label('URL token:')} {url_token}")
                    if uid:
                        echo(f"  {f_label('UID:')} {f_num(uid)}")
            else:
                warning("No Cookie header found.")
        else:
            error("No headers cached. Run 'zhihu auth paste' first.")

    @auth.command("clear")
    def auth_clear() -> None:
        """Remove cached headers."""
        cache_manager.save_headers({})
        success("Headers cache cleared.")

    _LOGOUT_PROFILE = "_logout_"

    @auth.command("logout")
    def auth_logout() -> None:
        """Log out — switch to an unauthenticated session (hidden profile).

        Switches the active profile to a hidden profile with no stored
        credentials. Use 'zhihu auth login' or 'zhihu auth paste' to log back in.
        """
        active = cache_manager.get_active_profile()
        if active == _LOGOUT_PROFILE:
            info("Already logged out.")
            return

        # Ensure the hidden logout profile exists with empty headers
        if _LOGOUT_PROFILE not in cache_manager.list_profiles():
            cache_manager.save_headers({}, profile_name=_LOGOUT_PROFILE)

        cache_manager.switch_profile(_LOGOUT_PROFILE)
        reload_session()
        echo("Logged out. Use 'zhihu auth login' or 'zhihu auth paste' to log back in.")

    @auth.command("captcha")
    @click.option("--url", "-u", "test_url", default=None, help="URL to test for captcha (default: hot list API)")
    @click.option("--open-browser/--no-browser", default=True, help="Auto-open browser for verification")
    def auth_captcha(test_url: str | None, open_browser: bool) -> None:
        """Check and resolve Zhihu risk-control / captcha challenges.

        Tests the current session against a Zhihu API endpoint. If the
        response indicates a captcha is required, displays instructions
        and optionally opens the verification page in a browser.

        \033[2mExamples:\033[0m
          zhihu auth captcha                    # test with hot list API
          zhihu auth captcha --url <api-url>    # test a specific API
          zhihu auth captcha --no-browser       # don't auto-open browser
        """
        from zhihu_cli.content.handlers.requests import session

        if test_url is None:
            test_url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=1&desktop=true"

        info(f"Testing endpoint for captcha: {test_url}")
        blank()

        # Temporarily disable automatic captcha handling — we control the flow here
        old_handler = session.captcha_handler
        session.captcha_handler = "ignore"

        def _test_request():
            """Make a test request and return the response (auto-handler disabled)."""
            return session.get(test_url)

        try:
            resp = _test_request()
        except Exception as e:
            session.captcha_handler = old_handler
            error(f"Error making request: {e}")
            raise SystemExit(1)

        captcha_error = detect_captcha(resp)
        if captcha_error is None:
            if resp.status_code == 200:
                success("No captcha detected — session is working normally.")
            else:
                echo(f"  {f_label('Status:')} {resp.status_code} (non-captcha error)")
                try:
                    body = resp.text[:500]
                    echo(f"  {f_label('Response:')} {body}")
                except Exception:
                    pass
            return

        # Captcha detected
        warning("Captcha/risk-control detected!")
        blank()

        redirect_url = captcha_error.get("redirect", "")
        params = parse_redirect(redirect_url)
        captcha_type = params.get("type", "unknown")
        session_id = params.get("session", "")

        echo(f"  {f_label('Type:')}    {captcha_type}")
        echo(f"  {f_label('Session:')} {session_id}")
        echo(f"  {f_label('Message:')} {captcha_error.get('message', 'N/A')}")
        blank()

        # Try to get more details from the appeal API
        if session_id:
            try:
                captcha_info = get_captcha_info(session, session_id)
                echo(f"  {f_label('Block level:')} {captcha_info.get('block_level', 'N/A')}")
                img = captcha_info.get("img_base64", "")
                if img:
                    echo(f"  {f_label('Captcha image:')} {len(img)} chars (base64)")
                redirect_msg = captcha_info.get("redirect_url", "")
                if redirect_msg:
                    from urllib.parse import unquote

                    echo(f"  {f_label('Detail:')} {unquote(redirect_msg)[:200]}")
            except Exception as e:
                info(f"(Could not fetch captcha details: {e})")

        blank()

        # Handle the captcha
        result = handle_captcha(
            session,
            captcha_error,
            interactive=True,
            auto_open_browser=open_browser,
        )

        if result == "resolved":
            # Verify by retesting
            blank()
            try:
                resp2 = _test_request()
                if resp2.status_code == 200:
                    success("Verification successful — session is now working.")
                else:
                    warning(f"Still getting status {resp2.status_code} after verification.")
                    captcha_error2 = detect_captcha(resp2)
                    if captcha_error2:
                        echo("   Captcha is still active. Try using a browser or 'zhihu auth login'.")
            except Exception as e:
                warning(f"Error during verification test: {e}")
        elif result == "skipped":
            echo("Skipped. You can retry later with 'zhihu auth captcha'.")
        else:
            echo("Cancelled.")

        # Restore original handler mode
        session.captcha_handler = old_handler

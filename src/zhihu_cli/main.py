"""zhihu CLI — unified entry point for all Zhihu operations."""

import json
import os
import sys
import time
from pathlib import Path

import click

from zhihu_cli.content.download_contents import ContentDownloader, sanitize_filename, save_article, save_pin
from zhihu_cli.content.handlers import get_data_dir, get_type_and_id, get_user_agent, set_user_agent
from zhihu_cli.content.handlers.agora import (
    VALID_VOTES,
    VOTE_LABELS,
    fetch_agora_me,
    fetch_comment_detail,
    fetch_court_page,
    fetch_reviews,
    vote_discussion,
)
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.captcha import detect_captcha, get_captcha_info, handle_captcha, parse_redirect
from zhihu_cli.content.handlers.chat import get_inbox, iter_chat_history, send_text_message
from zhihu_cli.content.handlers.collection import (
    add_to_collection,
    collect,
    create_collection,
    delete_collection,
    delete_to_collection,
    list_collections,
)
from zhihu_cli.content.handlers.comments import comment_item, delete_comment, fetch_comments, print_comments
from zhihu_cli.content.handlers.draft import draft_to_markdown
from zhihu_cli.content.handlers.feed import fetch_feed, fetch_feed_with_markdown
from zhihu_cli.content.handlers.following import (
    fetch_followees,
    fetch_followers,
    fetch_following_collections,
    fetch_following_columns,
    fetch_following_questions,
    fetch_following_topics,
    get_my_url_token,
)
from zhihu_cli.content.handlers.hot import fetch_hot_list
from zhihu_cli.content.handlers.people import (
    block,
    fetch_member_answers,
    fetch_member_articles,
    fetch_member_pins,
    fetch_member_profile,
    fetch_member_questions,
    follow,
    unblock,
    unfollow,
)
from zhihu_cli.content.handlers.pin import scrape_pin
from zhihu_cli.content.handlers.publishing import modify_answer, modify_article, publish_answer, publish_article
from zhihu_cli.content.handlers.question import (
    downvote_answer,
    downvote_question,
    follow_question,
    neutral_answer,
    scrape_answer_page,
    scrape_answers,
    scrape_question_data,
    thank_answer,
    unfollow_question,
    unthank_answer,
    upvote_answer,
    upvote_question,
)
from zhihu_cli.content.handlers.question_log import fetch_question_log
from zhihu_cli.content.handlers.report import fetch_report_reasons, flatten_reasons, submit_report
from zhihu_cli.content.handlers.requests import reload_session, session
from zhihu_cli.content.handlers.search import search_articles, search_questions, search_topics, search_users
from zhihu_cli.content.handlers.stats import get_item_stats
from zhihu_cli.content.handlers.yanxuan import extract_url_token, fetch_yanxuan_segments, segments_to_text
from zhihu_cli.content.universal_converter import convert_items, load_json
from zhihu_cli.extensions import discover_extensions

# ── helpers ──────────────────────────────────────────────────────────────


def _parse_item_url(url: str) -> tuple[str, str]:
    """Parse a Zhihu URL into (item_type, item_id). Raises click.BadParameter on failure."""
    item_type, item_id = get_type_and_id(url)
    if not item_type or not item_id:
        raise click.BadParameter(f"Cannot parse Zhihu URL: {url}")
    return item_type, item_id


def _resolve_answer_id(item_id: str) -> str:
    """Extract answer_id from composite 'question_id/answer_id' format."""
    if "/" in item_id:
        return item_id.split("/")[1]
    return item_id


def _save_markdown(metadata: dict, markdown: str, output_dir: str, prefix: str = "") -> str:
    """Save markdown content to output_dir. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    title = sanitize_filename(metadata.get("title", "untitled"))

    # author may be a string or a dict (e.g. from scrape_article)
    author_raw = metadata.get("author", "unknown")
    if isinstance(author_raw, dict):
        author = sanitize_filename(author_raw.get("name", "unknown"))
    else:
        author = sanitize_filename(author_raw)

    created = sanitize_filename(metadata.get("created_time", "unknown"))
    filename = f"{prefix}{title}_{author}_{created}.md"[:200]
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    return filepath


def _read_content(file: str | None) -> str:
    """Read content from a file (use '-' for stdin)."""
    if file is None or file == "-":
        return sys.stdin.read()
    return Path(file).read_text(encoding="utf-8")


# ── CLI root ─────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version="0.1.0", prog_name="zhihu")
def main() -> None:
    """zhihu — Zhihu scraping, automation, and analysis toolkit.

    Authenticate once with \033[1mzhihu auth paste\033[0m, then use any command.
    """


# ── auth ─────────────────────────────────────────────────────────────────


@main.group()
def auth() -> None:
    """Manage authentication (cURL headers)."""


@auth.command("paste")
@click.option("--profile", "-p", "profile_name", default=None, help="Save to a named profile")
def auth_paste(profile_name: str | None) -> None:
    """Paste a cURL command from browser DevTools to cache headers.

    \033[2m1. Open Zhihu in browser, F12 → Network tab\033[0m
    \033[2m2. Find any API request, right-click → Copy → Copy as cURL\033[0m
    \033[2m3. Paste here and press Ctrl+D\033[0m

    Use --profile to save to a named profile (e.g. work, personal).
    """
    if profile_name and profile_name.startswith("_"):
        click.echo("Profile names starting with '_' are reserved for internal use.", err=True)
        raise SystemExit(1)

    print("Paste cURL command (Ctrl+D to finish):")
    try:
        curl_text = sys.stdin.read()
    except EOFError:
        curl_text = ""

    if not curl_text.strip():
        click.echo("Error: no input detected.", err=True)
        raise SystemExit(1)

    import re

    headers: dict[str, str] = {}
    for h in re.findall(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", curl_text):
        if ":" in h:
            k, v = h.split(":", 1)
            if k.strip().lower() not in ("accept-encoding", "content-length"):
                headers[k.strip()] = v.strip()

    if not headers:
        click.echo("Error: could not parse any headers from the input.", err=True)
        raise SystemExit(1)

    cache_manager.save_headers(headers, profile_name=profile_name)
    active = cache_manager.get_active_profile() or "default"
    click.echo(f"Saved {len(headers)} headers to profile '{active}'")


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
        click.echo("Profile names starting with '_' are reserved for internal use.", err=True)
        raise SystemExit(1)

    from zhihu_cli.content.handlers.auth_login import qr_login

    click.echo("Starting QR code login...")
    try:
        headers = qr_login()
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if not headers:
        click.echo("Login failed.", err=True)
        raise SystemExit(1)

    cache_manager.save_headers(headers, profile_name=profile_name)
    active = cache_manager.get_active_profile() or "default"
    click.echo(f"Saved credentials to profile '{active}'")
    reload_session()


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
        except Exception:
            pass  # Silently ignore — user info is a best-effort bonus

    if output_json:
        click.echo(
            json.dumps(
                {
                    "active_profile": active,
                    "profiles": profiles,
                    "headers_count": len(headers),
                    "has_cookie": has_cookie,
                    "username": username,
                    "user_id": user_id,
                    "url_token": url_token,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if active:
        click.echo(f"Active profile: {active}")
    else:
        click.echo("No active profile set.")

    if profiles:
        click.echo(f"Saved profiles: {', '.join(profiles)}")

    if headers:
        click.echo(f"Headers: {len(headers)} cached")
        if has_cookie:
            click.echo("Cookie: present")
            if username:
                click.echo(f"Username: {username}")
                if user_id:
                    click.echo(f"User ID: {user_id}")
                if url_token:
                    click.echo(f"URL token: {url_token}")
        else:
            click.echo("Warning: no Cookie header found.", err=True)
    else:
        click.echo("No headers cached. Run 'zhihu auth paste' first.", err=True)


@auth.command("clear")
def auth_clear() -> None:
    """Remove cached headers."""
    cache_manager.save_headers({})
    click.echo("Headers cache cleared.")


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

    click.echo(f"Testing endpoint for captcha: {test_url}")
    click.echo()

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
        click.echo(f"Error making request: {e}", err=True)
        raise SystemExit(1)

    captcha_error = detect_captcha(resp)
    if captcha_error is None:
        if resp.status_code == 200:
            click.echo("✅ No captcha detected — session is working normally.")
        else:
            click.echo(f"Status: {resp.status_code} (non-captcha error)")
            try:
                body = resp.text[:500]
                click.echo(f"Response: {body}")
            except Exception:
                pass
        return

    # Captcha detected
    click.echo("⚠️  Captcha/risk-control detected!")
    click.echo()

    redirect_url = captcha_error.get("redirect", "")
    params = parse_redirect(redirect_url)
    captcha_type = params.get("type", "unknown")
    session_id = params.get("session", "")

    click.echo(f"  Type:    {captcha_type}")
    click.echo(f"  Session: {session_id}")
    click.echo(f"  Message: {captcha_error.get('message', 'N/A')}")
    click.echo()

    # Try to get more details from the appeal API
    if session_id:
        try:
            info = get_captcha_info(session, session_id)
            click.echo(f"  Block level: {info.get('block_level', 'N/A')}")
            img = info.get("img_base64", "")
            if img:
                click.echo(f"  Captcha image: {len(img)} chars (base64)")
            redirect_msg = info.get("redirect_url", "")
            if redirect_msg:
                from urllib.parse import unquote

                click.echo(f"  Detail: {unquote(redirect_msg)[:200]}")
        except Exception as e:
            click.echo(f"  (Could not fetch captcha details: {e})")

    click.echo()

    # Handle the captcha
    result = handle_captcha(
        session,
        captcha_error,
        interactive=True,
        auto_open_browser=open_browser,
    )

    if result == "resolved":
        # Verify by retesting
        click.echo()
        try:
            resp2 = _test_request()
            if resp2.status_code == 200:
                click.echo("✅ Verification successful — session is now working.")
            else:
                click.echo(f"⚠️  Still getting status {resp2.status_code} after verification.")
                captcha_error2 = detect_captcha(resp2)
                if captcha_error2:
                    click.echo("   Captcha is still active. Try using a browser or 'zhihu auth login'.")
        except Exception as e:
            click.echo(f"⚠️  Error during verification test: {e}")
    elif result == "skipped":
        click.echo("Skipped. You can retry later with 'zhihu auth captcha'.")
    else:
        click.echo("Cancelled.")

    # Restore original handler mode
    session.captcha_handler = old_handler


# ── config ────────────────────────────────────────────────────────────────


@main.group()
def config() -> None:
    """Manage zhihu-cli configuration."""


@config.group("user-agent")
def config_user_agent() -> None:
    """Manage the custom User-Agent override."""


@config_user_agent.command("set")
@click.argument("user_agent")
def config_ua_set(user_agent: str) -> None:
    """Set a custom User-Agent for all requests.

    \033[2mExample:\033[0m
      zhihu config user-agent set "Mozilla/5.0 ... Chrome/145.0.0.0 Safari/537.36"
    """
    set_user_agent(user_agent)
    from zhihu_cli.content.handlers.requests import reload_session

    reload_session()
    click.echo(f"User-Agent set to:\n{user_agent}")


@config_user_agent.command("show")
def config_ua_show() -> None:
    """Show the currently configured User-Agent."""
    ua = get_user_agent()
    if ua:
        click.echo(f"Configured User-Agent:\n{ua}")
    else:
        click.echo("No custom User-Agent configured (using per-profile default).")


@config_user_agent.command("clear")
def config_ua_clear() -> None:
    """Remove the custom User-Agent override."""
    set_user_agent(None)
    from zhihu_cli.content.handlers.requests import reload_session

    reload_session()
    click.echo("Custom User-Agent cleared (now using per-profile default).")


# ── config start-date ─────────────────────────────────────────────────────


@config.group("start-date")
def config_start_date() -> None:
    """Manage the default start date for data fetching."""


@config_start_date.command("set")
@click.argument("date_str")
def config_sd_set(date_str: str) -> None:
    """Set the default start date for creator-tools data fetching.

    DATE_STR must be in YYYY-MM-DD format.

    \033[2mExample:\033[0m
      zhihu config start-date set 2024-01-01
    """
    cache_manager.set_start_date(date_str)
    click.echo(f"Default start date set to: {date_str}")


@config_start_date.command("show")
def config_sd_show() -> None:
    """Show the currently configured default start date."""
    date_str = cache_manager.get_start_date()
    click.echo(f"Default start date: {date_str}")


@config_start_date.command("clear")
def config_sd_clear() -> None:
    """Reset the default start date to the built-in default (2026-01-16)."""
    cache_manager.set_start_date("2026-01-16")
    click.echo("Default start date reset to: 2026-01-16")


# ── config crank-llm ──────────────────────────────────────────────────────


@config.group("crank-llm")
def config_crank_llm() -> None:
    """Manage the cached LLM configuration for the crank extension."""


@config_crank_llm.command("set")
@click.option("--api-base", required=True, help="LLM API endpoint URL.")
@click.option("--api-key", required=True, help="API key for authentication.")
@click.option("--model", required=True, help="Model name to use.")
def config_llm_set(api_base: str, api_key: str, model: str) -> None:
    """Cache LLM credentials for the crank archiver.

    \033[2mExample:\033[0m
      zhihu config crank-llm set --api-base https://api.openai.com/v1 --api-key sk-xxx --model gpt-4
    """
    try:
        from zhihu_cli.extensions.crank.archiver import save_llm_config
    except ImportError:
        click.echo("Error: crank extension is not available (missing dependencies).", err=True)
        raise SystemExit(1)

    save_llm_config(api_base, api_key, model)
    click.echo(f"LLM config saved:\n  api_base: {api_base}\n  model: {model}")


@config_crank_llm.command("show")
def config_llm_show() -> None:
    """Show the currently cached LLM configuration."""
    try:
        from zhihu_cli.extensions.crank.archiver import load_llm_config
    except ImportError:
        click.echo("Error: crank extension is not available (missing dependencies).", err=True)
        raise SystemExit(1)

    cfg = load_llm_config()
    if cfg:
        click.echo("Cached LLM config:")
        for k, v in cfg.items():
            if k == "api_key" and v:
                v = v[:8] + "..." if len(v) > 8 else v
            click.echo(f"  {k}: {v}")
    else:
        click.echo("No cached LLM config found.")


@config_crank_llm.command("clear")
def config_llm_clear() -> None:
    """Remove the cached LLM configuration."""
    try:
        from zhihu_cli.extensions.crank.archiver import LLM_CONFIG_PATH
    except ImportError:
        click.echo("Error: crank extension is not available (missing dependencies).", err=True)
        raise SystemExit(1)

    if os.path.exists(LLM_CONFIG_PATH):
        os.remove(LLM_CONFIG_PATH)
        click.echo("Cached LLM config removed.")
    else:
        click.echo("No cached LLM config to remove.")


# ── profile ──────────────────────────────────────────────────────────────


@main.group()
def profile() -> None:
    """Manage account profiles — save and switch between multiple logins."""


@profile.command("list")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def profile_list(output_json: bool) -> None:
    """List all saved profiles."""
    active = cache_manager.get_active_profile()
    profiles = [p for p in cache_manager.list_profiles() if not p.startswith("_")]
    if not profiles:
        if output_json:
            click.echo(json.dumps([], ensure_ascii=False, indent=2))
        else:
            click.echo("No profiles found. Use 'zhihu auth paste --profile <name>' to create one.")
        return
    if output_json:
        result = []
        for name in profiles:
            path = cache_manager._resolve_profile_path(name)
            try:
                data = json.loads(path.read_text())
                has_cookie = "cookie" in {k.lower() for k in data}
            except (json.JSONDecodeError, OSError):
                has_cookie = False
            result.append({"name": name, "active": name == active, "has_cookie": has_cookie})
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for name in profiles:
        marker = " *" if name == active else ""
        path = cache_manager._resolve_profile_path(name)
        try:
            data = json.loads(path.read_text())
            cookie = "cookie" in {k.lower() for k in data}
        except (json.JSONDecodeError, OSError):
            cookie = False
        status = "cookie" if cookie else "no cookie"
        click.echo(f"  {name}{marker}  ({status})")


@profile.command("switch")
@click.argument("name")
def profile_switch(name: str) -> None:
    """Switch to a different profile."""
    try:
        cache_manager.switch_profile(name)
        reload_session()
        click.echo(f"Switched to profile '{name}'.")
    except ValueError:
        click.echo(f"Profile '{name}' does not exist. Use 'zhihu profile list' to see saved profiles.", err=True)
        raise SystemExit(1)


@profile.command("delete")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation")
def profile_delete(name: str, force: bool) -> None:
    """Delete a saved profile."""
    profiles = cache_manager.list_profiles()
    if name not in profiles:
        click.echo(f"Profile '{name}' does not exist.", err=True)
        raise SystemExit(1)
    if name.startswith("_"):
        click.echo(f"Cannot delete internal profile '{name}'.", err=True)
        raise SystemExit(1)

    if not force:
        click.confirm(f"Delete profile '{name}'?", abort=True)

    cache_manager.delete_profile(name)
    click.echo(f"Deleted profile '{name}'.")


@profile.command("current")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def profile_current(output_json: bool) -> None:
    """Show the currently active profile."""
    active = cache_manager.get_active_profile()
    if output_json:
        click.echo(json.dumps({"active_profile": active}, ensure_ascii=False, indent=2))
        return
    if active:
        click.echo(active)
    else:
        click.echo("No active profile set.", err=True)


_LOGOUT_PROFILE = "_logout_"


@profile.command("logout")
def profile_logout() -> None:
    """Switch to an unauthenticated session (hidden profile).

    Switches the active profile to a hidden profile with no stored
    credentials. Use 'zhihu profile switch <name>' to log back in.
    """
    active = cache_manager.get_active_profile()
    if active == _LOGOUT_PROFILE:
        click.echo("Already logged out.")
        return

    # Ensure the hidden logout profile exists with empty headers
    if _LOGOUT_PROFILE not in cache_manager.list_profiles():
        cache_manager.save_headers({}, profile_name=_LOGOUT_PROFILE)

    cache_manager.switch_profile(_LOGOUT_PROFILE)
    reload_session()
    click.echo("Logged out. Use 'zhihu profile switch <name>' to log back in.")


# ── download ─────────────────────────────────────────────────────────────


@main.group()
def download() -> None:
    """Download Zhihu content as Markdown files."""


@download.command("article")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "articles"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_article(url: str, output_dir: str, output_json: bool) -> None:
    """Download a single Zhihu article as Markdown."""
    metadata, markdown = scrape_article(url)
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        click.echo(json.dumps({"metadata": metadata, "filepath": filepath}, ensure_ascii=False, indent=2))
        return
    click.echo(f"{metadata.get('title', 'untitled')}")
    click.echo(f"  -> {filepath}")


@download.command("question")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "questions"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_question(url: str, output_dir: str, output_json: bool) -> None:
    """Download a Zhihu question and all its answers as Markdown."""
    q_meta, q_detail_md = scrape_question_data(url)
    os.makedirs(output_dir, exist_ok=True)

    title = sanitize_filename(q_meta.get("title", "untitled"))
    filepath = os.path.join(output_dir, f"{title}_question.md")[:200]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {q_meta['title']}\n\n{q_detail_md}\n")

    ans_dir = os.path.join(output_dir, f"{title}_answers")
    os.makedirs(ans_dir, exist_ok=True)
    count = 0
    for ans in scrape_answers(q_meta):
        count += 1
        afile = os.path.join(ans_dir, f"{count:04d}_{sanitize_filename(ans['author'])}.md")[:200]
        with open(afile, "w", encoding="utf-8") as f:
            f.write(f"# Answer by {ans['author']} (+{ans['vote']})\n\n{ans['content']}\n")

    if output_json:
        click.echo(
            json.dumps(
                {"metadata": q_meta, "filepath": filepath, "answers_count": count, "answers_dir": ans_dir},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    click.echo(f"Question: {q_meta['title']}")
    click.echo(f"  -> {filepath}")
    click.echo(f"  {count} answers saved to {ans_dir}")


@download.command("pin")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "pins"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_pin(url: str, output_dir: str, output_json: bool) -> None:
    """Download a single Zhihu pin as Markdown."""
    metadata, markdown = scrape_pin(url)
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        click.echo(json.dumps({"metadata": metadata, "filepath": filepath}, ensure_ascii=False, indent=2))
        return
    click.echo(f"Pin by {metadata.get('author', 'unknown')}")
    click.echo(f"  -> {filepath}")


@download.command("batch-answers")
@click.option(
    "--input",
    "-i",
    "input_file",
    default=str(get_data_dir() / "exports" / "all_assets_list.json"),
    help="Assets JSON file",
)
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "answers"), help="Output directory")
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests (seconds)")
@click.option("--no-cache-headers", is_flag=True, help="Force re-paste of cURL")
def download_batch_answers(input_file: str, output_dir: str, delay: float, no_cache_headers: bool) -> None:
    """Batch download all answers listed in an assets JSON file."""
    if not os.path.exists(input_file):
        click.echo(f"Error: file not found: {input_file}", err=True)
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://www.zhihu.com/answer/{a['id']}" for a in assets if a.get("type") == "answer"]
    if not urls:
        click.echo("No answers found in the assets file.")
        return

    click.echo(f"Found {len(urls)} answers.")
    dl = ContentDownloader(output_dir=output_dir)
    if not dl.load_headers_from_curl(quick_mode=not no_cache_headers):
        raise SystemExit(1)
    dl.download_answers(urls, delay=delay)


@download.command("batch-articles")
@click.option(
    "--input",
    "-i",
    "input_file",
    default=str(get_data_dir() / "exports" / "all_assets_list.json"),
    help="Assets JSON file",
)
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "articles"), help="Output directory")
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests (seconds)")
@click.option("--no-cache-headers", is_flag=True, help="Force re-paste of cURL")
def download_batch_articles(input_file: str, output_dir: str, delay: float, no_cache_headers: bool) -> None:
    """Batch download all articles listed in an assets JSON file."""
    if not os.path.exists(input_file):
        click.echo(f"Error: file not found: {input_file}", err=True)
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://zhuanlan.zhihu.com/p/{a['id']}" for a in assets if a.get("type") == "article"]
    if not urls:
        click.echo("No articles found in the assets file.")
        return

    click.echo(f"Found {len(urls)} articles.")
    dl = ContentDownloader(output_dir=output_dir)
    if not dl.load_headers_from_curl(quick_mode=not no_cache_headers):
        raise SystemExit(1)
    dl.download_articles(urls, delay=delay)


@download.command("user")
@click.argument("user")
@click.option(
    "--output-dir", "-o", default=None, help="Base output directory (default: ~/.zhihu-cli/downloads/<username>)"
)
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests in seconds")
@click.option("--max-items", "-n", type=int, default=None, help="Max items per content type")
@click.option(
    "--type",
    "content_types",
    default="all",
    type=click.Choice(["answers", "articles", "pins", "all"]),
    help="Content types to download (default: all)",
)
def download_user(user: str, output_dir: str | None, delay: float, max_items: int | None, content_types: str) -> None:
    """Download all answers, articles, and pins from a Zhihu user."""
    url_token = _extract_url_token(user)

    profile = fetch_member_profile(url_token)
    user_name = profile["name"] if profile else url_token
    click.echo(f"User: {user_name} (url_token: {url_token})")

    if output_dir is None:
        base_dir = get_data_dir() / "downloads" / sanitize_filename(user_name)
    else:
        base_dir = Path(output_dir)

    downloaded: dict[str, int] = {"answers": 0, "articles": 0, "pins": 0}

    if content_types in ("answers", "all"):
        answers_dir = str(base_dir / "answers")
        click.echo(f"\nFetching answers list for {user_name}...")
        answer_items = fetch_member_answers(url_token, max_items=max_items)
        click.echo(f"  Found {len(answer_items)} answers. Downloading full content...")

        for i, item in enumerate(answer_items, 1):
            try:
                meta, md = scrape_answer_page(item["url"])
                save_meta = {
                    "title": meta.get("title", "untitled"),
                    "author": meta.get("author", "unknown"),
                    "created": meta.get("created", "unknown"),
                }
                filepath = save_article(item["url"], save_meta, md, answers_dir)
                click.echo(f"  [{i}/{len(answer_items)}] {save_meta['title'][:50]} -> {os.path.basename(filepath)}")
                downloaded["answers"] += 1
            except Exception as e:
                click.echo(f"  [{i}/{len(answer_items)}] Error: {e}", err=True)
            time.sleep(delay)

    if content_types in ("articles", "all"):
        articles_dir = str(base_dir / "articles")
        click.echo(f"\nFetching articles list for {user_name}...")
        article_items = fetch_member_articles(url_token, max_items=max_items)
        click.echo(f"  Found {len(article_items)} articles. Downloading full content...")

        for i, item in enumerate(article_items, 1):
            try:
                meta, md = scrape_article(item["url"])
                author = meta.get("author", {})
                author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
                save_meta = {
                    "title": meta.get("title", "untitled"),
                    "author": author_name,
                    "created": (meta.get("created_time", "unknown") or "unknown")[:10],
                }
                filepath = save_article(item["url"], save_meta, md, articles_dir)
                click.echo(f"  [{i}/{len(article_items)}] {save_meta['title'][:50]} -> {os.path.basename(filepath)}")
                downloaded["articles"] += 1
            except Exception as e:
                click.echo(f"  [{i}/{len(article_items)}] Error: {e}", err=True)
            time.sleep(delay)

    if content_types in ("pins", "all"):
        pins_dir = str(base_dir / "pins")
        click.echo(f"\nFetching pins list for {user_name}...")
        pin_items = fetch_member_pins(url_token, max_items=max_items)
        click.echo(f"  Found {len(pin_items)} pins. Downloading full content...")

        for i, item in enumerate(pin_items, 1):
            try:
                meta, md = scrape_pin(item["url"])
                author = meta.get("author", {})
                author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
                save_meta = {
                    "author": author_name,
                    "created": (meta.get("created_time", "unknown") or "unknown")[:10],
                    "pin_id": str(meta.get("id", "")),
                }
                filepath = save_pin(item["url"], save_meta, md, pins_dir)
                preview = (meta.get("excerpt", "") or "")[:30]
                click.echo(f"  [{i}/{len(pin_items)}] {preview} -> {os.path.basename(filepath)}")
                downloaded["pins"] += 1
            except Exception as e:
                click.echo(f"  [{i}/{len(pin_items)}] Error: {e}", err=True)
            time.sleep(delay)

    click.echo(f"\nDone! Downloaded from {user_name}:")
    click.echo(f"  Answers: {downloaded['answers']}")
    click.echo(f"  Articles: {downloaded['articles']}")
    click.echo(f"  Pins: {downloaded['pins']}")
    click.echo(f"  Output: {base_dir}")


# ── browse ───────────────────────────────────────────────────────────────


@main.group()
def browse() -> None:
    """Browse Zhihu content in the terminal."""


@browse.command("question")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_question(url: str, reading_mode: bool, output_json: bool) -> None:
    """Browse a Zhihu question and all its answers."""
    q_meta, q_detail_md = scrape_question_data(url)

    answers = list(scrape_answers(q_meta))

    if output_json:
        click.echo(
            json.dumps({"question": q_meta, "detail_md": q_detail_md, "answers": answers}, ensure_ascii=False, indent=2)
        )
        return

    if reading_mode:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            reading_mode = False

    question_md = f"# {q_meta['title']}\n\n{q_detail_md}"
    if reading_mode:
        console = Console()
        with console.pager(styles=True, links=True):
            console.print(Markdown(question_md))
            for i, ans in enumerate(answers, 1):
                console.print(
                    f"\n--- Answer #{i} (ID: {ans['id']}) by {ans['author']} (+{ans['vote']} votes, {ans['comment']} comments, {ans['favorite']} favorites) ---"
                )
                console.print(Markdown(ans["content"]))
    else:
        click.echo(question_md)
        for i, ans in enumerate(answers, 1):
            click.echo(
                f"\n--- Answer #{i} (ID: {ans['id']}) by {ans['author']} (+{ans['vote']} votes, {ans['comment']} comments, {ans['favorite']} favorites) ---"
            )
            click.echo(ans["content"])


@browse.command("answer")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_answer(url: str, reading_mode: bool, output_json: bool) -> None:
    """View a single Zhihu answer in the terminal."""
    metadata, markdown = scrape_answer_page(url)

    if output_json:
        click.echo(json.dumps({"metadata": metadata, "content_md": markdown}, ensure_ascii=False, indent=2))
        return

    if reading_mode:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            reading_mode = False

    title = metadata.get("title", "untitled")
    author = metadata.get("author", "unknown")
    created = metadata.get("created", "unknown")
    upvotes = metadata.get("vote", 0)
    comments = metadata.get("comment", 0)
    favorites = metadata.get("favorite", 0)
    header = f"# {title}\n\n**Author:** {author} | **Date:** {created} | **Upvotes:** {upvotes} | **Comments:** {comments} | **Favorites:** {favorites}"

    if reading_mode:
        console = Console()
        with console.pager(styles=True, links=True):
            console.print(Markdown(header))
            console.print(Markdown(markdown))
    else:
        click.echo(header)
        click.echo()
        click.echo(markdown)


@browse.command("article")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_article(url: str, reading_mode: bool, output_json: bool) -> None:
    """Read a Zhihu article in the terminal."""
    metadata, markdown = scrape_article(url)

    if output_json:
        click.echo(json.dumps({"metadata": metadata, "content_md": markdown}, ensure_ascii=False, indent=2))
        return

    if reading_mode:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            reading_mode = False

    title = metadata.get("title", "untitled")
    author = metadata.get("author", {}).get("name", "unknown")
    stats = metadata.get("stats", {})
    upvotes = stats.get("voteup_count", 0)
    comments = stats.get("comment_count", 0)
    favorites = stats.get("favlists_count", 0)
    header = f"# {title}\n\n**Author:** {author} | **Upvotes:** {upvotes} | **Comments:** {comments} | **Favorites:** {favorites}"

    if reading_mode:
        console = Console()
        with console.pager(styles=True, links=True):
            console.print(Markdown(header))
            console.print(Markdown(markdown))
    else:
        click.echo(header)
        click.echo()
        click.echo(markdown)


@browse.command("log")
@click.argument("url")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_log(url: str, output_json: bool) -> None:
    """View the edit history (log) of a Zhihu question."""
    _, question_id = _parse_item_url(url)
    if not question_id:
        raise click.BadParameter(f"Cannot parse question ID from URL: {url}")

    entries = fetch_question_log(question_id)

    if output_json:
        click.echo(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    if not entries:
        click.echo("No edit history found.")
        return

    for entry in entries:
        user = entry["user"] or "unknown"
        action = entry["action"]
        time_str = entry["time"]
        detail = entry["detail"]

        click.echo(f"[{time_str}] {user} {action}")
        if detail:
            click.echo(f"  {detail[:200]}")
        click.echo()


@browse.command("comments")
@click.argument("url")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_comments(url: str, output_json: bool) -> None:
    """Print the comment tree for any Zhihu item."""
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    if output_json:
        click.echo(json.dumps(fetch_comments(item_type, item_id), ensure_ascii=False, indent=2))
        return
    print_comments(item_type, item_id)


@browse.command("feed")
@click.option("--type", "-t", "feed_type", type=click.Choice(["recommend", "follow"]), default="recommend")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
@click.option("--markdown/--no-markdown", default=False, help="Convert HTML to Markdown")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def browse_feed(
    feed_type: str, limit: int, max_items: int | None, markdown: bool, output_json: bool, output: str
) -> None:
    """Stream Zhihu recommend or follow feed."""
    fetch_fn = fetch_feed_with_markdown if markdown else fetch_feed
    items = fetch_fn(feed_type, limit, max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    for item in items:
        ttype = item.get("target_type", "?")
        title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
        author = item.get("author", {}).get("name", "unknown")
        url = item.get("url", "")
        excerpt = item.get("excerpt", "")

        click.echo(f"[{ttype}] {title[:120]}")
        if excerpt:
            click.echo(f"  preview: {excerpt[:200]}")
        click.echo(f"  author={author}  votes={item.get('voteup_count', 0)}")
        if url:
            click.echo(f"  link: {url}")
        click.echo()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")


@browse.command("hot")
@click.option("--limit", "-n", type=int, default=30, help="Number of hot items to show (default: 30)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def browse_hot(limit: int, output_json: bool, output: str) -> None:
    """View the Zhihu real-time hot list."""
    items = fetch_hot_list(limit=50)

    if limit and len(items) > limit:
        items = items[:limit]

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    for i, item in enumerate(items, 1):
        title = item["title"] or "(no title)"
        heat = item["heat"]
        ttype = item["target_type"]
        url = item["url"]
        card_label = item["card_label"]
        answer_count = item["answer_count"]
        follower_count = item["follower_count"]

        label_str = f" [{card_label}]" if card_label else ""
        click.echo(f"[{i}] {heat}{label_str}  {ttype}")
        click.echo(f"    {title}")
        excerpt = item["excerpt"]
        if excerpt:
            click.echo(f"    preview: {excerpt[:200]}")
        author = item["author"]
        if author and author != "anonymous":
            click.echo(f"    author: {author}")
        if answer_count or follower_count:
            parts = []
            if answer_count:
                parts.append(f"{answer_count} answers")
            if follower_count:
                parts.append(f"{follower_count} followers")
            click.echo(f"    {'  '.join(parts)}")
        if url:
            click.echo(f"    {url}")
        click.echo()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")

    if not items:
        click.echo("No hot items found. Try logging in first: zhihu auth login")


@browse.command("notifications")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def browse_notifications(limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """View your Zhihu notifications."""
    from zhihu_cli.content.handlers.notifications import fetch_notifications

    items = fetch_notifications(limit=limit, max_items=max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    for i, item in enumerate(items, 1):
        marker = " " if item["is_read"] else "*"
        verb = item["verb"]
        actor = item["actor_name"]
        target_text = item["target_text"]
        target_link = item["target_link"]
        rtype = item["resource_type"]
        time_str = item["time"]
        merge = item["merge_count"]

        merge_str = f" (+{merge - 1})" if merge > 1 else ""

        click.echo(f"[{i}]{marker} {actor} {verb}{merge_str}  ({rtype}: {target_text})")
        comment = item["comment_text"]
        if comment:
            click.echo(f"    > {comment}")
        if target_link:
            click.echo(f"    {target_link}")
        click.echo(f"    {time_str}")
        click.echo()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")

    if not items:
        click.echo("No notifications found. Try logging in first: zhihu auth login")


@browse.command("history")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def browse_history(limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """View your Zhihu read history."""
    from zhihu_cli.content.handlers.read_history import fetch_read_history

    items = fetch_read_history(limit=limit, max_items=max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    for i, item in enumerate(items, 1):
        ctype = item["content_type"]
        title = item["title"] or "(no title)"
        author = item["author_name"]
        summary = item["summary"]
        stats = item["stats_text"]
        url = item["url"]
        read_time = item["read_time"]

        click.echo(f"[{i}] [{ctype}] {title[:120]}")
        if author:
            click.echo(f"    author: {author}")
        if summary:
            click.echo(f"    {summary[:200]}")
        if stats:
            click.echo(f"    {stats}")
        if url:
            click.echo(f"    {url}")
        click.echo(f"    read: {read_time}")
        click.echo()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")

    if not items:
        click.echo("No read history found. Try logging in first: zhihu auth login")


@browse.command("yanxuan")
@click.argument("url_or_id")
@click.option("--offset", type=int, default=0, help="Starting segment offset (default: 0)")
@click.option("--max-segments", "-n", type=int, default=None, help="Max segments to fetch")
@click.option("--max-pages", type=int, default=None, help="Max API pages to fetch")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output segments as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to file")
def browse_yanxuan(
    url_or_id: str,
    offset: int,
    max_segments: int | None,
    max_pages: int | None,
    reading_mode: bool,
    output_json: bool,
    output: str,
) -> None:
    """Read Zhihu Yanxuan (盐选) premium content in the terminal.

    URL_OR_ID can be a full answer URL, a composite question_id/answer_id,
    or a raw answer ID (url_token).
    """
    url_token = extract_url_token(url_or_id)

    meta, segments = fetch_yanxuan_segments(
        url_token,
        offset=offset,
        max_segments=max_segments,
        max_pages=max_pages,
    )

    if output_json:
        click.echo(json.dumps({"meta": meta, "segments": segments}, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "segments": segments}, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(segments)} segments to {output}", err=True)
        return

    if not segments:
        click.echo("No content found for this yanxuan item.")
        return

    # Build header from meta
    title = meta.get("title", "") or meta.get("story_name", "")
    brand = meta.get("brand", "")
    copyright_ = meta.get("copyright", "")

    header_parts = []
    if title:
        header_parts.append(f"# {title}")
    if brand:
        header_parts.append(f"**{brand}**")
    if copyright_:
        header_parts.append(f"*{copyright_}*")

    header = "\n\n".join(header_parts) if header_parts else ""
    body = segments_to_text(segments)
    full_text = f"{header}\n\n{body}" if header else body

    if reading_mode:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            reading_mode = False

    if reading_mode:
        console = Console()
        with console.pager(styles=True, links=True):
            console.print(Markdown(full_text))
    else:
        click.echo(full_text)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(full_text)
        click.echo(f"Saved {len(segments)} segments to {output}")


# ── browse following ──────────────────────────────────────────────────────


@browse.group("following")
def browse_following() -> None:
    """View your followed users, topics, questions, columns, and collections."""


def _resolve_following_token(url_token: str | None) -> str:
    """Resolve the url_token: use provided value or auto-detect from /api/v4/me."""
    if url_token:
        return _extract_url_token(url_token)
    token = get_my_url_token()
    if not token:
        raise click.UsageError(
            "Cannot detect your url_token. Please authenticate first (zhihu auth login) "
            "or provide --url-token explicitly."
        )
    return token


def _display_following_items(items: list[dict], totals: int | None = None) -> None:
    """Display a list of following items in terminal mode."""
    for i, item in enumerate(items, 1):
        ttype = item.get("type", "?")

        if ttype == "user":
            name = item.get("name", "")
            headline = item.get("headline", "")
            is_followed = item.get("is_followed", False)
            is_following = item.get("is_following", False)
            mutual = " [互关]" if (is_followed and is_following) else ""
            f_cnt = item.get("follower_count", 0)
            a_cnt = item.get("answer_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = click.style(f"followers: {f_cnt}  answers: {a_cnt}  articles: {art_cnt}", dim=True)
            click.echo(f"[{i}] {click.style(name, bold=True)}{mutual}")
            if headline:
                click.echo(f"    {headline[:120]}")
            click.echo(f"    {stats}")
            click.echo(f"    {click.style(item.get('url', ''), dim=True)}")

        elif ttype == "topic":
            name = item.get("name", "")
            intro = item.get("introduction", "") or item.get("excerpt", "")
            f_cnt = item.get("followers_count", 0)
            q_cnt = item.get("questions_count", 0)
            stats = click.style(f"followers: {f_cnt}  questions: {q_cnt}", dim=True)
            click.echo(f"[{i}] {click.style(name, bold=True)} [topic]")
            if intro:
                click.echo(f"    {intro[:120]}")
            click.echo(f"    {stats}")
            click.echo(f"    {click.style(item.get('url', ''), dim=True)}")

        elif ttype == "question":
            title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            ctime = item.get("created_time", "")
            stats = click.style(f"answers: {a_cnt}  followers: {f_cnt}  created: {ctime}", dim=True)
            click.echo(f"[{i}] {title[:120]}")
            click.echo(f"    {stats}")
            click.echo(f"    {click.style(item.get('url', ''), dim=True)}")

        elif ttype == "column":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "") or item.get("excerpt", "")
            creator = item.get("creator", "")
            f_cnt = item.get("followers_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = click.style(f"followers: {f_cnt}  articles: {art_cnt}", dim=True)
            click.echo(f"[{i}] {click.style(title, bold=True)} [column]")
            if creator:
                click.echo(f"    by {creator}")
            if desc:
                click.echo(f"    {desc[:120]}")
            click.echo(f"    {stats}")
            click.echo(f"    {click.style(item.get('url', ''), dim=True)}")

        elif ttype == "collection":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "")
            creator_name = item.get("creator_name", "")
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            stats = click.style(f"items: {a_cnt}  followers: {f_cnt}", dim=True)
            click.echo(f"[{i}] {click.style(title, bold=True)} [collection]")
            if creator_name:
                click.echo(f"    by {creator_name}")
            if desc:
                click.echo(f"    {desc[:120]}")
            click.echo(f"    {stats}")
            click.echo(f"    {click.style(item.get('url', ''), dim=True)}")

        click.echo()

    if items:
        total_str = f"/{totals}" if totals else ""
        click.echo(f"── {len(items)}{total_str} items")


def _following_command(
    fetch_fn,
    url_token: str | None,
    limit: int,
    max_items: int | None,
    output_json: bool,
    output: str,
    label: str,
) -> None:
    """Shared execution path for following sub-commands."""
    token = _resolve_following_token(url_token)
    click.echo(f"Fetching {label} for {token}...", err=True)
    items = fetch_fn(token, limit=limit, max_items=max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    if not items:
        click.echo(f"No {label} found.")
        return

    _display_following_items(items)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")


@browse_following.command("users")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_users(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """List users you follow."""
    _following_command(fetch_followees, url_token, limit, max_items, output_json, output, "followed users")


@browse_following.command("followers")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_followers(
    url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str
) -> None:
    """List your followers (people who follow you)."""
    _following_command(fetch_followers, url_token, limit, max_items, output_json, output, "followers")


@browse_following.command("topics")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_topics(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """List topics you follow."""
    _following_command(fetch_following_topics, url_token, limit, max_items, output_json, output, "followed topics")


@browse_following.command("questions")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_questions(
    url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str
) -> None:
    """List questions you follow."""
    _following_command(
        fetch_following_questions, url_token, limit, max_items, output_json, output, "followed questions"
    )


@browse_following.command("columns")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_columns(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """List columns (zhuanlan) you follow."""
    _following_command(fetch_following_columns, url_token, limit, max_items, output_json, output, "followed columns")


@browse_following.command("collections")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def following_collections(
    url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str
) -> None:
    """List collections (favorites) you follow."""
    _following_command(
        fetch_following_collections, url_token, limit, max_items, output_json, output, "followed collections"
    )


# ── people ───────────────────────────────────────────────────────────────


def _extract_url_token(token_or_url: str) -> str:
    """Extract a Zhihu url_token from a full profile URL or return as-is."""
    import re

    m = re.search(r"zhihu\.com/people/([^/?]+)", token_or_url)
    if m:
        return m.group(1)
    return token_or_url.rstrip("/").split("/")[-1]


def _print_stat(label: str, value: int) -> None:
    """Print a labeled stat line with dimmed label."""
    click.echo(f"  {click.style(label + ':', dim=True)} {value}")


def _print_content_item(item: dict, show_type: bool = False) -> None:
    """Print a single content item in a compact format."""
    ttype = item.get("type", "")
    type_label = f"[{ttype}] " if show_type else ""
    title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
    created = item.get("created_time", "")
    votes = item.get("voteup_count", 0)
    comments = item.get("comment_count", 0)

    parts = [created]
    if votes:
        parts.append(f"+{votes}")
    if comments:
        parts.append(f"{comments} comments")
    if "answer_count" in item and item["answer_count"]:
        parts.append(f"{item['answer_count']} answers")
    if "follower_count" in item and item["follower_count"]:
        parts.append(f"{item['follower_count']} followers")

    click.echo(f"  {type_label}{title[:100]}")
    click.echo(f"  {click.style('  '.join(parts), dim=True)}")
    click.echo(f"  {click.style(item.get('url', ''), dim=True)}")
    click.echo()


def _show_profile_rich(profile: dict) -> None:
    """Display a user profile using Rich if available, otherwise plain text."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = Console()
        name = profile.get("name", "Unknown")
        headline = profile.get("headline", "")
        url_token = profile.get("url_token", "")

        header = Text(name, style="bold cyan")
        if headline:
            header.append(f"\n{headline}", style="dim")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_column(style="dim")
        table.add_column()
        table.add_row(
            f"followers: {profile.get('follower_count', 0)}",
            f"following: {profile.get('following_count', 0)}",
            f"answers: {profile.get('answer_count', 0)}",
            f"articles: {profile.get('articles_count', 0)}",
        )
        table.add_row(
            f"pins: {profile.get('pins_count', 0)}",
            f"questions: {profile.get('question_count', 0)}",
            f"upvotes: {profile.get('voteup_count', 0)}",
            f"thanked: {profile.get('thanked_count', 0)}",
        )

        panel = Panel(
            table,
            title=header.plain[:40],
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(panel)
        click.echo(f"  Profile: https://www.zhihu.com/people/{url_token}")
        click.echo()
    except ImportError:
        click.echo(f"\n{click.style(profile.get('name', 'Unknown'), bold=True)}")
        if headline := profile.get("headline"):
            click.echo(f"  {headline}")
        click.echo(f"  https://www.zhihu.com/people/{profile.get('url_token', '')}")
        click.echo()
        _print_stat("Followers", profile.get("follower_count", 0))
        _print_stat("Following", profile.get("following_count", 0))
        _print_stat("Answers", profile.get("answer_count", 0))
        _print_stat("Articles", profile.get("articles_count", 0))
        _print_stat("Pins", profile.get("pins_count", 0))
        _print_stat("Questions", profile.get("question_count", 0))
        _print_stat("Upvotes received", profile.get("voteup_count", 0))
        click.echo()


def _list_content_section(
    fetch_fn,
    url_token: str,
    section_title: str,
    limit: int = 5,
    *,
    show_type: bool = False,
) -> list:
    """Fetch and display a content section. Returns the fetched items."""
    try:
        items = fetch_fn(url_token, limit=limit, max_items=limit)
    except Exception:
        return []

    if items:
        click.echo(f"── Recent {len(items)} {section_title}" + "─" * 40)
        for item in items:
            _print_content_item(item, show_type=show_type)
    return items


@main.group()
def people() -> None:
    """Browse a Zhihu user's public profile and content."""


@people.command("show")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=5, help="Items per content type (default: 5)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_show(url_token: str, limit: int, output_json: bool) -> None:
    """Display a user's profile and recent content across all types.

    URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL
    (e.g. https://www.zhihu.com/people/zhangsan).
    """
    token = _extract_url_token(url_token)

    click.echo(f"Fetching profile for {token}...", err=True)
    profile = fetch_member_profile(token)
    if profile is None:
        click.echo(f"Error: could not fetch profile for '{token}'. Check the token and try again.", err=True)
        raise SystemExit(1)

    if output_json:
        result: dict = {"profile": profile}
        for key, fn in [
            ("answers", fetch_member_answers),
            ("articles", fetch_member_articles),
            ("pins", fetch_member_pins),
            ("questions", fetch_member_questions),
        ]:
            try:
                result[key] = fn(token, limit=limit, max_items=limit)
            except Exception:
                result[key] = []
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    _show_profile_rich(profile)

    _list_content_section(fetch_member_answers, token, "Answers", limit)
    _list_content_section(fetch_member_articles, token, "Articles", limit)
    _list_content_section(fetch_member_pins, token, "Pins", limit)
    _list_content_section(fetch_member_questions, token, "Questions", limit)


@people.command("answers")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_answers(url_token: str, limit: int, output_json: bool) -> None:
    """List a user's answers."""
    token = _extract_url_token(url_token)
    click.echo(f"Fetching answers for {token}...", err=True)
    items = fetch_member_answers(token, max_items=limit)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("No answers found.")
        return
    for item in items:
        _print_content_item(item)
    click.echo(f"── {len(items)} answers total")


@people.command("articles")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_articles(url_token: str, limit: int, output_json: bool) -> None:
    """List a user's articles."""
    token = _extract_url_token(url_token)
    click.echo(f"Fetching articles for {token}...", err=True)
    items = fetch_member_articles(token, max_items=limit)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("No articles found.")
        return
    for item in items:
        _print_content_item(item)
    click.echo(f"── {len(items)} articles total")


@people.command("pins")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_pins(url_token: str, limit: int, output_json: bool) -> None:
    """List a user's pins (想法)."""
    token = _extract_url_token(url_token)
    click.echo(f"Fetching pins for {token}...", err=True)
    items = fetch_member_pins(token, max_items=limit)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("No pins found.")
        return
    for item in items:
        t = click.style(item.get("created_time", ""), dim=True)
        content = item.get("content_text", "") or item.get("excerpt", "")
        click.echo(f"  {content[:120]}")
        click.echo(f"  {t}  +{item.get('voteup_count', 0)}  {item.get('comment_count', 0)} comments")
        click.echo(f"  {click.style(item.get('url', ''), dim=True)}")
        click.echo()
    click.echo(f"── {len(items)} pins total")


@people.command("questions")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_questions(url_token: str, limit: int, output_json: bool) -> None:
    """List questions asked by a user."""
    token = _extract_url_token(url_token)
    click.echo(f"Fetching questions for {token}...", err=True)
    items = fetch_member_questions(token, max_items=limit)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("No questions found (this endpoint may not be available).")
        return
    for item in items:
        _print_content_item(item)
    click.echo(f"── {len(items)} questions total")


# ── search ────────────────────────────────────────────────────────────────


@main.group()
def search() -> None:
    """Search Zhihu for questions, articles, users, and topics."""


@search.command("question")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_question_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu questions by keyword."""
    items = search_questions(query, limit=limit, max_items=max_items)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for i, q in enumerate(items, 1):
        click.echo(f"[{i}] {q['title']}")
        click.echo(f"    {q['answer_count']} answers  {q['follower_count']} followers")
        click.echo(f"    updated: {q['updated_time']}")
        click.echo(f"    {q['url']}")
        click.echo()
    if not items:
        click.echo(f"No questions found for '{query}'.")


@search.command("article")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_article_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu articles by keyword."""
    items = search_articles(query, limit=limit, max_items=max_items)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for i, a in enumerate(items, 1):
        click.echo(f"[{i}] {a['title']}")
        click.echo(f"    by {a['author']['name']}  {a['voteup_count']} upvotes")
        if a["excerpt"]:
            click.echo(f"    {a['excerpt'][:120]}")
        click.echo(f"    {a['created_time']}")
        click.echo(f"    {a['url']}")
        click.echo()
    if not items:
        click.echo(f"No articles found for '{query}'.")


@search.command("user")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_user_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu users by keyword."""
    items = search_users(query, limit=limit, max_items=max_items)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for i, u in enumerate(items, 1):
        click.echo(f"[{i}] {u['name']}  ({u['gender']})")
        if u["headline"]:
            click.echo(f"    {u['headline']}")
        click.echo(f"    {u['follower_count']} followers  {u['answer_count']} answers  {u['articles_count']} articles")
        click.echo(f"    {u['url']}")
        click.echo()
    if not items:
        click.echo(f"No users found for '{query}'.")


@search.command("topic")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_topic_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu topics by keyword."""
    items = search_topics(query, limit=limit, max_items=max_items)
    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for i, t in enumerate(items, 1):
        click.echo(f"[{i}] {t['name']}")
        intro = t["introduction"] or t["excerpt"]
        if intro:
            click.echo(f"    {intro[:120]}")
        click.echo(f"    {t['questions_count']} questions  {t['followers_count']} followers")
        click.echo(f"    {t['url']}")
        click.echo()
    if not items:
        click.echo(f"No topics found for '{query}'.")


# ── interact ─────────────────────────────────────────────────────────────


@main.group()
def interact() -> None:
    """Social interactions — vote, thank, follow, block, comment, collect."""


@interact.group("vote")
def interact_vote() -> None:
    """Vote on answers and questions."""


@interact_vote.command("up")
@click.argument("url_or_id")
def vote_up(url_or_id: str) -> None:
    """Upvote an answer or question."""
    item_type, item_id = _parse_item_url_safe(url_or_id)
    if item_type in ("answers", "answer"):
        click.echo(upvote_answer(_resolve_answer_id(item_id)))
    elif item_type in ("questions", "question"):
        click.echo(upvote_question(item_id))
    else:
        upvote_answer(url_or_id)  # treat as raw ID


@interact_vote.command("neutral")
@click.argument("url_or_id")
def vote_neutral(url_or_id: str) -> None:
    """Remove vote from an answer."""
    item_type, item_id = _parse_item_url_safe(url_or_id)
    click.echo(neutral_answer(_resolve_answer_id(item_id) if item_type else url_or_id))


@interact_vote.command("down")
@click.argument("url_or_id")
def vote_down(url_or_id: str) -> None:
    """Downvote an answer or question."""
    item_type, item_id = _parse_item_url_safe(url_or_id)
    if item_type in ("answers", "answer"):
        click.echo(downvote_answer(_resolve_answer_id(item_id)))
    elif item_type in ("questions", "question"):
        click.echo(downvote_question(item_id))
    else:
        downvote_answer(url_or_id)


def _parse_item_url_safe(url_or_id: str) -> tuple[str | None, str | None]:
    """Try to parse as URL, fall back to treating as raw ID."""
    result = get_type_and_id(url_or_id)
    if result != (None, None):
        return result
    return (None, url_or_id)


@interact.group("thank")
def interact_thank() -> None:
    """Thank or unthank answers."""


@interact_thank.command("add")
@click.argument("answer_id")
def thank_add(answer_id: str) -> None:
    """Thank an answer."""
    click.echo(thank_answer(answer_id))


@interact_thank.command("remove")
@click.argument("answer_id")
def thank_remove(answer_id: str) -> None:
    """Remove thanks from an answer."""
    click.echo(unthank_answer(answer_id))


@interact.group("follow")
def interact_follow() -> None:
    """Follow or unfollow users and questions."""


@interact_follow.command("user")
@click.argument("user_id")
def follow_user(user_id: str) -> None:
    """Follow a user by URL token or ID."""
    click.echo(follow(user_id))


@interact_follow.command("question")
@click.argument("question_id")
def follow_question_cmd(question_id: str) -> None:
    """Follow a question."""
    click.echo(follow_question(question_id))


@interact_follow.command("unfollow-user")
@click.argument("user_id")
def unfollow_user(user_id: str) -> None:
    """Unfollow a user."""
    click.echo(unfollow(user_id))


@interact_follow.command("unfollow-question")
@click.argument("question_id")
def unfollow_question_cmd(question_id: str) -> None:
    """Unfollow a question."""
    click.echo(unfollow_question(question_id))


@interact.group("block")
def interact_block() -> None:
    """Block or unblock users."""


@interact_block.command("add")
@click.argument("user_id")
def block_user(user_id: str) -> None:
    """Block a user."""
    block(user_id)
    click.echo(f"Blocked {user_id}")


@interact_block.command("remove")
@click.argument("user_id")
def block_remove(user_id: str) -> None:
    """Unblock a user."""
    unblock(user_id)
    click.echo(f"Unblocked {user_id}")


@interact.group("comment")
def interact_comment() -> None:
    """Post or delete comments."""


@interact_comment.command("post")
@click.argument("url")
@click.argument("content")
def comment_post(url: str, content: str) -> None:
    """Post a comment on an item. Use URL to identify the item."""
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    resp = comment_item(item_type, item_id, content)
    click.echo(resp)


@interact_comment.command("delete")
@click.argument("comment_id")
def comment_delete(comment_id: str) -> None:
    """Delete a comment by ID."""
    delete_comment(comment_id)
    click.echo(f"Deleted comment {comment_id}")


@interact.group("collect")
def interact_collect() -> None:
    """Manage collections."""


@interact_collect.command("add")
@click.argument("url")
@click.option("--collection", "-c", "collection_id", help="Target collection ID")
def collect_add(url: str, collection_id: str | None) -> None:
    """Add an item to the default or specified collection."""
    item_type, item_id = _parse_item_url(url)
    if collection_id:
        click.echo(add_to_collection(item_type, item_id, collection_id))
    else:
        click.echo(collect(item_type, item_id))


@interact_collect.command("remove")
@click.argument("url")
@click.option("--collection", "-c", "collection_id", required=True, help="Target collection ID")
def collect_remove(url: str, collection_id: str) -> None:
    """Remove an item from a collection."""
    item_type, item_id = _parse_item_url(url)
    click.echo(delete_to_collection(item_type, item_id, collection_id))


@interact_collect.command("create")
@click.argument("title")
@click.option("--description", "-d", default="", help="Collection description")
@click.option("--public/--private", default=True, help="Visibility")
def collect_create(title: str, description: str, public: bool) -> None:
    """Create a new collection."""
    click.echo(create_collection(title, description, public))


@interact_collect.command("delete")
@click.argument("collection_id")
def collect_delete(collection_id: str) -> None:
    """Delete a collection."""
    click.echo(delete_collection(collection_id))


@interact_collect.command("list")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def collect_list(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """List your collections."""
    token = _resolve_following_token(url_token)
    click.echo(f"Fetching collections for {token}...", err=True)

    items = list_collections(token, limit=limit, max_items=max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            click.echo(f"Saved {len(items)} items to {output}", err=True)
        return

    if not items:
        click.echo("No collections found.")
        return

    _display_following_items(items)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        click.echo(f"Saved {len(items)} items to {output}")


# ── report ──────────────────────────────────────────────────────────────


@interact.group("report")
def interact_report() -> None:
    """Report (举报) content — list reasons or submit a report."""


@interact_report.command("reasons")
@click.argument("object_type", default="answer")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def report_reasons(object_type: str, output_json: bool) -> None:
    """List available report reasons for an object type.

    OBJECT_TYPE: answer, question, article, comment, or pin (default: answer).
    """
    data = fetch_report_reasons(object_type)

    if output_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    reasons = flatten_reasons(data)
    if not reasons:
        click.echo(f"No report reasons found for type '{object_type}'.")
        return

    click.echo(f"Report reasons for '{object_type}':\n")
    current_category = None
    for r in reasons:
        if r["category"] and r["category"] != current_category:
            current_category = r["category"]
            click.echo(f"  [{current_category}]")
        label = f"    {r['id']} — {r['text']}" if r["category"] else f"  {r['id']} — {r['text']}"
        click.echo(label)


@interact_report.command("submit")
@click.argument("url")
@click.option("--reason", "-r", "reason_id", required=True, help="Reason ID (from 'zhihu interact report reasons')")
@click.option("--custom-reason", "-c", default="", help="Custom explanation text")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def report_submit(url: str, reason_id: str, custom_reason: str, output_json: bool) -> None:
    """Submit a report for content at URL.

    \b
    Examples:
      zhihu interact report reasons answer
      zhihu interact report submit https://www.zhihu.com/question/123/answer/456 -r 1040-irrelevant-answer
      zhihu interact report submit https://www.zhihu.com/question/123/answer/456 -r 1040-irrelevant-answer -c "广告垃圾"
    """
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    # Map URL type to API object_type
    type_map = {
        "articles": "article",
        "questions": "question",
        "answers": "answer",
        "pins": "pin",
    }
    object_type = type_map.get(item_type, item_type)

    resp = submit_report(
        resource_id=item_id,
        object_type=object_type,
        reason_id=reason_id,
        custom_reason=custom_reason,
        url=url,
    )

    if output_json:
        click.echo(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        if resp.get("is_reported") or (isinstance(resp, dict) and resp.get("success", True)):
            click.echo(f"✓ Report submitted for {item_type} {item_id}")
            click.echo(f"  Reason: {reason_id}")
            if custom_reason:
                click.echo(f"  Detail: {custom_reason}")
        else:
            click.echo(f"✗ Report failed: {json.dumps(resp, ensure_ascii=False)}")


# ── publish ──────────────────────────────────────────────────────────────


@main.group()
def publish() -> None:
    """Publish or modify answers and articles."""


@publish.command("answer")
@click.argument("question_id")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_answer_cmd(question_id: str, file: str | None) -> None:
    """Publish a new answer to a question. Reads Markdown from file or stdin."""
    content = _read_content(file)
    if not content.strip():
        click.echo("Error: empty content.", err=True)
        raise SystemExit(1)
    resp = publish_answer(question_id, content)
    click.echo(json.dumps(resp, ensure_ascii=False, indent=2))


@publish.command("modify-answer")
@click.argument("answer_id")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_modify_answer(answer_id: str, file: str | None) -> None:
    """Modify an existing answer."""
    content = _read_content(file)
    if not content.strip():
        click.echo("Error: empty content.", err=True)
        raise SystemExit(1)
    resp = modify_answer(answer_id, content)
    click.echo(json.dumps(resp, ensure_ascii=False, indent=2))


@publish.command("article")
@click.argument("title")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_article_cmd(title: str, file: str | None) -> None:
    """Publish a new article. Reads Markdown from file or stdin."""
    content = _read_content(file)
    if not content.strip():
        click.echo("Error: empty content.", err=True)
        raise SystemExit(1)
    resp = publish_article(title, content)
    click.echo(json.dumps(resp, ensure_ascii=False, indent=2))


@publish.command("modify-article")
@click.argument("article_id")
@click.argument("title")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_modify_article(article_id: str, title: str, file: str | None) -> None:
    """Modify an existing article."""
    content = _read_content(file)
    if not content.strip():
        click.echo("Error: empty content.", err=True)
        raise SystemExit(1)
    resp = modify_article(article_id, title, content)
    click.echo(json.dumps(resp, ensure_ascii=False, indent=2))


# ── chat ─────────────────────────────────────────────────────────────────


@main.group()
def chat() -> None:
    """Read inbox, view chat history, send messages."""


@chat.command("inbox")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def chat_inbox(output_json: bool) -> None:
    """List recent conversations."""
    messages = get_inbox()
    if output_json:
        click.echo(json.dumps(messages, ensure_ascii=False, indent=2))
        return
    if not messages:
        click.echo("Inbox is empty.")
        return
    for msg in messages:
        click.echo(f"[{msg['unread_count']} unread] {msg['from']}")
        click.echo(f"  {msg['snippet'][:80]}")
        click.echo(f"  id={msg['id']}  token={msg['url_token']}  time={msg['updated_time']}")
        click.echo()


@chat.command("history")
@click.argument("chat_id")
@click.option("--limit", "-n", type=int, default=50, help="Max messages to fetch")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def chat_history(chat_id: str, limit: int, output_json: bool) -> None:
    """Read messages from a chat conversation."""
    count = 0
    if output_json:
        msgs = []
        for msg in iter_chat_history(chat_id):
            msgs.append(msg)
            count += 1
            if count >= limit:
                break
        click.echo(json.dumps(msgs, ensure_ascii=False, indent=2))
        return
    for msg in iter_chat_history(chat_id):
        click.echo(f"[{msg['time']}]{msg['sender']}: {msg['content']}")
        count += 1
        if count >= limit:
            break


@chat.command("send")
@click.argument("user_id")
@click.argument("content")
def chat_send(user_id: str, content: str) -> None:
    """Send a text message to a user."""
    resp = send_text_message(user_id, content)
    click.echo(resp)


# ── listen ───────────────────────────────────────────────────────────────


@main.command("listen")
@click.argument("url_token")
@click.option(
    "--topic", "-t", default="notification", type=click.Choice(["notification", "imchat"]), help="MQTT topic type"
)
@click.option("--incognito/--no-incognito", default=False, help="Connect incognito")
def listen(url_token: str, topic: str, incognito: bool) -> None:
    """Start real-time MQTT listener for notifications or messages."""
    from zhihu_cli.content.handlers.imchat import IMCHAT_TOPIC, NOTIFICATION_TOPIC, ZhihuMessageListener

    topic_str = NOTIFICATION_TOPIC if topic == "notification" else IMCHAT_TOPIC
    click.echo(f"Connecting to Zhihu MQTT ({topic})...")
    listener = ZhihuMessageListener(url_token, topic_str, incognito=incognito)
    click.echo("Listening — press Ctrl+C to stop.")
    try:
        listener.start()
    except KeyboardInterrupt:
        click.echo("\nStopped.")


# ── agora ─────────────────────────────────────────────────────────────────
@main.group()
def agora() -> None:
    """众裁 (community moderation) — review reported comments and vote."""


@agora.command("next")
@click.option("--discussion-id", "-d", default=None, help="Specific discussion ID to fetch")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_next(discussion_id: str | None, output_json: bool) -> None:
    """Get the next agora discussion to judge (众裁案例).

    Fetches the court page and extracts the current discussion case.
    Use -d to request a specific discussion by ID.
    """
    try:
        data = fetch_court_page(discussion_id=discussion_id)
    except ValueError as e:
        raise click.ClickException(str(e))

    if output_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    juror = data.get("juror_info", {})

    # Show juror status header
    if juror.get("is_juror"):
        today = juror.get("today_jury_count", 0)
        max_day = juror.get("max_day_jury_count", 20)
        remaining = max(0, max_day - today)
        click.echo(f"众裁官 | 总投票: {juror.get('vote_count', 0)} | 今日: {today}/{max_day} (剩余 {remaining})")
    else:
        click.echo(click.style("你尚不是众裁官", fg="yellow"))

    disc = data.get("current_discussion")
    if not disc:
        disc_id = data.get("discussion_id", "")
        if disc_id:
            click.echo(f"\nDiscussion ID: {disc_id}")
            click.echo("Discussion data not in initialData. Try fetching details with 'zhihu agora detail {disc_id}'.")
        else:
            click.echo("\nNo pending discussions. Check back later!")
        return

    click.echo()

    # Report reason
    reason = disc.get("report_reason", "")
    note = disc.get("report_note", "")
    click.echo(click.style(f"举报理由: {reason}", fg="red", bold=True))
    if note:
        click.echo(f"  {note}")
    click.echo()

    # The reported comment
    comment = disc.get("comment", {})
    _print_agora_comment(comment, disc.get("reported_user", ""))
    click.echo()

    # Origin context
    origin_title = disc.get("origin_title", "")
    origin_url = disc.get("origin_url", "")
    if origin_title:
        click.echo(f"评论所在内容: {click.style(origin_title, bold=True)}")
    if origin_url:
        click.echo(f"  {click.style(origin_url, dim=True)}")
    click.echo()

    # Status
    status = disc.get("status", "")
    my_vote = disc.get("my_vote", "")
    status_str = f"状态: {status}"
    if my_vote:
        status_str += f"  我的投票: {my_vote}"
    click.echo(click.style(status_str, dim=True))

    if not my_vote and status == "Voting":
        click.echo()
        click.echo(
            "投票: zhihu agora vote {} -v {{affirmative,abstain,dissenting}}".format(
                disc.get("id", data.get("discussion_id", "<id>"))
            )
        )


def _print_agora_comment(comment: dict, reported_user: str) -> None:
    """Print a single comment block for agora display."""
    author = comment.get("author", {})
    author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
    headline = author.get("headline", "") if isinstance(author, dict) else ""
    content = comment.get("content", "(no content)")
    created = comment.get("created_time", 0)
    votes = comment.get("vote_count", 0)
    url = comment.get("url", "")

    click.echo(
        f"被举报评论 — {click.style(author_name, bold=True)}{' (' + reported_user + ')' if reported_user else ''}"
    )
    if headline:
        click.echo(f"  {click.style(headline, dim=True)}")
    click.echo()
    click.echo(f"  {content}")
    click.echo()
    click.echo(f"  赞同: {votes}  |  时间: {click.style(str(created), dim=True)}  |  {click.style(url, dim=True)}")


@agora.command("me")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_me(output_json: bool) -> None:
    """Show your agora (众裁) juror status and statistics."""
    data = fetch_agora_me()

    if output_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    juror = data.get("juror_info", {})

    if not data.get("is_juror"):
        click.echo("You are not a juror (众裁官).")
        return

    click.echo(click.style("众裁官 (Juror)", bold=True, fg="green"))
    click.echo()
    click.echo(f"  总投票 (total votes):      {juror.get('vote_count', 0)}")
    click.echo(f"  总评审 (total reviews):     {juror.get('review_count', 0)}")
    click.echo(f"  评审获赞 (review likes):    {juror.get('review_liked_count', 0)}")
    click.echo()
    click.echo(
        f"  今日已裁 (today judged):    {juror.get('today_jury_count', 0)} / {juror.get('max_day_jury_count', 20)}"
    )
    click.echo()
    click.echo(f"  本周投票 (week votes):      {juror.get('week_vote_count', 0)}")
    click.echo(f"  本周评审 (week reviews):    {juror.get('week_review_count', 0)}")
    click.echo(f"  本周获赞 (week likes):      {juror.get('week_review_liked_count', 0)}")


@agora.command("reviews")
@click.argument("discussion_id")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_reviews(discussion_id: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """List review cases in an agora discussion."""
    items = fetch_reviews(discussion_id, limit=limit, max_items=max_items)

    if output_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return

    if not items:
        click.echo("No review cases found.")
        return

    for i, item in enumerate(items, 1):
        comment_content = item.get("comment_content", "") or "(no content)"
        author = item.get("comment_author", {})
        author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
        reason = item.get("reason", "") or "(no reason)"
        status = item.get("status", "")
        my_vote = item.get("my_vote", "")

        status_str = f" [{status}]" if status else ""
        vote_str = f" my_vote={my_vote}" if my_vote else ""

        click.echo(f"[{i}] {click.style(author_name, bold=True)}{status_str}{vote_str}")
        click.echo(f"    comment: {comment_content[:200]}")
        if reason:
            click.echo(f"    reason: {reason}")
        click.echo(f"    赞同: {item.get('affirmative_count', 0)}  反对: {item.get('dissenting_count', 0)}")
        click.echo()


@agora.command("detail")
@click.argument("discussion_id")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_detail(discussion_id: str, output_json: bool) -> None:
    """Show the reported comment detail for an agora discussion."""
    detail = fetch_comment_detail(discussion_id)

    if output_json:
        click.echo(json.dumps(detail, ensure_ascii=False, indent=2))
        return

    comment = detail.get("comment", {})
    author = comment.get("author", {})

    click.echo(f"Resource: {detail.get('resource_id', '?')}")
    click.echo(f"Reported comment ID: {detail.get('reported_comment_id', '?')}")
    click.echo()

    author_name = author.get("name", "unknown")
    click.echo(f"Author: {click.style(author_name, bold=True)}")
    if author.get("headline"):
        click.echo(f"  {author['headline']}")
    click.echo(f"  url_token: {author.get('url_token', '?')}")
    click.echo()

    click.echo(f"Comment (id={comment.get('id', '?')}):")
    click.echo(f"  {comment.get('content', '(no content)')}")
    click.echo()
    click.echo(
        f"created: {comment.get('created_time', '?')}  "
        f"votes: {comment.get('vote_count', 0)}  "
        f"child_comments: {comment.get('child_comment_count', 0)}"
    )
    click.echo(f"url: {comment.get('url', '?')}")
    click.echo()

    children = detail.get("child_comments", [])
    if children:
        click.echo(f"Child comments ({len(children)}):")
        for cc in children:
            cc_content = cc.get("content", "")[:150]
            cc_author = cc.get("author", {}).get("member", {}).get("name", "?")
            click.echo(f"  [{cc_author}] {cc_content}")


@agora.command("vote")
@click.argument("discussion_id")
@click.option(
    "--vote",
    "-v",
    "vote_type",
    required=True,
    type=click.Choice(VALID_VOTES),
    help="Vote choice",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_vote(discussion_id: str, vote_type: str, output_json: bool) -> None:
    """Cast a vote on an agora discussion (众裁投票).

    \b
    Vote types:
      affirmative  — 赞同 (agree the comment should be removed)
      abstain      — 弃权 (abstain)
      dissenting   — 反对 (dissent, the comment should stay)
    """
    try:
        result = vote_discussion(discussion_id, vote_type)
    except ValueError as e:
        raise click.BadParameter(str(e))

    if output_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    label = VOTE_LABELS.get(vote_type, vote_type)
    click.echo(f"Vote: {label}")
    click.echo(f"  赞同 (affirmative): {result['affirmative_count']}")
    click.echo(f"  反对 (dissenting):  {result['dissenting_count']}")
    if result["blind_test_wrong"]:
        click.echo(f"  {click.style('盲测错误 (blind test wrong)', fg='yellow')}")
        click.echo(f"  今日盲测错误: {result['blind_test_today_wrong_count']}")


# ── stats ─────────────────────────────────────────────────────────────────


@main.command("stats")
@click.argument("url")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-share", is_flag=True, default=False, help="Include share count via creator API (author only)")
@click.option(
    "--with-pv", is_flag=True, default=False, help="Include page views (阅读量) via creator API (author only)"
)
@click.option(
    "--with-show", is_flag=True, default=False, help="Include impressions (展现量) via creator API (author only)"
)
def stats(url: str, output_json: bool, with_share: bool, with_pv: bool, with_show: bool) -> None:
    """Show engagement summary (赞同/收藏/评论/喜欢) for a Zhihu post.

    URL can be an article, answer, or pin (想法).

    Use --with-share / --with-pv / --with-show to also fetch data from the
    creator analytics API. This only works when you are the author of the content.
    """
    try:
        result = get_item_stats(url, with_share=with_share, with_pv=with_pv, with_show=with_show)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if output_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    click.echo(f"  {click.style(result['title'], bold=True)}")
    click.echo(f"  {click.style(result['url'], dim=True)}")
    click.echo()
    click.echo(f"  赞同 (voteup):  {result['voteup_count']}")
    click.echo(f"  收藏 (favorite): {result['favlists_count']}")
    click.echo(f"  评论 (comment):  {result['comment_count']}")
    click.echo(f"  喜欢 (thanks):   {result['thanks_count']}")
    if with_pv:
        pv = result.get("pv")
        if pv is None:
            click.echo(f"  阅读 (pv):       {click.style('(not author / unavailable)', dim=True)}")
        else:
            click.echo(f"  阅读 (pv):       {pv}")
    if with_show:
        show = result.get("show")
        if show is None:
            click.echo(f"  展现 (show):     {click.style('(not author / unavailable)', dim=True)}")
        else:
            click.echo(f"  展现 (show):     {show}")
    if with_share:
        sc = result.get("share_count")
        if sc is None:
            click.echo(f"  分享 (share):    {click.style('(not author / unavailable)', dim=True)}")
        else:
            click.echo(f"  分享 (share):    {sc}")


# ── scrape ───────────────────────────────────────────────────────────────


@main.group()
def scrape() -> None:
    """Batch scrape user content lists to JSON files."""


@scrape.command("creations")
@click.option(
    "--output", "-o", default=str(get_data_dir() / "exports" / "all_assets_list.json"), help="Output JSON file"
)
def scrape_creations(output: str) -> None:
    """Fetch all user creation IDs (answers, articles, pins) → JSON."""
    from zhihu_cli.creator_tools.parse_content_datas import generate_assets_file

    generate_assets_file(Path(output))


def _generic_list_scrape(api_description: str, output_file: str) -> None:
    """Generic stdin-based list scraper. User pastes the API's cURL command."""
    import re

    headers = cache_manager.load_headers()
    if not headers:
        click.echo("No cached headers. Run 'zhihu auth paste' first.", err=True)
        raise SystemExit(1)

    click.echo(f"Paste the cURL command for the {api_description} API (Ctrl+D to finish):")
    try:
        curl_text = sys.stdin.read()
    except EOFError:
        curl_text = ""

    if not curl_text.strip():
        click.echo("Error: no input.", err=True)
        raise SystemExit(1)

    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    if not url_match:
        click.echo("Error: could not parse URL from cURL.", err=True)
        raise SystemExit(1)

    full_url = url_match.group(1)
    from urllib.parse import parse_qs, urlencode, urlparse

    parsed = urlparse(full_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for k, v in query.items():
        if isinstance(v, list) and len(v) == 1:
            query[k] = v[0]

    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    all_items: list[dict] = []
    limit_val = int(query.get("limit", 20))
    offset_val = int(query.get("offset", 0))
    is_end = False

    while not is_end:
        query["offset"] = offset_val
        request_url = f"{base_url}?{urlencode(query, doseq=True)}"
        try:
            resp = session.get(request_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                click.echo(f"Error: HTTP {resp.status_code}", err=True)
                break
            data = resp.json()
            items = data.get("data", [])
            all_items.extend(items)
            paging = data.get("paging", {})
            is_end = paging.get("is_end", True)
            click.echo(f"  Page: {len(items)} items (total: {len(all_items)})")
            if not is_end:
                next_url = paging.get("next", "")
                next_match = re.search(r"[?&]offset=(\d+)", next_url)
                if next_match:
                    offset_val = int(next_match.group(1))
                else:
                    offset_val += limit_val
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            break
        time.sleep(1.5)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)
    click.echo(f"Saved {len(all_items)} items to {output_file}")


@scrape.command("activities")
@click.option(
    "--output", "-o", default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"), help="Output JSON file"
)
def scrape_activities(output: str) -> None:
    """Fetch user activity feed → JSON. Requires pasting the activities API cURL."""
    _generic_list_scrape("activities", output)


@scrape.command("answers")
@click.option("--output", "-o", default=str(get_data_dir() / "exports" / "zhihu_answers.json"), help="Output JSON file")
def scrape_answers_list(output: str) -> None:
    """Fetch user's answer list → JSON. Requires pasting the answers API cURL."""
    _generic_list_scrape("answers list", output)


@scrape.command("articles")
@click.option(
    "--output", "-o", default=str(get_data_dir() / "exports" / "zhihu_articles.json"), help="Output JSON file"
)
def scrape_articles_list(output: str) -> None:
    """Fetch user's article list → JSON. Requires pasting the articles API cURL."""
    _generic_list_scrape("articles list", output)


# ── convert ──────────────────────────────────────────────────────────────


@main.group()
def convert() -> None:
    """Convert between JSON export formats."""


@convert.command("universal")
@click.argument("inputs", nargs=-1, required=True)
@click.option("--output", "-o", default=str(get_data_dir() / "exports" / "all_assets_list.json"), help="Output file")
@click.option("--type", "-t", "forced_type", default=None, help="Force a specific type")
def convert_universal(inputs: tuple[str, ...], output: str, forced_type: str | None) -> None:
    """Normalize multiple JSON export files into a unified assets list."""
    all_items: list[dict] = []
    for fpath in inputs:
        all_items.extend(load_json(fpath))

    if not all_items:
        click.echo("No valid items found.", err=True)
        raise SystemExit(1)

    converted = convert_items(all_items, forced_type)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    click.echo(f"Converted {len(converted)} items → {output}")


@convert.command("user-act")
@click.argument("input_file", default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"))
@click.argument("output_file", default=str(get_data_dir() / "exports" / "all_assets_list.json"))
def convert_user_act(input_file: str, output_file: str) -> None:
    """Convert zhihu_user_activities.json to all_assets_list.json format."""
    if not os.path.exists(input_file):
        click.echo(f"Error: file not found: {input_file}", err=True)
        raise SystemExit(1)

    converted = convert_items(load_json(input_file))

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    click.echo(f"Converted {len(converted)} items → {output_file}")


@convert.command("draft")
@click.argument("url")
@click.option("--output", "-o", default=None, help="Save Markdown to file instead of printing")
def convert_draft(url: str, output: str | None) -> None:
    """Convert the latest draft of a Zhihu question/answer to Markdown.

    Provide a Zhihu question URL (e.g. https://www.zhihu.com/question/123456)
    to fetch and convert your unpublished draft to Markdown.
    """
    try:
        metadata, markdown = draft_to_markdown(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(markdown)
        click.echo(f"Draft saved to {output}")
    else:
        click.echo(markdown)


# ── extensions ────────────────────────────────────────────────────────────

# Auto-discover and register extension command groups.
for _ext_mod in discover_extensions():
    _ext_mod.register_cli(main)


# ── tools ────────────────────────────────────────────────────────────────


@main.group()
def tools() -> None:
    """Analysis tools — creator analytics and NLP text analysis."""


@tools.group("creator")
def tools_creator() -> None:
    """Zhihu creator analytics."""


@tools_creator.command("fetch")
def creator_fetch() -> None:
    """Fetch incremental income data from Zhihu creator API."""
    from zhihu_cli.creator_tools.parse_zhihu_incomes import run_task

    run_task()


@tools_creator.command("monthly")
@click.option(
    "--file",
    "-f",
    "file_path",
    default=str(get_data_dir() / "exports" / "zhihu_income_report.json"),
    help="Income report JSON",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def creator_monthly(file_path: str, output_json: bool) -> None:
    """Print monthly income summary table."""
    from zhihu_cli.creator_tools.analyze_monthly_income import analyze_monthly_income, get_monthly_income_data

    if output_json:
        click.echo(json.dumps(get_monthly_income_data(file_path), ensure_ascii=False, indent=2))
        return
    analyze_monthly_income(file_path)


@tools_creator.command("plot")
def creator_plot() -> None:
    """Generate basic income plot (bar chart + EMA + trend)."""
    from zhihu_cli.creator_tools.plot_zhihu_incomes import plot_analysis

    plot_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'income_analysis.png'}")


@tools_creator.command("advanced")
def creator_advanced() -> None:
    """Generate advanced analysis plot (Bollinger + MACD)."""
    from zhihu_cli.creator_tools.plot_zhihu_incomes_advanced import plot_advanced_analysis

    plot_advanced_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'income_advanced_analysis.png'}")


@tools_creator.command("derivative")
def creator_derivative() -> None:
    """Generate derivative analysis plot (velocity, acceleration, jerk)."""
    from zhihu_cli.creator_tools.derivative_analysis import plot_derivative_analysis

    plot_derivative_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'derivative_analysis.png'}")


@tools_creator.command("weekday")
def creator_weekday() -> None:
    """Generate weekday income distribution plot."""
    from zhihu_cli.creator_tools.weekday_income_analysis import plot_weekday_analysis

    plot_weekday_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'weekday_income_analysis.png'}")


@tools_creator.command("metrics")
@click.option("--aggr", is_flag=True, help="Use aggregated endpoint (single datapoint per content)")
def creator_metrics(aggr: bool) -> None:
    """Fetch per-content daily metrics from Zhihu API."""
    from zhihu_cli.creator_tools.parse_content_datas import run_batch_daily_analysis

    run_batch_daily_analysis(use_aggr=aggr)


@tools_creator.command("growth")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def creator_growth(output_json: bool) -> None:
    """Fetch and display creator growth-level (创作分) data."""
    from zhihu_cli.creator_tools.growth_level import show_growth_level

    show_growth_level(json_output=output_json)


@tools_creator.command("score")
def creator_score() -> None:
    """Fetch incremental creator score detail (创作分明细) from Zhihu API."""
    from zhihu_cli.creator_tools.parse_score_detail import run_task

    run_task()


@tools_creator.command("income")
@click.option("--start-date", default=None, help="Start date (YYYY-MM-DD), default: 30 days ago")
@click.option("--end-date", default=None, help="End date (YYYY-MM-DD), default: today")
@click.option("--order-field", default="content_publish_at", help="Sort field (default: content_publish_at)")
@click.option("--order-sort", default="desc", help="Sort direction (default: desc)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def creator_income(
    start_date: str | None,
    end_date: str | None,
    order_field: str,
    order_sort: str,
    output_json: bool,
) -> None:
    """Fetch per-content income detail (单篇内容盐粒) from Zhihu API."""
    from zhihu_cli.creator_tools.parse_income_range import run_task

    run_task(
        start_date=start_date,
        end_date=end_date,
        order_field=order_field,
        order_sort=order_sort,
        json_output=output_json,
    )


@tools_creator.group("follower")
def tools_creator_follower() -> None:
    """Follower analytics (关注者分析)."""


@tools_creator_follower.command("fetch")
@click.option("--days", type=int, default=90, help="Number of days to fetch (default: 90)")
def creator_follower_fetch(days: int) -> None:
    """Fetch follower detail data from Zhihu API."""
    from zhihu_cli.creator_tools.parse_follower_detail import run_task

    run_task(days=days)


@tools_creator_follower.command("analysis")
def creator_follower_analysis() -> None:
    """Fetch follower profile/demographics (关注者画像) from Zhihu API."""
    from zhihu_cli.creator_tools.parse_follower_profile import run_task

    run_task()


@tools.group("nlp")
def tools_nlp() -> None:
    """NLP text analysis on downloaded Markdown files."""


@tools_nlp.command("count")
@click.option("--folder", default=str(get_data_dir() / "downloads" / "answers"), help="Folder with Markdown files")
@click.option("--no-code", is_flag=True, help="Exclude code blocks")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def nlp_count(folder: str, no_code: bool, output_json: bool) -> None:
    """Count words in downloaded Markdown files."""
    import numpy as np

    from zhihu_cli.nlp_tools.count_answer_words import count_words

    word_counts = []
    for filename in os.listdir(folder):
        if filename.endswith(".md"):
            word_counts.append(count_words(os.path.join(folder, filename), no_code=no_code))

    if not word_counts:
        if output_json:
            click.echo(json.dumps({"files": 0}, ensure_ascii=False, indent=2))
        else:
            click.echo("No markdown files found.")
        return

    wc = [int(x) for x in word_counts]
    if output_json:
        click.echo(
            json.dumps(
                {
                    "files": len(wc),
                    "mean": round(float(np.mean(wc)), 1),
                    "std": round(float(np.std(wc)), 1),
                    "p50": int(np.percentile(wc, 50)),
                    "p90": int(np.percentile(wc, 90)),
                    "max": int(max(wc)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    click.echo(f"Files: {len(word_counts)}")
    click.echo(f"Mean: {np.mean(word_counts):.0f}  Std: {np.std(word_counts):.0f}")
    click.echo(f"P50: {np.percentile(word_counts, 50):.0f}  P90: {np.percentile(word_counts, 90):.0f}")
    click.echo(f"Max: {max(word_counts)}")


@tools_nlp.command("wordcloud")
@click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
@click.option("--topk", type=int, default=200, help="Top K keywords")
@click.option("--only-print", is_flag=True, help="Only print keywords, skip image generation")
def nlp_wordcloud(source_dir: str, topk: int, only_print: bool) -> None:
    """Generate a word cloud from downloaded content."""
    from zhihu_cli.nlp_tools.wordcloud_generator import main as wc_main

    wc_main(topk_words=topk, source_dir=source_dir, only_print=only_print)


@tools_nlp.command("cluster")
@click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
@click.option("--output", "-o", default=str(get_data_dir() / "plots" / "zhihu_clusters.png"), help="Output image")
@click.option("--n-clusters", type=int, default=8, help="Number of clusters")
@click.option("--n-terms", type=int, default=10, help="Top terms per cluster")
@click.option("--mode", type=click.Choice(["pca", "tsne", "hybrid"]), default="pca", help="Dimensionality reduction")
@click.option("--evaluate-k", is_flag=True, help="Run elbow/silhouette analysis to help choose K")
def nlp_cluster(source_dir: str, output: str, n_clusters: int, n_terms: int, mode: str, evaluate_k: bool) -> None:
    """KMeans cluster visualization of downloaded content."""
    from zhihu_cli.nlp_tools.cluster_visualizer import (
        find_best_k,
        load_and_clean_data,
        process_clusters,
        visualize_with_plotly,
    )

    documents, file_names = load_and_clean_data(source_dir)
    if not documents:
        click.echo("No documents found.", err=True)
        return

    if evaluate_k:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
        X = vectorizer.fit_transform(documents)
        find_best_k(X, max_k=20)
        click.echo("Check the elbow/silhouette plot to choose K.")
        return

    X, labels, vectorizer, kmeans = process_clusters(documents, n_clusters)
    visualize_with_plotly(
        X, labels, file_names, vectorizer, kmeans, n_clusters, mode=mode, output_path=output, n_terms=n_terms
    )  # type: ignore[arg-type]
    click.echo(f"Saved {output}")


@tools_nlp.command("conetwork")
@click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
@click.option("--topk", type=int, default=80, help="Top N words to include in network")
@click.option("--window-size", type=int, default=5, help="Co-occurrence window size within documents")
@click.option("--min-edge-weight", type=int, default=3, help="Minimum co-occurrence count to show edge")
@click.option("--output", "-o", default=str(get_data_dir() / "plots" / "zhihu_conetwork.png"), help="Output image path")
def nlp_conetwork(source_dir: str, topk: int, window_size: int, min_edge_weight: int, output: str) -> None:
    """Word co-occurrence network visualization of downloaded content."""
    from zhihu_cli.nlp_tools.cooccurrence_network import main as conetwork_main

    conetwork_main(
        source_dir=source_dir,
        topk=topk,
        window_size=window_size,
        min_edge_weight=min_edge_weight,
        output=output,
    )


@tools_nlp.command("graph")
@click.option("--url-token", default=None, help="User url_token to analyze (auto-detects logged-in user if omitted)")
@click.option("--max-followees", type=int, default=200, help="Max followees to fetch")
@click.option("--max-followers", type=int, default=200, help="Max followers to fetch")
@click.option(
    "--output", "-o", default="", help="Output image path (default: ~/.zhihu-cli/plots/zhihu_social_graph.png)"
)
@click.option(
    "--layout",
    type=click.Choice(["spring", "kamada_kawai", "circular", "shell"]),
    default="spring",
    help="Graph layout algorithm",
)
@click.option("--no-viz", is_flag=True, help="Print statistics only, skip image generation")
@click.option("--depth", type=int, default=1, help="Graph depth: 1=ego-network, ≥2=recursively expand (default: 1)")
@click.option("--max-expand", type=int, default=20, help="Max nodes to expand per hop level (default: 20)")
@click.option("--max-per-node", type=int, default=50, help="Max followees fetched per expanded node (default: 50)")
def nlp_graph(
    url_token: str | None,
    max_followees: int,
    max_followers: int,
    output: str,
    layout: str,
    no_viz: bool,
    depth: int,
    max_expand: int,
    max_per_node: int,
) -> None:
    """Social graph visualization of following/follower relationships."""
    from zhihu_cli.nlp_tools.graph import main as graph_main

    graph_main(
        url_token=url_token,
        max_followees=max_followees,
        max_followers=max_followers,
        output=output,
        layout=layout,
        no_viz=no_viz,
        depth=depth,
        max_expand=max_expand,
        max_per_node=max_per_node,
    )


# ── entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()

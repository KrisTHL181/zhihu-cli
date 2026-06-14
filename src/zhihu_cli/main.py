"""zhihu CLI — unified entry point for all Zhihu operations."""

import json
import os
import sys
from pathlib import Path

import click

from zhihu_cli.content.download_contents import (
    ContentDownloader,
    download_media_files,
    sanitize_filename,
    save_article,
    save_pin,
)
from zhihu_cli.content.handlers import fmt_time, get_data_dir, get_type_and_id, get_user_agent, set_user_agent
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
    get_my_url_token,
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
from zhihu_cli.content.handlers.upload_image import to_visible_url, upload_image
from zhihu_cli.content.handlers.upvoter import fetch_upvoters
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.handlers.yanxuan import extract_url_token, fetch_yanxuan_segments, segments_to_text
from zhihu_cli.content.handlers.zvideo import get_best_video_url, scrape_zvideo
from zhihu_cli.content.universal_converter import convert_items, load_json
from zhihu_cli.content.utils.wait import wait
from zhihu_cli.extensions import discover_extensions
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_path,
    f_tag,
    f_title,
    f_url,
    file_saved,
    heading,
    info,
    item_index,
    print_json,
    section,
    stat,
    success,
    warning,
)

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
        error("Profile names starting with '_' are reserved for internal use.")
        raise SystemExit(1)

    echo("Paste cURL command (Ctrl+D to finish):")
    try:
        curl_text = sys.stdin.read()
    except EOFError:
        curl_text = ""

    if not curl_text.strip():
        error("No input detected.")
        raise SystemExit(1)

    import re

    headers: dict[str, str] = {}
    for h in re.findall(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", curl_text):
        if ":" in h:
            k, v = h.split(":", 1)
            if k.strip().lower() not in ("accept-encoding", "content-length"):
                headers[k.strip()] = v.strip()

    if not headers:
        error("Could not parse any headers from the input.")
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
        print_json(
            {
                "active_profile": active,
                "profiles": profiles,
                "headers_count": len(headers),
                "has_cookie": has_cookie,
                "username": username,
                "user_id": user_id,
                "url_token": url_token,
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
        else:
            warning("No Cookie header found.")
    else:
        error("No headers cached. Run 'zhihu auth paste' first.")


@auth.command("clear")
def auth_clear() -> None:
    """Remove cached headers."""
    cache_manager.save_headers({})
    success("Headers cache cleared.")


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
    success(f"User-Agent set to:\n{user_agent}")


@config_user_agent.command("show")
def config_ua_show() -> None:
    """Show the currently configured User-Agent."""
    ua = get_user_agent()
    if ua:
        echo(f"{f_label('Configured User-Agent:')}\n{ua}")
    else:
        info("No custom User-Agent configured (using per-profile default).")


@config_user_agent.command("clear")
def config_ua_clear() -> None:
    """Remove the custom User-Agent override."""
    set_user_agent(None)
    from zhihu_cli.content.handlers.requests import reload_session

    reload_session()
    success("Custom User-Agent cleared (now using per-profile default).")


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
    success(f"Default start date set to: {date_str}")


@config_start_date.command("show")
def config_sd_show() -> None:
    """Show the currently configured default start date."""
    date_str = cache_manager.get_start_date()
    echo(f"{f_label('Default start date:')} {date_str}")


@config_start_date.command("clear")
def config_sd_clear() -> None:
    """Reset the default start date to the built-in default (2026-01-16)."""
    cache_manager.set_start_date("2026-01-16")
    success("Default start date reset to: 2026-01-16")


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
        error("crank extension is not available (missing dependencies).")
        raise SystemExit(1)

    save_llm_config(api_base, api_key, model)
    success(f"LLM config saved:\n  {f_label('api_base:')} {api_base}\n  {f_label('model:')} {model}")


@config_crank_llm.command("show")
def config_llm_show() -> None:
    """Show the currently cached LLM configuration."""
    try:
        from zhihu_cli.extensions.crank.archiver import load_llm_config
    except ImportError:
        error("crank extension is not available (missing dependencies).")
        raise SystemExit(1)

    cfg = load_llm_config()
    if cfg:
        echo(f"{f_title('Cached LLM config:')}")
        for k, v in cfg.items():
            if k == "api_key" and v:
                v = v[:8] + "..." if len(v) > 8 else v
            echo(f"  {f_label(k + ':')} {v}")
    else:
        info("No cached LLM config found.")


@config_crank_llm.command("clear")
def config_llm_clear() -> None:
    """Remove the cached LLM configuration."""
    try:
        from zhihu_cli.extensions.crank.archiver import LLM_CONFIG_PATH
    except ImportError:
        error("crank extension is not available (missing dependencies).")
        raise SystemExit(1)

    if os.path.exists(LLM_CONFIG_PATH):
        os.remove(LLM_CONFIG_PATH)
        success("Cached LLM config removed.")
    else:
        info("No cached LLM config to remove.")


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
            print_json([])
        else:
            info("No profiles found. Use 'zhihu auth paste --profile <name>' to create one.")
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
        print_json(result)
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
        echo(f"  {f_bold(name)}{marker}  ({f_meta(status)})")


@profile.command("switch")
@click.argument("name")
def profile_switch(name: str) -> None:
    """Switch to a different profile."""
    try:
        cache_manager.switch_profile(name)
        reload_session()
        success(f"Switched to profile '{name}'.")
    except ValueError:
        error(f"Profile '{name}' does not exist. Use 'zhihu profile list' to see saved profiles.")
        raise SystemExit(1)


@profile.command("delete")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation")
def profile_delete(name: str, force: bool) -> None:
    """Delete a saved profile."""
    profiles = cache_manager.list_profiles()
    if name not in profiles:
        error(f"Profile '{name}' does not exist.")
        raise SystemExit(1)
    if name.startswith("_"):
        error(f"Cannot delete internal profile '{name}'.")
        raise SystemExit(1)

    if not force:
        click.confirm(f"Delete profile '{name}'?", abort=True)

    cache_manager.delete_profile(name)
    success(f"Deleted profile '{name}'.")


@profile.command("current")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def profile_current(output_json: bool) -> None:
    """Show the currently active profile."""
    active = cache_manager.get_active_profile()
    if output_json:
        print_json({"active_profile": active})
        return
    if active:
        echo(active)
    else:
        error("No active profile set.")


_LOGOUT_PROFILE = "_logout_"


@profile.command("logout")
def profile_logout() -> None:
    """Switch to an unauthenticated session (hidden profile).

    Switches the active profile to a hidden profile with no stored
    credentials. Use 'zhihu profile switch <name>' to log back in.
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
    echo("Logged out. Use 'zhihu profile switch <name>' to log back in.")


# ── download ─────────────────────────────────────────────────────────────


@main.group()
def download() -> None:
    """Download Zhihu content as Markdown files."""


@download.command("article")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "articles"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_article(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a single Zhihu article as Markdown."""
    metadata, markdown = scrape_article(url)
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        print_json({"metadata": metadata, "filepath": filepath})
        return
    echo(f"  {f_title(str(metadata.get('title', 'untitled')))}")
    file_saved(filepath)


@download.command("question")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "questions"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_question(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a Zhihu question and all its answers as Markdown."""
    q_meta, q_detail_md = scrape_question_data(url)
    os.makedirs(output_dir, exist_ok=True)

    title = sanitize_filename(q_meta.get("title", "untitled"))

    # Question detail
    if with_media:
        q_detail_md, _ = download_media_files(q_detail_md, output_dir)
    filepath = os.path.join(output_dir, f"{title}_question.md")[:200]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {q_meta['title']}\n\n{q_detail_md}\n")

    ans_dir = os.path.join(output_dir, f"{title}_answers")
    os.makedirs(ans_dir, exist_ok=True)
    count = 0
    for ans in scrape_answers(q_meta):
        count += 1
        content = ans["content"]
        if with_media:
            content, _ = download_media_files(content, ans_dir)
        afile = os.path.join(ans_dir, f"{count:04d}_{sanitize_filename(ans['author'])}.md")[:200]
        with open(afile, "w", encoding="utf-8") as f:
            f.write(f"# Answer by {ans['author']} (+{ans['vote']})\n\n{content}\n")

    if output_json:
        print_json({"metadata": q_meta, "filepath": filepath, "answers_count": count, "answers_dir": ans_dir})
        return

    echo(f"  {f_title('Question:')} {q_meta['title']}")
    file_saved(filepath)
    echo(f"  {f_num(count)} answers saved to {f_path(ans_dir)}")


@download.command("pin")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "pins"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_pin(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a single Zhihu pin as Markdown."""
    metadata, markdown = scrape_pin(url)
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        print_json({"metadata": metadata, "filepath": filepath})
        return
    echo(f"  {f_title('Pin')} by {f_name(str(metadata.get('author', 'unknown')))}")
    file_saved(filepath)


@download.command("video")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "videos"), help="Output directory")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--no-download-video", is_flag=True, default=False, help="Skip downloading the video file")
def download_video(url: str, output_dir: str, output_json: bool, no_download_video: bool) -> None:
    """Download a Zhihu zvideo and its metadata."""
    metadata, markdown = scrape_zvideo(url)
    filepath = _save_markdown(metadata, markdown, output_dir)

    video_path: str | None = None
    if not no_download_video:
        video_url = get_best_video_url(metadata)
        if video_url:
            os.makedirs(output_dir, exist_ok=True)
            title = sanitize_filename(metadata.get("title", "video"))
            ext = ".mp4"
            video_path = os.path.join(output_dir, f"{title}{ext}")[:200]
            info(f"Downloading video ({metadata.get('quality_tiers', [{}])[0].get('tier', 'best')} quality)...")
            try:
                resp = session.get(video_url, timeout=300, stream=True)
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                success(f"Video saved to {f_path(video_path)}")
            except Exception as e:
                warning(f"Video download failed: {e}")
                video_path = None

    if output_json:
        result: dict = {"metadata": metadata, "filepath": filepath}
        if video_path:
            result["video_path"] = video_path
        print_json(result)
        return

    echo(f"  {f_title(str(metadata.get('title', 'untitled')))}")
    file_saved(filepath)
    if video_path:
        file_saved(video_path)


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
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_batch_answers(
    input_file: str, output_dir: str, delay: float, no_cache_headers: bool, with_media: bool
) -> None:
    """Batch download all answers listed in an assets JSON file."""
    if not os.path.exists(input_file):
        error(f"file not found: {input_file}")
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://www.zhihu.com/answer/{a['id']}" for a in assets if a.get("type") == "answer"]
    if not urls:
        info("No answers found in the assets file.")
        return

    echo(f"  {f_label('Found')} {f_num(len(urls))} {f_dim('answers.')}")
    dl = ContentDownloader(output_dir=output_dir, with_media=with_media)
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
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_batch_articles(
    input_file: str, output_dir: str, delay: float, no_cache_headers: bool, with_media: bool
) -> None:
    """Batch download all articles listed in an assets JSON file."""
    if not os.path.exists(input_file):
        error(f"file not found: {input_file}")
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://zhuanlan.zhihu.com/p/{a['id']}" for a in assets if a.get("type") == "article"]
    if not urls:
        info("No articles found in the assets file.")
        return

    echo(f"  {f_label('Found')} {f_num(len(urls))} {f_dim('articles.')}")
    dl = ContentDownloader(output_dir=output_dir, with_media=with_media)
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
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_user(
    user: str,
    output_dir: str | None,
    delay: float,
    max_items: int | None,
    content_types: str,
    with_media: bool,
) -> None:
    """Download all answers, articles, and pins from a Zhihu user."""
    url_token = _extract_url_token(user)

    profile = fetch_member_profile(url_token)
    user_name = profile["name"] if profile else url_token
    echo(f"  {f_label('User:')} {f_name(user_name)} (url_token: {f_meta(url_token)})")

    if output_dir is None:
        base_dir = get_data_dir() / "downloads" / sanitize_filename(user_name)
    else:
        base_dir = Path(output_dir)

    downloaded: dict[str, int] = {"answers": 0, "articles": 0, "pins": 0}

    if content_types in ("answers", "all"):
        answers_dir = str(base_dir / "answers")
        info(f"\nFetching answers list for {user_name}...")
        answer_items = fetch_member_answers(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(answer_items))} {f_dim('answers. Downloading full content...')}")

        for i, item in enumerate(answer_items, 1):
            try:
                meta, md = scrape_answer_page(item["url"])
                save_meta = {
                    "title": meta.get("title", "untitled"),
                    "author": meta.get("author", "unknown"),
                    "created": meta.get("created", "unknown"),
                }
                filepath = save_article(item["url"], save_meta, md, answers_dir, with_media=with_media)
                echo(
                    f"  {item_index(i, len(answer_items))} {save_meta['title'][:50]} -> {f_path(os.path.basename(filepath))}"
                )
                downloaded["answers"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(answer_items))} Error: {e}")
            wait(delay)

    if content_types in ("articles", "all"):
        articles_dir = str(base_dir / "articles")
        info(f"\nFetching articles list for {user_name}...")
        article_items = fetch_member_articles(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(article_items))} {f_dim('articles. Downloading full content...')}")

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
                filepath = save_article(item["url"], save_meta, md, articles_dir, with_media=with_media)
                echo(
                    f"  {item_index(i, len(article_items))} {save_meta['title'][:50]} -> {f_path(os.path.basename(filepath))}"
                )
                downloaded["articles"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(article_items))} Error: {e}")
            wait(delay)

    if content_types in ("pins", "all"):
        pins_dir = str(base_dir / "pins")
        info(f"\nFetching pins list for {user_name}...")
        pin_items = fetch_member_pins(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(pin_items))} {f_dim('pins. Downloading full content...')}")

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
                filepath = save_pin(item["url"], save_meta, md, pins_dir, with_media=with_media)
                preview = (meta.get("excerpt", "") or "")[:30]
                echo(f"  {item_index(i, len(pin_items))} {preview} -> {f_path(os.path.basename(filepath))}")
                downloaded["pins"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(pin_items))} Error: {e}")
            wait(delay)

    section(f"Done! Downloaded from {f_name(user_name)}:")
    echo(f"  {f_label('Answers:')}  {f_num(downloaded['answers'])}")
    echo(f"  {f_label('Articles:')} {f_num(downloaded['articles'])}")
    echo(f"  {f_label('Pins:')}     {f_num(downloaded['pins'])}")
    echo(f"  {f_label('Output:')}   {f_path(str(base_dir))}")


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
        print_json({"question": q_meta, "detail_md": q_detail_md, "answers": answers})
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
        echo(question_md)
        for i, ans in enumerate(answers, 1):
            ans_id = ans["id"]
            author = ans["author"]
            vote = ans["vote"]
            comment = ans["comment"]
            favorite = ans["favorite"]
            echo(
                f"\n{f_bold('--- Answer')} #{i} "
                f"({f_label('ID:')} {ans_id}) "
                f"{f_bold('by')} {f_name(author)} "
                f"({f_green('+' + str(vote))} {f_meta('votes')}, "
                f"{f_num(comment)} {f_meta('comments')}, "
                f"{f_num(favorite)} {f_meta('favorites')}) ---"
            )
            echo(ans["content"])


@browse.command("answer")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_answer(url: str, reading_mode: bool, output_json: bool) -> None:
    """View a single Zhihu answer in the terminal."""
    metadata, markdown = scrape_answer_page(url)

    if output_json:
        print_json({"metadata": metadata, "content_md": markdown})
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
        echo(header)
        blank()
        echo(markdown)


@browse.command("article")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_article(url: str, reading_mode: bool, output_json: bool) -> None:
    """Read a Zhihu article in the terminal."""
    metadata, markdown = scrape_article(url)

    if output_json:
        print_json({"metadata": metadata, "content_md": markdown})
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
        echo(header)
        blank()
        echo(markdown)


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
        print_json(entries)
        return

    if not entries:
        info("No edit history found.")
        return

    for entry in entries:
        user = entry["user"] or "unknown"
        action = entry["action"]
        time_str = entry["time"]
        detail = entry["detail"]

        echo(f"  {f_meta(f'[{time_str}]')} {f_name(user)} {action}")
        if detail:
            echo(f"    {f_dim(detail[:200])}")
        blank()


@browse.command("comments")
@click.argument("url")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_comments(url: str, output_json: bool) -> None:
    """Print the comment tree for any Zhihu item."""
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    if output_json:
        print_json(fetch_comments(item_type, item_id))
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
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
        return

    for item in items:
        ttype = item.get("target_type", "?")
        title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
        author = item.get("author", {}).get("name", "unknown")
        url = item.get("url", "")
        excerpt = item.get("excerpt", "")

        echo(f"  {f_tag(ttype)} {f_bold(title[:120])}")
        if excerpt:
            echo(f"    {f_dim(f'preview: {excerpt[:200]}')}")
        echo(f"    {f_label('author=')}{f_name(author)}  {f_label('votes=')}{f_num(item.get('voteup_count', 0))}")
        if url:
            echo(f"    {f_label('link:')} {f_url(url)}")
        blank()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")


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
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
        return

    for i, item in enumerate(items, 1):
        title = item["title"] or "(no title)"
        heat = item["heat"]
        ttype = item["target_type"]
        url = item["url"]
        card_label = item["card_label"]
        answer_count = item["answer_count"]
        follower_count = item["follower_count"]

        label_str = f" {f_tag(card_label)}" if card_label else ""
        echo(f"  {item_index(i)} {f_num(heat)}{label_str}  {f_tag(ttype)}")
        echo(f"    {f_bold(title)}")
        excerpt = item["excerpt"]
        if excerpt:
            echo(f"    {f_dim(f'preview: {excerpt[:200]}')}")
        author = item["author"]
        if author and author != "anonymous":
            echo(f"    {f_label('author:')} {f_name(author)}")
        if answer_count or follower_count:
            parts = []
            if answer_count:
                parts.append(f"{f_num(answer_count)} {f_dim('answers')}")
            if follower_count:
                parts.append(f"{f_num(follower_count)} {f_dim('followers')}")
            echo(f"    {'  '.join(parts)}")
        if url:
            echo(f"    {f_url(url)}")
        blank()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")

    if not items:
        info("No hot items found. Try logging in first: zhihu auth login")


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
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
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

        merge_str = f" (+{f_num(merge - 1)})" if merge > 1 else ""

        echo(f"  {item_index(i)}{marker} {f_name(actor)} {verb}{merge_str}  ({f_tag(rtype)}: {target_text})")
        comment = item["comment_text"]
        if comment:
            echo(f"    {f_dim(f'> {comment}')}")
        if target_link:
            echo(f"    {f_url(target_link)}")
        echo(f"    {f_meta(time_str)}")
        blank()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")

    if not items:
        info("No notifications found. Try logging in first: zhihu auth login")


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
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
        return

    for i, item in enumerate(items, 1):
        ctype = item["content_type"]
        title = item["title"] or "(no title)"
        author = item["author_name"]
        summary_text = item["summary"]
        stats = item["stats_text"]
        url = item["url"]
        read_time = item["read_time"]

        echo(f"  {item_index(i)} {f_tag(ctype)} {f_bold(title[:120])}")
        if author:
            echo(f"    {f_label('author:')} {f_name(author)}")
        if summary_text:
            echo(f"    {f_dim(summary_text[:200])}")
        if stats:
            echo(f"    {f_meta(stats)}")
        if url:
            echo(f"    {f_url(url)}")
        echo(f"    {f_label('read:')} {f_meta(read_time)}")
        blank()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")

    if not items:
        info("No read history found. Try logging in first: zhihu auth login")


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
        print_json({"meta": meta, "segments": segments})
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "segments": segments}, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(segments)} segments to {output}")
        return

    if not segments:
        info("No content found for this yanxuan item.")
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
        echo(full_text)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(full_text)
        success(f"Saved {len(segments)} segments to {output}")


@browse.command("upvoters")
@click.argument("url")
@click.option("--limit", "-n", type=int, default=20, help="Items per page (default: 20, max: 20)")
@click.option("--max", "-m", "max_items", type=int, default=None, help="Max total items (default: fetch all)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def browse_upvoters(url: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """List users who upvoted an answer or article.

    \b
    URL can be an answer or article URL.
    Examples:
      zhihu browse upvoters https://www.zhihu.com/question/123/answer/456
      zhihu browse upvoters https://zhuanlan.zhihu.com/p/123456
    """
    item_type, item_id = _parse_item_url(url)

    if item_type not in ("answers", "articles"):
        error(f"Upvoters are only available for answers and articles. Got: {item_type}")
        raise SystemExit(1)

    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)

    info(f"Fetching upvoters for {item_type} {item_id}...")
    items = fetch_upvoters(item_type, item_id, limit=limit, max_items=max_items)

    if output_json:
        print_json(items)
        return

    if not items:
        info("No upvoters found.")
        return

    for i, u in enumerate(items, 1):
        name = u["name"] or u["url_token"]
        endorse = u.get("relationship_endorse", "")
        influence = u.get("zhihu_influence", "")
        headline = u.get("headline", "")

        echo(f"  {f_bold(f'{i}.')} {f_name(name)}")
        if headline:
            echo(f"     {f_dim(headline[:100])}")
        if influence:
            echo(f"     {f_meta(influence)}")
        if endorse:
            echo(f"     {f_dim(endorse)}")
        echo(f"     {f_url(u['url'])}")
        echo(
            f"     {f_label('followers:')} {f_num(u['follower_count'])}  "
            f"{f_label('upvotes given:')} {f_num(u['member_upvote_cnt'])}"
        )
        if i < len(items):
            blank()

    echo(f"  {f_dim(f'── {len(items)} upvoters')}")


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
            mutual = f" {f_green('[互关]')}" if (is_followed and is_following) else ""
            f_cnt = item.get("follower_count", 0)
            a_cnt = item.get("answer_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('answers:')} {f_num(a_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
            echo(f"  {item_index(i)} {f_bold(name)}{mutual}")
            if headline:
                echo(f"    {f_dim(headline[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "topic":
            name = item.get("name", "")
            intro = item.get("introduction", "") or item.get("excerpt", "")
            f_cnt = item.get("followers_count", 0)
            q_cnt = item.get("questions_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('questions:')} {f_num(q_cnt)}"
            echo(f"  {item_index(i)} {f_bold(name)} {f_tag('topic')}")
            if intro:
                echo(f"    {f_dim(intro[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "question":
            title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            ctime = item.get("created_time", "")
            stats = f"{f_label('answers:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}  {f_label('created:')} {f_meta(ctime)}"
            echo(f"  {item_index(i)} {f_bold(title[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "column":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "") or item.get("excerpt", "")
            creator = item.get("creator", "")
            f_cnt = item.get("followers_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
            echo(f"  {item_index(i)} {f_bold(title)} {f_tag('column')}")
            if creator:
                echo(f"    {f_name(creator)}")
            if desc:
                echo(f"    {f_dim(desc[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "collection":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "")
            creator_name = item.get("creator_name", "")
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            stats = f"{f_label('items:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}"
            echo(f"  {item_index(i)} {f_bold(title)} {f_tag('collection')}")
            if creator_name:
                echo(f"    {f_label('by')} {f_name(creator_name)}")
            if desc:
                echo(f"    {f_dim(desc[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        blank()

    if items:
        total_str = f"/{totals}" if totals else ""
        echo(f"  {f_dim(f'── {len(items)}{total_str} items')}")


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
    info(f"Fetching {label} for {token}...")
    items = fetch_fn(token, limit=limit, max_items=max_items)

    if output_json:
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
        return

    if not items:
        info(f"No {label} found.")
        return

    _display_following_items(items)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")


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
    stat(label, value)


def _print_content_item(item: dict, show_type: bool = False) -> None:
    """Print a single content item in a compact format."""
    ttype = item.get("type", "")
    type_label = f"{f_tag(ttype)} " if show_type else ""
    title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
    created = item.get("created_time", "")
    votes = item.get("voteup_count", 0)
    comments = item.get("comment_count", 0)

    parts = [f_meta(created)]
    if votes:
        parts.append(f_green(f"+{votes}"))
    if comments:
        parts.append(f"{f_num(comments)} {f_dim('comments')}")
    if "answer_count" in item and item["answer_count"]:
        parts.append(f"{f_num(item['answer_count'])} {f_dim('answers')}")
    if "follower_count" in item and item["follower_count"]:
        parts.append(f"{f_num(item['follower_count'])} {f_dim('followers')}")

    echo(f"  {type_label}{f_bold(title[:100])}")
    echo(f"  {f_dim('  '.join(parts))}")
    echo(f"  {f_url(item.get('url', ''))}")
    blank()


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
        echo(f"  {f_label('Profile:')} {f_url(f'https://www.zhihu.com/people/{url_token}')}")
        blank()
    except ImportError:
        echo(f"\n{f_bold(profile.get('name', 'Unknown'))}")
        if headline := profile.get("headline"):
            echo(f"  {f_dim(headline)}")
        echo(f"  {f_url(f'https://www.zhihu.com/people/{profile.get("url_token", "")}')}")
        blank()
        _print_stat("Followers", profile.get("follower_count", 0))
        _print_stat("Following", profile.get("following_count", 0))
        _print_stat("Answers", profile.get("answer_count", 0))
        _print_stat("Articles", profile.get("articles_count", 0))
        _print_stat("Pins", profile.get("pins_count", 0))
        _print_stat("Questions", profile.get("question_count", 0))
        _print_stat("Upvotes received", profile.get("voteup_count", 0))
        blank()


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
        heading(f"Recent {len(items)} {section_title}")
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

    info(f"Fetching profile for {token}...")
    profile = fetch_member_profile(token)
    if profile is None:
        error(f"Could not fetch profile for '{token}'. Check the token and try again.")
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
        print_json(result)
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
    info(f"Fetching answers for {token}...")
    items = fetch_member_answers(token, max_items=limit)
    if output_json:
        print_json(items)
        return
    if not items:
        info("No answers found.")
        return
    for item in items:
        _print_content_item(item)
    echo(f"  {f_dim(f'── {len(items)} answers total')}")


@people.command("articles")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_articles(url_token: str, limit: int, output_json: bool) -> None:
    """List a user's articles."""
    token = _extract_url_token(url_token)
    info(f"Fetching articles for {token}...")
    items = fetch_member_articles(token, max_items=limit)
    if output_json:
        print_json(items)
        return
    if not items:
        info("No articles found.")
        return
    for item in items:
        _print_content_item(item)
    echo(f"  {f_dim(f'── {len(items)} articles total')}")


@people.command("pins")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_pins(url_token: str, limit: int, output_json: bool) -> None:
    """List a user's pins (想法)."""
    token = _extract_url_token(url_token)
    info(f"Fetching pins for {token}...")
    items = fetch_member_pins(token, max_items=limit)
    if output_json:
        print_json(items)
        return
    if not items:
        info("No pins found.")
        return
    for item in items:
        t = f_meta(item.get("created_time", ""))
        content = item.get("content_text", "") or item.get("excerpt", "")
        v = item.get("voteup_count", 0)
        c = item.get("comment_count", 0)
        echo(f"  {f_dim(content[:120])}")
        echo(f"  {t}  {f_green(f'+{v}')}  {f_num(c)} {f_dim('comments')}")
        echo(f"  {f_url(item.get('url', ''))}")
        blank()
    echo(f"  {f_dim(f'── {len(items)} pins total')}")


@people.command("questions")
@click.argument("url_token")
@click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def people_questions(url_token: str, limit: int, output_json: bool) -> None:
    """List questions asked by a user."""
    token = _extract_url_token(url_token)
    info(f"Fetching questions for {token}...")
    items = fetch_member_questions(token, max_items=limit)
    if output_json:
        print_json(items)
        return
    if not items:
        info("No questions found (this endpoint may not be available).")
        return
    for item in items:
        _print_content_item(item)
    echo(f"  {f_dim(f'── {len(items)} questions total')}")


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
        print_json(items)
        return
    for i, q in enumerate(items, 1):
        echo(f"  {item_index(i)} {f_bold(q['title'])}")
        echo(f"    {f_num(q['answer_count'])} {f_dim('answers')}  {f_num(q['follower_count'])} {f_dim('followers')}")
        echo(f"    {f_label('updated:')} {f_meta(q['updated_time'])}")
        echo(f"    {f_url(q['url'])}")
        blank()
    if not items:
        info(f"No questions found for '{query}'.")


@search.command("article")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_article_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu articles by keyword."""
    items = search_articles(query, limit=limit, max_items=max_items)
    if output_json:
        print_json(items)
        return
    for i, a in enumerate(items, 1):
        echo(f"  {item_index(i)} {f_bold(a['title'])}")
        echo(f"    {f_label('by')} {f_name(a['author']['name'])}  {f_num(a['voteup_count'])} {f_dim('upvotes')}")
        if a["excerpt"]:
            echo(f"    {f_dim(a['excerpt'][:120])}")
        echo(f"    {f_meta(a['created_time'])}")
        echo(f"    {f_url(a['url'])}")
        blank()
    if not items:
        info(f"No articles found for '{query}'.")


@search.command("user")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_user_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu users by keyword."""
    items = search_users(query, limit=limit, max_items=max_items)
    if output_json:
        print_json(items)
        return
    for i, u in enumerate(items, 1):
        echo(f"  {item_index(i)} {f_name(u['name'])}  ({f_dim(u['gender'])})")
        if u["headline"]:
            echo(f"    {f_dim(u['headline'])}")
        echo(
            f"    {f_num(u['follower_count'])} {f_dim('followers')}  {f_num(u['answer_count'])} {f_dim('answers')}  {f_num(u['articles_count'])} {f_dim('articles')}"
        )
        echo(f"    {f_url(u['url'])}")
        blank()
    if not items:
        info(f"No users found for '{query}'.")


@search.command("topic")
@click.argument("query")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def search_topic_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """Search Zhihu topics by keyword."""
    items = search_topics(query, limit=limit, max_items=max_items)
    if output_json:
        print_json(items)
        return
    for i, t in enumerate(items, 1):
        echo(f"  {item_index(i)} {f_bold(t['name'])}")
        intro = t["introduction"] or t["excerpt"]
        if intro:
            echo(f"    {f_dim(intro[:120])}")
        echo(
            f"    {f_num(t['questions_count'])} {f_dim('questions')}  {f_num(t['followers_count'])} {f_dim('followers')}"
        )
        echo(f"    {f_url(t['url'])}")
        blank()
    if not items:
        info(f"No topics found for '{query}'.")


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
        echo(upvote_answer(_resolve_answer_id(item_id)))
    elif item_type in ("questions", "question"):
        echo(upvote_question(item_id))
    else:
        upvote_answer(url_or_id)  # treat as raw ID


@interact_vote.command("neutral")
@click.argument("url_or_id")
def vote_neutral(url_or_id: str) -> None:
    """Remove vote from an answer."""
    item_type, item_id = _parse_item_url_safe(url_or_id)
    echo(neutral_answer(_resolve_answer_id(item_id) if item_type else url_or_id))


@interact_vote.command("down")
@click.argument("url_or_id")
def vote_down(url_or_id: str) -> None:
    """Downvote an answer or question."""
    item_type, item_id = _parse_item_url_safe(url_or_id)
    if item_type in ("answers", "answer"):
        echo(downvote_answer(_resolve_answer_id(item_id)))
    elif item_type in ("questions", "question"):
        echo(downvote_question(item_id))
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
    echo(thank_answer(answer_id))


@interact_thank.command("remove")
@click.argument("answer_id")
def thank_remove(answer_id: str) -> None:
    """Remove thanks from an answer."""
    echo(unthank_answer(answer_id))


@interact.group("follow")
def interact_follow() -> None:
    """Follow or unfollow users and questions."""


@interact_follow.command("user")
@click.argument("user_id")
def follow_user(user_id: str) -> None:
    """Follow a user by URL token or ID."""
    echo(follow(user_id))


@interact_follow.command("question")
@click.argument("question_id")
def follow_question_cmd(question_id: str) -> None:
    """Follow a question."""
    echo(follow_question(question_id))


@interact_follow.command("unfollow-user")
@click.argument("user_id")
def unfollow_user(user_id: str) -> None:
    """Unfollow a user."""
    echo(unfollow(user_id))


@interact_follow.command("unfollow-question")
@click.argument("question_id")
def unfollow_question_cmd(question_id: str) -> None:
    """Unfollow a question."""
    echo(unfollow_question(question_id))


@interact.group("block")
def interact_block() -> None:
    """Block or unblock users."""


@interact_block.command("add")
@click.argument("user_id")
def block_user(user_id: str) -> None:
    """Block a user."""
    block(user_id)
    success(f"Blocked {user_id}")


@interact_block.command("remove")
@click.argument("user_id")
def block_remove(user_id: str) -> None:
    """Unblock a user."""
    unblock(user_id)
    success(f"Unblocked {user_id}")


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
    echo(resp)


@interact_comment.command("delete")
@click.argument("comment_id")
def comment_delete(comment_id: str) -> None:
    """Delete a comment by ID."""
    delete_comment(comment_id)
    success(f"Deleted comment {comment_id}")


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
        echo(add_to_collection(item_type, item_id, collection_id))
    else:
        echo(collect(item_type, item_id))


@interact_collect.command("remove")
@click.argument("url")
@click.option("--collection", "-c", "collection_id", required=True, help="Target collection ID")
def collect_remove(url: str, collection_id: str) -> None:
    """Remove an item from a collection."""
    item_type, item_id = _parse_item_url(url)
    echo(delete_to_collection(item_type, item_id, collection_id))


@interact_collect.command("create")
@click.argument("title")
@click.option("--description", "-d", default="", help="Collection description")
@click.option("--public/--private", default=True, help="Visibility")
def collect_create(title: str, description: str, public: bool) -> None:
    """Create a new collection."""
    echo(create_collection(title, description, public))


@interact_collect.command("delete")
@click.argument("collection_id")
def collect_delete(collection_id: str) -> None:
    """Delete a collection."""
    echo(delete_collection(collection_id))


@interact_collect.command("list")
@click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
def collect_list(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
    """List your collections."""
    token = _resolve_following_token(url_token)
    info(f"Fetching collections for {token}...")

    items = list_collections(token, limit=limit, max_items=max_items)

    if output_json:
        print_json(items)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")
        return

    if not items:
        info("No collections found.")
        return

    _display_following_items(items)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {len(items)} items to {output}")


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
        print_json(data)
        return

    reasons = flatten_reasons(data)
    if not reasons:
        info(f"No report reasons found for type '{object_type}'.")
        return

    heading(f"Report reasons for '{object_type}'")
    current_category = None
    for r in reasons:
        if r["category"] and r["category"] != current_category:
            current_category = r["category"]
            echo(f"  {f_tag(current_category)}")
        label = f"    {r['id']} — {r['text']}" if r["category"] else f"  {r['id']} — {r['text']}"
        echo(label)


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
        print_json(resp)
    else:
        if resp.get("is_reported") or (isinstance(resp, dict) and resp.get("success", True)):
            success(f"Report submitted for {f_tag(item_type)} {item_id}")
            echo(f"  {f_label('Reason:')} {reason_id}")
            if custom_reason:
                echo(f"  {f_label('Detail:')} {custom_reason}")
        else:
            error(f"Report failed: {json.dumps(resp, ensure_ascii=False)}")


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
        error("Empty content.")
        raise SystemExit(1)
    resp = publish_answer(question_id, content)
    print_json(resp)


@publish.command("modify-answer")
@click.argument("answer_id")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_modify_answer(answer_id: str, file: str | None) -> None:
    """Modify an existing answer."""
    content = _read_content(file)
    if not content.strip():
        error("Empty content.")
        raise SystemExit(1)
    resp = modify_answer(answer_id, content)
    print_json(resp)


@publish.command("article")
@click.argument("title")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_article_cmd(title: str, file: str | None) -> None:
    """Publish a new article. Reads Markdown from file or stdin."""
    content = _read_content(file)
    if not content.strip():
        error("Empty content.")
        raise SystemExit(1)
    resp = publish_article(title, content)
    print_json(resp)


@publish.command("modify-article")
@click.argument("article_id")
@click.argument("title")
@click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
def publish_modify_article(article_id: str, title: str, file: str | None) -> None:
    """Modify an existing article."""
    content = _read_content(file)
    if not content.strip():
        error("Empty content.")
        raise SystemExit(1)
    resp = modify_article(article_id, title, content)
    print_json(resp)


@publish.command("upload-image")
@click.argument("file_path")
@click.option(
    "--source",
    "-s",
    default="article",
    help="Upload context: article (default), pin, answer, question",
)
def publish_upload_image(file_path: str, source: str) -> None:
    """Upload an image to Zhihu. Outputs the uploaded image URL."""
    try:
        img_info = upload_image(file_path, source=source)
        echo(img_info["src"])
        visible = to_visible_url(img_info.get("original_src", img_info["src"]))
        echo(f_green(f"Visible URL: {visible}"))
    except FileNotFoundError as e:
        error(f"{e}")
        raise SystemExit(1)
    except RuntimeError as e:
        error(f"{e}")
        raise SystemExit(1)


# ── chat ─────────────────────────────────────────────────────────────────


@main.group()
def chat() -> None:
    """Read inbox, view chat history, send messages."""


@chat.command("inbox")
@click.option("--limit", "-n", type=int, default=0, help="Max threads to fetch (0 = all pages)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def chat_inbox(limit: int, output_json: bool) -> None:
    """List recent conversations (paginated — walks all pages by default)."""
    messages, total_unread = get_inbox(limit=limit)
    if output_json:
        print_json(messages)
        return
    if not messages:
        info("Inbox is empty.")
        return
    echo(
        f"  {f_label('Total unread threads:')} {f_num(total_unread)}  {f_label('Showing')} {f_num(len(messages))} {f_dim('threads')}"
    )
    blank()
    for msg in messages:
        unread = msg["unread_count"]
        echo(f"  {f_tag(f'{unread} unread')} {f_name(msg['from'])}")
        echo(f"    {f_dim(msg['snippet'][:80])}")
        echo(
            f"    {f_label('id=')}{msg['id']}  {f_label('token=')}{msg['url_token']}  {f_label('time=')}{f_meta(msg['updated_time'])}"
        )
        blank()


@chat.command("history")
@click.argument("chat_id")
@click.option("--limit", "-n", type=int, default=50, help="Max messages to fetch")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def chat_history(chat_id: str, limit: int, output_json: bool) -> None:
    """Read messages from a chat conversation."""
    if output_json:
        msgs = list(iter_chat_history(chat_id, limit=limit))
        print_json(msgs)
        return
    for msg in iter_chat_history(chat_id, limit=limit):
        t = msg["time"]
        s = msg["sender"]
        echo(f"  {f_meta(f'[{t}]')}{f_name(s)}: {msg['content']}")


@chat.command("send")
@click.argument("user_id")
@click.argument("content")
def chat_send(user_id: str, content: str) -> None:
    """Send a text message to a user."""
    resp = send_text_message(user_id, content)
    echo(resp)


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
    info(f"Connecting to Zhihu MQTT ({topic})...")
    listener = ZhihuMessageListener(url_token, topic_str, incognito=incognito)
    echo("Listening — press Ctrl+C to stop.")
    try:
        listener.start()
    except KeyboardInterrupt:
        echo("\nStopped.")


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
        print_json(data)
        return

    juror = data.get("juror_info", {})

    # Show juror status header
    if juror.get("is_juror"):
        today = juror.get("today_jury_count", 0)
        max_day = juror.get("max_day_jury_count", 20)
        remaining = max(0, max_day - today)
        echo(
            f"  {f_title('众裁官')} | {f_label('总投票:')} {f_num(juror.get('vote_count', 0))} | {f_label('今日:')} {f_num(today)}/{f_num(max_day)} ({f_label('剩余')} {f_num(remaining)})"
        )
    else:
        warning("你尚不是众裁官")

    disc = data.get("current_discussion")
    if not disc:
        disc_id = data.get("discussion_id", "")
        if disc_id:
            section(f"Discussion ID: {disc_id}")
            info("Discussion data not in initialData. Try fetching details with 'zhihu agora detail {disc_id}'.")
        else:
            section("No pending discussions. Check back later!")
        return

    blank()

    # Report reason
    reason = disc.get("report_reason", "")
    note = disc.get("report_note", "")
    echo(f"  {click.style(f'举报理由: {reason}', fg='red', bold=True)}")
    if note:
        echo(f"    {note}")
    blank()

    # The reported comment
    comment = disc.get("comment", {})
    _print_agora_comment(comment, disc.get("reported_user", ""))
    blank()

    # Origin context
    origin_title = disc.get("origin_title", "")
    origin_url = disc.get("origin_url", "")
    if origin_title:
        echo(f"  {f_label('评论所在内容:')} {f_bold(origin_title)}")
    if origin_url:
        echo(f"    {f_url(origin_url)}")
    blank()

    # Status
    status = disc.get("status", "")
    my_vote = disc.get("my_vote", "")
    status_str = f"状态: {status}"
    if my_vote:
        status_str += f"  我的投票: {my_vote}"
    echo(f"  {f_dim(status_str)}")

    if not my_vote and status == "Voting":
        blank()
        echo(
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

    reported_str = f" ({reported_user})" if reported_user else ""
    echo(f"  {f_label('被举报评论')} — {f_bold(author_name)}{reported_str}")
    if headline:
        echo(f"    {f_dim(headline)}")
    blank()
    echo(f"    {content}")
    blank()
    echo(f"    {f_label('赞同:')} {f_num(votes)}  {f_label('时间:')} {f_meta(fmt_time(created))}  {f_url(url)}")


@agora.command("me")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_me(output_json: bool) -> None:
    """Show your agora (众裁) juror status and statistics."""
    data = fetch_agora_me()

    if output_json:
        print_json(data)
        return

    juror = data.get("juror_info", {})

    if not data.get("is_juror"):
        info("You are not a juror (众裁官).")
        return

    echo(f"  {f_green(f_bold('众裁官 (Juror)'))}")
    blank()
    echo(f"  {f_label('总投票 (total votes):')}      {f_num(juror.get('vote_count', 0))}")
    echo(f"  {f_label('总评审 (total reviews):')}     {f_num(juror.get('review_count', 0))}")
    echo(f"  {f_label('评审获赞 (review likes):')}    {f_num(juror.get('review_liked_count', 0))}")
    blank()
    echo(
        f"  {f_label('今日已裁 (today judged):')}    {f_num(juror.get('today_jury_count', 0))} / {f_num(juror.get('max_day_jury_count', 20))}"
    )
    blank()
    echo(f"  {f_label('本周投票 (week votes):')}      {f_num(juror.get('week_vote_count', 0))}")
    echo(f"  {f_label('本周评审 (week reviews):')}    {f_num(juror.get('week_review_count', 0))}")
    echo(f"  {f_label('本周获赞 (week likes):')}      {f_num(juror.get('week_review_liked_count', 0))}")


@agora.command("reviews")
@click.argument("discussion_id")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_reviews(discussion_id: str, limit: int, max_items: int | None, output_json: bool) -> None:
    """List review cases in an agora discussion."""
    items = fetch_reviews(discussion_id, limit=limit, max_items=max_items)

    if output_json:
        print_json(items)
        return

    if not items:
        info("No review cases found.")
        return

    for i, item in enumerate(items, 1):
        comment_content = item.get("comment_content", "") or "(no content)"
        author = item.get("comment_author", {})
        author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
        reason = item.get("reason", "") or "(no reason)"
        status = item.get("status", "")
        my_vote = item.get("my_vote", "")

        status_str = f" {f_tag(status)}" if status else ""
        vote_str = f" {f_label('my_vote=')}{my_vote}" if my_vote else ""

        echo(f"  {item_index(i)} {f_bold(author_name)}{status_str}{vote_str}")
        echo(f"    {f_label('comment:')} {comment_content[:200]}")
        if reason:
            echo(f"    {f_label('reason:')} {reason}")
        echo(
            f"    {f_label('赞同:')} {f_num(item.get('affirmative_count', 0))}  {f_label('反对:')} {f_num(item.get('dissenting_count', 0))}"
        )
        blank()


@agora.command("detail")
@click.argument("discussion_id")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def agora_detail(discussion_id: str, output_json: bool) -> None:
    """Show the reported comment detail for an agora discussion."""
    detail = fetch_comment_detail(discussion_id)

    if output_json:
        print_json(detail)
        return

    comment = detail.get("comment", {})
    author = comment.get("author", {})

    echo(f"  {f_label('Resource:')} {detail.get('resource_id', '?')}")
    echo(f"  {f_label('Reported comment ID:')} {detail.get('reported_comment_id', '?')}")
    blank()

    author_name = author.get("name", "unknown")
    echo(f"  {f_label('Author:')} {f_bold(author_name)}")
    if author.get("headline"):
        echo(f"    {author['headline']}")
    echo(f"    {f_label('url_token:')} {author.get('url_token', '?')}")
    blank()

    cid = comment.get("id", "?")
    echo(f"  {f_label(f'Comment (id={cid}):')}")
    echo(f"    {comment.get('content', '(no content)')}")
    blank()
    echo(
        f"    {f_label('created:')} {f_meta(str(comment.get('created_time', '?')))}  "
        f"{f_label('votes:')} {f_num(comment.get('vote_count', 0))}  "
        f"{f_label('child_comments:')} {f_num(comment.get('child_comment_count', 0))}"
    )
    echo(f"    {f_label('url:')} {f_url(str(comment.get('url', '?')))}")
    blank()

    children = detail.get("child_comments", [])
    if children:
        echo(f"  {f_label(f'Child comments ({len(children)}):')}")
        for cc in children:
            cc_content = cc.get("content", "")[:150]
            cc_author = cc.get("author", {}).get("member", {}).get("name", "?")
            echo(f"    [{f_name(cc_author)}] {cc_content}")


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
        print_json(result)
        return

    label = VOTE_LABELS.get(vote_type, vote_type)
    echo(f"  {f_label('Vote:')} {label}")
    echo(f"  {f_label('赞同 (affirmative):')} {f_num(result['affirmative_count'])}")
    echo(f"  {f_label('反对 (dissenting):')}  {f_num(result['dissenting_count'])}")
    if result["blind_test_wrong"]:
        warning("盲测错误 (blind test wrong)")
        echo(f"  {f_label('今日盲测错误:')} {f_num(result['blind_test_today_wrong_count'])}")


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
        error(f"{e}")
        raise SystemExit(1)

    if output_json:
        print_json(result)
        return

    echo(f"  {f_bold(result['title'])}")
    echo(f"  {f_url(result['url'])}")
    blank()
    echo(f"  {f_label('赞同 (voteup):')}  {f_num(result['voteup_count'])}")
    echo(f"  {f_label('收藏 (favorite):')} {f_num(result['favlists_count'])}")
    echo(f"  {f_label('评论 (comment):')}  {f_num(result['comment_count'])}")
    echo(f"  {f_label('喜欢 (thanks):')}   {f_num(result['thanks_count'])}")
    if with_pv:
        pv = result.get("pv")
        if pv is None:
            echo(f"  {f_label('阅读 (pv):')}       {f_dim('(not author / unavailable)')}")
        else:
            echo(f"  {f_label('阅读 (pv):')}       {f_num(pv)}")
    if with_show:
        show = result.get("show")
        if show is None:
            echo(f"  {f_label('展现 (show):')}     {f_dim('(not author / unavailable)')}")
        else:
            echo(f"  {f_label('展现 (show):')}     {f_num(show)}")
    if with_share:
        sc = result.get("share_count")
        if sc is None:
            echo(f"  {f_label('分享 (share):')}    {f_dim('(not author / unavailable)')}")
        else:
            echo(f"  {f_label('分享 (share):')}    {f_num(sc)}")


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

    if not cache_manager.load_headers():
        error("No cached headers. Run 'zhihu auth paste' first.")
        raise SystemExit(1)

    echo(f"Paste the cURL command for the {api_description} API (Ctrl+D to finish):")
    try:
        curl_text = sys.stdin.read()
    except EOFError:
        curl_text = ""

    if not curl_text.strip():
        error("No input.")
        raise SystemExit(1)

    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    if not url_match:
        error("Could not parse URL from cURL.")
        raise SystemExit(1)

    initial_url = url_match.group(1).replace("http://", "https://")

    def parse_items(data: dict) -> list[dict]:
        return data.get("data", [])

    all_items: list[dict] = []
    for item in stream_handler(initial_url, parse_items):
        all_items.append(item)
        if len(all_items) % 20 == 0:
            info(f"Collected {len(all_items)} items...")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)
    success(f"Saved {f_num(len(all_items))} items to {f_path(output_file)}")


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
        error("No valid items found.")
        raise SystemExit(1)

    converted = convert_items(all_items, forced_type)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    success(f"Converted {f_num(len(converted))} items {f_dim('→')} {f_path(output)}")


@convert.command("user-act")
@click.argument("input_file", default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"))
@click.argument("output_file", default=str(get_data_dir() / "exports" / "all_assets_list.json"))
def convert_user_act(input_file: str, output_file: str) -> None:
    """Convert zhihu_user_activities.json to all_assets_list.json format."""
    if not os.path.exists(input_file):
        error(f"file not found: {input_file}")
        raise SystemExit(1)

    converted = convert_items(load_json(input_file))

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    success(f"Converted {f_num(len(converted))} items {f_dim('→')} {f_path(output_file)}")


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
        error(f"{e}")
        raise SystemExit(1)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(markdown)
        success(f"Draft saved to {f_path(output)}")
    else:
        echo(markdown)


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
        print_json(get_monthly_income_data(file_path))
        return
    analyze_monthly_income(file_path)


@tools_creator.command("plot")
def creator_plot() -> None:
    """Generate basic income plot (bar chart + EMA + trend)."""
    from zhihu_cli.creator_tools.plot_zhihu_incomes import plot_analysis

    plot_analysis()
    success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'income_analysis.png'))}")


@tools_creator.command("advanced")
def creator_advanced() -> None:
    """Generate advanced analysis plot (Bollinger + MACD)."""
    from zhihu_cli.creator_tools.plot_zhihu_incomes_advanced import plot_advanced_analysis

    plot_advanced_analysis()
    success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'income_advanced_analysis.png'))}")


@tools_creator.command("derivative")
def creator_derivative() -> None:
    """Generate derivative analysis plot (velocity, acceleration, jerk)."""
    from zhihu_cli.creator_tools.derivative_analysis import plot_derivative_analysis

    plot_derivative_analysis()
    success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'derivative_analysis.png'))}")


@tools_creator.command("weekday")
def creator_weekday() -> None:
    """Generate weekday income distribution plot."""
    from zhihu_cli.creator_tools.weekday_income_analysis import plot_weekday_analysis

    plot_weekday_analysis()
    success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'weekday_income_analysis.png'))}")


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
def creator_follower_fetch() -> None:
    """Fetch follower detail data from Zhihu API (from configured start-date to today)."""
    from datetime import date, datetime

    from zhihu_cli.creator_tools.parse_follower_detail import run_task

    start_str = cache_manager.get_start_date()
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    days = (date.today() - start).days
    run_task(days=days)


@tools_creator_follower.command("analysis")
def creator_follower_analysis() -> None:
    """Fetch follower profile/demographics (关注者画像) from Zhihu API."""
    from zhihu_cli.creator_tools.parse_follower_profile import run_task

    run_task()


@tools_creator_follower.command("plot")
def creator_follower_plot() -> None:
    """Plot follower detail line chart from follower_detail.json."""
    from zhihu_cli.creator_tools.plot_follower_detail import plot_follower_detail

    plot_follower_detail()


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
            print_json({"files": 0})
        else:
            info("No markdown files found.")
        return

    wc = [int(x) for x in word_counts]
    if output_json:
        print_json(
            {
                "files": len(wc),
                "mean": round(float(np.mean(wc)), 1),
                "std": round(float(np.std(wc)), 1),
                "p50": int(np.percentile(wc, 50)),
                "p90": int(np.percentile(wc, 90)),
                "max": int(max(wc)),
            }
        )
        return

    echo(f"  {f_label('Files:')} {f_num(len(word_counts))}")
    echo(
        f"  {f_label('Mean:')} {f_num(f'{np.mean(word_counts):.0f}')}  {f_label('Std:')} {f_num(f'{np.std(word_counts):.0f}')}"
    )
    echo(
        f"  {f_label('P50:')} {f_num(f'{np.percentile(word_counts, 50):.0f}')}  {f_label('P90:')} {f_num(f'{np.percentile(word_counts, 90):.0f}')}"
    )
    echo(f"  {f_label('Max:')} {f_num(max(word_counts))}")


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
        error("No documents found.")
        return

    if evaluate_k:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
        X = vectorizer.fit_transform(documents)
        find_best_k(X, max_k=20)
        info("Check the elbow/silhouette plot to choose K.")
        return

    X, labels, vectorizer, kmeans = process_clusters(documents, n_clusters)
    visualize_with_plotly(
        X, labels, file_names, vectorizer, kmeans, n_clusters, mode=mode, output_path=output, n_terms=n_terms
    )  # type: ignore[arg-type]
    success(f"Saved {f_path(output)}")


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

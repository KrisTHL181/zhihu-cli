"""zhihu CLI — unified entry point for all Zhihu operations."""

import json
import os
import sys
import time
from pathlib import Path

import click

from zhihu_cli.content.download_contents import ContentDownloader, sanitize_filename
from zhihu_cli.content.handlers import get_data_dir, get_type_and_id
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.chat import get_inbox, iter_chat_history, send_text_message
from zhihu_cli.content.handlers.collection import (
    add_to_collection,
    collect,
    create_collection,
    delete_collection,
    delete_to_collection,
)
from zhihu_cli.content.handlers.comments import comment_item, delete_comment, print_comments
from zhihu_cli.content.handlers.draft import draft_to_markdown
from zhihu_cli.content.handlers.feed import fetch_feed, fetch_feed_with_markdown
from zhihu_cli.content.handlers.people import block, follow, unblock, unfollow
from zhihu_cli.content.handlers.pin import scrape_pin
from zhihu_cli.content.handlers.publishing import modify_answer, modify_article, publish_answer, publish_article
from zhihu_cli.content.handlers.question import (
    downvote_answer,
    downvote_question,
    follow_question,
    neutral_answer,
    scrape_answers,
    scrape_question_data,
    thank_answer,
    unfollow_question,
    unthank_answer,
    upvote_answer,
    upvote_question,
)
from zhihu_cli.content.handlers.requests import session
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
    """问答类型的 id 格式为 'question_id/answer_id'，提取 answer_id。"""
    if "/" in item_id:
        return item_id.split("/")[1]
    return item_id


def _save_markdown(metadata: dict, markdown: str, output_dir: str, prefix: str = "") -> str:
    """Save markdown content to output_dir. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    title = sanitize_filename(metadata.get("title", "untitled"))
    author = sanitize_filename(metadata.get("author", "unknown"))
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


@auth.command("status")
def auth_status() -> None:
    """Show authentication status and active profile."""
    active = cache_manager.get_active_profile()
    if active:
        click.echo(f"Active profile: {active}")
    else:
        click.echo("No active profile set.")

    profiles = cache_manager.list_profiles()
    if profiles:
        click.echo(f"Saved profiles: {', '.join(profiles)}")

    headers = cache_manager.load_headers()
    if headers:
        click.echo(f"Headers: {len(headers)} cached")
        if "cookie" in {k.lower() for k in headers}:
            click.echo("Cookie: present")
        else:
            click.echo("Warning: no Cookie header found.", err=True)
    else:
        click.echo("No headers cached. Run 'zhihu auth paste' first.", err=True)


@auth.command("clear")
def auth_clear() -> None:
    """Remove cached headers."""
    cache_manager.save_headers({})
    click.echo("Headers cache cleared.")


# ── profile ──────────────────────────────────────────────────────────────


@main.group()
def profile() -> None:
    """Manage account profiles — save and switch between multiple logins."""


@profile.command("list")
def profile_list() -> None:
    """List all saved profiles."""
    active = cache_manager.get_active_profile()
    profiles = cache_manager.list_profiles()
    if not profiles:
        click.echo("No profiles found. Use 'zhihu auth paste --profile <name>' to create one.")
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

    if not force:
        click.confirm(f"Delete profile '{name}'?", abort=True)

    cache_manager.delete_profile(name)
    click.echo(f"Deleted profile '{name}'.")


@profile.command("current")
def profile_current() -> None:
    """Show the currently active profile."""
    active = cache_manager.get_active_profile()
    if active:
        click.echo(active)
    else:
        click.echo("No active profile set.", err=True)


# ── download ─────────────────────────────────────────────────────────────


@main.group()
def download() -> None:
    """Download Zhihu content as Markdown files."""


@download.command("article")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "articles"), help="Output directory")
def download_article(url: str, output_dir: str) -> None:
    """Download a single Zhihu article as Markdown."""
    metadata, markdown = scrape_article(url)
    filepath = _save_markdown(metadata, markdown, output_dir)
    click.echo(f"{metadata.get('title', 'untitled')}")
    click.echo(f"  -> {filepath}")


@download.command("question")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "questions"), help="Output directory")
def download_question(url: str, output_dir: str) -> None:
    """Download a Zhihu question and all its answers as Markdown."""
    q_meta, q_detail_md = scrape_question_data(url)
    os.makedirs(output_dir, exist_ok=True)

    title = sanitize_filename(q_meta.get("title", "untitled"))
    filepath = os.path.join(output_dir, f"{title}_question.md")[:200]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {q_meta['title']}\n\n{q_detail_md}\n")

    click.echo(f"Question: {q_meta['title']}")
    click.echo(f"  -> {filepath}")

    ans_dir = os.path.join(output_dir, f"{title}_answers")
    os.makedirs(ans_dir, exist_ok=True)
    count = 0
    for ans in scrape_answers(q_meta):
        count += 1
        afile = os.path.join(ans_dir, f"{count:04d}_{sanitize_filename(ans['author'])}.md")[:200]
        with open(afile, "w", encoding="utf-8") as f:
            f.write(f"# Answer by {ans['author']} (+{ans['vote']})\n\n{ans['content']}\n")
    click.echo(f"  {count} answers saved to {ans_dir}")


@download.command("pin")
@click.argument("url")
@click.option("--output-dir", "-o", default=str(get_data_dir() / "downloads" / "pins"), help="Output directory")
def download_pin(url: str, output_dir: str) -> None:
    """Download a single Zhihu pin (想法) as Markdown."""
    metadata, markdown = scrape_pin(url)
    filepath = _save_markdown(metadata, markdown, output_dir)
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


# ── browse ───────────────────────────────────────────────────────────────


@main.group()
def browse() -> None:
    """Browse Zhihu content in the terminal."""


@browse.command("answers")
@click.argument("url")
@click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
def browse_answers(url: str, reading_mode: bool) -> None:
    """Stream answers under a Zhihu question."""
    q_meta, q_detail_md = scrape_question_data(url)

    answers = list(scrape_answers(q_meta))

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
                console.print(f"\n--- Answer {i} by {ans['author']} (+{ans['vote']}) ---")
                console.print(Markdown(ans["content"]))
    else:
        click.echo(question_md)
        for i, ans in enumerate(answers, 1):
            click.echo(f"\n--- Answer {i} by {ans['author']} (+{ans['vote']}) ---")
            click.echo(ans["content"])


@browse.command("comments")
@click.argument("url")
def browse_comments(url: str) -> None:
    """Print the comment tree for any Zhihu item."""
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    print_comments(item_type, item_id)


@browse.command("feed")
@click.option("--type", "-t", "feed_type", type=click.Choice(["recommend", "follow"]), default="recommend")
@click.option("--limit", type=int, default=20, help="Items per page")
@click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
@click.option("--markdown/--no-markdown", default=False, help="Convert HTML to Markdown")
@click.option("--output", "-o", type=str, default="", help="Save to JSON file")
@click.option("--verbose", "-v", is_flag=True, help="Print items while fetching")
def browse_feed(feed_type: str, limit: int, max_items: int | None, markdown: bool, output: str, verbose: bool) -> None:
    """Stream Zhihu recommend or follow feed."""
    fetch_fn = fetch_feed_with_markdown if markdown else fetch_feed
    items = fetch_fn(feed_type, limit, max_items)

    for item in items:
        if verbose:
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
    elif not verbose:
        click.echo(f"Fetched {len(items)} items (use --verbose to print, --output to save)")


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
    """Thank / unthank answers."""


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
    """Follow / unfollow users or questions."""


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
    """Block / unblock users."""


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
def chat_inbox() -> None:
    """List recent conversations."""
    messages = get_inbox()
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
def chat_history(chat_id: str, limit: int) -> None:
    """Read messages from a chat conversation."""
    count = 0
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

    headers = cache_manager.load_headers()
    if not headers:
        click.echo("No cached headers. Run 'zhihu auth paste' first.", err=True)
        raise SystemExit(1)

    base_url = "https://www.zhihu.com/api/v4/creations/all"
    all_assets: list[dict[str, str]] = []
    offset = 0
    limit = 20

    click.echo("Scanning creations...")
    while True:
        params = {"start": 0, "end": 0, "limit": limit, "offset": offset, "need_co_creation": 1, "sort_type": "created"}
        try:
            resp = session.get(base_url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                click.echo(f"Error: HTTP {resp.status_code}", err=True)
                break

            data = resp.json()
            for item in data.get("data", []):
                asset_type = item.get("type")
                asset_id = item.get("data", {}).get("id")
                if asset_id and asset_type in ("answer", "pin", "article"):
                    all_assets.append(
                        {"id": asset_id, "type": asset_type, "title": item.get("data", {}).get("title", "")}
                    )

            paging = data.get("paging", {})
            totals = paging.get("totals", 0)
            offset += limit
            click.echo(f"  {min(offset, totals)}/{totals} — collected {len(all_assets)}")

            if paging.get("is_end", True) or offset >= totals:
                break
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            break
        time.sleep(1.2)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(all_assets, f, ensure_ascii=False, indent=2)
    click.echo(f"Saved {len(all_assets)} assets to {output}")


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

    with open(input_file, encoding="utf-8") as f:
        activities = json.load(f)

    converted = [{"id": a.get("id", ""), "type": a.get("type", ""), "title": a.get("title", "")} for a in activities]

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
    """Analysis tools — income analytics and NLP text analysis."""


@tools.group("income")
def tools_income() -> None:
    """Zhihu creator income analytics."""


@tools_income.command("fetch")
def income_fetch() -> None:
    """Fetch incremental income data from Zhihu creator API."""
    from zhihu_cli.money_tools.parse_zhihu_incomes import run_task

    run_task()


@tools_income.command("monthly")
@click.option(
    "--file",
    "-f",
    "file_path",
    default=str(get_data_dir() / "exports" / "zhihu_income_report.json"),
    help="Income report JSON",
)
def income_monthly(file_path: str) -> None:
    """Print monthly income summary table."""
    from zhihu_cli.money_tools.analyze_monthly_income import analyze_monthly_income

    analyze_monthly_income(file_path)


@tools_income.command("plot")
def income_plot() -> None:
    """Generate basic income plot (bar chart + EMA + trend)."""
    from zhihu_cli.money_tools.plot_zhihu_incomes import plot_analysis

    plot_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'income_analysis.png'}")


@tools_income.command("advanced")
def income_advanced() -> None:
    """Generate advanced analysis plot (Bollinger + MACD)."""
    from zhihu_cli.money_tools.plot_zhihu_incomes_advanced import plot_advanced_analysis

    plot_advanced_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'income_advanced_analysis.png'}")


@tools_income.command("derivative")
def income_derivative() -> None:
    """Generate derivative analysis plot (velocity, acceleration, jerk)."""
    from zhihu_cli.money_tools.derivative_analysis import plot_derivative_analysis

    plot_derivative_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'derivative_analysis.png'}")


@tools_income.command("weekday")
def income_weekday() -> None:
    """Generate weekday income distribution plot."""
    from zhihu_cli.money_tools.weekday_income_analysis import plot_weekday_analysis

    plot_weekday_analysis()
    click.echo(f"Saved {get_data_dir() / 'plots' / 'weekday_income_analysis.png'}")


@tools_income.command("metrics")
@click.option("--aggr", is_flag=True, help="Use aggregated endpoint (single datapoint per content)")
def income_metrics(aggr: bool) -> None:
    """Fetch per-content daily metrics from Zhihu API."""
    from zhihu_cli.money_tools.parse_content_datas import run_batch_daily_analysis

    run_batch_daily_analysis(use_aggr=aggr)


@tools.group("nlp")
def tools_nlp() -> None:
    """NLP text analysis on downloaded Markdown files."""


@tools_nlp.command("count")
@click.option("--folder", default=str(get_data_dir() / "downloads" / "answers"), help="Folder with Markdown files")
@click.option("--no-code", is_flag=True, help="Exclude code blocks")
def nlp_count(folder: str, no_code: bool) -> None:
    """Count words in downloaded Markdown files."""
    import numpy as np

    from zhihu_cli.nlp_tools.count_answer_words import count_words

    word_counts = []
    for filename in os.listdir(folder):
        if filename.endswith(".md"):
            word_counts.append(count_words(os.path.join(folder, filename), no_code=no_code))

    if not word_counts:
        click.echo("No markdown files found.")
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


# ── entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()

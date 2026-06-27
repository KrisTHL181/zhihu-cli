"""Shared helper functions used across command group modules."""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import click

from zhihu_cli.content.download_contents import sanitize_filename
from zhihu_cli.content.handlers import get_type_and_id
from zhihu_cli.output import (
    blank,
    echo,
    f_bold,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    f_url,
    item_index,
    success,
)

# ── URL helpers ────────────────────────────────────────────────────────────


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


def _extract_url_token(token_or_url: str) -> str:
    """Extract a Zhihu url_token from a full profile URL or return as-is."""
    m = re.search(r"zhihu\.com/people/([^/?]+)", token_or_url)
    if m:
        return m.group(1)
    return token_or_url.rstrip("/").split("/")[-1]


def _resolve_url_token(url_token: str | None) -> str:
    """Resolve the url_token: use provided value or auto-detect from /api/v4/me."""
    from zhihu_cli.content.handlers.people import get_my_url_token

    if url_token:
        return _extract_url_token(url_token)
    token = get_my_url_token()
    if not token:
        raise click.UsageError(
            "Cannot detect your url_token. Please authenticate first (zhihu auth login) "
            "or provide --url-token explicitly."
        )
    return token


# ── File I/O helpers ───────────────────────────────────────────────────────


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


def _save_json_output(data: Any, output_path: str, label: str = "items") -> None:
    """Save data to a JSON file with a success message. No-op if *output_path* is empty."""
    if not output_path:
        return
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    length = len(data) if isinstance(data, (list, dict)) else 0
    success(f"Saved {length} {label} to {output_path}")


# ── Display helpers ────────────────────────────────────────────────────────


def _display_following_items(items: list[dict], totals: int | None = None) -> None:
    """Display a list of following/followed items in terminal mode."""
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

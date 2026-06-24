"""Shared helper functions used across command group modules."""

import os
import sys
from pathlib import Path

import click

from zhihu_cli.content.download_contents import sanitize_filename
from zhihu_cli.content.handlers import get_type_and_id


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

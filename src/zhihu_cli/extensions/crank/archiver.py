#!/usr/bin/env python3
"""One-shot pipeline: scrape a user's articles → download → LLM names the series → archive.

Usage:
    python pipeline_crank_archiver.py -u <zhihu_user_token>

Environment variables:
    LLM_API_BASE   – OpenAI-compatible API endpoint (default: https://api.openai.com/v1)
    LLM_API_KEY    – API key (required)
    LLM_MODEL      – Model name (default: gpt-4o-mini)
"""

import argparse
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from zhihu_cli.content.download_contents import sanitize_filename, save_article
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.universal_converter import convert_items
from zhihu_cli.content.utils.llm_config import (
    MISSING_API_KEY_MESSAGE,
    build_client,
    llm_supports_vision,  # noqa: F401  (re-exported for backward compatibility)
    load_llm_config,  # noqa: F401  (re-exported for backward compatibility)
    resolve,
    save_llm_config,  # noqa: F401  (re-exported for backward compatibility)
)
from zhihu_cli.content.utils.wait import wait
from zhihu_cli.output import error, warning
from zhihu_cli.paths import zhihu_home
from zhihu_cli.prompts import (
    COMMIT_MESSAGE_SYSTEM_PROMPT,
    COMMIT_MESSAGE_USER_PROMPT,
    COMMIT_STYLE_EXAMPLES,
    SERIES_NAMING_SYSTEM_PROMPT,
    SERIES_NAMING_USER_PROMPT,
)

ARTICLES_API = "https://www.zhihu.com/api/v4/members/{token}/articles"

CRANK_DIR = str(zhihu_home() / "crank")
HALL_OF_FLAMES_ROOT = CRANK_DIR
SERIAL_PAPERS_DIR = os.path.join(CRANK_DIR, "papers")


# ── article list scraping ──────────────────────────────────────────────────


def fetch_article_list(user_token: str) -> list[dict[str, Any]]:
    """Paginate through a user's articles API using the standard waterfall streamer."""
    if not cache_manager.load_headers():
        print("No cached headers. Run 'zhihu auth paste' first.", file=sys.stderr)
        sys.exit(1)

    initial_url = ARTICLES_API.format(token=user_token) + "?offset=0&limit=20&sort_by=created"

    def parser(data: dict[str, Any]):
        yield from data.get("data", [])

    print(f"Fetching article list for user: {user_token}")
    # The articles list endpoint returns 500 when the x-app-za=OS=Android
    # app-identity header is present, so skip it for this fetch.
    items = list(stream_handler(initial_url, parser, skip_app_headers=True))
    print(f"Fetched {len(items)} articles total.")
    return items


# ── LLM naming ─────────────────────────────────────────────────────────────


def build_naming_prompt(author_name: str, samples: list[tuple[str, str]]) -> str:
    """Build the dynamic user prompt for the LLM to generate a series name.

    The role, task and output rules live in ``SERIES_NAMING_SYSTEM_PROMPT``;
    ``SERIES_NAMING_USER_PROMPT`` supplies the layout. The stable part of every
    request therefore stays one cacheable prefix, while this function fills in
    only runtime material, ordered from the slowest-changing (the concept
    README) to the per-author excerpts.
    """
    serial_papers_readme = os.path.join(SERIAL_PAPERS_DIR, "README.md")

    concept_text = ""
    if os.path.exists(serial_papers_readme):
        concept_text = Path(serial_papers_readme).read_text(encoding="utf-8")[:3000]

    concept_block = concept_text or "（暂无）"
    existing_names = _collect_existing_series_names()
    names_block = "\n".join(f"- {n}" for n in existing_names) or "（暂无）"

    samples_text = "\n\n".join(
        f"### 样本 {i}: {filename}\n\n{content[:2000]}" for i, (filename, content) in enumerate(samples, 1)
    )
    return SERIES_NAMING_USER_PROMPT.format(
        concept_block=concept_block,
        names_block=names_block,
        author_name=author_name,
        sample_count=len(samples),
        samples=samples_text,
    )


def _collect_existing_series_names() -> list[str]:
    """Collect existing series directory names from serial papers dir."""
    names = []
    if os.path.isdir(SERIAL_PAPERS_DIR):
        for entry in sorted(os.listdir(SERIAL_PAPERS_DIR)):
            full = os.path.join(SERIAL_PAPERS_DIR, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                names.append(entry)
    return names


def call_llm_for_name(
    author_name: str,
    samples: list[tuple[str, str]],
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str | None:
    """Send samples to LLM and get a series name back.

    Args:
        api_base: OpenAI-compatible endpoint. Defaults to ``LLM_API_BASE`` env var or ``https://api.openai.com/v1``.
        api_key: API key. Defaults to ``LLM_API_KEY`` env var.
        model: Model name. Defaults to ``LLM_MODEL`` env var or ``gpt-4o-mini``.
    """
    cfg = resolve(api_base=api_base, api_key=api_key, model=model)
    if cfg is None:
        error(MISSING_API_KEY_MESSAGE)
        return None

    prompt = build_naming_prompt(author_name, samples)

    client = build_client(cfg, purpose="LLM series naming")
    if client is None:
        return None

    print(f"Calling LLM ({cfg.model}) to generate series name...")
    try:
        response = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {
                    "role": "system",
                    "content": SERIES_NAMING_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=64,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = response.choices[0].message.content
        if not raw:
            print("LLM returned empty response.", file=sys.stderr)
            return None
        name = raw.strip()
        # Clean up common artifacts
        name = name.strip("'\"。. ")
        print(f"LLM suggested name: {name}")
        return name
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return None


# ── LLM commit message suggestion ──────────────────────────────────────────


def _find_git_repo() -> str | None:
    """Locate the Hall of Flames git repository.

    Walks up from the crank data directory; also follows the *papers* symlink
    when present.
    """
    # Check HALL_OF_FLAMES_ROOT and its ancestors
    candidate = HALL_OF_FLAMES_ROOT
    while candidate and candidate != os.path.sep:
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent

    # Follow the papers symlink if present
    if os.path.islink(SERIAL_PAPERS_DIR):
        real_papers = os.path.realpath(SERIAL_PAPERS_DIR)
        candidate = os.path.dirname(real_papers)
        while candidate and candidate != os.path.sep:
            if os.path.isdir(os.path.join(candidate, ".git")):
                return candidate
            parent = os.path.dirname(candidate)
            if parent == candidate:
                break
            candidate = parent

    return None


def _get_recent_commit_messages(repo_path: str, count: int = 12) -> list[str]:
    """Return recent commit messages (subject only) from *repo_path*."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "--oneline", "--no-decorate", f"-{count}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            messages = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    messages.append(parts[1])
            return messages
    except Exception:
        pass
    return []


def build_commit_message_prompt(
    author_name: str,
    series_name: str,
    samples: list[tuple[str, str]],
    recent_commits: list[str] | None = None,
) -> str:
    """Build the dynamic user prompt for generating a git commit message.

    The role, task, requirements and output rules live in
    ``COMMIT_MESSAGE_SYSTEM_PROMPT``, and ``COMMIT_MESSAGE_USER_PROMPT``
    supplies the layout. This function fills in only the runtime material:
    style examples, the paper being archived, and its excerpts.
    """
    # Fall back to the bundled examples when the repo is unreachable.
    style_examples = recent_commits or COMMIT_STYLE_EXAMPLES

    samples_text = "\n\n".join(
        f"### 样本 {i}: {filename}\n\n{content[:1500]}" for i, (filename, content) in enumerate(samples, 1)
    )
    style_block = "\n".join(f"- {m}" for m in style_examples)
    return COMMIT_MESSAGE_USER_PROMPT.format(
        style_block=style_block,
        author_name=author_name,
        series_name=series_name,
        sample_count=len(samples),
        samples=samples_text,
    )


def call_llm_for_commit_message(
    author_name: str,
    series_name: str,
    samples: list[tuple[str, str]],
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str | None:
    """Generate a suggested git commit message for the newly archived series.

    Tries to read recent commit messages from the Hall of Flames repo
    so the LLM can match the user's personal style.
    """
    cfg = resolve(api_base=api_base, api_key=api_key, model=model)
    if cfg is None:
        warning("LLM API key not provided. Skipping commit message suggestion.")
        return None

    recent_commits: list[str] | None = None
    git_repo = _find_git_repo()
    if git_repo:
        recent_commits = _get_recent_commit_messages(git_repo)
        if recent_commits:
            print(f"  (read {len(recent_commits)} recent commits from {git_repo} for style)")

    prompt = build_commit_message_prompt(author_name, series_name, samples, recent_commits)

    client = build_client(cfg, purpose="commit message suggestions")
    if client is None:
        return None

    print(f"Calling LLM ({cfg.model}) to generate commit message suggestion...")
    try:
        response = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {
                    "role": "system",
                    "content": COMMIT_MESSAGE_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=128,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = response.choices[0].message.content
        if not raw:
            print("LLM returned empty response for commit message.", file=sys.stderr)
            return None
        msg = raw.strip().strip("'\"`。. ")
        return msg
    except Exception as e:
        print(f"LLM commit message generation failed: {e}", file=sys.stderr)
        return None


# ── pipeline core ───────────────────────────────────────────────────────────


def parse_since(since: str | None) -> float | None:
    """Parse a --since date string into a Unix timestamp.

    Accepts ``YYYY-MM-DD`` or ``YYYY/MM/DD``.  Returns *None* if *since* is
    falsy or unparseable.
    """
    if not since:
        return None
    since = since.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(since, fmt).timestamp()
        except ValueError:
            continue
    print(f"Warning: could not parse --since date '{since}'. Expected YYYY-MM-DD or YYYY/MM/DD.", file=sys.stderr)
    return None


def run_archiver(
    user_token: str,
    output_dir: str = SERIAL_PAPERS_DIR,
    sample_count: int = 4,
    *,
    since: str | None = None,
    dry_run: bool = False,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str | None:
    """Fetch a user's articles, download, LLM-name the series, archive.

    Returns the final series directory path, or *None* on failure / dry-run.
    """
    user_token = user_token.strip()

    # 1. Fetch article list
    raw_articles = fetch_article_list(user_token)
    if not raw_articles:
        print("No articles found. Exiting.", file=sys.stderr)
        return None

    # 1b. Filter by --since cutoff
    since_ts = parse_since(since)
    if since_ts is not None:
        before = len(raw_articles)
        raw_articles = [a for a in raw_articles if a.get("created", 0) >= since_ts]
        skipped = before - len(raw_articles)
        if skipped:
            print(f"Skipped {skipped} article(s) before {(since or '').strip()}.")
        if not raw_articles:
            print("No articles remain after --since filter. Exiting.", file=sys.stderr)
            return None

    # Determine author name from first article
    first = raw_articles[0]
    author_name = first.get("author", {}).get("name", user_token)

    # 2. Convert to unified format
    assets = convert_items(raw_articles, forced_type="article")
    print(f"Converted to {len(assets)} unified assets.")

    # 3. Download articles to temp dir via scrape_article (js-initialData-based, no cURL headers needed)
    temp_dir = tempfile.mkdtemp(prefix="zhihu_articles_")
    print(f"Downloading articles to: {temp_dir}")

    article_urls = [f"https://zhuanlan.zhihu.com/p/{a['id']}" for a in assets]
    for url in article_urls:
        try:
            metadata, markdown = scrape_article(url)
            meta = {
                "title": metadata["title"],
                "author": metadata["author"]["name"],
                "created": metadata.get("created_time", "")[:10] or "unknown",
            }
            filepath = save_article(url, meta, markdown, temp_dir)
            print(f"  [OK] {url} → {os.path.basename(filepath)}")
        except Exception as e:
            print(f"  [Error] {url}: {e}", file=sys.stderr)
        wait(1.0)

    # 4. Gather downloaded files
    md_files = sorted(
        [f for f in os.listdir(temp_dir) if f.endswith(".md")],
        key=lambda f: os.path.getmtime(os.path.join(temp_dir, f)),
    )

    if not md_files:
        print("No markdown files downloaded. Exiting.", file=sys.stderr)
        return None

    print(f"Downloaded {len(md_files)} articles.")

    # 5. Random sample for LLM
    n_samples = min(sample_count, len(md_files))
    sampled = random.sample(md_files, n_samples)
    samples: list[tuple[str, str]] = []
    print(f"\nSampled {n_samples} files for LLM review:")
    for fname in sampled:
        fpath = os.path.join(temp_dir, fname)
        try:
            content = Path(fpath).read_text(encoding="utf-8")
            preview = content[:100].replace("\n", " ")
            print(f"  - {fname}  ({len(content)} chars) → {preview}...")
            samples.append((fname, content))
        except Exception as e:
            print(f"  - {fname}  ERROR: {e}")

    if dry_run:
        print(f"\n[dry-run] Articles downloaded to: {temp_dir}")
        print("[dry-run] Skipping LLM naming. Files remain in temp dir for inspection.")
        return None

    if not samples:
        print("No valid samples to send to LLM.", file=sys.stderr)
        return None

    # 6. LLM naming
    series_name = call_llm_for_name(author_name, samples, api_base=api_base, api_key=api_key, model=model)

    if not series_name:
        print("\nLLM naming failed. Falling back to manual mode.")
        print(f"Articles are in: {temp_dir}")
        print("Please name the series manually and move the files.")
        print(f"Suggested output: {output_dir}/<AuthorName>-<TheoryCoreName>/")
        return None

    # 7. Move files to output directory
    safe_series = sanitize_filename(series_name)
    final_dir = os.path.join(output_dir, safe_series)
    os.makedirs(final_dir, exist_ok=True)

    for fname in md_files:
        src = os.path.join(temp_dir, fname)
        dst = os.path.join(final_dir, fname)
        shutil.move(src, dst)

    print(f"\nArchived {len(md_files)} articles to:")
    print(f"  {final_dir}")

    # 8. Generate suggested commit message
    print(f"\n{'─' * 55}")
    commit_msg = call_llm_for_commit_message(
        author_name,
        series_name,
        samples,
        api_base=api_base,
        api_key=api_key,
        model=model,
    )
    if commit_msg:
        print("\n📋 Suggested commit message:")
        print(f"   {commit_msg}")
        # Also show the canonical git command
        git_repo = _find_git_repo()
        if git_repo:
            print(f"\n   cd {git_repo} && git add -A && git commit -m '{commit_msg}'")
    else:
        # Fallback: craft a simple message from the series name
        fallback_theory = series_name.split("-", 1)[1] if "-" in series_name else series_name
        fallback_msg = f'[Accept] 收容伟大的民间科学家"{author_name}"的{fallback_theory}'
        print("\n📋 Suggested commit message (fallback):")
        print(f"   {fallback_msg}")

    # Clean up temp dir
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass

    return final_dir


# ── standalone CLI (also exposed as ``zhihu crank archive``) ─────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Crank paper auto-archiver with LLM naming")
    parser.add_argument("--user-token", "-u", required=True, help="Zhihu user URL token")
    parser.add_argument(
        "--output-dir",
        "-o",
        default=SERIAL_PAPERS_DIR,
        help=f"Output directory for series (default: {SERIAL_PAPERS_DIR})",
    )
    parser.add_argument("--sample-count", "-n", type=int, default=4, help="Number of random samples (default: 4)")
    parser.add_argument(
        "--since",
        default=None,
        help="Only download articles created on or after this date (YYYY-MM-DD or YYYY/MM/DD)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM naming, only download and show sample paths")
    args = parser.parse_args()

    run_archiver(
        user_token=args.user_token,
        output_dir=args.output_dir,
        sample_count=args.sample_count,
        since=args.since,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

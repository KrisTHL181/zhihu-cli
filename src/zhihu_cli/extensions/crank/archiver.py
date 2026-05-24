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
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from zhihu_cli.content.download_contents import sanitize_filename
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.universal_converter import convert_items

ARTICLES_API = "https://www.zhihu.com/api/v4/members/{token}/articles"

CRANK_DIR = str(Path.home() / ".zhihu-cli" / "crank")
HALL_OF_FLAMES_ROOT = CRANK_DIR
SERIAL_PAPERS_DIR = os.path.join(CRANK_DIR, "papers")
LLM_CONFIG_PATH = os.path.join(CRANK_DIR, "llm_config.json")


def load_llm_config() -> dict[str, str]:
    """Load cached LLM config from disk. Returns empty dict if no cache exists."""
    try:
        if os.path.exists(LLM_CONFIG_PATH):
            with open(LLM_CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, str) and v}
    except Exception:
        pass
    return {}


def save_llm_config(api_base: str, api_key: str, model: str) -> None:
    """Persist LLM config to disk cache."""
    os.makedirs(CRANK_DIR, exist_ok=True)
    data = {"api_base": api_base, "api_key": api_key, "model": model}
    with open(LLM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    items = list(stream_handler(initial_url, parser, delay=1.2))
    print(f"Fetched {len(items)} articles total.")
    return items


# ── LLM naming ─────────────────────────────────────────────────────────────


def build_naming_prompt(author_name: str, samples: list[tuple[str, str]]) -> str:
    """Build the prompt for the LLM to generate a series name."""
    serial_papers_readme = os.path.join(SERIAL_PAPERS_DIR, "README.md")

    concept_text = ""
    if os.path.exists(serial_papers_readme):
        concept_text = Path(serial_papers_readme).read_text(encoding="utf-8")[:3000]

    existing_names = _collect_existing_series_names()

    samples_text = ""
    for i, (filename, content) in enumerate(samples, 1):
        truncated = content[:2000]
        samples_text += f"\n### 样本 {i}: {filename}\n\n{truncated}\n"

    return f"""你是一位资深的科学文献策展人，为「烈火殿·连环论文」收藏单元命名。

## 连环论文的概念

{concept_text}

## 已有的系列名称示例

{chr(10).join(f"- {n}" for n in existing_names)}

## 命名任务

请为以下这位民间科学家的论文系列起一个简洁的名称。格式为：

**作者名-理论核心系列名**

其中「理论核心系列名」应当：
1. 捕捉该系列最核心、反复出现的主题概念
2. 使用学术化但不失冲击力的语言，可以有书名号
3. 2-12个汉字为佳
4. 参考上述已有示例的风格

作者名：{author_name}

以下是从该作者论文中随机抽取的 {len(samples)} 篇正文节选：

{samples_text}

请直接输出系列名称，格式：作者名-理论核心系列名
不要包含其他解释文字。"""


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
    _cached = load_llm_config()
    _api_base = api_base or os.environ.get("LLM_API_BASE") or _cached.get("api_base", "https://api.openai.com/v1")
    _api_key = api_key or os.environ.get("LLM_API_KEY") or _cached.get("api_key", "")
    _model = model or os.environ.get("LLM_MODEL") or _cached.get("model", "gpt-4o-mini")

    if not _api_key:
        print("Error: LLM API key not provided. Use --api-key or set LLM_API_KEY env var.", file=sys.stderr)
        return None

    prompt = build_naming_prompt(author_name, samples)

    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: 'openai' package is required. Install with: pip install openai",
            file=sys.stderr,
        )
        return None

    client = OpenAI(base_url=_api_base, api_key=_api_key)

    print(f"Calling LLM ({_model}) to generate series name...")
    try:
        response = client.chat.completions.create(
            model=_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一位资深的科学文献策展人，专门为民间科学家的连载论文命名。你总是直接输出命名结果，不附加任何解释。",
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


# ── pipeline core ───────────────────────────────────────────────────────────


def run_archiver(
    user_token: str,
    output_dir: str = SERIAL_PAPERS_DIR,
    sample_count: int = 4,
    *,
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
            title = sanitize_filename(metadata["title"])
            author = sanitize_filename(metadata["author"]["name"])
            created = metadata.get("created_time", "")[:10] or "unknown"
            filename = f"{title}_{author}_{created}.md"
            if len(filename) > 200:
                filename = filename[:200]
            filepath = os.path.join(temp_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(markdown)
            print(f"  [OK] {url} → {filename}")
        except Exception as e:
            print(f"  [Error] {url}: {e}", file=sys.stderr)
        time.sleep(1.0)

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
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM naming, only download and show sample paths")
    args = parser.parse_args()

    run_archiver(
        user_token=args.user_token,
        output_dir=args.output_dir,
        sample_count=args.sample_count,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

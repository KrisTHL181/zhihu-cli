#!/usr/bin/env python3
"""Fully automated crank paper monitor — incremental fetch of new articles from watched authors.

Core workflow:
  1. Load author registry (name → zhihu_token → series_dir)
  2. For each author, fetch article list from Zhihu API
  3. Diff against already-downloaded papers (by parsing YAML frontmatter ``source`` IDs)
  4. Download only NEW articles with Hall-of-Flames-compatible naming + YAML frontmatter
  5. Optionally trigger AI review regeneration for authors with new papers
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zhihu_cli.content.download_contents import build_yaml_frontmatter, get_safe_filename, sanitize_filename
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.utils.wait import wait
from zhihu_cli.extensions.crank.archiver import (
    call_llm_for_name,
    fetch_article_list,
    load_llm_config,
    parse_since,
    save_llm_config,
)

if TYPE_CHECKING:
    import click

CRANK_DIR = str(Path.home() / ".zhihu-cli" / "crank")
HALL_OF_FLAMES_ROOT = CRANK_DIR
SERIAL_PAPERS_DIR = os.path.join(CRANK_DIR, "papers")
DEFAULT_REGISTRY_PATH = os.path.join(CRANK_DIR, "authors_registry.json")

# ── article ID extraction ───────────────────────────────────────────────────


def extract_article_id(url: str | None) -> str | None:
    """Extract numeric article ID from a Zhihu article URL.

    >>> extract_article_id('https://zhuanlan.zhihu.com/p/638541668')
    '638541668'
    """
    if not url:
        return None
    m = re.search(r"/p/(\d+)", url)
    return m.group(1) if m else None


# ── YAML frontmatter helpers ────────────────────────────────────────────────


def parse_frontmatter_field(filepath: Path, field: str) -> str | None:
    """Extract a single field value from YAML frontmatter of a .md file.

    Only handles simple ``key: value`` lines — sufficient for the Hall of Flames format.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, UnicodeError):
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        end = text.find("---", 3)
    if end == -1:
        return None
    fm = text[3:end]
    for line in fm.split("\n"):
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return None


def generate_filename(author: str, title: str, created: str) -> str:
    """Generate Hall-of-Flames-style filename: {YYYY-MM-DD}_{author}_{title}.md"""
    safe_author = sanitize_filename(author)
    safe_title = sanitize_filename(title)
    date_str = created if created else "unknown"
    full_name = f"{date_str}_{safe_author}_{safe_title}"
    return get_safe_filename(full_name, ext=".md", max_bytes=240)


# ── CrankMonitor ────────────────────────────────────────────────────────────


class CrankMonitor:
    """Monitors watched crank authors for new papers and downloads them incrementally."""

    def __init__(
        self,
        registry_path: str = DEFAULT_REGISTRY_PATH,
        hall_of_flames_root: str = HALL_OF_FLAMES_ROOT,
        *,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        classify_downloads: bool = False,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.hof_root = Path(hall_of_flames_root)
        self.serial_dir = self.hof_root / "papers"
        self.registry: dict[str, Any] = {"authors": []}
        self.llm_api_base = llm_api_base
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.classify_downloads = classify_downloads

    # ── registry management ─────────────────────────────────────────────

    def load_registry(self) -> dict[str, Any]:
        """Load the author registry from JSON, returning defaults if missing."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, encoding="utf-8") as f:
                    data = json.load(f)
                if "authors" in data:
                    self.registry = data
                    return data
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: failed to load registry: {e}", file=sys.stderr)
        self.registry = {"authors": []}
        return self.registry

    def save_registry(self) -> None:
        """Persist the registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self.registry, f, ensure_ascii=False, indent=2)

    def upsert_author(self, name: str, zhihu_token: str, series_dir: str, since: str | None = None) -> None:
        """Add a new author or update an existing one in the registry.

        Unlike ``bootstrap_registry``, this fills in *zhihu_token* immediately
        so the author is ready for ``crank fetch`` without manual editing.
        """
        self.load_registry()
        for a in self.registry["authors"]:
            if a["name"] == name:
                a["zhihu_token"] = zhihu_token
                a["series_dir"] = series_dir
                if since:
                    a["since"] = since
                elif "since" in a:
                    del a["since"]
                self.save_registry()
                print(f"Updated author in registry: {name} (token: {zhihu_token})")
                return
        entry: dict[str, object] = {
            "name": name,
            "zhihu_token": zhihu_token,
            "series_dir": series_dir,
            "enabled": True,
        }
        if since:
            entry["since"] = since
        self.registry["authors"].append(entry)
        self.save_registry()
        print(f"Added author to registry: {name} (token: {zhihu_token})")

    def list_authors(self) -> None:
        """Print all authors in the registry."""
        self.load_registry()
        authors = self.registry.get("authors", [])
        if not authors:
            print("No authors in registry. Run 'zhihu crank bootstrap' or 'zhihu crank archive' first.")
            return
        print("\t".join(["Name", "Token", "Series", "Since", "Enabled"]))
        print("-" * 100)
        for a in authors:
            enabled = "yes" if a.get("enabled", True) else "no"
            since = a.get("since", "-")
            print("\t".join([a["name"], a.get("zhihu_token", ""), a.get("series_dir", ""), since, enabled]))

    def remove_author(self, name: str) -> bool:
        """Remove an author from the registry by name.

        Returns True if the author was found and removed.
        """
        self.load_registry()
        for i, a in enumerate(self.registry["authors"]):
            if a["name"] == name:
                removed = self.registry["authors"].pop(i)
                self.save_registry()
                print(f"Removed author from registry: {name} (series: {removed['series_dir']})")
                return True
        print(f"Author not found in registry: {name}", file=sys.stderr)
        return False

    def bootstrap_registry(self) -> int:
        """Scan serial papers directories and build initial registry entries.

        Each discovered author gets an entry with ``zhihu_token`` left empty
        for manual filling.  Existing entries (matched by name) are preserved.

        Returns the number of newly bootstrapped authors.
        """
        self.load_registry()
        existing_names = {a["name"] for a in self.registry["authors"]}
        new_count = 0

        if not self.serial_dir.is_dir():
            print(f"Serial papers directory not found: {self.serial_dir}", file=sys.stderr)
            return 0

        for entry in sorted(self.serial_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            # Extract author name from dir name: {Author}-{TheoryName}
            author_name = (entry.name.split("-")[0] if "-" in entry.name else entry.name).strip()
            if author_name in existing_names:
                continue
            self.registry["authors"].append(
                {
                    "name": author_name,
                    "zhihu_token": "",
                    "series_dir": entry.name,
                    "enabled": True,
                }
            )
            existing_names.add(author_name)
            new_count += 1
            print(f"  + {author_name} → {entry.name}")

        self.save_registry()
        print(f"Bootstrapped {new_count} new author(s).  Total: {len(self.registry['authors'])}")
        print("Fill in the 'zhihu_token' fields before fetching.")
        return new_count

    def _get_enabled_authors(self, author_filter: str | None = None) -> list[dict[str, Any]]:
        """Return enabled author entries, optionally filtered by name."""
        authors = self.registry.get("authors", [])
        result = []
        for a in authors:
            if not a.get("enabled", True):
                continue
            if author_filter and a["name"] != author_filter:
                continue
            result.append(a)
        return result

    # ── incremental detection ───────────────────────────────────────────

    def _get_existing_article_ids(self, series_dir_name: str) -> set[str]:
        """Scan a series directory and return all article IDs found in YAML frontmatter."""
        series_path = self.serial_dir / series_dir_name
        if not series_path.is_dir():
            return set()

        ids: set[str] = set()
        for md_file in sorted(series_path.glob("*.md")):
            if md_file.name == "README.md":
                continue
            source = parse_frontmatter_field(md_file, "source")
            if source:
                aid = extract_article_id(source)
                if aid:
                    ids.add(aid)
        return ids

    # ── per-author operations ───────────────────────────────────────────

    def check_author(self, author_entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch article list and return only articles NOT already downloaded.

        Returns a list of raw article dicts from the API.
        """
        token = author_entry.get("zhihu_token", "")
        if not token:
            print(f"  [skip] {author_entry['name']}: no zhihu_token set")
            return []

        series_dir = author_entry.get("series_dir", "")
        existing_ids: set[str] = set()
        if series_dir:
            existing_ids = self._get_existing_article_ids(series_dir)

        print(f"  Fetching article list for {author_entry['name']} (token={token})...")
        try:
            all_articles = fetch_article_list(token)
        except Exception as e:
            print(f"  [error] Failed to fetch articles for {author_entry['name']}: {e}", file=sys.stderr)
            return []

        # Apply since filter if author has one
        since_str = author_entry.get("since")
        if since_str:
            since_ts = parse_since(str(since_str))
            if since_ts is not None:
                before = len(all_articles)
                all_articles = [a for a in all_articles if a.get("created", 0) >= since_ts]
                skipped = before - len(all_articles)
                if skipped:
                    print(f"  Skipped {skipped} article(s) before {since_str} (registry since).")

        new_articles: list[dict[str, Any]] = []
        for art in all_articles:
            art_url = art.get("url", "")
            aid = extract_article_id(art_url) if art_url else None
            if aid and aid in existing_ids:
                continue
            new_articles.append(art)

        return new_articles

    def fetch_author(
        self,
        author_entry: dict[str, Any],
        new_articles: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        classify: bool = False,
    ) -> list[Path]:
        """Download *new_articles* for one author and save to their series directory.

        If the author has no ``series_dir`` yet, LLM naming is invoked.
        Returns paths of newly saved files.
        """
        author_name = author_entry["name"]
        series_dir = author_entry.get("series_dir", "")
        saved: list[Path] = []

        # Lazy-load classifier model if needed
        if classify:
            from zhihu_cli.extensions.crank.classifier.model import load_model, predict

            load_model()

        # Resolve series directory — possibly via LLM naming
        if not series_dir:
            if dry_run:
                print(f"    [dry-run] Would download {len(new_articles)} articles to temp, then LLM-name the series")
                return saved
            series_dir = self._name_and_create_series(author_entry, new_articles)
            if not series_dir:
                print(f"    [error] Failed to name series for {author_name}", file=sys.stderr)
                return saved
            author_entry["series_dir"] = series_dir
            self.save_registry()

        target_dir = self.serial_dir / series_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for i, art in enumerate(new_articles):
            art_url = art.get("url", "")
            if not art_url:
                continue

            print(f"    [{i + 1}/{len(new_articles)}] {art_url}")

            if dry_run:
                title = art.get("title", "(unknown)")
                print(f"      [dry-run] {title}")
                continue

            try:
                metadata, markdown = scrape_article(art_url)
            except Exception as e:
                print(f"      [error] Download failed: {e}", file=sys.stderr)
                continue

            title = metadata.get("title") or art.get("title") or "untitled"
            author = metadata.get("author", {}).get("name") or author_name
            created = (metadata.get("created_time") or "")[:10] or "unknown"

            # Classification
            classification = "unknown"
            crank_probability: float | None = None
            if classify:
                try:
                    result = predict(title, markdown)
                    classification = result["label"]
                    crank_probability = result["probability"]
                    print(f"      [classify] {classification} (prob={crank_probability:.2f})")
                except Exception as e:
                    print(f"      [classify] failed: {e}", file=sys.stderr)

            filename = generate_filename(author, title, created)
            filepath = target_dir / filename

            meta: dict[str, str] = {
                "title": title,
                "author": author,
                "created": created,
                "source": art_url,
            }
            if classification != "unknown":
                meta["classification"] = classification
            if crank_probability is not None:
                meta["crank_probability"] = f"{crank_probability:.4f}"
            yaml_block = build_yaml_frontmatter(meta)
            filepath.write_text(yaml_block + markdown, encoding="utf-8")

            print(f"      → {filepath}")
            saved.append(filepath)
            wait(1.0)

        return saved

    def _name_and_create_series(self, author_entry: dict[str, Any], articles: list[dict[str, Any]]) -> str | None:
        """Download articles, sample for LLM, create a named series directory.

        Returns the directory basename, or None on failure.
        """
        author_name = author_entry["name"]
        print(f"    Naming new series for {author_name} ({len(articles)} articles)...")

        # Download article samples for LLM review
        samples: list[tuple[str, str]] = []

        # Take up to 4 random samples for the LLM
        import random

        sample_articles = random.sample(articles, min(4, len(articles)))

        for art in sample_articles:
            art_url = art.get("url", "")
            if not art_url:
                continue
            try:
                metadata, markdown = scrape_article(art_url)
            except Exception as e:
                print(f"      [error] Sample download failed: {e}", file=sys.stderr)
                continue
            title = metadata.get("title") or art.get("title") or "untitled"
            filename = generate_filename(author_name, title, (metadata.get("created_time") or "")[:10] or "unknown")
            samples.append((filename, markdown))
            wait(1.0)

        if not samples:
            print("    [error] No samples to send to LLM.", file=sys.stderr)
            return None

        series_name = call_llm_for_name(
            author_name,
            samples,
            api_base=self.llm_api_base,
            api_key=self.llm_api_key,
            model=self.llm_model,
        )
        if not series_name:
            print("    [error] LLM naming failed.", file=sys.stderr)
            return None

        safe_name = sanitize_filename(series_name)
        series_path = self.serial_dir / safe_name
        series_path.mkdir(parents=True, exist_ok=True)
        print(f"    Created series directory: {safe_name}")
        return safe_name

    # ── main entry ──────────────────────────────────────────────────────

    def run(
        self,
        *,
        check: bool = False,
        fetch: bool = False,
        author_filter: str | None = None,
        dry_run: bool = False,
        classify: bool = False,
    ) -> dict[str, Any]:
        """Main entry point.

        Args:
            check: Only report new articles, don't download.
            fetch: Download new articles.
            author_filter: Only process this author (by name).
            dry_run: Don't actually write files.
            classify: Classify downloaded articles with BERT model.

        Returns a summary dict ``{author_name: new_count}``.
        """
        self.load_registry()
        authors = self._get_enabled_authors(author_filter)

        if not authors:
            print("No enabled authors found in registry.")
            if author_filter:
                print(f'  Filter: "{author_filter}"')
            print(f"  Registry: {self.registry_path}")
            return {}

        print(f"Processing {len(authors)} author(s)...")
        summary: dict[str, Any] = {}

        for author_entry in authors:
            name = author_entry["name"]
            print(f"\n── {name} ──")

            new_articles = self.check_author(author_entry)
            summary[name] = {
                "new_count": len(new_articles),
                "articles": [a.get("title", "") for a in new_articles],
            }

            if not new_articles:
                print("  No new articles.")
                continue

            print(f"  Found {len(new_articles)} new article(s):")
            for art in new_articles:
                print(f"    - {art.get('title', '(untitled)')}  ({art.get('url', '')})")

            if check:
                continue  # check-only mode: report but don't download

            if fetch:
                saved = self.fetch_author(author_entry, new_articles, dry_run=dry_run, classify=classify)
                summary[name]["saved"] = len(saved)

        # Print final summary
        print("\n" + "=" * 50)
        print("Summary:")
        total_new = 0
        for name, info in summary.items():
            cnt = info["new_count"]
            total_new += cnt
            status = f"{cnt} new"
            if "saved" in info:
                status += f", {info['saved']} saved"
            print(f"  {name}: {status}")
        print(f"Total: {total_new} new article(s) across {len(summary)} author(s)")

        if dry_run:
            print("\n[dry-run] No files were written.")

        return summary


# ── CLI registration ────────────────────────────────────────────────────────


def register_commands(main_group: click.Group) -> None:
    """Register ``zhihu crank`` command group and its subcommands."""
    import click as _click

    @main_group.group(name="crank")
    def crank_group() -> None:
        """Crank paper monitor & archiver."""

    @crank_group.command("check")
    @_click.option("--author", "-a", "author_name", default=None, help="Only check a specific author")
    def crank_check(author_name: str | None) -> None:
        """Check for new articles from watched authors (report only)."""
        mon = CrankMonitor()
        mon.run(check=True, author_filter=author_name)

    # Shared LLM options used by ``fetch`` and ``archive``.
    _llm_options = [
        _click.option("--api-endpoint", envvar="LLM_API_BASE", help="OpenAI-compatible API endpoint (saved to cache)"),
        _click.option("--api-key", envvar="LLM_API_KEY", help="API key (saved to cache)"),
        _click.option("--model", envvar="LLM_MODEL", help="Model name (saved to cache, default: gpt-4o-mini)"),
    ]

    def _resolve_llm_config(
        api_endpoint: str | None,
        api_key: str | None,
        model: str | None,
    ) -> tuple[str, str, str]:
        """Resolve final LLM config: CLI arg → env var → cache → hardcoded default."""
        cached = load_llm_config()
        _api_base = (
            api_endpoint or os.environ.get("LLM_API_BASE") or cached.get("api_base", "https://api.openai.com/v1")
        )
        _api_key = api_key or os.environ.get("LLM_API_KEY") or cached.get("api_key", "")
        _model = model or os.environ.get("LLM_MODEL") or cached.get("model", "gpt-4o-mini")
        return _api_base, _api_key, _model

    def _llm_decorator(fn):
        for opt in reversed(_llm_options):
            fn = opt(fn)
        return fn

    @crank_group.command("fetch")
    @_click.option("--author", "-a", "author_name", default=None, help="Only fetch for a specific author")
    @_click.option("--dry-run", is_flag=True, help="Show what would be downloaded without writing files")
    @_click.option("--classify", is_flag=True, help="Classify downloaded articles with BERT model")
    @_llm_decorator
    def crank_fetch(
        author_name: str | None,
        dry_run: bool,
        classify: bool,
        api_endpoint: str | None,
        api_key: str | None,
        model: str | None,
    ) -> None:
        """Download new articles from watched authors."""
        api_base, api_key, model = _resolve_llm_config(api_endpoint, api_key, model)
        save_llm_config(api_base, api_key, model)
        mon = CrankMonitor(
            llm_api_base=api_base,
            llm_api_key=api_key,
            llm_model=model,
            classify_downloads=classify,
        )
        mon.run(fetch=True, author_filter=author_name, dry_run=dry_run, classify=classify)

    @crank_group.command("archive")
    @_click.option("--user-token", "-u", required=True, help="Zhihu user URL token (e.g. chen-bao-qun)")
    @_click.option(
        "--output-dir",
        "-o",
        default=SERIAL_PAPERS_DIR,
        help=f"Output directory for the new series (default: {SERIAL_PAPERS_DIR})",
    )
    @_click.option("--sample-count", "-n", type=int, default=4, help="Articles to sample for LLM naming")
    @_click.option(
        "--since",
        default=None,
        help="Only download articles created on or after this date (YYYY-MM-DD or YYYY/MM/DD)",
    )
    @_click.option("--dry-run", is_flag=True, help="Download but skip LLM naming")
    @_llm_decorator
    def crank_archive(
        user_token: str,
        output_dir: str,
        sample_count: int,
        since: str | None,
        dry_run: bool,
        api_endpoint: str | None,
        api_key: str | None,
        model: str | None,
    ) -> None:
        """One-shot: fetch ALL articles from a Zhihu user, LLM-name the series, and archive.

        This is the full pipeline for a newly discovered crank author.
        For known authors with a series_dir already in the registry, use ``crank fetch`` instead.
        """
        from zhihu_cli.extensions.crank.archiver import run_archiver

        api_base, api_key, model = _resolve_llm_config(api_endpoint, api_key, model)
        save_llm_config(api_base, api_key, model)

        result = run_archiver(
            user_token=user_token,
            output_dir=output_dir,
            sample_count=sample_count,
            since=since,
            dry_run=dry_run,
            api_base=api_base,
            api_key=api_key,
            model=model,
        )
        if result:
            series_dir_name = os.path.basename(result)
            author_name = (series_dir_name.split("-")[0] if "-" in series_dir_name else series_dir_name).strip()
            mon = CrankMonitor()
            mon.upsert_author(author_name, user_token, series_dir_name, since=since)
            print(f"\nSeries created: {result}")

    @crank_group.command("bootstrap")
    def crank_bootstrap() -> None:
        """Scan serial papers directories and generate initial authors_registry.json.

        Authors are discovered from existing series subdirectories.
        Fill in 'zhihu_token' fields manually before running fetch.
        """
        mon = CrankMonitor()
        mon.bootstrap_registry()

    @crank_group.command("list")
    def crank_list() -> None:
        """List all authors in the monitor registry."""
        mon = CrankMonitor()
        mon.list_authors()

    @crank_group.command("remove")
    @_click.option("--author", "-a", "author_name", required=True, help="Author name to remove from registry")
    def crank_remove(author_name: str) -> None:
        """Remove an author from the monitor registry."""
        mon = CrankMonitor()
        mon.remove_author(author_name)

    @crank_group.command("register")
    def crank_register() -> None:
        """Interactively register a new crank author to the monitor registry."""
        name = _click.prompt("Author display name", type=str).strip()

        # Scan papers dir for existing series directories matching this author
        papers_dir = Path(SERIAL_PAPERS_DIR)
        candidates: list[str] = []
        if papers_dir.is_dir():
            candidates = sorted(
                d.name
                for d in papers_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name.startswith(name)
            )

        if len(candidates) == 1:
            series_dir = _click.prompt(
                f"Series directory name [{candidates[0]}]",
                type=str,
                default=candidates[0],
                show_default=False,
            ).strip()
        elif len(candidates) > 1:
            _click.echo("Found multiple matching series directories:")
            for i, d in enumerate(candidates, 1):
                _click.echo(f"  {i}. {d}")
            _click.echo(f"  {len(candidates) + 1}. (enter manually)")
            _click.echo(f"  {len(candidates) + 2}. (skip — leave blank for LLM auto-naming)")
            choice = _click.prompt(
                "Select",
                type=int,
                default=1,
            )
            if 1 <= choice <= len(candidates):
                series_dir = candidates[choice - 1]
            elif choice == len(candidates) + 1:
                series_dir = _click.prompt("Series directory name", type=str, default="", show_default=False).strip()
            else:
                series_dir = ""
        else:
            series_dir = _click.prompt(
                "Series directory name (enter to skip, will be LLM-named on first fetch)",
                type=str,
                default="",
                show_default=False,
            ).strip()

        zhihu_token = _click.prompt("Zhihu URL token (e.g. mersenne-20)", type=str).strip()

        mon = CrankMonitor()
        mon.upsert_author(name, zhihu_token, series_dir)

    # ── classify subcommand group ──────────────────────────────────────

    @crank_group.group(name="classify")
    def crank_classify_group() -> None:
        """Crank text classifier — train, predict, discover."""

    @crank_classify_group.command("predict")
    @_click.argument("url")
    @_click.option("--threshold", type=float, default=0.5, help="Crank probability threshold")
    def crank_classify_predict(url: str, threshold: float) -> None:
        """Classify a single Zhihu article URL as crank or normal."""
        from zhihu_cli.content.handlers.article import scrape_article
        from zhihu_cli.extensions.crank.classifier.model import load_model, predict

        load_model()
        metadata, markdown = scrape_article(url)
        title = metadata.get("title", "")
        result = predict(title, markdown, threshold=threshold)

        label = result["label"]
        prob = result["probability"]
        conf = result["confidence"]
        probs = result["probabilities"]

        color = "red" if label == "crank" else "green"
        _click.echo(f"Title: {title}")
        _click.echo(f"Classification: {_click.style(label.upper(), bold=True, fg=color)}")
        _click.echo(f"Crank probability: {prob:.1%}")
        _click.echo(f"Confidence: {conf:.1%}")
        _click.echo(f"Probabilities: normal={probs[0]:.1%}, crank={probs[1]:.1%}")

    @crank_classify_group.command("train")
    @_click.option("--epochs", type=int, default=None, help="Override number of epochs")
    @_click.option("--lr", type=float, default=None, help="Override learning rate")
    @_click.option("--batch-size", type=int, default=None, help="Override batch size")
    @_click.option(
        "--hof-dir",
        default=None,
        help=f"Path to Hall of Flames serial papers dir (default: {SERIAL_PAPERS_DIR})",
    )
    def crank_classify_train(
        epochs: int | None,
        lr: float | None,
        batch_size: int | None,
        hof_dir: str | None,
    ) -> None:
        """Train the crank classifier on HoF papers + scraped negative data."""
        from pathlib import Path

        from zhihu_cli.extensions.crank.classifier.train import check_training_data_ready, train_classifier

        if not check_training_data_ready():
            raise SystemExit(1)

        overrides = {}
        if epochs is not None:
            overrides["num_epochs"] = epochs
        if lr is not None:
            overrides["learning_rate"] = lr
        if batch_size is not None:
            overrides["batch_size"] = batch_size

        hof_path = Path(hof_dir) if hof_dir else Path(SERIAL_PAPERS_DIR)
        _click.echo("Training crank classifier...")
        metadata = train_classifier(hof_path, **overrides)
        val = metadata.get("val_metrics", {})
        f1 = val.get("eval_f1", 0)
        _click.echo(f"\nTraining complete! Best F1: {f1:.4f}")

    @crank_classify_group.command("stats")
    def crank_classify_stats() -> None:
        """Show classifier model statistics and training data summary."""
        from zhihu_cli.extensions.crank.classifier.data_loader import print_data_summary
        from zhihu_cli.extensions.crank.classifier.model import get_model_stats

        stats = get_model_stats()
        _click.echo("Classifier Status")
        _click.echo(f"  Model: {stats.get('model_name', 'N/A')}")
        _click.echo(f"  Status: {stats.get('status', 'unknown')}")
        if "val_metrics" in stats:
            for k, v in stats["val_metrics"].items():
                _click.echo(f"  {k}: {v:.4f}")
        if "train_date" in stats:
            _click.echo(f"  Trained: {stats['train_date']}")
        print()
        print_data_summary()

    @crank_classify_group.command("discover")
    @_click.option(
        "--keywords",
        "-k",
        default=None,
        help="Comma-separated search keywords (default: built-in crank keywords)",
    )
    @_click.option("--threshold", type=float, default=0.7, help="Crank probability threshold")
    @_click.option("--min-ratio", type=float, default=0.5, help="Minimum crank/total ratio to flag an author")
    @_click.option("--max-per-keyword", type=int, default=50, help="Max articles per keyword")
    @_click.option("--show-articles", type=int, default=3, help="Top crank articles to show per author")
    def crank_classify_discover(
        keywords: str | None,
        threshold: float,
        min_ratio: float,
        max_per_keyword: int,
        show_articles: int,
    ) -> None:
        """Search Zhihu and identify potential new crank authors."""
        from zhihu_cli.extensions.crank.classifier.discovery import discover_cranks, discover_report

        kw_list = [k.strip() for k in keywords.split(",")] if keywords else None
        results = discover_cranks(
            keywords=kw_list,
            max_per_keyword=max_per_keyword,
            threshold=threshold,
            min_crank_ratio=min_ratio,
        )
        discover_report(results, show_articles=show_articles)

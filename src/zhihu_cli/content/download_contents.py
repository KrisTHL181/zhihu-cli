from __future__ import annotations

import argparse
import html
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, reload_session, session
from zhihu_cli.content.utils.html2markdown import PageToMarkdown
from zhihu_cli.content.utils.wait import wait

# ── media download helpers ──────────────────────────────────────────────────

# Regex patterns for media references in markdown / html
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_HTML_VIDEO_RE = re.compile(r'<video[^>]*\ssrc="([^"]+)"[^>]*>')
_HTML_IMG_RE = re.compile(r'<img[^>]*\ssrc="([^"]+)"[^>]*>')

_ZHIMG_HOST_RE = re.compile(r"\.zhimg\.com$", re.IGNORECASE)


def download_media_files(markdown: str, output_dir: str) -> tuple[str, int]:
    """Download media files referenced in *markdown* and rewrite URLs to local paths.

    Files are saved to ``<output_dir>/media/``.  Image alt-text is preferred as
    the filename stem; when unavailable a hash of the URL is used.  The session
    singleton is reused so Zhihu CDN images are fetched with auth cookies.

    Returns ``(updated_markdown, downloaded_count)``.
    """
    # ── collect (alt_text, url) pairs ──────────────────────────────────────
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for m in _MD_IMAGE_RE.finditer(markdown):
        alt, url = m.group(1), m.group(2)
        if url and url not in seen:
            pairs.append((alt, url))
            seen.add(url)

    for pattern in (_HTML_IMG_RE, _HTML_VIDEO_RE):
        for m in pattern.finditer(markdown):
            url = m.group(1)
            if url and url not in seen:
                pairs.append(("", url))
                seen.add(url)

    if not pairs:
        return markdown, 0

    # ── download ───────────────────────────────────────────────────────────
    media_dir = os.path.join(output_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    url_to_local: dict[str, str] = {}
    downloaded = 0

    for alt, url in pairs:
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception:
            continue

        # Guess extension: Content-Type → URL path → .jpg fallback
        ext = ""
        content_type = resp.headers.get("Content-Type", "")
        if content_type:
            ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""

        if not ext:
            parsed = urlparse(url)
            path = parsed.path
            # Strip Zhihu query params that mask the real extension
            if _ZHIMG_HOST_RE.search(parsed.hostname or ""):
                path = path.split("?")[0]
            guess = os.path.splitext(path)[1]
            if guess:
                ext = guess.lower()

        if not ext:
            ext = ".jpg"

        # Prefer alt-text as filename stem, otherwise hash the URL
        stem = sanitize_filename(alt)[:80] if alt and alt.strip() else ""
        if not stem:
            import hashlib

            stem = hashlib.sha256(url.encode()).hexdigest()[:12]

        # Deduplicate within this batch
        candidate = f"{stem}{ext}"
        filepath = os.path.join(media_dir, candidate)
        counter = 1
        while os.path.exists(filepath):
            candidate = f"{stem}_{counter}{ext}"
            filepath = os.path.join(media_dir, candidate)
            counter += 1

        with open(filepath, "wb") as f:
            f.write(resp.content)

        rel_path = os.path.join("media", candidate)
        url_to_local[url] = rel_path
        downloaded += 1

    if not url_to_local:
        return markdown, 0

    # ── rewrite references ─────────────────────────────────────────────────
    updated = markdown
    for remote_url, local_path in url_to_local.items():
        updated = updated.replace(remote_url, local_path)

    return updated, downloaded


def extract_config_from_curl(curl_text: str) -> tuple[str, dict[str, str]]:
    """Extract URL and headers from a cURL command."""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    full_url = url_match.group(1) if url_match else ""

    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    # Let curl_cffi handle Accept-Encoding
    headers.pop("Accept-Encoding", None)

    return full_url, headers


def extract_metadata_from_html(html_content: str) -> dict[str, str]:
    """Extract metadata (title, author, time) from HTML."""
    metadata = {"title": "untitled", "author": "unknown", "created": ""}

    # Extract title from <meta itemprop="name"> or <h1 class="Post-Title">
    title_pattern = (
        r'<meta\s+itemprop="name"\s+content="([^"]+)"|<h1[^>]*class="[^"]*Post-Title[^"]*"[^>]*>([^<]+)</h1>'
    )
    match = re.search(title_pattern, html_content)

    if match:
        raw_title = match.group(1) or match.group(2)
        clean_title = html.unescape(raw_title).strip()
        metadata["title"] = clean_title.replace("/", "_")

    # Extract author
    author_match = re.search(r'<a[^>]*class="[^"]*UserLink-link[^"]*"[^>]*>([^<]+)</a>', html_content)
    if author_match:
        metadata["author"] = html.unescape(author_match.group(1)).strip().replace("/", "_")

    # Extract publish time from <meta itemprop="datePublished">
    created_match = re.search(r'<meta\s+itemProp="datePublished"\s+content="([^"]+)"', html_content)
    if created_match:
        created_raw = created_match.group(1)
        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            metadata["created"] = created_dt.strftime("%Y-%m-%d")
        except ValueError:
            metadata["created"] = created_raw[:10] if len(created_raw) >= 10 else created_raw

    # Fallback: extract from page text
    if not metadata["created"]:
        time_match = re.search(r"published\s+(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}", html_content)
        if time_match:
            metadata["created"] = time_match.group(1)

    return metadata


def sanitize_filename(name: str) -> str:
    """Sanitize filename by removing illegal characters."""
    import html

    name = html.unescape(name)
    if os.name == "nt":
        illegal_chars = ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]
        illegal_filenames = [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        ]
    else:
        illegal_chars = ["/", "\0"]
        illegal_filenames = []

    for char in illegal_chars:
        name = name.replace(char, "_")
    base_name = os.path.splitext(name)[0]
    if base_name.upper() in illegal_filenames:
        name = f"_{name}_"
    name = name.rstrip(". ")
    name = re.sub(r"_+", "_", name)
    return name.strip().strip("_")


def get_safe_filename(long_title: str, ext: str = ".md", max_bytes: int = 240) -> str:
    """Truncate title to fit within max_bytes UTF-8 bytes, preserving whole characters."""
    ext_bytes = len(ext.encode("utf-8"))
    available_bytes = max_bytes - ext_bytes

    if len(long_title.encode("utf-8")) <= available_bytes:
        return long_title + ext

    current_bytes = 0
    truncated_title = ""

    for char in long_title:
        char_bytes = len(char.encode("utf-8"))
        if current_bytes + char_bytes <= available_bytes:
            truncated_title += char
            current_bytes += char_bytes
        else:
            break

    return f"{truncated_title}...{ext}"


def build_yaml_frontmatter(metadata: dict[str, str]) -> str:
    """Build a YAML frontmatter block from a metadata dict."""
    body = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{body}\n---\n\n"


def save_article(url: str, metadata: dict[str, str], markdown: str, output_dir: str, with_media: bool = False) -> str:
    """Save a downloaded article as Markdown with YAML frontmatter.

    Returns the file path.
    """
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}

    title = sanitize_filename(metadata.get("title", "untitled"))
    author = sanitize_filename(metadata.get("author", "unknown"))
    created = metadata.get("created", "") or "unknown"
    full_name = f"{title}_{author}_{created}"
    filename = get_safe_filename(full_name, ext=".md", max_bytes=240)
    filepath = os.path.join(output_dir, filename)

    meta = {**metadata, "source": url}
    file_content = build_yaml_frontmatter(meta) + markdown

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(file_content)
    return filepath


def save_pin(url: str, metadata: dict[str, str], markdown: str, output_dir: str, with_media: bool = False) -> str:
    """Save a downloaded pin as Markdown with YAML frontmatter.

    Returns the file path.
    """
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}

    author = sanitize_filename(metadata.get("author", "unknown"))
    created = metadata.get("created", "unknown")
    preview = re.sub(r"\s+", " ", markdown)[:30]
    preview = sanitize_filename(preview)
    if not preview:
        preview = metadata.get("pin_id", "unknown")
    full_name = f"{author}_{created}_{preview}"
    filename = get_safe_filename(full_name, ext=".md", max_bytes=240)
    filepath = os.path.join(output_dir, filename)

    meta = {**metadata, "source": url}
    file_content = build_yaml_frontmatter(meta) + markdown

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(file_content)
    return filepath


def _resolve_author(question_data: dict, answer_data: dict, users: dict[str, dict]) -> str:
    """Resolve author name from entity data (question > answer > users)."""
    # Try question author
    author = question_data.get("author", {})
    if isinstance(author, dict) and author.get("name"):
        return author["name"]

    # Try answer author as string (user ID reference)
    ans_author = answer_data.get("author", "")
    if isinstance(ans_author, str) and ans_author in users:
        return users[ans_author].get("name", "unknown")

    # Try answer author as dict
    if isinstance(ans_author, dict) and ans_author.get("name"):
        return ans_author["name"]

    return "unknown"


def _resolve_created(answer_data: dict, question_data: dict) -> str:
    """Resolve created date from entity timestamps (answer > question)."""
    created_ts = answer_data.get("created_time") or answer_data.get("created") or answer_data.get("createdTime")
    if not created_ts:
        created_ts = question_data.get("created")

    if created_ts:
        try:
            if isinstance(created_ts, (int, float)) and created_ts > 1e12:
                created_ts = created_ts / 1000
            return datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass

    return "unknown"


class ContentDownloader:
    """Zhihu content downloader."""

    def __init__(self, output_dir: str | None = None, with_media: bool = False) -> None:
        if output_dir is None:
            output_dir = str(Path.home() / ".zhihu-cli" / "downloads")
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.headers: dict[str, str] = {}
        self.md_converter: PageToMarkdown = PageToMarkdown(skip_empty=True)
        self.with_media = with_media

    def load_headers_from_curl(self, quick_mode: bool = False) -> bool:
        """Load request headers from user-pasted cURL command."""
        if quick_mode:
            headers = cache_manager.load_headers()
            if headers:
                self.headers = headers
                print("[Success] Loaded cached headers")
                return True

        print("--- Paste cURL command for article page ---")
        print("(Copy as cURL from browser DevTools)")
        curl_input = sys.stdin.read()

        if not curl_input.strip():
            print("[Error] No valid cURL input")
            return False

        url, headers = extract_config_from_curl(curl_input)

        if not headers:
            print("[Error] Could not extract headers from cURL")
            return False

        self.headers = headers
        cache_manager.save_headers(headers)
        reload_session()
        print("[Success] Headers configured and cached.")
        return True

    def download_answers(self, urls: list[str], delay: float = 1.0) -> list[dict]:
        """Download answer pages and convert to Markdown.

        :returns: list of result dicts with keys ``url``, ``title``, ``author``,
            ``filepath``, ``success``, and ``error``.
        """
        results: list[dict] = []
        if not self.headers:
            print("[Error] Load headers first")
            return results

        for url in urls:
            try:
                html_content = fetch_page_html(url)

                page_data = get_page_state(html_content)
                question_data = page_data["questions"][next(iter(page_data["questions"]))]
                answer_data = page_data["answers"][next(iter(page_data["answers"]))]

                question_title = question_data["title"]
                question_detail = self.md_converter.convert(question_data.get("detail", ""))

                author = _resolve_author(question_data, answer_data, page_data.get("users", {}))

                # Created: prefer entity timestamps
                created = _resolve_created(answer_data, question_data)

                answer_markdown = self.md_converter.convert(answer_data["content"], url)

                meta = {
                    "title": question_title,
                    "question_detail": question_detail,
                    "author": author,
                    "created": created,
                }
                filepath = save_article(url, meta, answer_markdown, self.output_dir, with_media=self.with_media)

                results.append(
                    {
                        "url": url,
                        "title": question_title,
                        "author": author,
                        "filepath": filepath,
                        "success": True,
                        "error": None,
                    }
                )

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  Question: {question_title}")
                print(f"  Author: {author}")
                print(f"  Time: {created}")

            except TimeoutError:
                results.append(
                    {
                        "url": url,
                        "title": None,
                        "author": None,
                        "filepath": None,
                        "success": False,
                        "error": "Timeout",
                    }
                )
                print(f"[Timeout] {url}")
                continue
            except Exception as e:
                results.append(
                    {
                        "url": url,
                        "title": None,
                        "author": None,
                        "filepath": None,
                        "success": False,
                        "error": str(e),
                    }
                )
                print(f"[Error] Failed to process {url}: {e}")
                continue
            wait(delay)

        return results

    def fetch_article(self, url: str) -> tuple[dict[str, str], str]:
        """Fetch and convert a single article. Returns (metadata, markdown_content)."""
        if not self.headers:
            raise RuntimeError("Headers not loaded")
        html_content = fetch_page_html(url)

        entities = get_page_state(html_content)
        articles = entities.get("articles", {})
        if articles:
            article = next(iter(articles.values()))

            # ── metadata from structured entity data ──────────────
            title = article.get("title", "untitled")
            title = html.unescape(title).strip().replace("/", "_")

            author_info = article.get("author", {}) or {}
            author = author_info.get("name", "unknown")
            author = html.unescape(author).strip().replace("/", "_")

            created = ""
            created_ts = article.get("created")
            if created_ts:
                try:
                    if isinstance(created_ts, (int, float)) and created_ts > 1e12:
                        created_ts = created_ts / 1000
                    created = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass

            metadata = {"title": title, "author": author, "created": created}

            # Use the article content from the entity directly (cleaner
            # than parsing the full page HTML — fewer lingering HTML
            # entities, and matches what scrape_article() does).
            content_html = article.get("content", "")
            markdown_content = self.md_converter.convert(content_html, url)
            return metadata, markdown_content

    def download_articles(self, urls: list[str], delay: float = 1.0) -> list[dict]:
        """Download article pages and convert to Markdown.

        :returns: list of result dicts with keys ``url``, ``title``, ``author``,
            ``filepath``, ``success``, and ``error``.
        """
        results: list[dict] = []
        if not self.headers:
            print("[Error] Load headers first")
            return results

        for url in urls:
            try:
                metadata, markdown_content = self.fetch_article(url)
                filepath = save_article(url, metadata, markdown_content, self.output_dir, with_media=self.with_media)

                results.append(
                    {
                        "url": url,
                        "title": metadata["title"],
                        "author": metadata["author"],
                        "filepath": filepath,
                        "success": True,
                        "error": None,
                    }
                )

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  Title: {metadata['title']}")
                print(f"  Author: {metadata['author']}")
                print(f"  Time: {metadata['created']}")

            except TimeoutError:
                results.append(
                    {
                        "url": url,
                        "title": None,
                        "author": None,
                        "filepath": None,
                        "success": False,
                        "error": "Timeout",
                    }
                )
                print(f"[Timeout] {url}")
                continue
            except Exception as e:
                results.append(
                    {
                        "url": url,
                        "title": None,
                        "author": None,
                        "filepath": None,
                        "success": False,
                        "error": str(e),
                    }
                )
                print(f"[Error] Failed to process {url}: {e}")
                continue
            wait(delay)

        return results

    def download_pins(self, urls: list[str], delay: float = 1.0) -> None:
        """Download pin pages and convert to Markdown."""
        if not self.headers:
            print("[Error] Load headers first")
            return

        for url in urls:
            try:
                html_content = fetch_page_html(url)

                try:
                    entities = get_page_state(html_content)
                except ValueError as e:
                    print(f"[Error] {e} in {url}")
                    continue
                pins = entities.get("pins", {})
                if not pins:
                    print(f"[Error] No pin data found in {url}")
                    continue

                pin_id = next(iter(pins.keys()))
                pin = pins[pin_id]

                users = entities.get("users", {})
                author_id = pin.get("author")
                author_name = "unknown"
                if author_id and author_id in users:
                    author_name = users[author_id].get("name", "unknown")

                created_ts = pin.get("created")
                if created_ts:
                    created_date = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
                else:
                    created_date = "unknown"

                ip_info = pin.get("ipInfo", "")

                content_parts = pin.get("content", [])
                markdown_lines = []

                for part in content_parts:
                    part_type = part.get("type")
                    if part_type == "text":
                        html_fragment = part.get("content", "")
                        if html_fragment:
                            md = self.md_converter.convert(html_fragment, url)
                            if md:
                                markdown_lines.append(md)
                    elif part_type == "image":
                        img_url = part.get("url", "")
                        alt = part.get("alt", "image")
                        if img_url:
                            markdown_lines.append(f"![{alt}]({img_url})")
                    elif part_type == "video":
                        video_url = part.get("url", "")
                        if video_url:
                            markdown_lines.append(f"[video]({video_url})")

                if not markdown_lines and pin.get("contentHtml"):
                    markdown_lines.append(self.md_converter.convert(pin["contentHtml"], url))

                markdown_content = "\n\n".join(markdown_lines).strip()
                if not markdown_content:
                    markdown_content = "(no content)"

                meta = {
                    "author": author_name,
                    "created": created_date,
                    "ip": ip_info,
                    "pin_id": pin_id,
                }
                filepath = save_pin(url, meta, markdown_content, self.output_dir, with_media=self.with_media)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  Author: {author_name}")
                print(f"  Time: {created_date}")
                print(f"  IP: {ip_info}")

            except TimeoutError:
                print(f"[Timeout] {url}")
                continue
            except Exception as e:
                print(f"[Error] Failed to process {url}: {e}")
                continue
            wait(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zhihu content downloader (HTML → Markdown)")

    parser.add_argument(
        "--download-articles", nargs="+", metavar="URL", help="Download article pages and convert to Markdown"
    )
    parser.add_argument("--download-answers", nargs="+", metavar="URL", help="Download answer pages")
    parser.add_argument("--download-pins", nargs="+", metavar="URL", help="Download pin pages")
    parser.add_argument(
        "--output-dir", type=str, default=str(Path.home() / ".zhihu-cli" / "downloads"), help="Output directory"
    )

    args = parser.parse_args()

    downloader = ContentDownloader(output_dir=args.output_dir)

    if not downloader.load_headers_from_curl(quick_mode=True):
        sys.exit(1)

    try:
        if args.download_articles:
            downloader.download_articles(args.download_articles)
        elif args.download_answers:
            downloader.download_answers(args.download_answers)
        elif args.download_pins:
            downloader.download_pins(args.download_pins)
        else:
            parser.print_help()
            sys.exit(1)
    except NotImplementedError as e:
        print(f"[Error] {e}")
        sys.exit(1)
    except TimeoutError:
        print("[Fatal] Operation timed out")
        sys.exit(1)


if __name__ == "__main__":
    main()

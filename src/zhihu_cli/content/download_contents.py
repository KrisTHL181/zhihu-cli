import argparse
import html
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, reload_session, session
from zhihu_cli.content.utils.html2markdown import PageToMarkdown


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


def save_article(url: str, metadata: dict[str, str], markdown: str, output_dir: str) -> str:
    """Save a downloaded article as Markdown with YAML frontmatter.

    Returns the file path.
    """
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


def save_pin(url: str, metadata: dict[str, str], markdown: str, output_dir: str) -> str:
    """Save a downloaded pin as Markdown with YAML frontmatter.

    Returns the file path.
    """
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

    def __init__(self, output_dir: str | None = None) -> None:
        if output_dir is None:
            output_dir = str(Path.home() / ".zhihu-cli" / "downloads")
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.headers: dict[str, str] = {}
        self.md_converter: PageToMarkdown = PageToMarkdown(skip_empty=True)

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

    def _html_to_markdown(self, html_content: str, url: str = "") -> str:
        """Convert HTML to Markdown, falling back to BeautifulSoup on failure."""
        if not html_content:
            return ""

        try:
            markdown = self.md_converter.convert(html_content, url)
            if markdown and markdown.strip():
                return markdown
        except Exception as e:
            print(f"[Warning] md_converter failed: {e}, falling back to BeautifulSoup")

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator="\n")
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)
            return text
        except Exception as e:
            print(f"[Error] BeautifulSoup fallback failed: {e}")
            return ""

    def download_answers(self, urls: list[str], delay: float = 1.0) -> None:
        """Download answer pages and convert to Markdown."""
        if not self.headers:
            print("[Error] Load headers first")
            return

        for url in urls:
            try:
                html_content = fetch_page_html(url)

                page_data = get_page_state(html_content)
                question_data = page_data["questions"][next(iter(page_data["questions"]))]
                answer_data = page_data["answers"][next(iter(page_data["answers"]))]

                question_title = question_data["title"]
                question_detail = self._html_to_markdown(question_data.get("detail", ""))

                # Author: prefer entity, fall back to HTML
                author = _resolve_author(question_data, answer_data, page_data.get("users", {}))
                if author == "unknown":
                    soup = BeautifulSoup(html_content, "html.parser")
                    author_elem = soup.select_one("div.AuthorInfo a.UserLink-link")
                    author = author_elem.img["alt"].strip() if author_elem and author_elem.img else "unknown"

                # Created: prefer entity timestamps
                created = _resolve_created(answer_data, question_data)

                answer_markdown = self._html_to_markdown(answer_data["content"], url)

                meta = {
                    "title": question_title,
                    "question_detail": question_detail,
                    "author": author,
                    "created": created,
                }
                filepath = save_article(url, meta, answer_markdown, self.output_dir)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  Question: {question_title}")
                print(f"  Author: {author}")
                print(f"  Time: {created}")

            except TimeoutError:
                print(f"[Timeout] {url}")
                continue
            except Exception as e:
                print(f"[Error] Failed to process {url}: {e}")
                continue
            time.sleep(delay)

    def fetch_article(self, url: str) -> tuple[dict[str, str], str]:
        """Fetch and convert a single article. Returns (metadata, markdown_content)."""
        if not self.headers:
            raise RuntimeError("Headers not loaded")
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        html_content = resp.text
        metadata = extract_metadata_from_html(html_content)
        markdown_content = self.md_converter.convert(html_content, url)
        return metadata, markdown_content

    def download_articles(self, urls: list[str], delay: float = 1.0) -> None:
        """Download article pages and convert to Markdown."""
        if not self.headers:
            print("[Error] Load headers first")
            return

        for url in urls:
            try:
                metadata, markdown_content = self.fetch_article(url)
                filepath = save_article(url, metadata, markdown_content, self.output_dir)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  Title: {metadata['title']}")
                print(f"  Author: {metadata['author']}")
                print(f"  Time: {metadata['created']}")

            except TimeoutError:
                print(f"[Timeout] {url}")
                continue
            time.sleep(delay)

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
                            md = self._html_to_markdown(html_fragment, url)
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
                    markdown_lines.append(self._html_to_markdown(pin["contentHtml"], url))

                markdown_content = "\n\n".join(markdown_lines).strip()
                if not markdown_content:
                    markdown_content = "(no content)"

                meta = {
                    "author": author_name,
                    "created": created_date,
                    "ip": ip_info,
                    "pin_id": pin_id,
                }
                filepath = save_pin(url, meta, markdown_content, self.output_dir)

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
            time.sleep(delay)


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

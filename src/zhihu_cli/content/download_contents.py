import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import reload_session, session
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

    def _extract_answer_metadata(self, soup: BeautifulSoup) -> dict[str, str]:
        """Extract question title, detail, author, and publish time from answer page."""
        metadata = {}

        # Question title
        title_elem = soup.select_one("h1.QuestionHeader-title")
        metadata["question_title"] = title_elem.get_text(strip=True) if title_elem else "untitled"

        # Question detail HTML
        detail_elem = soup.select_one('span[itemprop="text"]')
        if not detail_elem:
            detail_elem = soup.select_one("div.QuestionRichText")
        if detail_elem:
            metadata["question_detail_html"] = str(detail_elem)
        else:
            metadata["question_detail_html"] = ""

        # Author
        author_elem = soup.select_one("div.AuthorInfo a.UserLink-link")
        metadata["author"] = author_elem.img["alt"].strip() if author_elem else "unknown"

        # Publish time
        created_meta = soup.select_one('meta[itemprop="dateCreated"]')
        if created_meta and created_meta.get("content"):
            created_raw = created_meta["content"]
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                metadata["created"] = created_dt.strftime("%Y-%m-%d")
            except ValueError:
                metadata["created"] = created_raw[:10] if len(created_raw) >= 10 else ""
        else:
            time_elem = soup.select_one(".ContentItem-time")
            if time_elem:
                time_text = time_elem.get_text()
                match = re.search(r"(\d{4}-\d{2}-\d{2})", time_text)
                if match:
                    metadata["created"] = match.group(1)
                else:
                    metadata["created"] = "unknown"
            else:
                metadata["created"] = "unknown"

        return metadata

    def _extract_answer_content_html(self, soup: BeautifulSoup) -> str:
        """Extract answer content HTML."""
        content_elem = soup.select_one("div.RichContent-inner span.RichText")
        if not content_elem:
            content_elem = soup.select_one("div.RichContent-inner")
        if content_elem:
            return str(content_elem)
        return ""

    def download_answers(self, urls: list[str], delay: float = 1.0) -> None:
        """Download answer pages and convert to Markdown."""
        if not self.headers:
            print("[Error] Load headers first")
            return

        for url in urls:
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                html_content = resp.text

                soup = BeautifulSoup(html_content, "html.parser")

                meta = self._extract_answer_metadata(soup)

                page_data = json.loads(soup.find("script", id="js-initialData").string)["initialState"]["entities"]
                question_data = page_data["questions"][next(iter(page_data["questions"]))]
                answer_data = page_data["answers"][next(iter(page_data["answers"]))]

                question_title = question_data["title"]
                question_detail = self._html_to_markdown(question_data["detail"])

                author = meta["author"]
                created = meta["created"]

                answer_markdown = self._html_to_markdown(answer_data["content"], url)

                json_line = json.dumps(
                    {"question_name": question_title, "question_detail": question_detail}, ensure_ascii=False
                )

                file_content = f"{json_line}\n---\n{answer_markdown}"

                safe_title = sanitize_filename(question_title)
                safe_author = sanitize_filename(author)
                safe_created = sanitize_filename(created) if created else "unknown"
                filename = f"{safe_title}_{safe_author}_{safe_created}.md"
                if len(filename) > 200:
                    filename = filename[:200]

                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)

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

                title = sanitize_filename(metadata["title"])
                author = sanitize_filename(metadata["author"])
                created = metadata["created"] if metadata["created"] else "unknown"

                filename = f"{title}_{author}_{created}.md"
                if len(filename) > 200:
                    filename = filename[:200]

                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(markdown_content)

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
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                html_content = resp.text
                soup = BeautifulSoup(html_content, "html.parser")

                init_script = soup.find("script", id="js-initialData")
                if not init_script:
                    print(f"[Error] No js-initialData found in {url}")
                    continue

                data = json.loads(init_script.string)
                entities = data.get("initialState", {}).get("entities", {})
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

                preview = re.sub(r"\s+", " ", markdown_content)[:30]
                preview = sanitize_filename(preview)
                if not preview:
                    preview = pin_id
                safe_author = sanitize_filename(author_name)
                filename = f"{safe_author}_{created_date}_{preview}.md"
                if len(filename) > 200:
                    filename = filename[:200]
                filepath = os.path.join(self.output_dir, filename)

                json_meta = json.dumps(
                    {"author": author_name, "created": created_date, "ip": ip_info, "pin_id": pin_id, "url": url},
                    ensure_ascii=False,
                )
                file_content = f"{json_meta}\n---\n{markdown_content}"

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)

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

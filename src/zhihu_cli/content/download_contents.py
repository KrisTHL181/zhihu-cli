import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime

from bs4 import BeautifulSoup

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import reload_session, session
from zhihu_cli.content.utils.html2markdown import PageToMarkdown


def extract_config_from_curl(curl_text: str) -> tuple[str, dict[str, str]]:
    """从 cURL 命令中提取 URL 和 Headers"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    full_url = url_match.group(1) if url_match else ""

    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    # 移除 Accept-Encoding，让 curl_cffi 自己处理
    headers.pop("Accept-Encoding", None)

    return full_url, headers


def extract_metadata_from_html(html_content: str) -> dict[str, str]:
    """
    从 HTML 中提取元数据（标题、作者、时间）
    Returns:
        {'title': str, 'author': str, 'created': str}
    """
    metadata = {"title": "untitled", "author": "unknown", "created": ""}

    # 提取标题
    # 匹配 <meta itemprop="name"> 或 <h1 class="Post-Title">
    title_pattern = (
        r'<meta\s+itemprop="name"\s+content="([^"]+)"|<h1[^>]*class="[^"]*Post-Title[^"]*"[^>]*>([^<]+)</h1>'
    )
    match = re.search(title_pattern, html_content)

    if match:
        # 找到非空的 group
        raw_title = match.group(1) or match.group(2)
        # 核心修改：只转义，只处理斜杠，不限制中英文数字
        clean_title = html.unescape(raw_title).strip()
        metadata["title"] = clean_title.replace("/", "_")  # 仅替换路径分隔符

    # 提取作者 (同理，不再使用正则强制限制字符)
    author_match = re.search(r'<a[^>]*class="[^"]*UserLink-link[^"]*"[^>]*>([^<]+)</a>', html_content)
    if author_match:
        metadata["author"] = html.unescape(author_match.group(1)).strip().replace("/", "_")

    # 提取发布时间
    # 尝试从 meta 标签中获取
    created_match = re.search(r'<meta\s+itemProp="datePublished"\s+content="([^"]+)"', html_content)
    if created_match:
        created_raw = created_match.group(1)
        try:
            # 解析 ISO 8601 格式时间并转换为 YYYY-mm-dd
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            metadata["created"] = created_dt.strftime("%Y-%m-%d")
        except ValueError:
            metadata["created"] = created_raw[:10] if len(created_raw) >= 10 else created_raw

    # 如果 meta 标签中没有，尝试从页面显示的发布时间中获取
    if not metadata["created"]:
        time_match = re.search(r"发布于\s+(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}", html_content)
        if time_match:
            metadata["created"] = time_match.group(1)

    return metadata


def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    import html

    name = html.unescape(name)
    # 只保留字母、数字、中文、下划线、连字符
    name = re.sub(r"[^\w\u4e00-\u9fa5\-]", "_", name)
    # 压缩连续下划线
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


class ContentDownloader:
    """知乎内容下载器"""

    def __init__(self, output_dir: str = "./downloads") -> None:
        """
        初始化下载器
        Args:
            output_dir: 输出目录
        """
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.headers: dict[str, str] = {}
        self.md_converter: PageToMarkdown = PageToMarkdown(skip_empty=True)

    def load_headers_from_curl(self, quick_mode: bool = False) -> bool:
        """
        从用户粘贴的 cURL 命令中加载请求头
        Returns:
            是否成功加载
        """
        if quick_mode:
            headers = cache_manager.load_headers()
            if headers:
                self.headers = headers
                print("[Success] Loaded cached headers from .cache/headers.json")
                return True

        print("--- 请粘贴【获取文章页面】的 cURL 命令 ---")
        print("(在浏览器开发者工具中复制为 cURL 格式)")
        curl_input = sys.stdin.read()

        if not curl_input.strip():
            print("[Error] 未输入有效的 cURL 命令")
            return False

        url, headers = extract_config_from_curl(curl_input)

        if not headers:
            print("[Error] 无法从 cURL 中提取请求头")
            return False

        self.headers = headers
        cache_manager.save_headers(headers)
        reload_session()
        print("[Success] Headers configured and cached.")
        return True

    def _html_to_markdown(self, html_content: str, url: str = "") -> str:
        """
        将 HTML 转换为 Markdown，优先使用 md_converter，失败时回退到 BeautifulSoup
        """
        if not html_content:
            return ""

        try:
            # 尝试使用 PageToMarkdown 转换
            markdown = self.md_converter.convert(html_content, url)
            if markdown and markdown.strip():
                return markdown
        except Exception as e:
            print(f"[Warning] md_converter failed: {e}, falling back to BeautifulSoup")

        # 回退：使用 BeautifulSoup 提取纯文本（简单转换，不处理复杂格式）
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            # 移除脚本和样式
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
        """
        从回答页面的 BeautifulSoup 对象中提取问题标题、问题详情、作者、发布时间
        Returns:
            {'question_title': str, 'question_detail_html': str, 'author': str, 'created': str}
        """
        metadata = {}

        # 问题标题
        title_elem = soup.select_one("h1.QuestionHeader-title")
        metadata["question_title"] = title_elem.get_text(strip=True) if title_elem else "untitled"

        # 问题详情 HTML - 优先使用 itemProp="text" 的 span，其次使用 .QuestionRichText
        detail_elem = soup.select_one('span[itemprop="text"]')
        if not detail_elem:
            detail_elem = soup.select_one("div.QuestionRichText")
        if detail_elem:
            # 保留内部 HTML 以便转换
            metadata["question_detail_html"] = str(detail_elem)
        else:
            metadata["question_detail_html"] = ""

        # 作者
        author_elem = soup.select_one("div.AuthorInfo a.UserLink-link")
        metadata["author"] = author_elem.img["alt"].strip() if author_elem else "unknown"

        # 发布时间 - 优先使用 meta[itemprop="dateCreated"]
        created_meta = soup.select_one('meta[itemprop="dateCreated"]')
        if created_meta and created_meta.get("content"):
            created_raw = created_meta["content"]
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                metadata["created"] = created_dt.strftime("%Y-%m-%d")
            except ValueError:
                metadata["created"] = created_raw[:10] if len(created_raw) >= 10 else ""
        else:
            # 回退：从页面文本中提取
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
        """
        提取回答内容的 HTML
        """
        content_elem = soup.select_one("div.RichContent-inner span.RichText")
        if not content_elem:
            content_elem = soup.select_one("div.RichContent-inner")
        if content_elem:
            return str(content_elem)
        return ""

    def download_answers(self, urls: list[str], delay: float = 1.0) -> None:
        """
        下载回答页面并转换为 Markdown
        输出格式：
        - 文件名：{问题标题}_{作者}_{回答日期}.md
        - 文件内容：第一行 JSON（含 question_name, question_detail）
                    第二行 ---
                    第三行开始为回答的 Markdown 内容
        """
        if not self.headers:
            print("[Error] 请先加载请求头 (会自动提示)")
            return

        for url in urls:
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                html_content = resp.text

                # 使用 BeautifulSoup 解析页面
                soup = BeautifulSoup(html_content, "html.parser")

                meta = self._extract_answer_metadata(soup)

                page_data = json.loads(soup.find("script", id="js-initialData").string)["initialState"]["entities"]
                question_data = page_data["questions"][next(iter(page_data["questions"]))]
                answer_data = page_data["answers"][next(iter(page_data["answers"]))]

                question_title = question_data["title"]
                question_detail = self._html_to_markdown(question_data["detail"])

                author = meta["author"]
                created = meta["created"]

                # 提取回答内容
                answer_markdown = self._html_to_markdown(answer_data["content"], url)

                # 构建 JSON 行
                json_line = json.dumps(
                    {"question_name": question_title, "question_detail": question_detail}, ensure_ascii=False
                )

                # 构建完整文件内容
                file_content = f"{json_line}\n---\n{answer_markdown}"

                # 生成文件名：{question_title}_{author}_{created}.md
                safe_title = sanitize_filename(question_title)
                safe_author = sanitize_filename(author)
                safe_created = sanitize_filename(created) if created else "unknown"
                filename = f"{safe_title}_{safe_author}_{safe_created}.md"
                # 限制文件名长度
                if len(filename) > 200:
                    filename = filename[:200]

                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  问题：{question_title}")
                print(f"  作者：{author}")
                print(f"  时间：{created}")

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
        """
        下载文章页面 HTML 并转换为 Markdown
        Args:
            urls: 文章 URL 列表
            delay: 请求间隔时间
        """
        if not self.headers:
            print("[Error] 请先加载请求头 (会自动提示)")
            return

        for url in urls:
            try:
                metadata, markdown_content = self.fetch_article(url)

                # 生成文件名：内容标题_作者名_时间.md
                title = sanitize_filename(metadata["title"])
                author = sanitize_filename(metadata["author"])
                created = metadata["created"] if metadata["created"] else "unknown"

                filename = f"{title}_{author}_{created}.md"
                # 限制文件名长度
                if len(filename) > 200:
                    filename = filename[:200]

                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(markdown_content)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  标题：{metadata['title']}")
                print(f"  作者：{metadata['author']}")
                print(f"  时间：{metadata['created']}")

            except TimeoutError:
                print(f"[Timeout] {url}")
                continue
            time.sleep(delay)

    def download_pins(self, urls: list[str], delay: float = 1.0) -> None:
        """
        下载想法页面并转换为 Markdown

        输出格式：
        - 文件名：{作者}_{日期}_{内容预览}.md
        - 文件内容：第一行 JSON（含 author, created, ip, pin_id, url）
                    第二行 ---
                    第三行开始为想法的 Markdown 内容
        """
        if not self.headers:
            print("[Error] 请先加载请求头 (会自动提示)")
            return

        for url in urls:
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                html_content = resp.text
                soup = BeautifulSoup(html_content, "html.parser")

                # 1. 提取 js-initialData
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

                # 获取唯一的 pin 对象
                pin_id = next(iter(pins.keys()))
                pin = pins[pin_id]

                # 2. 提取作者信息
                users = entities.get("users", {})
                author_id = pin.get("author")
                author_name = "unknown"
                if author_id and author_id in users:
                    author_name = users[author_id].get("name", "unknown")

                # 3. 提取时间戳并格式化
                created_ts = pin.get("created")
                if created_ts:
                    created_date = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
                else:
                    created_date = "unknown"

                # 4. 提取 IP 属地
                ip_info = pin.get("ipInfo", "")

                # 5. 提取内容并转换为 Markdown
                content_parts = pin.get("content", [])
                markdown_lines = []

                for part in content_parts:
                    part_type = part.get("type")
                    if part_type == "text":
                        # 文本片段，可能包含 HTML 标签
                        html_fragment = part.get("content", "")
                        if html_fragment:
                            # 使用已有的 _html_to_markdown 方法转换
                            md = self._html_to_markdown(html_fragment, url)
                            if md:
                                markdown_lines.append(md)
                    elif part_type == "image":
                        # 图片
                        img_url = part.get("url", "")
                        alt = part.get("alt", "image")
                        if img_url:
                            markdown_lines.append(f"![{alt}]({img_url})")
                    elif part_type == "video":
                        # 视频（简单记录链接）
                        video_url = part.get("url", "")
                        if video_url:
                            markdown_lines.append(f"[视频]({video_url})")
                    # 其他类型可以按需扩展

                # 如果没有解析出内容，回退到 contentHtml 字段
                if not markdown_lines and pin.get("contentHtml"):
                    markdown_lines.append(self._html_to_markdown(pin["contentHtml"], url))

                # 合并 Markdown 内容
                markdown_content = "\n\n".join(markdown_lines).strip()
                if not markdown_content:
                    markdown_content = "(无内容)"

                # 6. 生成文件名：{作者}_{日期}_{内容预览}.md
                # 内容预览：取前30个非空白字符
                preview = re.sub(r"\s+", " ", markdown_content)[:30]
                preview = sanitize_filename(preview)
                if not preview:
                    preview = pin_id
                safe_author = sanitize_filename(author_name)
                filename = f"{safe_author}_{created_date}_{preview}.md"
                if len(filename) > 200:
                    filename = filename[:200]
                filepath = os.path.join(self.output_dir, filename)

                # 7. 构建文件内容（JSON 元数据行 + --- + Markdown）
                json_meta = json.dumps(
                    {"author": author_name, "created": created_date, "ip": ip_info, "pin_id": pin_id, "url": url},
                    ensure_ascii=False,
                )
                file_content = f"{json_meta}\n---\n{markdown_content}"

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)

                print(f"[Success] {url}")
                print(f"  -> {filepath}")
                print(f"  作者：{author_name}")
                print(f"  时间：{created_date}")
                print(f"  IP：{ip_info}")

            except TimeoutError:
                print(f"[Timeout] {url}")
                continue
            except Exception as e:
                print(f"[Error] Failed to process {url}: {e}")
                continue
            time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="知乎内容下载工具 (HTML → Markdown)")

    # 定义下载子命令/参数
    parser.add_argument(
        "--download-articles", nargs="+", metavar="URL", help="下载文章页面并转换为 Markdown (支持多个 URL)"
    )
    parser.add_argument("--download-answers", nargs="+", metavar="URL", help="下载回答页面")
    parser.add_argument("--download-pins", nargs="+", metavar="URL", help="下载想法页面")
    parser.add_argument("--output-dir", type=str, default="./downloads", help="输出目录 (默认：./downloads)")

    args = parser.parse_args()

    downloader = ContentDownloader(output_dir=args.output_dir)

    # 加载 cURL 请求头
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

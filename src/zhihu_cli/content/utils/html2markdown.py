"""
Zhihu Backup to Markdown Converter

Convert Zhihu HTML content (answers, articles, pins) to Markdown format.
Adapted from the Tampermonkey script "zhihu-backup-collect".
"""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

_eeimg_re = re.compile(r"eeimg|equation")


class ZhihuLinkConverter:
    """Convert Zhihu internal links to normal URLs."""

    @staticmethod
    def normalize_link(link: str) -> str:
        """Convert a Zhihu link to its original target."""
        if not link:
            return link

        # Handle link.zhihu.com redirections
        if "link.zhihu.com" in link:
            parsed = urlparse(link)
            query = parse_qs(parsed.query)
            target = query.get("target", [None])[0]
            if target:
                return target
        return link


class ZhihuHTMLParser(HTMLParser):
    """
    Custom HTML parser for extracting text and structure.
    This is a fallback when BeautifulSoup is not available.
    However, the main converter uses BeautifulSoup.
    """

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.current_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current_tag = tag

    def handle_endtag(self, tag: str) -> None:
        self.current_tag = None

    def handle_data(self, data: str) -> None:
        if self.current_tag not in ("script", "style"):
            self.text.append(data.strip())


class ZhihuMarkdownConverter:
    """
    Convert Zhihu HTML content to Markdown.
    """

    def __init__(self, skip_empty: bool = True) -> None:
        self.skip_empty = skip_empty
        self.link_converter = ZhihuLinkConverter()

    def tex_normalize(self, content: str) -> str:
        """
        Convert inline-mode
         <img eeimg="1" src="https://www.zhihu.com/equation?tex=A" alt="B"/>
        To:
         $B$

        Convert display-mode
         ![A](https://www.zhihu.com/equation?tex=B)
        to
         $$B$$
        """
        pattern = r"!\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(https://www\.zhihu\.com/equation\?tex=[^)]*\)"

        content = re.sub(pattern, lambda match: f"$${match.group(1)}$$", content)

        soup = BeautifulSoup(content, "html.parser")

        # Find all img tags with eeimg="1" and alt attribute (inline formulas)
        for img in soup.find_all("img", eeimg="1"):
            latex_content = img.get("alt", "")
            if latex_content:
                img.replace_with(f"${latex_content}$")
        # Handle block/display mode formulas (eeimg="2")
        for img in soup.find_all("img", eeimg="2"):
            latex_content = img.get("alt", "")
            if latex_content:
                img.replace_with(f"\n$$\n{latex_content}\n$$\n")
        return str(soup)

    def convert(self, html_content: str, url: str = "") -> str:
        """
        Convert HTML content to Markdown.

        :param html_content: The HTML string to convert.
        :param url: Optional URL for resolving relative links.
        :return: Markdown string.
        """
        soup = BeautifulSoup(html_content, "html.parser")

        # Remove useless elements
        for element in soup(["script", "style"]):
            element.decompose()

        # Handle Zhihu full pages (with .RichText container)
        rich_texts = soup.select(".RichText")
        if rich_texts:
            markdown_parts = []
            for rt in rich_texts:
                md = self._process_element(rt)
                if md:
                    markdown_parts.append(md)
            return "\n\n".join(markdown_parts)

        # Handle raw HTML fragments (e.g. API content field)
        parts = []
        for child in soup.children:
            if isinstance(child, NavigableString):
                text = child.strip()
                if text:
                    parts.append(text)
            elif isinstance(child, Tag):
                md = self._process_element(child)
                if md:
                    parts.append(md)

        if not parts:
            return ""

        return "\n\n".join(parts)

    def _process_element(self, element: Tag | NavigableString) -> str | None:
        """Recursively convert a single element to Markdown."""
        if isinstance(element, NavigableString):
            text = element.strip()
            return text if text else None

        if not isinstance(element, Tag):
            return None

        tag = element.name.lower()

        # Skip non-content elements
        if tag in ("script", "style", "svg", "button", "input", "form", "nav", "header", "footer", "aside"):
            return None

        # Skip empty paragraphs if requested
        if tag == "p" and self.skip_empty and not element.get_text(strip=True):
            return None

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = self._process_inline(element)
            return f"{'#' * level} {text}"

        # Paragraph
        if tag == "p":
            return self._process_inline(element)

        # Lists
        if tag == "ul":
            items = []
            for li in element.find_all("li", recursive=False):
                item_text = self._process_inline(li)
                items.append(f"- {item_text}")
            return "\n".join(items) if items else None

        if tag == "ol":
            items = []
            for idx, li in enumerate(element.find_all("li", recursive=False), start=1):
                item_text = self._process_inline(li)
                items.append(f"{idx}. {item_text}")
            return "\n".join(items) if items else None

        # Blockquote
        if tag == "blockquote":
            content = self._process_inline(element)
            lines = content.split("\n")
            quoted = "\n".join(f"> {line}" for line in lines)
            return quoted

        # Code block
        if tag == "pre":
            code = element.get_text()
            language = ""
            code_tag = element.find("code")
            if code_tag and code_tag.get("class"):
                # Try to extract language from class (e.g., "language-python")
                classes = code_tag.get("class", [])
                for cls in classes:
                    if cls.startswith("language-"):
                        language = cls.split("-")[1]
                        break
            return f"```{language}\n{code}\n```"

        # Inline code (handled in _process_inline)

        # Horizontal rule
        if tag == "hr":
            return "---"

        # Images
        if tag == "img":
            src = element.get("src", "")
            alt = element.get("alt", "")
            src = self.link_converter.normalize_link(src)
            return f"![{alt}]({src})"

        # Figure (often contains img and figcaption)
        if tag == "figure":
            img = element.find("img")
            if img:
                src = img.get("src", "")
                alt = img.get("alt", "")
                src = self.link_converter.normalize_link(src)
                md_img = f"![{alt}]({src})"
            else:
                md_img = ""

            figcaption = element.find("figcaption")
            if figcaption:
                caption = figcaption.get_text(strip=True)
                md_img += f"\n*{caption}*"

            return md_img

        # Tables
        if tag == "table":
            return self._process_table(element)

        # Links
        if tag == "div" and element.get("class") and "RichText-LinkCardContainer" in element.get("class", []):
            a_tag = element.find("a")
            if a_tag:
                href = a_tag.get("href", "")
                # Prefer data-text attribute (Zhihu link card title)
                text = a_tag.get("data-text") or a_tag.get_text(strip=True)
                if not text:
                    text = href
                href = self.link_converter.normalize_link(href)
                return f"[{text}]({href})"
            # No a tag, recurse into children
            return self._process_inline(element)

        # Block Zhihu ad cards and paid-consult cards
        if tag == "a" and (  # Paid consult ad
            element.get("data-draft-type") == "ad-link-card" or element.get("data-ad-id") is not None
        ):
            return None
        if tag == "a" and (  # Zhixuetang ad
            element.get("data-draft-type") == "edu-card" or element.get("data-edu-card-id") is not None
        ):
            return None

        if tag == "a":
            href = element.get("href", "")
            text = element.get_text(strip=True)
            # If text is empty, try title or data-text attribute
            if not text:
                text = element.get("title") or element.get("data-text") or href
            href = self.link_converter.normalize_link(href)
            return f"[{text}]({href})"

        # Bold / strong
        if tag in ("b", "strong"):
            return f"**{self._process_inline(element)}**"

        # Italic / em
        if tag in ("i", "em"):
            return f"*{self._process_inline(element)}*"
        # Underline
        if tag == "u":
            return f"<u>{self._process_inline(element)}</u>"

        # Inline code
        if tag == "code":
            return f"`{element.get_text()}`"

        # Line break
        if tag == "br":
            return "\n"

        # Math (Zhihu specific)
        if tag == "span" and element.get("class") and "ztext-math" in element.get("class", []):
            tex = element.get("data-tex", "")
            eeimg = element.get("data-eeimg", "")  # Zhihu formula type identifier

            if tex:
                # data-eeimg="2" means block/display formula
                if eeimg == "2" or "\\tag" in tex:
                    return f"\n$$\n{tex}\n$$\n"
                else:
                    return f"${tex}$"
            return element.get_text()

        # Video (Zhihu specific)
        if tag == "div" and element.find("video"):
            video = element.find("video")
            src = video.get("src", "")
            if src:
                return f'<video src="{src}"></video>'

        # Generic: recursively process children
        parts = []
        for child in element.children:
            md = self._process_element(child)
            if md:
                parts.append(md)

        # If no parts, return None
        if not parts:
            return None

        # If the element is a block-level element, join with newlines
        if tag in ("div", "section", "article", "main"):
            return "\n\n".join(parts)
        else:
            return " ".join(parts)

    def _process_inline(self, element: Tag | NavigableString) -> str:
        """Process inline content, returning plain text with inline Markdown."""
        if isinstance(element, NavigableString):
            return element.strip()

        parts = []
        for child in element.children:
            md = self._process_element(child)
            if md:
                parts.append(md)
        return " ".join(parts)

    def _process_table(self, table: Tag) -> str:
        """Convert an HTML table to Markdown table."""
        rows = []
        header_row = table.find("thead")
        body_rows = table.find("tbody") or table

        if header_row:
            headers = []
            for th in header_row.find_all("th"):
                headers.append(th.get_text(strip=True))
            rows.append(headers)

        for tr in body_rows.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                cells.append(td.get_text(strip=True))
            if cells:
                rows.append(cells)

        if not rows:
            return ""

        # Determine column count
        max_cols = max(len(row) for row in rows)

        # Normalize rows to same column count
        for row in rows:
            while len(row) < max_cols:
                row.append("")

        # Build Markdown table
        markdown = []
        # Header row
        markdown.append("| " + " | ".join(rows[0]) + " |")
        # Separator row
        markdown.append("| " + " | ".join(["---"] * max_cols) + " |")
        # Data rows
        for row in rows[1:]:
            markdown.append("| " + " | ".join(row) + " |")

        return "\n".join(markdown)


class PageToMarkdown:
    """
    Main class for converting a Zhihu page HTML to Markdown.
    """

    def __init__(self, skip_empty: bool = True) -> None:
        self.converter = ZhihuMarkdownConverter(skip_empty=skip_empty)

    def convert(self, html_content: str, url: str = "", strip: bool = True) -> str:
        """
        Convert HTML content to Markdown.

        :param html_content: HTML string of the Zhihu page.
        :param url: Base URL for resolving relative links (optional).
        :return: Markdown string.
        """
        content = self.converter.tex_normalize(html_content)
        result = self.converter.convert(content, url)
        return result.strip() if strip else result


def calculate_text_length(html_content: str) -> int:
    soup = BeautifulSoup(html_content, "html.parser")

    for img in soup.find_all("img", class_=_eeimg_re):
        img.replace_with(" ")

    for img in soup.find_all("img", class_=lambda x: x != "eeimg"):
        img.decompose()

    pure_text = soup.get_text(strip=False)
    return len(pure_text)


converter = PageToMarkdown()

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        html_file = sys.argv[1]
        with open(html_file, encoding="utf-8") as f:
            html_content = f.read()
        print(converter.convert(html_content))
    else:
        print("Usage: python html2markdown.py <html_file>")

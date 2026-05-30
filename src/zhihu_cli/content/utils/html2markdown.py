"""
Zhihu Backup to Markdown Converter

Convert Zhihu HTML content (answers, articles, pins) to Markdown format.
Adapted from the Tampermonkey script "zhihu-backup-collect".
"""

import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from lxml import html as lxml_html

if TYPE_CHECKING:
    from lxml.html import HtmlElement

_eeimg_re = re.compile(r"eeimg|equation")


# ── lxml helpers ──────────────────────────────────────────────────────────────


def replace_with_text(elem: "HtmlElement", text: str) -> None:
    """Replace an lxml element with a text node, preserving surrounding tail text.

    lxml has no direct equivalent of BeautifulSoup's ``replace_with()``.
    This inserts *text* at the element's position and appends the element's
    original ``.tail`` so text that follows the replaced element is kept.
    """
    parent = elem.getparent()
    if parent is None:
        return
    tail = elem.tail or ""
    prev = elem.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text + tail
    else:
        parent.text = (parent.text or "") + text + tail
    parent.remove(elem)


def _iter_nodes(element: "HtmlElement"):
    """Yield text strings and child elements in document order.

    lxml's tree model is different from BeautifulSoup's:

    * ``element.text``  – text before the first child
    * ``child.tail``    – text after each child element

    BeautifulSoup treats text nodes as children (NavigableString);
    this generator bridges the gap by yielding ``str`` for text and
    ``HtmlElement`` for tags, matching the BS ``.children`` contract.
    """
    if element.text:
        yield element.text
    for child in element:
        yield child
        if child.tail:
            yield child.tail


def _tag_name(element: "HtmlElement") -> str:
    """Return the lowercase tag name of an lxml element."""
    tag = element.tag
    return tag.lower() if isinstance(tag, str) else str(tag)


# ── link converter ────────────────────────────────────────────────────────────


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


# ── fallback parser (kept for reference, not used by main converter) ─────────


class ZhihuHTMLParser(HTMLParser):
    """
    Custom HTML parser for extracting text and structure.
    This is a fallback when lxml is not available.
    However, the main converter uses lxml.
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


# ── main converter ────────────────────────────────────────────────────────────


class ZhihuMarkdownConverter:
    """
    Convert Zhihu HTML content to Markdown.
    """

    def __init__(self, skip_empty: bool = True) -> None:
        self.skip_empty = skip_empty
        self.link_converter = ZhihuLinkConverter()

    # ── LaTeX pre-processing ──────────────────────────────────────────────

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

        doc = lxml_html.fromstring(content)

        # self:: axis covers the root element when content is a single img fragment
        for img in doc.xpath(".//img[@eeimg='1'] | self::img[@eeimg='1']"):
            latex_content = img.get("alt", "")
            if latex_content:
                if img is doc:
                    return f"${latex_content}$"
                replace_with_text(img, f"${latex_content}$")
        # Handle block/display mode formulas (eeimg="2")
        for img in doc.xpath(".//img[@eeimg='2'] | self::img[@eeimg='2']"):
            latex_content = img.get("alt", "")
            if latex_content:
                if img is doc:
                    return f"\n$$\n{latex_content}\n$$\n"
                replace_with_text(img, f"\n$$\n{latex_content}\n$$\n")
        return lxml_html.tostring(doc, encoding="unicode")

    # ── top-level entry point ────────────────────────────────────────────

    def convert(self, html_content: str, url: str = "") -> str:
        """
        Convert HTML content to Markdown.

        :param html_content: The HTML string to convert.
        :param url: Optional URL for resolving relative links.
        :return: Markdown string.
        """
        doc = lxml_html.fromstring(html_content)

        # Remove useless elements
        for element in doc.xpath(".//script") + doc.xpath(".//style"):
            element.drop_tree()

        # Handle Zhihu full pages (with .RichText container)
        rich_texts = doc.cssselect(".RichText")
        if rich_texts:
            markdown_parts = []
            for rt in rich_texts:
                md = self._process_element(rt)
                if md:
                    markdown_parts.append(md)
            return "\n\n".join(markdown_parts)

        # Full HTML document – iterate children of <html>
        if doc.tag == "html":
            parts = []
            for node in _iter_nodes(doc):
                md = self._process_element(node)
                if md:
                    parts.append(md)
            return "\n\n".join(parts) if parts else ""

        # HTML fragment – process the root element directly
        md = self._process_element(doc)
        return md if md else ""

    # ── recursive element processor ──────────────────────────────────────

    def _process_element(self, element) -> str | None:
        """Recursively convert a single element to Markdown.

        Accepts either an lxml ``HtmlElement`` or a plain ``str`` (text node).
        """
        # Text node
        if isinstance(element, str):
            text = element.strip()
            return text if text else None

        # Safety net – not an element
        if not hasattr(element, "tag"):
            return None

        tag = _tag_name(element)

        # Skip non-content elements
        if tag in (
            "script",
            "style",
            "svg",
            "button",
            "input",
            "form",
            "nav",
            "header",
            "footer",
            "aside",
        ):
            return None

        # Skip empty paragraphs if requested
        if tag == "p" and self.skip_empty and not element.text_content().strip():
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
            for li in element.findall("li"):  # direct children only
                item_text = self._process_inline(li)
                items.append(f"- {item_text}")
            return "\n".join(items) if items else None

        if tag == "ol":
            items = []
            for idx, li in enumerate(element.findall("li"), start=1):  # direct children only
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
            code = element.text_content()
            language = ""
            code_tag = element.find(".//code")
            if code_tag is not None:
                for cls in code_tag.classes:
                    if cls.startswith("language-"):
                        language = cls.split("-")[1]
                        break
            return f"```{language}\n{code}\n```"

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
            img = element.find(".//img")
            if img is not None:
                src = img.get("src", "")
                alt = img.get("alt", "")
                src = self.link_converter.normalize_link(src)
                md_img = f"![{alt}]({src})"
            else:
                md_img = ""

            figcaption = element.find(".//figcaption")
            if figcaption is not None:
                caption = figcaption.text_content().strip()
                md_img += f"\n*{caption}*"

            return md_img

        # Tables
        if tag == "table":
            return self._process_table(element)

        # Link card (Zhihu specific: div.RichText-LinkCardContainer)
        if tag == "div" and "RichText-LinkCardContainer" in element.classes:
            a_tag = element.find(".//a")
            if a_tag is not None:
                href = a_tag.get("href", "")
                # Prefer data-text attribute (Zhihu link card title)
                text = a_tag.get("data-text") or a_tag.text_content().strip()
                if not text:
                    text = href
                href = self.link_converter.normalize_link(href)
                return f"[{text}]({href})"
            # No a tag, recurse into children
            return self._process_inline(element)

        # Block Zhihu ad cards and paid-consult cards
        if tag == "a" and (element.get("data-draft-type") == "ad-link-card" or element.get("data-ad-id") is not None):
            return None
        if tag == "a" and (element.get("data-draft-type") == "edu-card" or element.get("data-edu-card-id") is not None):
            return None

        if tag == "a":
            href = element.get("href", "")
            text = element.text_content().strip()
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
            return f"`{element.text_content()}`"

        # Line break
        if tag == "br":
            return "\n"

        # Math (Zhihu specific)
        if tag == "span" and "ztext-math" in element.classes:
            tex = element.get("data-tex", "")
            eeimg = element.get("data-eeimg", "")  # Zhihu formula type identifier

            if tex:
                # data-eeimg="2" means block/display formula
                if eeimg == "2" or "\\tag" in tex:
                    return f"\n$$\n{tex}\n$$\n"
                else:
                    return f"${tex}$"
            return element.text_content()

        # Video (Zhihu specific)
        video = element.find(".//video")
        if tag == "div" and video is not None:
            src = video.get("src", "")
            if src:
                return f'<video src="{src}"></video>'

        # Generic: recursively process children
        parts = []
        for node in _iter_nodes(element):
            md = self._process_element(node)
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

    # ── inline processor ─────────────────────────────────────────────────

    def _process_inline(self, element) -> str:
        """Process inline content, returning plain text with inline Markdown."""
        if isinstance(element, str):
            return element.strip()

        if not hasattr(element, "tag"):
            return ""

        parts = []
        for node in _iter_nodes(element):
            md = self._process_element(node)
            if md:
                parts.append(md)
        return " ".join(parts)

    # ── table processor ──────────────────────────────────────────────────

    def _process_table(self, table: "HtmlElement") -> str:
        """Convert an HTML table to Markdown table."""
        rows = []
        header_row = table.find(".//thead")
        body_rows = table.find(".//tbody")
        if body_rows is None:
            body_rows = table

        if header_row is not None:
            headers = []
            for th in header_row.findall(".//th"):
                headers.append(th.text_content().strip())
            rows.append(headers)

        for tr in body_rows.findall(".//tr"):
            cells = []
            for td in tr.findall(".//td") + tr.findall(".//th"):
                cells.append(td.text_content().strip())
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


# ── public API ────────────────────────────────────────────────────────────────


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
    """Return the length of visible text in *html_content*, excluding markup."""
    doc = lxml_html.fromstring(html_content)

    # self:: covers root element for single-img fragments
    for img in doc.xpath(".//img | self::img"):
        cls = img.get("class", "")
        if _eeimg_re.search(cls):
            if img is doc:
                return 0  # root img with eeimg class replaced by space
            replace_with_text(img, " ")
        else:
            img.drop_tree()

    return len(doc.text_content())


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

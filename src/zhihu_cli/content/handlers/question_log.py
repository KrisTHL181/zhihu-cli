from __future__ import annotations

import difflib
import re
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from zhihu_cli.content.handlers.requests import session

if TYPE_CHECKING:
    from lxml.html import HtmlElement

LOG_URL = "https://www.zhihu.com/question/{question_id}/log"


def _has_diff_tags(detail_div) -> bool:
    """Return True if the detail div contains ins/del diff markup."""
    return bool(detail_div.cssselect("ins, del"))


def _convert_blocks_to_newlines(element: HtmlElement) -> HtmlElement:
    """Replace <p> and <br> tags with newline text nodes so line-level diffs work.

    Returns a deep copy with block elements converted to newlines.
    """
    clone = deepcopy(element)
    for p in list(clone.iter("p")):
        if p.text:
            p.text = "\n" + p.text
        else:
            p.text = "\n"
        if p.tail:
            p.tail = "\n" + p.tail
        else:
            p.tail = "\n"
        p.drop_tag()
    for br in list(clone.iter("br")):
        if br.tail:
            br.tail = "\n" + br.tail
        else:
            br.tail = "\n"
        br.drop_tag()
    return clone


def _extract_diff_texts(detail_div: HtmlElement) -> tuple[str, str]:
    """Extract old and new text from a detail div with ins/del diff markup.

    :param detail_div: the ``div.zg-item-log-detail`` element containing diff markup
    :returns: ``(old_text, new_text)`` — the old and new versions of the content
    """
    preprocessed = _convert_blocks_to_newlines(detail_div)

    # Old text: remove <ins> subtrees entirely, keep <del> content
    old_tree = deepcopy(preprocessed)
    for el in old_tree.cssselect("ins"):
        el.drop_tree()
    for el in old_tree.cssselect("del"):
        el.drop_tag()
    old_text = old_tree.text_content().strip()
    # Collapse 3+ consecutive newlines to at most 2
    old_text = re.sub(r"\n{3,}", "\n\n", old_text)

    # New text: remove <del> subtrees entirely, keep <ins> content
    new_tree = deepcopy(preprocessed)
    for el in new_tree.cssselect("del"):
        el.drop_tree()
    for el in new_tree.cssselect("ins"):
        el.drop_tag()
    new_text = new_tree.text_content().strip()
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)

    return old_text, new_text


def _compute_diff(old_text: str, new_text: str, style: str = "wikidiff") -> str:
    """Line-level diff between old and new text.

    :param style: ``"wikidiff"`` for clean ``-``/``+``/`` `` lines,
                  ``"git"`` for unified-diff with ``---``/``+++``/``@@`` headers
    :returns: a diff string
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    if not old_lines:
        old_lines = ["\n"]
    if not new_lines:
        new_lines = ["\n"]

    if style == "git":
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile="旧", tofile="新", lineterm=""))
        return "\n".join(diff_lines)

    # wikidiff: clean line-level diff, no headers
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    result: list[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                result.append(" " + line.rstrip("\n"))
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                result.append("-" + line.rstrip("\n"))
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                result.append("+" + line.rstrip("\n"))
        elif tag == "replace":
            for line in old_lines[i1:i2]:
                result.append("-" + line.rstrip("\n"))
            for line in new_lines[j1:j2]:
                result.append("+" + line.rstrip("\n"))

    return "\n".join(result)


def fetch_question_log(question_id: str, diff_style: str = "wikidiff") -> list[dict[str, Any]]:
    """Fetch and parse the question edit history page.

    Returns a list of log entries, each with:
      - log_id: internal log item ID
      - user: display name of the editor
      - user_url: relative profile URL (or None)
      - action: action description (e.g. "编辑了问题")
      - detail: change detail text (or None). For entries with diff markup,
        this is the new (current) text only.
      - detail_old: old text before the edit (only for entries with diff markup)
      - detail_diff: unified diff string (only for entries with diff markup)
      - time: timestamp string
    """
    resp = session.get(LOG_URL.format(question_id=question_id))
    resp.raise_for_status()

    from lxml import html as lxml_html

    doc = lxml_html.fromstring(resp.text)
    entries = []

    for item in doc.cssselect("div.zm-item"):
        log_id = item.get("id", "").replace("logitem-", "")

        action_div = item.find("div")
        user = None
        user_url = None
        action = ""

        if action_div is not None:
            user_link = action_div.find("a")
            if user_link is not None:
                user = user_link.text_content().strip()
                user_url = user_link.get("href", "")
            # Action text is the span after the user link, or the whole text
            action_span = action_div.find("span")
            if action_span is not None:
                action = action_span.text_content().strip()
            else:
                raw_text = action_div.text_content().strip()
                if user:
                    action = raw_text.replace(user, "").strip()

        detail = None
        detail_old = None
        detail_diff = None
        detail_divs = item.cssselect("div.zg-item-log-detail")
        if detail_divs:
            detail_div = detail_divs[0]
            if _has_diff_tags(detail_div):
                old_text, new_text = _extract_diff_texts(detail_div)
                # Only compute a meaningful diff when BOTH sides have content.
                # Pure additions (e.g. topic adds) and pure deletions
                # (e.g. topic removes) are better shown as plain text.
                if old_text and new_text:
                    detail = new_text
                    detail_old = old_text
                    detail_diff = _compute_diff(old_text, new_text, style=diff_style)
                else:
                    # Pure addition or deletion — just show the non-empty side
                    detail = new_text or old_text
            else:
                detail = detail_divs[0].text_content().strip() or None

        time_str = ""
        meta_divs = item.cssselect("div.zm-item-meta")
        if meta_divs:
            meta_div = meta_divs[0]
            time_tag = meta_div.find("time")
            if time_tag is not None:
                time_str = time_tag.get("datetime", time_tag.text_content().strip())

        entry = {
            "log_id": log_id,
            "user": user,
            "user_url": user_url or None,
            "action": action,
            "detail": detail,
            "time": time_str,
        }
        if detail_diff is not None:
            entry["detail_old"] = detail_old
            entry["detail_diff"] = detail_diff

        entries.append(entry)

    return entries

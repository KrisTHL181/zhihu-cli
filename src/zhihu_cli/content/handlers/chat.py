from collections.abc import Generator, Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from lxml import html as lxml_html

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import ZhihuLinkConverter, replace_with_text


def _sanitize_html(raw: str) -> str:
    """Convert chat message HTML to clean text.

    Zhihu chat messages contain raw HTML with link wrappers
    (link.zhihu.com redirects, invisible/visible spans, etc.).
    This extracts readable text and resolves link targets.
    """
    doc = lxml_html.fromstring(raw)

    # Use xpath with self:: to also match the root element (handles single-<a> fragments)
    for a_tag in doc.xpath(".//a | self::a"):
        href = ZhihuLinkConverter.normalize_link(str(a_tag.get("href", "")))
        text = a_tag.text_content().strip()
        if text == href or not text:
            replacement = href
        else:
            replacement = f"[{text}]({href})"
        if a_tag is doc:
            # Root is the <a> tag itself; return replacement text directly
            return replacement
        replace_with_text(a_tag, replacement)

    return doc.text_content()


def get_inbox() -> list[dict[str, Any]]:
    messages = []
    resp = session.get("https://www.zhihu.com/api/v4/inbox")
    resp.raise_for_status()

    inbox = resp.json()["data"]
    for message in inbox:
        messages.append(
            {
                "id": message.get("participant", {}).get("id"),
                "url_token": message.get("participant", {}).get("url_token", ""),
                "from": message.get("participant", {}).get("name", "unknown"),
                "snippet": message.get("snippet", "(no content)"),
                "updated_time": fmt_time(message.get("updated_time")),
                "message_count": message.get("message_count", 0),
                "unread_count": message.get("unread_count", 0),
            }
        )
    return messages


def _build_next_url(base_url: str, after_id: str) -> str:
    """Construct the next page URL by adding/updating after_id and limit query params."""
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    params = {k: v[0] for k, v in query.items()}
    params["after_id"] = after_id
    params["limit"] = "20"
    new_query = urlencode(params)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _parse_messages_page(
    data: dict[str, Any],
) -> tuple[list[dict[str, str]], str | None, tuple[str | None, str | None]]:
    data_obj = data.get("data", {})
    messages = data_obj.get("messages", [])
    if not messages:
        return [], None, (None, None)

    receiver_name = data_obj.get("receiver", {}).get("name", "Unknown")
    sender_name = data_obj.get("sender", {}).get("name", "Unknown")

    page_msgs = []
    for msg in messages:
        if msg.get("type") != "message":
            continue

        sender = sender_name if msg.get("user_type") == "sender" else receiver_name
        content = _sanitize_html(msg.get("text", ""))
        time_str = fmt_time(msg.get("created_time"))
        page_msgs.append({"sender": sender, "content": content, "time": time_str})

    last_id = messages[-1].get("id")
    return page_msgs, last_id, (receiver_name, sender_name)


def iter_chat_history(chat_id: str) -> Generator[dict[str, str], None, None]:
    """Stream chat history pages via waterfall, collecting and reversing for chronological order."""
    initial_url = f"https://www.zhihu.com/api/v4/chat?sender_id={chat_id}"

    # Closure state shared between parser and extract_next
    state: dict[str, str | None] = {"last_id": None, "current_url": initial_url}

    def parse_messages(data: dict[str, Any]) -> Iterable[dict[str, str]]:
        page_msgs, last_id, _ = _parse_messages_page(data)
        state["last_id"] = last_id
        yield from page_msgs

    def extract_next(data: dict[str, Any]) -> str | None:
        paging = data.get("paging", {})
        if paging.get("is_end", True):
            return None
        last_id = state["last_id"]
        if not last_id:
            return None
        next_url = _build_next_url(str(state["current_url"]), str(last_id))
        state["current_url"] = next_url
        return next_url

    # API returns messages newest-first; collect and reverse to chronological order
    all_messages = list(stream_handler(initial_url, parse_messages, extract_next, delay=0.6))
    yield from reversed(all_messages)


def send_text_message(their_id: str, content: str) -> dict[str, Any]:
    resp = session.post(
        "https://www.zhihu.com/api/v4/chat", json={"content_type": 0, "text": content, "receiver_id": their_id}
    )

    data = resp.json()
    if resp.status_code == 403 and "error" in data.keys():
        raise RuntimeError(f"Failed to send message: {data['error']['message']}")
    resp.raise_for_status()

    return data

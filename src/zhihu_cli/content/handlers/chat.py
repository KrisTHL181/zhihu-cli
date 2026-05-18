import time
from collections.abc import Generator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.html2markdown import ZhihuLinkConverter


def _sanitize_html(raw: str) -> str:
    """Convert chat message HTML to clean text.

    Zhihu chat messages contain raw HTML with link wrappers
    (link.zhihu.com redirects, invisible/visible spans, etc.).
    This extracts readable text and resolves link targets.
    """
    soup = BeautifulSoup(raw, "html.parser")

    for a_tag in soup.find_all("a"):
        href = ZhihuLinkConverter.normalize_link(str(a_tag.get("href", "")))
        text = a_tag.get_text(strip=True)
        if text == href or not text:
            replacement = href
        else:
            replacement = f"[{text}]({href})"
        a_tag.replace_with(replacement)

    return soup.get_text()


def get_inbox() -> list[dict[str, Any]]:
    messages = []
    resp = session.get("https://www.zhihu.com/api/v4/inbox")
    resp.raise_for_status()

    inbox = resp.json()["data"]
    for message in inbox:
        messages.append(
            {
                "id": message.get("participant", {}).get("id"),
                "url_token": message.get("url_token", ""),
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
    current_url = f"https://www.zhihu.com/api/v4/chat?sender_id={chat_id}"
    all_messages: list[dict[str, str]] = []

    while current_url:
        resp = session.get(current_url, timeout=15)

        if resp.status_code != 200:
            raise RuntimeError(f"Chat history request failed: {resp.status_code} for {current_url}")

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Chat history returned invalid JSON for {current_url}")

        page_msgs, last_id, _ = _parse_messages_page(data)
        if not page_msgs:
            break

        all_messages.extend(page_msgs)

        paging = data.get("paging", {})

        if paging.get("is_end", True):
            break

        if last_id:
            current_url = _build_next_url(current_url, last_id)
            time.sleep(0.6)
        else:
            break

    # API returns messages newest-first; reverse to chronological order
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

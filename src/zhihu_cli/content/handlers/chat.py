import json
import queue
import threading
from collections.abc import Generator, Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import click
from lxml import html as lxml_html

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import ZhihuLinkConverter, replace_with_text


def _format_image_message(image_data: dict[str, Any] | None) -> str:
    """Extract image URL from a chat message's ``image`` field.

    For ``content_type=1`` (image) messages the Zhihu API returns an
    ``image`` dict with ``url``, ``height`` and ``width`` keys.
    """
    if not isinstance(image_data, dict):
        return "[]"
    url = image_data.get("url", "")
    if not url:
        return "[]"
    return f"![]({url})"


def _sanitize_html(raw: str) -> str:
    """Convert chat message HTML to clean text.

    Zhihu chat messages contain raw HTML with link wrappers
    (link.zhihu.com redirects, invisible/visible spans, etc.) and
    embedded ``<img>`` tags.  This extracts readable text, resolves
    link targets, and preserves image references as Markdown.
    """
    if not raw or not raw.strip():
        return raw
    doc = lxml_html.fromstring(raw)

    # Process <img> tags first so their Markdown survives text_content().
    for img_tag in doc.xpath(".//img"):
        src = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-original") or ""
        alt = img_tag.get("alt", "图片")
        replacement = f"\n![{alt}]({src})\n" if src else f"[{alt}]"
        replace_with_text(img_tag, replacement)

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


def get_inbox(limit: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Fetch inbox threads with pagination.

    The inbox API is paginated (waterfall-style).  This uses ``stream_handler``
    to walk through all pages automatically.

    Args:
        limit: Max threads to fetch (0 = all pages).
    Returns:
        Tuple of (threads, total_unread) where *total_unread* is the
        ``new_count`` reported by the first page.
    """
    messages: list[dict[str, Any]] = []
    initial_url = "https://www.zhihu.com/api/v4/inbox?limit=20"
    total_unread = 0

    def parse_inbox(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
        nonlocal total_unread
        if total_unread == 0:
            total_unread = data.get("new_count", 0)
        for message in data.get("data", []):
            yield {
                "id": message.get("participant", {}).get("id"),
                "url_token": message.get("participant", {}).get("url_token", ""),
                "from": message.get("participant", {}).get("name", "unknown"),
                "snippet": message.get("snippet", "(no content)"),
                "updated_time": fmt_time(message.get("updated_time")),
                "message_count": message.get("message_count", 0),
                "unread_count": message.get("unread_count", 0),
            }

    count = 0
    for msg in stream_handler(initial_url, parse_inbox, delay=0.6):
        messages.append(msg)
        count += 1
        if limit > 0 and count >= limit:
            break

    return messages, total_unread


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
        content_type = msg.get("content_type", 0)
        if content_type == 1:  # image
            content = _format_image_message(msg.get("image"))
        else:
            content = _sanitize_html(msg.get("text", ""))
        time_str = fmt_time(msg.get("created_time"))
        page_msgs.append({"sender": sender, "content": content, "time": time_str})

    last_id = messages[-1].get("id")
    return page_msgs, last_id, (receiver_name, sender_name)


def iter_chat_history(
    chat_id: str, limit: int = 0, partner_info: list[str] | None = None
) -> Generator[dict[str, str], None, None]:
    """Stream chat history pages via waterfall, yielding in chronological order.

    The Zhihu API returns messages newest-first.  When *limit* > 0 only the
    most recent *limit* messages are returned (applied before reversal so you
    always get the freshest conversation tail).

    If *partner_info* (a mutable list) is provided, the partner's and
    the current user's display names from the first API page are appended
    — ``partner_info[0]`` is the partner, ``partner_info[1]`` is you.
    """
    initial_url = f"https://www.zhihu.com/api/v4/chat?sender_id={chat_id}"

    # Closure state shared between parser and extract_next
    state: dict[str, str | None] = {"last_id": None, "current_url": initial_url}

    def parse_messages(data: dict[str, Any]) -> Iterable[dict[str, str]]:
        page_msgs, last_id, (receiver_name, sender_name) = _parse_messages_page(data)
        state["last_id"] = last_id
        if partner_info is not None and not partner_info:
            partner_info.append(sender_name)  # [0] — the partner
            partner_info.append(receiver_name)  # [1] — you
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

    # API returns messages newest-first; collect, optionally trim to newest N,
    # then reverse to chronological order.
    all_messages = list(stream_handler(initial_url, parse_messages, extract_next, delay=0.6))
    if limit > 0 and len(all_messages) > limit:
        all_messages = all_messages[:limit]
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


def _fmt_chat_line(sender: str, content: str, ts: int | float | None) -> str:
    """Format a single chat line in chat-history style: ``[time]sender: content``.

    Timestamps are dimmed, sender names are green-bold — matching the output
    of ``chat history`` and ``listen messages`` commands.
    """
    t = fmt_time(ts)
    time_part = click.style(f"[{t}]", dim=True)
    sender_part = click.style(sender, fg="green", bold=True)
    return f"  {time_part}{sender_part}: {content}"


def interactive_chat(chat_id: str, my_url_token: str, sender_filter: str | None = None) -> None:
    """Start an interactive chat session with real-time MQTT listener.

    Combines chat history display, a background MQTT listener for incoming
    messages, and a persistent input prompt — all rendered in a single
    terminal interface without jitter, using *prompt_toolkit*.

    Args:
        chat_id: The other user's ID (for history and sending messages).
        my_url_token: Current logged-in user's url_token (for MQTT connection).
        sender_filter: Optional MQTT filter (defaults to *chat_id*).
    """
    from prompt_toolkit import PromptSession, print_formatted_text
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout

    from zhihu_cli.content.handlers.imchat import IMCHAT_TOPIC, ZhihuMessageListener

    def _pt_echo(text: str) -> None:
        """Print a string through prompt_toolkit's ANSI renderer.

        Under ``patch_stdout()``, regular ``click.echo`` can't render ANSI
        escape codes produced by ``click.style`` — the raw escape sequences
        appear as literal characters.  ``print_formatted_text(ANSI(...))``
        tells prompt_toolkit to interpret those codes and render them
        correctly (dim, colours, bold, etc.).
        """
        print_formatted_text(ANSI(text))

    # ── 1. Load chat history & capture both names ───────────────────────
    partner_info: list[str] = []
    history_msgs = list(iter_chat_history(chat_id, partner_info=partner_info))
    if len(partner_info) >= 2:
        partner_name = partner_info[0]
        my_name = partner_info[1]
    else:
        partner_name = chat_id
        my_name = "Me"

    # ── 2. Setup MQTT listener ───────────────────────────────────────────
    mqtt_filter = sender_filter if sender_filter else chat_id
    listener = ZhihuMessageListener(my_url_token, IMCHAT_TOPIC, sender_filter=mqtt_filter)

    # Replace on_message to route incoming data to our display queue.
    display_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def on_message(client: Any, userdata: Any, msg: Any) -> None:
        raw = msg.payload.decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        # Filter by sender_id (not receiver_id — that is the logged-in user).
        if listener.receiver_id and data.get("meta", {}).get("sender_id") != listener.receiver_id:
            return
        display_queue.put(data)

    listener.client.on_message = on_message

    # ── 3. Format an MQTT message for display ────────────────────────────
    def _fmt_mqtt(data: dict[str, Any]) -> str:
        """Format an MQTT IM message dict to a display line (matches _format_message)."""
        meta = data.get("meta", {})
        content = data.get("content", {})
        sender = partner_name
        content_type = meta.get("content_type", "text")

        raw_ts = meta.get("created_at", 0)
        ts = int(raw_ts) / 1000 if raw_ts else None

        if content_type == "image":
            img = content.get("image") or {}
            img_url = img.get("url", "") if isinstance(img, dict) else ""
            text = f"![]({img_url})" if img_url else "[图片]"
        else:
            text = content.get("text", "")

        return _fmt_chat_line(sender, text, ts)

    # ── 4. Background display thread ─────────────────────────────────────
    stop_event = threading.Event()

    def display_loop() -> None:
        """Continuously drain the display queue and print formatted messages."""
        while not stop_event.is_set():
            try:
                data = display_queue.get(timeout=0.15)
            except queue.Empty:
                continue
            _pt_echo(_fmt_mqtt(data))

    # ── 5. Run the interactive session ───────────────────────────────────
    listener.client.loop_start()
    display_thread = threading.Thread(target=display_loop, daemon=True)

    with patch_stdout():
        # Print history (history messages have pre-formatted time strings).
        for msg in history_msgs:
            t = msg.get("time", "")
            time_part = click.style(f"[{t}]", dim=True) if t else ""
            sender_part = click.style(msg["sender"], fg="green", bold=True)
            _pt_echo(f"  {time_part}{sender_part}: {msg['content']}")

        if history_msgs:
            _pt_echo(click.style("  ── history loaded ──", dim=True))

        display_thread.start()

        session = PromptSession(history=InMemoryHistory())
        try:
            while True:
                try:
                    text = session.prompt("\n> ")
                except (EOFError, KeyboardInterrupt):
                    break

                text = text.strip()
                if not text:
                    continue
                if text in ("/quit", "/exit", "/q"):
                    break

                try:
                    import sys as _sys
                    import time as _time

                    send_text_message(chat_id, text)
                    now = _time.time()
                    # Prompt is "\n> " (2 lines).  \x1b[2A goes up both
                    # the blank line and "> text", \x1b[J clears to end
                    # of screen so both lines are erased before we print
                    # the sent message in the same space.
                    _sys.__stdout__.write("\x1b[2A\r\x1b[J")
                    _sys.__stdout__.flush()
                    _pt_echo(_fmt_chat_line(my_name, text, now))
                except Exception as exc:
                    _pt_echo(f"  {click.style('[error]', fg='red')} Failed to send: {exc}")
        finally:
            stop_event.set()
            listener.client.loop_stop()
            listener.client.disconnect()

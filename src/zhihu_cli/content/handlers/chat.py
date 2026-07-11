from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

if TYPE_CHECKING:
    from typing import Any

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


def _fmt_chat_line_rich(sender: str, content: str, ts: int | float | str | None):
    """Format a single chat line as a Rich :class:`~rich.text.Text` object.

    Uses ``Text`` instead of a raw markup string so that *sender* and
    *content* are treated as plain text — brackets, backslashes and other
    markup-significant characters are displayed literally without escaping.

    :param sender: Display name of the message sender.
    :param content: Message body (plain text, may contain brackets).
    :param ts: Unix timestamp (int/float), pre-formatted time string, or None.
    :returns: A :class:`~rich.text.Text` renderable ready for ``RichLog.write``.
    """
    from rich.text import Text

    if isinstance(ts, str) and ts:
        time_str = ts
    elif ts is not None:
        time_str = fmt_time(ts)
    else:
        time_str = ""
    result = Text("  ")
    if time_str:
        result.append(f"[{time_str}]", style="dim")
    result.append(sender, style="bold green")
    result.append(": ")
    result.append(content)
    return result


def interactive_chat(
    chat_id: str,
    my_url_token: str,
    sender_filter: str | None = None,
    desktop_notify: bool = True,
) -> None:
    """Start an interactive chat session with real-time MQTT listener.

    Launches a Textual TUI combining chat history display, a background
    MQTT listener for incoming messages, and a persistent input field.

    :param chat_id: The other user's ID (for history and sending messages).
    :param my_url_token: Current logged-in user's url_token (for MQTT connection).
    :param sender_filter: Optional MQTT filter (defaults to *chat_id*).
    :param desktop_notify: If True, send desktop notifications for incoming
        messages via ``desktop-notifier`` (when installed).
    """
    import asyncio
    import time as _time

    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.message import Message
    from textual.widgets import Footer, Header, Input, RichLog

    from zhihu_cli.content.handlers.imchat import IMCHAT_TOPIC, ZhihuMessageListener

    # ── Optional desktop-notifier import ────────────────────────────────
    notifier = None
    if desktop_notify:
        try:
            from desktop_notifier import DesktopNotifier

            notifier = DesktopNotifier(app_name="zhihu-cli")
        except ImportError:
            pass  # desktop-notifier not installed — silently skip notifications

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

    # ── 3. Custom Textual message for incoming MQTT data ────────────────

    class IncomingMessage(Message):
        """Posted when the MQTT worker receives a new chat message."""

        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data
            super().__init__()

    # ── 4. Textual App ──────────────────────────────────────────────────

    class ChatSessionApp(App):
        """Textual TUI for an interactive Zhihu chat session."""

        BINDINGS = [
            Binding("ctrl+q", "quit_app", "退出"),
            Binding("ctrl+c", "quit_app", "退出", show=False),
            Binding("ctrl+d", "quit_app", "退出", show=False),
            Binding("up", "history_prev", "上一条", show=False),
            Binding("down", "history_next", "下一条", show=False),
        ]

        CSS = """
        Screen {
            background: #1e1e2e;
        }

        RichLog#messages {
            height: 1fr;
            background: #1e1e2e;
            padding: 0 1;
            overflow-x: hidden;
        }

        Input#input {
            dock: bottom;
            margin: 0 1 1 1;
            background: #313244;
            color: #cdd6f4;
            border: none;
        }

        Input#input:focus {
            background: #45475a;
        }

        Header {
            background: #313244;
            color: #cba6f7;
        }

        Footer {
            background: #313244;
            color: #6c7086;
        }
        """

        def compose(self) -> ComposeResult:
            yield Header()
            yield RichLog(id="messages", highlight=True, markup=True, auto_scroll=True, wrap=True)
            yield Input(id="input", placeholder="输入消息... (/quit 退出)")
            yield Footer()

        def on_mount(self) -> None:
            """Print history and start MQTT listener."""
            self.title = f"Chat @ {partner_name}"
            log = self.query_one("#messages", RichLog)
            for msg in history_msgs:
                log.write(_fmt_chat_line_rich(msg["sender"], msg["content"], msg.get("time", "")))
            if history_msgs:
                log.write("[dim]  ── history loaded ──[/dim]")

            # Store references for use in handlers
            self._listener = listener
            self._notifier = notifier
            self._chat_id = chat_id
            self._my_name = my_name
            self._partner_name = partner_name
            self._history: list[str] = []
            self._history_idx: int = 0

            # Start MQTT background task
            self._mqtt_task = asyncio.create_task(self._mqtt_worker())

            self.query_one("#input", Input).focus()

        def on_unmount(self) -> None:
            """Cancel the MQTT task on exit."""
            if hasattr(self, "_mqtt_task") and not self._mqtt_task.done():
                self._mqtt_task.cancel()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """Handle message submission from the Input widget."""
            text = event.value.strip()
            event.input.clear()

            if not text:
                return
            if text in ("/quit", "/exit", "/q"):
                self.exit()
                return

            self._history.append(text)
            self._history_idx = len(self._history)

            # Capture the RichLog.write bound method on the main thread so the
            # worker thread never touches the Textual DOM directly.
            _write = self.query_one("#messages", RichLog).write

            def _send() -> None:
                """Send the message in a worker thread (blocking HTTP call)."""
                try:
                    send_text_message(self._chat_id, text)
                    now = _time.time()
                    formatted = _fmt_chat_line_rich(self._my_name, text, now)
                    self.call_from_thread(_write, formatted)
                except Exception as exc:
                    self.call_from_thread(
                        _write,
                        f"  [bold red]Failed to send:[/bold red] {exc}",
                    )

            self.run_worker(_send, thread=True)

        def action_quit_app(self) -> None:
            """Quit the application."""
            self.exit()

        def action_history_prev(self) -> None:
            """Recall the previous message from input history."""
            if not self._history:
                return
            inp = self.query_one("#input", Input)
            self._history_idx = max(0, self._history_idx - 1)
            inp.value = self._history[self._history_idx]
            inp.cursor_position = len(inp.value)

        def action_history_next(self) -> None:
            """Move forward through input history (or clear to new message)."""
            if not self._history:
                return
            inp = self.query_one("#input", Input)
            self._history_idx = min(len(self._history), self._history_idx + 1)
            if self._history_idx < len(self._history):
                inp.value = self._history[self._history_idx]
            else:
                inp.value = ""
            inp.cursor_position = len(inp.value)

        async def _mqtt_worker(self) -> None:
            """Bridge MQTT messages into the UI via post_message."""
            try:
                async for data in self._listener.iter_messages():
                    self.post_message(IncomingMessage(data))
            except asyncio.CancelledError:
                pass
            except Exception:
                import logging
                import traceback

                logger = logging.getLogger("zhihu_cli.chat")
                logger.debug("MQTT listener terminated unexpectedly:\n%s", traceback.format_exc())
                self.query_one("#messages", RichLog).write(
                    "  [bold red]Real-time listener stopped — messages may be missed[/bold red]"
                )

        def on_incoming_message(self, event: IncomingMessage) -> None:
            """Display an incoming MQTT message in the chat log."""
            data = event.data
            meta = data.get("meta", {})
            content = data.get("content", {})
            content_type = meta.get("content_type", "text")

            raw_ts = meta.get("created_at", 0)
            ts: int | float | None = int(raw_ts) / 1000 if raw_ts else None

            if content_type == "image":
                img = content.get("image") or {}
                img_url: str = img.get("url", "") if isinstance(img, dict) else ""
                text = f"![]({img_url})" if img_url else "[图片]"
            else:
                text = content.get("text", "")

            formatted = _fmt_chat_line_rich(self._partner_name, text, ts)
            self.query_one("#messages", RichLog).write(formatted)

            # Desktop notification
            if self._notifier is not None:
                title = self._partner_name
                body = "[图片]" if content_type == "image" else content.get("text", "")[:200]
                asyncio.create_task(self._notifier.send(title=title, message=body))

    # ── 5. Run the Textual app (synchronous — manages its own event loop) ──
    app = ChatSessionApp()
    app.run()

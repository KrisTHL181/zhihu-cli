from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

if TYPE_CHECKING:
    from typing import Any

    from rich.text import Text

import click
from lxml import html as lxml_html

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.chat_commands import ChatCommandRegistry
from zhihu_cli.content.handlers.chat_commands_builtin import (
    _apply_unsend_suggestion,
    _show_unsend_suggestions,
    register_builtin_commands,
)
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.upload_image import upload_image
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import ZhihuLinkConverter, replace_with_text
from zhihu_cli.output import warning


def _inject_message_id(url: str, message_id: str | None) -> str:
    """Inject ``&message_id=...`` into a ``pic-private.zhihu.com`` image URL.

    Private Zhihu image URLs require a ``message_id`` query parameter to
    be viewable; without it the CDN returns 403.  This helper appends the
    parameter when the URL domain matches and the ID is provided.

    :param url: The image URL (may already contain query params).
    :param message_id: The message ID to inject (ignored if ``None`` or empty).
    :returns: The URL with ``&message_id=...`` appended, or unchanged if
        the domain doesn't match or the parameter is already present.
    """
    if not message_id or "pic-private.zhihu.com" not in url:
        return url
    if "message_id=" in url:
        return url  # already injected
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}message_id={message_id}"


def _format_image_message(image_data: dict[str, Any] | None, message_id: str | None = None) -> str:
    """Extract image URL from a chat message's ``image`` field.

    For ``content_type=1`` (image) messages the Zhihu API returns an
    ``image`` dict with ``url``, ``height`` and ``width`` keys.

    :param image_data: The ``image`` sub-dict from the API response.
    :param message_id: Optional message ID — injected into
        ``pic-private.zhihu.com`` URLs via :func:`_inject_message_id`
        so the image is viewable.
    """
    if not isinstance(image_data, dict):
        return "[]"
    url = image_data.get("url", "")
    if not url:
        return "[]"
    url = _inject_message_id(url, message_id)
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


def get_inbox(limit: int = 0, unread_only: bool = False) -> tuple[list[dict[str, Any]], int]:
    """Fetch inbox threads with pagination.

    The inbox API is paginated (waterfall-style).  This uses ``stream_handler``
    to walk through all pages automatically.

    Args:
        limit: Max threads to fetch (0 = all pages).
        unread_only: Only include threads with ``unread_count > 0``.
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
        if unread_only and msg["unread_count"] == 0:
            continue
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
        msg_type = msg.get("type")
        if msg_type == "risk_tip":
            text = _sanitize_html(msg.get("text", ""))
            time_str = fmt_time(msg.get("created_time"))
            page_msgs.append(
                {
                    "sender": "",
                    "content": text,
                    "time": time_str,
                    "id": msg.get("id"),
                    "created_time": msg.get("created_time", 0),
                    "is_canceled": False,
                    "is_risk_tip": True,
                }
            )
            continue
        if msg_type != "message":
            continue

        sender = sender_name if msg.get("user_type") == "sender" else receiver_name
        content_type = msg.get("content_type", 0)
        if content_type == 1:  # image
            content = _format_image_message(msg.get("image"), msg.get("id"))
        else:
            content = _sanitize_html(msg.get("text", ""))
        time_str = fmt_time(msg.get("created_time"))
        page_msgs.append(
            {
                "sender": sender,
                "content": content,
                "time": time_str,
                "id": msg.get("id"),
                "created_time": msg.get("created_time", 0),
                "is_canceled": msg.get("is_canceled", False),
            }
        )

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


def unsend_message(message_id: str) -> dict[str, Any]:
    """Recall (unsend) a previously sent chat message.

    :param message_id: The ID of the message to recall.
    :returns: The API response dict (contains ``content`` and ``success``).
    :raises RuntimeError: If the API returns an error.
    """
    resp = session.post(
        "https://api.zhihu.com/messages/actions/cancel",
        data={"message_id": message_id},
    )
    data = resp.json()
    if resp.status_code == 403 and "error" in data:
        raise RuntimeError(f"Failed to unsend message: {data['error']['message']}")
    resp.raise_for_status()
    return data


def send_text_message(their_id: str, content: str) -> dict[str, Any]:
    resp = session.post(
        "https://www.zhihu.com/api/v4/chat", json={"content_type": 0, "text": content, "receiver_id": their_id}
    )

    data = resp.json()
    if resp.status_code == 403 and "error" in data.keys():
        raise RuntimeError(f"Failed to send message: {data['error']['message']}")
    resp.raise_for_status()

    return data


def send_image_message(their_id: str, file_path: str) -> dict[str, Any]:
    """Upload an image and send it as a chat message.

    Handles the full flow: upload to Zhihu image hosting (with
    ``source="message"``) then POST to the chat API as a
    ``content_type=1`` message.

    :param their_id: The recipient's user ID (receiver_id).
    :param file_path: Path to the local image file.
    :returns: The chat API response dict (includes ``id`` of the sent message).
    :raises FileNotFoundError: If *file_path* does not exist.
    :raises RuntimeError: If the upload or send fails.
    """
    image_info = upload_image(file_path, source="message")
    resp = session.post(
        "https://www.zhihu.com/api/v4/chat",
        json={
            "content_type": 1,
            "receiver_id": their_id,
            "image": {
                "url": image_info["src"],
                "width": image_info.get("width", 0),
                "height": image_info.get("height", 0),
            },
        },
    )
    data = resp.json()
    if resp.status_code == 403 and "error" in data:
        raise RuntimeError(f"Failed to send image: {data['error']['message']}")
    resp.raise_for_status()
    return data


def export_chat_history(
    chat_id: str,
    output_dir: str,
    limit: int = 0,
) -> str:
    """Export chat history to a markdown file with images downloaded locally.

    Fetches all messages in the conversation, builds a markdown document
    with YAML frontmatter, downloads referenced images to a ``media/``
    subdirectory, and rewrites image URLs to local paths.  Regular links
    in message text are preserved verbatim (only ``![alt](url)`` image
    references are downloaded).

    Progress is printed to stderr so ``--json`` stdout remains clean.

    :param chat_id: The other user's ID (``sender_id`` API parameter).
    :param output_dir: Directory to save the exported ``.md`` file and
        ``media/`` subdirectory.
    :param limit: Max messages to export (0 = all pages).
    :returns: Absolute path to the saved markdown file.
    """
    import os
    import re
    import sys
    from datetime import datetime

    import click

    from zhihu_cli.content.download_contents import (
        build_yaml_frontmatter,
        download_media_files,
        get_safe_filename,
        sanitize_filename,
    )

    # 1. Fetch all messages (resolve partner names from the first API page).
    click.echo("  Fetching messages...", err=True)
    partner_info: list[str] = []
    msgs = list(iter_chat_history(chat_id, limit=limit, partner_info=partner_info))
    click.echo(f"    {len(msgs)} messages fetched", err=True)

    if len(partner_info) >= 2:
        partner_name: str = partner_info[0]
        my_name: str = partner_info[1]
    else:
        partner_name = chat_id
        my_name = "Me"

    # 2. Build a markdown document — one block per message, separated by
    #    a blank line.  Multi-line message content is preserved as-is.
    lines: list[str] = []
    for msg in msgs:
        t = msg["time"]
        s = msg["sender"]
        content = msg["content"]
        if msg.get("is_canceled"):
            lines.append(f"**[{t}] {s}**: ~~{content}~~ *(已撤回)*")
        elif msg.get("is_risk_tip"):
            lines.append(f"**[{t}]** *[风险提示]* {content}")
        else:
            lines.append(f"**[{t}] {s}**: {content}")

    markdown = "\n\n".join(lines)

    # 3. Download images referenced as ![](url) → local media/ directory.
    #    Regular [text](url) links are left untouched.
    img_urls: set[str] = set()
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)\s]+)\)", markdown):
        img_urls.add(m.group(2))

    if img_urls:
        with click.progressbar(
            length=len(img_urls),
            label="  Downloading images",
            file=sys.stderr,
            show_pos=True,
        ) as bar:

            def _update(current: int, total: int) -> None:
                bar.update(1)

            markdown, media_count = download_media_files(markdown, output_dir, progress_callback=_update)
        click.echo(f"    {media_count}/{len(img_urls)} images downloaded", err=True)
    else:
        markdown, media_count = download_media_files(markdown, output_dir)

    # 4. Build YAML frontmatter and save.
    now = datetime.now().strftime("%Y-%m-%d")
    metadata: dict[str, str | int] = {
        "title": f"Chat with {partner_name}",
        "partner": partner_name,
        "me": my_name,
        "chat_id": chat_id,
        "exported": now,
        "message_count": len(msgs),
    }
    if media_count:
        metadata["media_files"] = media_count

    title = sanitize_filename(f"chat_{partner_name}")
    filename = get_safe_filename(f"{title}_{now}", ext=".md", max_bytes=240)
    filepath = os.path.join(output_dir, filename)

    file_content = build_yaml_frontmatter(metadata) + markdown

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(file_content)

    click.echo(f"  Saved to {filepath}", err=True)

    return filepath


def _fmt_chat_line(sender: str, content: str, ts: int | float | None) -> str:
    """Format a single chat line in chat-history style: ``[time]sender: content``.

    Timestamps are dimmed, sender names are green-bold — matching the output
    of ``chat history`` and ``listen messages`` commands.
    """
    t = fmt_time(ts)
    time_part = click.style(f"[{t}]", dim=True)
    sender_part = click.style(sender, fg="green", bold=True)
    return f"  {time_part}{sender_part}: {content}"


def _fmt_chat_line_rich(sender: str, content: str | Text, ts: int | float | str | None):
    """Format a single chat line as a Rich :class:`~rich.text.Text` object.

    Uses ``Text`` instead of a raw markup string so that *sender* and
    *content* are treated as plain text — brackets, backslashes and other
    markup-significant characters are displayed literally without escaping.

    :param sender: Display name of the message sender.
    :param content: Message body (plain text, may contain brackets), or a
        pre-styled :class:`~rich.text.Text` object.
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
            warning(
                "desktop-notifier is not installed; desktop notifications will be disabled.  "
                "Install with: pip install -e .[notify]"
            )

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
            Binding("tab", "complete_command", "补全", show=False),
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
                if msg.get("is_canceled"):
                    from rich.text import Text

                    canceled = Text("[已撤回] ", style="dim italic")
                    canceled.append(msg["content"], style="dim italic")
                    log.write(_fmt_chat_line_rich(msg["sender"], canceled, msg.get("time", "")))
                elif msg.get("is_risk_tip"):
                    from rich.text import Text

                    t = msg.get("time", "")
                    line = Text("  ")
                    if t:
                        line.append(f"[{t}]", style="dim")
                    line.append(" [风险提示] ", style="dim italic")
                    line.append(msg["content"], style="dim italic")
                    log.write(line)
                else:
                    log.write(_fmt_chat_line_rich(msg["sender"], msg["content"], msg.get("time", "")))
            if history_msgs:
                log.write("[dim]  ── history loaded ──[/dim]")

            # Store references for use in handlers
            self._listener = listener
            self._notifier = notifier
            self._chat_id = chat_id
            self._my_name = my_name
            self._partner_name = partner_name
            self._terminal_has_focus: bool = False  # It will update on blur/focus events
            self._history: list[str] = []
            self._history_idx: int = 0

            # Command system
            self._cmd_registry = ChatCommandRegistry()
            register_builtin_commands(self._cmd_registry)
            # Track sent messages with IDs (for /unsend recall).
            # Seed with own messages from history so they are undoable too.
            self._sent_messages: list[dict[str, Any]] = []
            for msg in history_msgs:
                if msg.get("id") and msg["sender"] == my_name and not msg.get("is_canceled"):
                    self._sent_messages.append(
                        {"text": msg["content"], "id": msg["id"], "time": msg.get("created_time", 0)}
                    )

            # Start MQTT background task
            self._mqtt_task = asyncio.create_task(self._mqtt_worker())

            self.query_one("#input", Input).focus()

        def on_unmount(self) -> None:
            """Cancel the MQTT task on exit."""
            if hasattr(self, "_mqtt_task") and not self._mqtt_task.done():
                self._mqtt_task.cancel()

        def on_app_focus(self) -> None:
            """Mark terminal as focused — suppress notifications."""
            self._terminal_has_focus = True

        def on_app_blur(self) -> None:
            """Mark terminal as unfocused — allow notifications."""
            self._terminal_has_focus = False

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """Handle message submission from the Input widget."""
            text = event.value.strip()
            event.input.clear()

            if not text:
                return

            # ── Slash-command dispatch ──────────────────────────
            if text.startswith("/"):
                # If suggestions popup is visible, don't interfere
                if hasattr(self, "_suggest_widget") and self._suggest_widget is not None:
                    return

                cmd_text = text[1:]  # strip leading /
                matched_name, command, candidates = self._cmd_registry.match(cmd_text)

                if command is not None:
                    # Exact or unique prefix match — extract args
                    parts = cmd_text.split(maxsplit=1)
                    token = parts[0]
                    cmd_args = parts[1] if len(parts) > 1 else ""
                    if matched_name != token:
                        # Unique prefix match: args are after the typed token
                        cmd_args = cmd_text[len(token) :].strip()
                    self._history.append(text)
                    self._history_idx = len(self._history)
                    try:
                        command.handler(self, cmd_args)
                    except Exception as exc:
                        log = self.query_one("#messages", RichLog)
                        log.write(f"  [bold red]命令执行失败:[/bold red] {exc}")
                elif candidates:
                    log = self.query_one("#messages", RichLog)
                    log.write(f"  [bold yellow]多个匹配:[/bold yellow] {' | '.join('/' + c for c in candidates)}")
                else:
                    log = self.query_one("#messages", RichLog)
                    token = cmd_text.split()[0] if cmd_text.strip() else cmd_text
                    log.write(f"  [bold red]未知命令:[/bold red] /{token} — 输入 [bold]/help[/bold] 查看可用命令")
                return

            # ── Normal message send ────────────────────────────
            self._history.append(text)
            self._history_idx = len(self._history)

            # Pre-track on main thread so /unsend can find it immediately.
            # The worker thread fills in the real message ID later.
            send_time = _time.time()
            sent_entry: dict[str, Any] = {"text": text, "id": None, "time": send_time}
            self._sent_messages.append(sent_entry)

            # Capture the RichLog.write bound method on the main thread so the
            # worker thread never touches the Textual DOM directly.
            _write = self.query_one("#messages", RichLog).write

            def _send() -> None:
                """Send the message in a worker thread (blocking HTTP call)."""
                try:
                    resp = send_text_message(self._chat_id, text)
                    now = _time.time()
                    # Patch in the real message ID on the main thread
                    msg_id = resp.get("info", {}).get("id")

                    def _patch_id() -> None:
                        sent_entry["id"] = msg_id
                        sent_entry["time"] = now

                    if msg_id:
                        self.call_from_thread(_patch_id)
                    formatted = _fmt_chat_line_rich(self._my_name, text, now)
                    self.call_from_thread(_write, formatted)
                except Exception as exc:
                    # Remove the placeholder on failure

                    def _remove_placeholder() -> None:
                        if sent_entry in self._sent_messages:
                            self._sent_messages.remove(sent_entry)

                    self.call_from_thread(_remove_placeholder)
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

        def action_complete_command(self) -> None:
            """Handle Tab key — show autocomplete for slash commands."""
            inp = self.query_one("#input", Input)
            text = inp.value.strip()

            if not text.startswith("/"):
                return

            # If suggestions are already visible, let the ListView handle Tab
            if hasattr(self, "_suggest_widget") and self._suggest_widget is not None:
                return

            cmd_text = text[1:]
            matched_name, command, candidates = self._cmd_registry.match(cmd_text)

            if candidates and len(candidates) == 1:
                # Unique prefix match — auto-complete the command name
                inp.value = f"/{candidates[0]} "
                inp.cursor_position = len(inp.value)
            elif candidates:
                # Multiple matches — show hint
                log = self.query_one("#messages", RichLog)
                log.write(f"  [dim]{' | '.join('/' + c for c in candidates)}[/dim]")
            elif command is not None and command.name == "unsend":
                # Exact match for /unsend — show suggestion popup
                _show_unsend_suggestions(self)

        def on_option_list_option_selected(self, event: Any) -> None:
            """Handle selection from the command suggestion popup."""
            ol = getattr(event, "option_list", None)
            if ol is None or getattr(ol, "id", None) != "cmd-suggestions":
                return
            event.stop()
            idx: int = getattr(event, "option_index", getattr(event, "index", -1))
            if idx >= 0:
                _apply_unsend_suggestion(self, idx)
                self.query_one("#input", Input).focus()

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
                # Inject message_id so pic-private.zhihu.com URLs are viewable.
                msg_id = meta.get("id") or data.get("id") or ""
                img_url = _inject_message_id(img_url, msg_id)
                text = f"![]({img_url})" if img_url else "[图片]"
            elif content_type == "risk_tip":
                text = content.get("text", "")
                from rich.text import Text

                line = Text("  ")
                if ts is not None:
                    line.append(f"[{fmt_time(ts)}]", style="dim")
                line.append(" [风险提示] ", style="dim italic")
                line.append(text, style="dim italic")
                self.query_one("#messages", RichLog).write(line)
                # Desktop notification for risk_tip
                if self._notifier is not None and not self._terminal_has_focus:
                    asyncio.create_task(self._notifier.send(title="风险提示", message=text[:200]))
                return
            else:
                text = content.get("text", "")

            formatted = _fmt_chat_line_rich(self._partner_name, text, ts)
            self.query_one("#messages", RichLog).write(formatted)

            # Desktop notification — only when terminal is NOT focused
            if self._notifier is not None and not self._terminal_has_focus:
                title = self._partner_name
                body = "[图片]" if content_type == "image" else content.get("text", "")[:200]
                asyncio.create_task(self._notifier.send(title=title, message=body))

    # ── 5. Run the Textual app (synchronous — manages its own event loop) ──
    app = ChatSessionApp()
    app.run()

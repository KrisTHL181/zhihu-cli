"""Chat commands for zhihu CLI — inbox, history, send, and interactive chat."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from typing import Any

    from rich.text import Text

from zhihu_cli.content.handlers.chat import (
    get_inbox,
    interactive_chat,
    iter_chat_history,
    mark_inbox_read,
    send_image_message,
    send_text_message,
    unsend_message,
)
from zhihu_cli.content.handlers.people import get_my_url_token
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    info,
    print_json,
)

# Regex for Markdown image syntax: ![alt](url)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _render_chat_content(text: str) -> Text:
    """Convert Markdown image references in *text* to Rich clickable ``[图片]`` links.

    Image patterns like ``![](https://pic.zhihu.com/xxx.jpg)`` or
    ``![描述](url)`` are replaced with a clickable ``[图片]`` styled as a
    Rich hyperlink.  All other text is preserved verbatim (no markup
    interpretation).

    :param text: Raw chat message content (may contain ``![alt](url)``).
    :returns: A :class:`~rich.text.Text` renderable.
    """
    from rich.text import Text

    result = Text()
    pos = 0
    for m in _IMG_RE.finditer(text):
        # Append any plain text before this image
        result.append(text[pos : m.start()])
        url = m.group(2)
        if url:
            result.append("[图片]", style=f"link {url}")
        else:
            result.append("[图片]")
        pos = m.end()
    # Append any trailing plain text
    result.append(text[pos:])
    return result


def _print_bot_event(event: dict[str, Any], output_json: bool) -> None:
    """Render one chat-bot event dict as styled output or JSON.

    :param event: Event dict produced by ``run_bot`` (``type`` is one of
        ``"started"``, ``"reply"``, ``"error"``).
    :param output_json: If True, print the raw event as JSON instead.
    """
    if output_json:
        print_json(event)
        return

    etype = event.get("type")
    if etype == "started":
        senders = event.get("senders") or ["*"]
        mode = " (dry-run — replies will not be sent)" if event.get("dry_run") else ""
        info(f"Bot listening — replying to: {', '.join(senders)}{mode}. Press Ctrl+C to stop.")
    elif etype == "reply":
        echo(f"  {f_dim('←')} {f_name(event['sender'])}: {event['message']}")
        marker = f"  {f_green('→')}" if not event.get("dry_run") else f"  {f_tag('dry')} {f_green('→')}"
        echo(f"{marker} {f_name(event['sender'])}: {event['reply']}")
    elif etype == "error":
        prefix = event.get("sender") or "bot"
        message = event.get("message") or ""
        reason = event.get("reason", "")
        if message:
            error(f"[{prefix}] {message} — {reason}")
        else:
            error(f"[{prefix}] {reason}")


def register_chat(main_group: click.Group) -> None:
    """Register the chat command group onto *main_group*."""

    @main_group.group()
    def chat() -> None:
        """Read inbox, view chat history, send messages."""

    @chat.command("inbox")
    @click.option("--limit", "-n", type=int, default=0, help="Max threads to fetch (0 = all pages)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--unread", is_flag=True, default=False, help="Show only threads with unread messages")
    def chat_inbox(limit: int, output_json: bool, unread: bool) -> None:
        """List recent conversations (paginated — walks all pages by default)."""
        messages, total_unread = get_inbox(limit=limit, unread_only=unread)
        if output_json:
            print_json(messages)
            return
        if not messages:
            info("Inbox is empty.")
            return
        echo(
            f"  {f_label('Total unread threads:')} {f_num(total_unread)}  {f_label('Showing')} {f_num(len(messages))} {f_dim('threads')}"
        )
        blank()
        for msg in messages:
            unread = msg["unread_count"]
            echo(f"  {f_tag(f'{unread} unread')} {f_name(msg['from'])}")
            echo(f"    {f_dim(msg['snippet'][:80])}")
            echo(
                f"    {f_label('id=')}{msg['id']}  {f_label('token=')}{msg['url_token']}  {f_label('time=')}{f_meta(msg['updated_time'])}"
            )
            blank()

    @chat.command("clear")
    @click.option("--dry-run", is_flag=True, default=False, help="Show what would be marked without doing it")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output result as JSON")
    def chat_clear(dry_run: bool, output_json: bool) -> None:
        """Mark all unread inbox threads as read.

        Fetches the inbox for unread threads, then accesses each
        conversation briefly so Zhihu automatically flips the unread flag.
        """
        if dry_run:
            messages, total_unread = get_inbox(unread_only=True)
            if output_json:
                print_json(
                    {
                        "dry_run": True,
                        "unread_threads": len(messages),
                        "total_unread": total_unread,
                        "threads": messages,
                    }
                )
            else:
                if not messages:
                    info("No unread threads.")
                    return
                echo(
                    f"  {f_label('Would mark')} {f_num(len(messages))} {f_label('threads as read')} ({f_num(total_unread)} {f_dim('total unread messages')})"
                )
                blank()
                for msg in messages:
                    echo(
                        f"  {f_tag(f'{msg["unread_count"]} unread')} {f_name(msg['from'])}  {f_dim(f'id={msg["id"]}')}"
                    )
                blank()
            return

        messages, _ = get_inbox(unread_only=True)
        threads = [m for m in messages if m.get("id") and m["unread_count"] > 0]

        if not threads:
            if output_json:
                print_json({"marked": [], "skipped": 0, "message": "No unread threads."})
            else:
                info("No unread threads — inbox is already clean.")
            return

        if not output_json:
            info(f"Marking {len(threads)} thread(s) as read...")
        marked, skipped = mark_inbox_read(threads)

        if output_json:
            print_json({"marked": marked, "skipped": skipped})
        else:
            if skipped:
                echo(f"  {f_green('Done')} — marked {f_num(len(marked))} as read, {f_dim(f'{skipped} skipped')}")
            else:
                echo(f"  {f_green('Done')} — marked {f_num(len(marked))} as read")
            if marked:
                blank()
                for msg in marked:
                    echo(
                        f"  {f_tag(f'{msg["unread_count"]} unread')} {f_name(msg['from'])}  {f_dim(f'id={msg["id"]}')}"
                    )

    @chat.command("history")
    @click.argument("chat_id")
    @click.option("--limit", "-n", type=int, default=50, help="Max messages to fetch")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--rich-images/--no-rich-images",
        "rich_images",
        is_flag=True,
        default=False,
        help="Render images as clickable Rich [图片] links instead of raw URLs",
    )
    @click.option("--show-id", is_flag=True, default=False, help="Show message IDs")
    def chat_history(chat_id: str, limit: int, output_json: bool, rich_images: bool, show_id: bool) -> None:
        """Read messages from a chat conversation."""
        if output_json:
            msgs = list(iter_chat_history(chat_id, limit=limit))
            print_json(msgs)
            return
        if rich_images:
            from rich.console import Console
            from rich.text import Text

            _console = Console()
            for msg in iter_chat_history(chat_id, limit=limit):
                t = msg["time"]
                s = msg["sender"]
                line = Text("  ")
                if show_id:
                    line.append(f"[{msg['id']}]", style="dim")
                    line.append(" ")
                line.append(f"[{t}]", style="dim")
                line.append(s, style="bold green")
                line.append(": ")
                line.append(_render_chat_content(msg["content"]))
                _console.print(line)
        else:
            for msg in iter_chat_history(chat_id, limit=limit):
                t = msg["time"]
                s = msg["sender"]
                msg_id = msg["id"]
                id_prefix = f"{f_dim(f'[{msg_id}]')} " if show_id else ""
                echo(f"  {id_prefix}{f_meta(f'[{t}]')}{f_name(s)}: {msg['content']}")

    @chat.command("send")
    @click.argument("user_id")
    @click.argument("content")
    def chat_send(user_id: str, content: str) -> None:
        """Send a text message to a user."""
        resp = send_text_message(user_id, content)
        echo(resp)

    @chat.command("unsend")
    @click.argument("message_id")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def chat_unsend(message_id: str, output_json: bool) -> None:
        """Recall (unsend) a previously sent chat message."""
        resp = unsend_message(message_id)
        if output_json:
            print_json(resp)
        else:
            if resp.get("success"):
                echo(f"  {resp.get('content', '已撤回')}")
            else:
                echo("  [red]撤回失败[/red]")

    @chat.command("send-image")
    @click.argument("user_id")
    @click.argument("file_path")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def chat_send_image(user_id: str, file_path: str, output_json: bool) -> None:
        """Upload and send an image to a user via chat."""
        try:
            resp = send_image_message(user_id, file_path)
        except FileNotFoundError as e:
            error(f"{e}")
            raise SystemExit(1)
        except RuntimeError as e:
            error(f"{e}")
            raise SystemExit(1)
        if output_json:
            print_json(resp)
        else:
            msg_id = resp.get("info", {}).get("id", "?")
            echo(f"  {f_green('Image sent')}  id={f_dim(msg_id)}")

    @chat.command("export")
    @click.argument("chat_id")
    @click.option("--limit", "-n", type=int, default=0, help="Max messages to export (0 = all)")
    @click.option(
        "--output-dir",
        "-o",
        default=None,
        help="Output directory (default: ~/.zhihu-cli/downloads/chats)",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output result as JSON")
    def chat_export(chat_id: str, limit: int, output_dir: str | None, output_json: bool) -> None:
        """Export chat history to a markdown file with images downloaded locally.

        Fetches the full conversation with CHAT_ID, builds a markdown document
        with YAML frontmatter, downloads all referenced images to a media/
        subdirectory, and rewrites image URLs to local relative paths.  Regular
        links in message text are left unchanged.
        """
        from pathlib import Path

        from zhihu_cli.content.handlers.chat import export_chat_history

        if output_dir is None:
            output_dir = str(Path.home() / ".zhihu-cli" / "downloads" / "chats")

        filepath = export_chat_history(chat_id, output_dir, limit=limit)

        if output_json:
            print_json({"filepath": filepath, "chat_id": chat_id})
        else:
            echo(f"  {f_green('Exported')} chat with {f_name(chat_id)}")
            echo(f"  {f_label('→')} {f_dim(filepath)}")

    @chat.command("interactive")
    @click.argument("user_id")
    @click.option("--sender", "-s", default=None, help="MQTT filter override (defaults to user_id)")
    @click.option(
        "--notify/--no-notify",
        "desktop_notify",
        default=True,
        help="Send desktop notifications for new incoming messages (requires: pip install -e .[notify])",
    )
    def chat_interactive(user_id: str, sender: str | None, desktop_notify: bool) -> None:
        """Start an interactive chat session with real-time messages.

        Loads chat history, starts a background MQTT listener for incoming
        messages, and provides a persistent input prompt.  Incoming messages
        appear in real time above the prompt without jitter.

        Type a message and press Enter to send.  Use /quit, /exit, /q, or
        Ctrl+D / Ctrl+C to exit.
        """
        try:
            import textual  # noqa: F401
        except ImportError:
            raise click.UsageError("textual is required for interactive chat.  Install with: pip install textual")

        url_token = get_my_url_token()
        if not url_token:
            raise click.UsageError("Cannot auto-detect your url_token. Please authenticate first (zhihu auth login).")

        mqtt_filter = sender if sender else user_id
        info(f"Connecting to Zhihu MQTT (messages from {mqtt_filter})...")
        interactive_chat(user_id, url_token, mqtt_filter, desktop_notify=desktop_notify)

    @chat.command("bot")
    @click.option(
        "--system-prompt-file",
        "-p",
        "prompt_file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=str),
        help="Path to a UTF-8 text file containing the bot's system prompt",
    )
    @click.option(
        "--sender",
        "-s",
        "senders",
        multiple=True,
        help="Only auto-reply to this sender (repeatable; default: all senders)",
    )
    @click.option(
        "--history",
        "-n",
        "history_limit",
        type=int,
        default=20,
        show_default=True,
        help="Recent messages to include as LLM context (0 = all)",
    )
    @click.option("--model", default=None, help="LLM model override (default: zhihu config llm / LLM_MODEL)")
    @click.option("--api-base", default=None, help="LLM API base override (default: zhihu config llm / LLM_API_BASE)")
    @click.option("--api-key", default=None, help="LLM API key override (default: zhihu config llm / LLM_API_KEY)")
    @click.option("--dry-run", is_flag=True, default=False, help="Generate replies but do not send them")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output structured JSON events")
    def chat_bot(
        prompt_file: str,
        senders: tuple[str, ...],
        history_limit: int,
        model: str | None,
        api_base: str | None,
        api_key: str | None,
        dry_run: bool,
        output_json: bool,
    ) -> None:
        """Run an LLM-powered auto-reply bot for incoming private messages.

        Watches real-time chat messages, builds a context from the recent
        conversation history, asks the configured LLM for a reply using the
        system prompt in SYSTEM_PROMPT_FILE, and sends it back automatically.
        Runs until interrupted (Ctrl+C).
        """
        from pathlib import Path

        from zhihu_cli.content.handlers.chat_bot import run_bot

        try:
            system_prompt = Path(prompt_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise click.UsageError(f"Cannot read system prompt file '{prompt_file}': {exc}")
        if not system_prompt:
            raise click.UsageError(f"System prompt file '{prompt_file}' is empty.")

        url_token = get_my_url_token()
        if not url_token:
            raise click.UsageError("Cannot auto-detect your url_token. Please authenticate first (zhihu auth login).")

        run_bot(
            url_token=url_token,
            system_prompt=system_prompt,
            senders=senders,
            history_limit=history_limit,
            api_base=api_base,
            api_key=api_key,
            model=model,
            dry_run=dry_run,
            on_event=lambda ev: _print_bot_event(ev, output_json=output_json),
        )

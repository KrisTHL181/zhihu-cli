from __future__ import annotations

import time as _time
from pathlib import Path
from typing import Any

from textual.widgets import Input, RichLog

from zhihu_cli.content.handlers.chat_commands import ChatCommandRegistry

# ── Command handlers ──────────────────────────────────────────────────


def _cmd_quit(app: Any, args: str) -> bool:  # noqa: ARG001
    """Exit the chat session.

    :param app: The :class:`~textual.app.App` instance.
    :param args: Unused argument string.
    :returns: Always ``True``.
    """
    app.exit()
    return True


def _cmd_help(app: Any, args: str) -> bool:  # noqa: ARG001
    """List available commands in the chat output area.

    :param app: The :class:`~textual.app.App` instance.
    :param args: Unused argument string.
    :returns: Always ``True``.
    """
    registry: ChatCommandRegistry = app._cmd_registry  # type: ignore[attr-defined]
    log = app.query_one("#messages", RichLog)
    log.write("[bold cyan]可用命令:[/bold cyan]")
    for name, cmd in sorted(registry.list_all().items()):
        log.write(f"  [bold]/[/bold]{name} — [dim]{cmd.help_text}[/dim]")
    return True


def _cmd_unsend(app: Any, args: str) -> bool:
    """Recall (unsend) a previously sent message.

    If *args* is empty, shows the 2-minute suggestion list.
    Otherwise *args* is treated as the message ID to recall.

    :param app: The :class:`~textual.app.App` instance.
    :param args: Optional message ID string (everything after ``/unsend``).
    :returns: ``True`` if the command was handled (suggestions shown or recall
        initiated), ``False`` if arguments are invalid.
    """
    if not args or not args.strip():
        # No message ID provided — show suggestion popup
        _show_unsend_suggestions(app)
        return True

    message_id = args.strip()
    log = app.query_one("#messages", RichLog)

    # Find the recalled message text for the ✗ line
    recalled_text = ""
    for m in app._sent_messages:  # type: ignore[attr-defined]
        if m.get("id") == message_id:
            recalled_text = m.get("text", "")
            break

    def _do_unsend() -> None:
        try:
            from zhihu_cli.content.handlers.chat import unsend_message

            resp = unsend_message(message_id)
            if resp.get("success"):
                app.call_from_thread(
                    log.write,
                    f"  [bold red]✗[/bold red] [bold green]{app._my_name}[/bold green]: [dim]{recalled_text}[/dim]",
                )
                # Remove from sent messages tracking
                app._sent_messages = [  # type: ignore[attr-defined]
                    m
                    for m in app._sent_messages
                    if m.get("id") != message_id  # type: ignore[attr-defined]
                ]
            else:
                app.call_from_thread(log.write, "  [bold red]撤回失败[/bold red]")
        except Exception as exc:
            app.call_from_thread(log.write, f"  [bold red]撤回失败:[/bold red] {exc}")

    app.run_worker(_do_unsend, thread=True)
    return True


def _cmd_postimg(app: Any, args: str) -> bool:
    """Upload and send an image file.

    *args* must be a file path to an image on disk.

    :param app: The :class:`~textual.app.App` instance.
    :param args: File path string (everything after ``/postimg``).
    :returns: ``True`` when the upload is initiated, ``False`` when *args*
        is empty (usage info displayed).
    """
    if not args or not args.strip():
        log = app.query_one("#messages", RichLog)
        log.write("  [bold yellow]用法:[/bold yellow] /postimg <图片路径>")
        return False

    file_path = str(Path(args.strip()).expanduser().resolve())
    log = app.query_one("#messages", RichLog)

    def _do_send_image() -> None:
        import os

        if not os.path.isfile(file_path):
            app.call_from_thread(log.write, f"  [bold red]文件不存在:[/bold red] {file_path}")
            return
        try:
            from zhihu_cli.content.handlers.chat import send_image_message

            resp = send_image_message(app._chat_id, file_path)  # type: ignore[attr-defined]
            msg_id = resp.get("info", {}).get("id", "?")
            app.call_from_thread(log.write, f"  [bold green]✓ 图片已发送[/bold green] [dim]id={msg_id}[/dim]")
            # Track sent message
            app._sent_messages.append(  # type: ignore[attr-defined]
                {
                    "text": f"[图片] ({file_path})",
                    "id": msg_id,
                    "time": _time.time(),
                }
            )
        except Exception as exc:
            app.call_from_thread(log.write, f"  [bold red]发送图片失败:[/bold red] {exc}")

    app.run_worker(_do_send_image, thread=True)
    return True


# ── Suggestion popup for /unsend ───────────────────────────────────────


def _show_unsend_suggestions(app: Any) -> None:
    """Show a popup list of recently-sent messages that can be recalled.

    Filters messages sent by the current user within the last 2 minutes.

    :param app: The :class:`~textual.app.App` instance.
    """
    from textual.widgets import OptionList

    now = _time.time()

    recent = [m for m in app._sent_messages if now - m.get("time", 0) < 120 and m.get("id") is not None]  # type: ignore[attr-defined]

    if not recent:
        inp = app.query_one("#input", Input)
        inp.placeholder = "没有2分钟内发送的消息可以撤回"
        return

    inp = app.query_one("#input", Input)
    inp.placeholder = f"选择要撤回的消息 ({len(recent)} 条) — ↑↓ 选择, Enter 确认"

    # Build option items with Rich markup
    import datetime

    option_labels: list[str] = []
    for i, msg in enumerate(recent):
        text = msg.get("text", "")
        display = text[:60] + ("…" if len(text) > 60 else "")
        ts = msg.get("time", 0)
        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        option_labels.append(f"[dim]{time_str}[/dim]  {display}")

    suggestions = OptionList(*option_labels, id="cmd-suggestions")
    suggestions.styles.height = min(len(option_labels) + 2, 9)
    suggestions.styles.margin = (0, 1, 0, 1)
    suggestions.styles.background = "#313244"
    suggestions.styles.border = ("heavy", "#cba6f7")

    # Store mapping for selection
    app._suggest_data = recent  # type: ignore[attr-defined]
    app._suggest_widget = suggestions  # type: ignore[attr-defined]

    # Mount between RichLog and Input
    app.mount(suggestions, before="#input")
    suggestions.focus()


def _apply_unsend_suggestion(app: Any, index: int) -> None:
    """Apply the selected suggestion: fill the message ID into the input.

    Removes the suggestion popup widget and fills the input field with
    ``"/unsend <message_id>"``.

    :param app: The :class:`~textual.app.App` instance.
    :param index: The index into :attr:`_suggest_data` to apply.
    """
    inp = app.query_one("#input", Input)
    data = app._suggest_data  # type: ignore[attr-defined]
    if 0 <= index < len(data):
        msg_id = data[index]["id"]
        inp.value = f"/unsend {msg_id}"
        inp.cursor_position = len(inp.value)
    # Remove suggestion widget and restore placeholder
    if hasattr(app, "_suggest_widget") and app._suggest_widget is not None:
        app._suggest_widget.remove()
        app._suggest_widget = None
    app._suggest_data = []  # type: ignore[attr-defined]
    inp.placeholder = "/help 查看命令, /unsend 撤回消息"


# ── Registration ──────────────────────────────────────────────────────


def register_builtin_commands(registry: ChatCommandRegistry) -> None:
    """Register all built-in slash commands onto *registry*.

    :param registry: The :class:`ChatCommandRegistry` to populate.
    """
    registry.register("quit", "退出聊天会话 (同 /exit, /q)", _cmd_quit)
    registry.register("exit", "退出聊天会话 (同 /quit, /q)", _cmd_quit)
    registry.register("q", "退出聊天会话 (同 /quit, /exit)", _cmd_quit)
    registry.register("help", "显示所有可用命令", _cmd_help)
    registry.register("unsend", "撤回消息 — /unsend 选择最近消息, /unsend <id> 直接撤回", _cmd_unsend)
    registry.register("postimg", "发送图片 — /postimg <图片路径>", _cmd_postimg)

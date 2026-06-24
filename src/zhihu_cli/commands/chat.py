"""Chat commands for zhihu CLI — inbox, history, send, and interactive chat."""

import click

from zhihu_cli.content.handlers.chat import (
    get_inbox,
    interactive_chat,
    iter_chat_history,
    send_text_message,
)
from zhihu_cli.content.handlers.people import get_my_url_token
from zhihu_cli.output import (
    blank,
    echo,
    f_dim,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    info,
    print_json,
)


def register_chat(main_group: click.Group) -> None:
    """Register the chat command group onto *main_group*."""

    @main_group.group()
    def chat() -> None:
        """Read inbox, view chat history, send messages."""

    @chat.command("inbox")
    @click.option("--limit", "-n", type=int, default=0, help="Max threads to fetch (0 = all pages)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def chat_inbox(limit: int, output_json: bool) -> None:
        """List recent conversations (paginated — walks all pages by default)."""
        messages, total_unread = get_inbox(limit=limit)
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

    @chat.command("history")
    @click.argument("chat_id")
    @click.option("--limit", "-n", type=int, default=50, help="Max messages to fetch")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def chat_history(chat_id: str, limit: int, output_json: bool) -> None:
        """Read messages from a chat conversation."""
        if output_json:
            msgs = list(iter_chat_history(chat_id, limit=limit))
            print_json(msgs)
            return
        for msg in iter_chat_history(chat_id, limit=limit):
            t = msg["time"]
            s = msg["sender"]
            echo(f"  {f_meta(f'[{t}]')}{f_name(s)}: {msg['content']}")

    @chat.command("send")
    @click.argument("user_id")
    @click.argument("content")
    def chat_send(user_id: str, content: str) -> None:
        """Send a text message to a user."""
        resp = send_text_message(user_id, content)
        echo(resp)

    @chat.command("interactive")
    @click.argument("user_id")
    @click.option("--sender", "-s", default=None, help="MQTT filter override (defaults to user_id)")
    def chat_interactive(user_id: str, sender: str | None) -> None:
        """Start an interactive chat session with real-time messages.

        Loads chat history, starts a background MQTT listener for incoming
        messages, and provides a persistent input prompt.  Incoming messages
        appear in real time above the prompt without jitter.

        Type a message and press Enter to send.  Use /quit, /exit, /q, or
        Ctrl+D / Ctrl+C to exit.
        """
        try:
            import prompt_toolkit  # noqa: F401
        except ImportError:
            raise click.UsageError(
                "prompt_toolkit is required for interactive chat.  Install with: pip install prompt-toolkit"
            )

        url_token = get_my_url_token()
        if not url_token:
            raise click.UsageError("Cannot auto-detect your url_token. Please authenticate first (zhihu auth login).")

        mqtt_filter = sender if sender else user_id
        info(f"Connecting to Zhihu MQTT (messages from {mqtt_filter})...")
        interactive_chat(user_id, url_token, mqtt_filter)

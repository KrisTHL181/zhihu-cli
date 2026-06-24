"""Listen command group — real-time MQTT event listening."""

import click

from zhihu_cli.content.handlers.people import get_my_url_token
from zhihu_cli.output import (
    echo,
    info,
)


def register_listen(main_group):
    """Register the ``listen`` command group on *main_group*."""

    @main_group.group()
    def listen() -> None:
        """Listen to real-time MQTT events."""
        pass

    @listen.command("notifications")
    @click.option("--incognito/--no-incognito", default=False, help="Connect incognito")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
    def listen_notifications(incognito: bool, output_json: bool) -> None:
        """Listen to Zhihu notification badge events via MQTT."""
        from zhihu_cli.content.handlers.imchat import NOTIFICATION_TOPIC, ZhihuMessageListener

        url_token = get_my_url_token()
        if not url_token:
            raise click.UsageError("Cannot auto-detect your url_token. Please authenticate first (zhihu auth login).")
        info("Connecting to Zhihu MQTT (notifications)...")
        listener = ZhihuMessageListener(url_token, NOTIFICATION_TOPIC, incognito=incognito)
        echo("Listening for notifications — press Ctrl+C to stop.")
        try:
            listener.start(output_json=output_json)
        except KeyboardInterrupt:
            echo("\nStopped.")

    @listen.command("messages")
    @click.option("--sender", "-s", default=None, help="Filter messages from a specific user (url_token or user hash)")
    @click.option("--incognito/--no-incognito", default=False, help="Connect incognito")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
    def listen_messages(sender: str | None, incognito: bool, output_json: bool) -> None:
        """Listen to Zhihu private messages (IM) via MQTT."""
        from zhihu_cli.content.handlers.imchat import IMCHAT_TOPIC, ZhihuMessageListener

        url_token = get_my_url_token()
        if not url_token:
            raise click.UsageError("Cannot auto-detect your url_token. Please authenticate first (zhihu auth login).")
        info("Connecting to Zhihu MQTT (messages)...")
        listener = ZhihuMessageListener(url_token, IMCHAT_TOPIC, incognito=incognito, sender_filter=sender)
        if sender:
            echo(f"Listening for messages from {sender} — press Ctrl+C to stop.")
        else:
            echo("Listening for messages — press Ctrl+C to stop.")
        try:
            listener.start(output_json=output_json)
        except KeyboardInterrupt:
            echo("\nStopped.")

import asyncio
import json
import random
import ssl
from collections.abc import AsyncGenerator
from typing import Any

import aiomqtt
import click

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.chat import _inject_message_id
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session

NOTIFICATION_TOPIC: str = "zhihu/notification/badge/web/v1/{USER_HASH}/"
IMCHAT_TOPIC: str = "zhihu/message/v1/im/user/{USER_HASH}/"


def get_pm_mqtt_topic(url_token: str) -> str:
    """Resolve a user's MQTT topic hash from their profile page.

    :param url_token: The user's URL token (e.g. ``"zhangsan"``).
    :returns: The 32-character hex hash used in MQTT topic subscriptions.
    """
    entities = get_page_state(fetch_page_html(f"https://www.zhihu.com/people/{url_token}"))
    item = entities["users"]
    return next(iter(item))


class ZhihuMessageListener:
    """Async MQTT listener for real-time Zhihu notifications and IM messages.

    Uses ``aiomqtt`` over WebSocket+TLS to connect to Zhihu's MQTT broker.
    Messages are received asynchronously and can be consumed via
    :func:`iter_messages` (programmatic) or :func:`listen` (standalone CLI).

    :param url_token: The logged-in user's URL token.
    :param topic: MQTT topic template string containing ``{USER_HASH}``.
    :param incognito: Unused; reserved for future use.
    :param sender_filter: Optional sender filter (url_token or 32-char hex hash).
    """

    def __init__(
        self,
        url_token: str,
        topic: str,
        incognito: bool = False,
        sender_filter: str | None = None,
    ) -> None:
        self.url_token = url_token
        self.user_hash = get_pm_mqtt_topic(url_token)

        self.topic = topic.replace("{USER_HASH}", self.user_hash)

        # Resolve sender_filter: accept url_token or raw user hash.
        # When a filter is set, only messages from that sender are printed,
        # and the sender's display name is shown instead of the raw hash.
        self.sender_label: str | None = sender_filter
        if sender_filter:
            if len(sender_filter) == 32 and all(c in "0123456789abcdef" for c in sender_filter):
                self.receiver_id = sender_filter
                # Resolve hash → human-readable name via chat API.
                try:
                    resp = session.get(f"https://www.zhihu.com/api/v4/chat?sender_id={sender_filter}")
                    data = resp.json()
                    partner = data.get("data", {}).get("sender", {})
                    self.sender_label = partner.get("name") or partner.get("url_token") or sender_filter
                except Exception:
                    pass  # keep the hash as fallback
            else:
                self.receiver_id = get_pm_mqtt_topic(sender_filter)
        else:
            self.receiver_id = None

        self.broker = "mqtt-web.zhihu.com"
        self.port = 443
        self.client_id = f"mqttjs_{random.randint(0, 0xFFFFFFFF):08x}"
        self.incognito = incognito

        # Pre-compute connection parameters (headers are cached at build time;
        # the connection itself is established lazily on first listen/iterate).
        self._tls_context = ssl.create_default_context()
        self._ws_headers = cache_manager.load_headers()
        self._ws_path = "/mqtt?client_info=OS%3DWeb&user_group=zhihu_web"

    def _build_client(self) -> aiomqtt.Client:
        """Build an :class:`aiomqtt.Client` configured for Zhihu's MQTT broker.

        The client is not connected — call this inside an ``async with`` block.
        """
        return aiomqtt.Client(
            hostname=self.broker,
            port=self.port,
            transport="websockets",
            tls_context=self._tls_context,
            websocket_path=self._ws_path,
            websocket_headers=self._ws_headers,
            identifier=self.client_id,
            keepalive=30,
        )

    async def iter_messages(self) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that connects to the MQTT broker and yields parsed message dicts.

        Each yielded dict is the JSON-decoded MQTT payload.  Messages that
        don't match *sender_filter* (when set) are silently skipped.  The
        connection is held open for the lifetime of the iteration — cancel
        the task or break out of the loop to disconnect.

        :yields: Parsed JSON message dicts.
        """
        async with self._build_client() as client:
            await client.subscribe(self.topic)
            async for message in client.messages:
                try:
                    data = json.loads(message.payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if self.receiver_id and data.get("meta", {}).get("sender_id") != self.receiver_id:
                    continue
                yield data

    async def listen(self, output_json: bool = False) -> None:
        """Connect to MQTT and print incoming messages to stdout (standalone mode).

        Designed for CLI usage via ``zhihu listen``.  Runs until cancelled
        (Ctrl+C), which triggers a clean disconnect via aiomqtt's context
        manager.

        :param output_json: If True, print raw JSON instead of formatted text.
        """
        try:
            async for data in self.iter_messages():
                if output_json:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
                else:
                    click.echo(self._format_message(data))
        except asyncio.CancelledError:
            pass  # clean shutdown on Ctrl+C

    def _format_message(self, data: dict) -> str:
        """Format a single MQTT IM message in chat-history style.

        Returns a string like ``  [2025-01-01 12:00:00]sender_hash: message text``
        where the timestamp is dimmed and the sender is green-bold — matching
        the ``chat history`` command's output format.

        When a sender filter is active, the original filter value (url_token or
        hash) is shown as the sender name instead of the raw ``meta.sender_id``.
        """
        meta = data.get("meta", {})
        content = data.get("content", {})

        # Use the original sender label when filtering, otherwise the raw hash.
        sender = self.sender_label if self.sender_label else meta.get("sender_id", "unknown")
        content_type = meta.get("content_type", "text")

        # MQTT timestamps are in milliseconds; fmt_time expects seconds.
        raw_ts = meta.get("created_at", 0)
        ts = int(raw_ts) / 1000 if raw_ts else None
        t = fmt_time(ts)

        if content_type == "image":
            img = content.get("image") or {}
            img_url = img.get("url", "") if isinstance(img, dict) else ""
            msg_id = meta.get("id") or data.get("id") or ""
            img_url = _inject_message_id(img_url, msg_id)
            text = f"![]({img_url})" if img_url else "[图片]"
        else:
            text = content.get("text", "")

        time_part = click.style(f"[{t}]", dim=True)
        sender_part = click.style(sender, fg="green", bold=True)
        return f"  {time_part}{sender_part}: {text}"

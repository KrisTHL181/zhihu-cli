import json
import queue
import random
from typing import Any

import click
from paho.mqtt import client as mqtt_client
from paho.mqtt.enums import CallbackAPIVersion

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state, session

NOTIFICATION_TOPIC: str = "zhihu/notification/badge/web/v1/{USER_HASH}/"
IMCHAT_TOPIC: str = "zhihu/message/v1/im/user/{USER_HASH}/"


def get_pm_mqtt_topic(url_token: str) -> str:
    entities = get_page_state(fetch_page_html(f"https://www.zhihu.com/people/{url_token}"))
    item = entities["users"]
    return next(iter(item))


class ZhihuMessageListener:
    def __init__(self, url_token: str, topic: str, incognito: bool = False, sender_filter: str | None = None) -> None:
        self.url_token = url_token
        self.user_hash = get_pm_mqtt_topic(url_token)
        self.msg_queue = queue.Queue()

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
        self.client = self._connect()

    def _connect(self) -> mqtt_client.Client:
        client = mqtt_client.Client(CallbackAPIVersion.VERSION2, self.client_id, transport="websockets")
        client.tls_set()

        headers = cache_manager.load_headers()

        ws_path = "/mqtt?client_info=OS%3DWeb&user_group=zhihu_web"
        client.ws_set_options(path=ws_path, headers=headers)

        client.on_connect = self.on_connect
        client.on_message = self.on_message

        client.connect(self.broker, self.port, keepalive=30)
        return client

    def on_connect(
        self, client: mqtt_client.Client, userdata: Any, flags: dict[str, Any], reason_code: Any, properties: Any
    ) -> None:
        if reason_code.is_failure:
            raise ConnectionError(
                f"Failed to connect to Zhihu MQTT Broker. Reason: {reason_code} (Code: {reason_code.value})"
            )
        else:
            client.subscribe(self.topic)

    def on_message(self, client: mqtt_client.Client, userdata: Any, msg: mqtt_client.MQTTMessage) -> None:
        raw_payload = msg.payload.decode("utf-8")
        data = json.loads(raw_payload)
        self.msg_queue.put(data)

    def start(self, output_json: bool = False) -> None:
        """Start the MQTT listener and print incoming messages to stdout.

        If ``receiver_id`` is set, only messages to that receiver are printed.
        When *output_json* is False (the default), messages are formatted in
        chat-history style (``[time]sender: content``).
        """
        self.client.loop_start()
        try:
            while True:
                data = self.msg_queue.get()
                # `meta.sender_id` carries the sender's hash; `meta.receiver_id`
                # is the recipient (i.e. the logged-in user).  Filter on sender.
                if self.receiver_id and data.get("meta", {}).get("sender_id") != self.receiver_id:
                    continue
                if output_json:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
                else:
                    click.echo(self._format_message(data))
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.client.disconnect()

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
            text = f"![]({img_url})" if img_url else "[图片]"
        else:
            text = content.get("text", "")

        time_part = click.style(f"[{t}]", dim=True)
        sender_part = click.style(sender, fg="green", bold=True)
        return f"  {time_part}{sender_part}: {text}"

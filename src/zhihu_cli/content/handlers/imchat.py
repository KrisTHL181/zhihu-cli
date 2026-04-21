import json
import queue
import time

from paho.mqtt import client as mqtt_client
from paho.mqtt.enums import CallbackAPIVersion

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import get_page_entities

NOTIFICATION_TOPIC = "zhihu/notification/badge/web/v1/{USER_HASH}/"
IMCHAT_TOPIC = "zhihu/message/v1/im/user/{USER_HASH}/"


def get_pm_mqtt_topic(url_token: str) -> str:
    entities = get_page_entities(f"https://www.zhihu.com/people/{url_token}")
    item = entities["users"]
    return next(iter(item))


class ZhihuMessageListener:
    def __init__(self, url_token: str, topic: str, incognito: bool = False):
        self.url_token = url_token
        self.user_hash = get_pm_mqtt_topic(url_token)
        self.msg_queue = queue.Queue()

        self.topic = topic.replace("{USER_HASH}", self.user_hash)

        self.broker = "mqtt-web.zhihu.com"
        self.port = 443
        self.client_id = f"mqttjs_{int(time.time() * 1000):x}"[:15]
        self.incognito = incognito
        self.client = self._connect()

    def _connect(self):
        client = mqtt_client.Client(CallbackAPIVersion.VERSION2, self.client_id, transport="websockets")
        client.tls_set()

        headers = cache_manager.load_headers()

        ws_path = "/mqtt?client_info=OS%3DWeb&user_group=zhihu_web"
        client.ws_set_options(path=ws_path, headers=headers)

        client.on_connect = self.on_connect
        client.on_message = self.on_message

        client.connect(self.broker, self.port)
        return client

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            raise ConnectionError(
                f"Failed to connect to Zhihu MQTT Broker. Reason: {reason_code} (Code: {reason_code.value})"
            )
        else:
            client.subscribe(self.topic)

    def on_message(self, client, userdata, msg):
        raw_payload = msg.payload.decode("utf-8")
        data = json.loads(raw_payload)
        self.msg_queue.put(data)

    def start(self):
        self.client.loop_forever()

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import session


def get_inbox() -> list[dict]:
    messages = []
    resp = session.get("https://www.zhihu.com/api/v4/inbox")
    resp.raise_for_status()

    inbox = resp.json()["data"]
    for message in inbox:
        messages.append(
            {
                "id": message.get("participant", {}).get("id"),
                "url_token": message.get("url_token", ""),
                "from": message.get("participant", {}).get("name", "未知用户"),
                "snippet": message.get("snippet", "(无内容)"),
                "updated_time": fmt_time(message.get("updated_time")),
                "message_count": message.get("message_count", 0),
                "unread_count": message.get("unread_count", 0),
            }
        )
    return messages


def _parse_messages_page(data: dict) -> tuple[list[dict], str | None, tuple]:
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

        role = "them" if msg.get("user_type") == "sender" else "me"
        content = msg.get("text", "")
        time_str = fmt_time(msg.get("created_time"))
        page_msgs.append({"role": role, "content": content, "time": time_str})

    last_id = messages[-1].get("id")
    return page_msgs, last_id, (receiver_name, sender_name)


def iter_chat_history(chat_id: str):
    current_url = f"https://www.zhihu.com/api/v4/chat?sender_id={chat_id}"

    while current_url:
        resp = session.get(current_url)
        resp.raise_for_status()

        breakpoint()
        data = resp.json()

        page_msgs, _, _ = _parse_messages_page(data)
        if not page_msgs:
            break

        yield from page_msgs

        paging = data.get("paging", {})

        if paging.get("is_end", True) or len(page_msgs) < 20:
            current_url = None
        else:
            current_url = paging.get("next")


def send_text_message(their_id: str, content: str) -> dict:
    resp = session.post(
        "https://www.zhihu.com/api/v4/chat", json={"content_type": 0, "text": content, "receiver_id": their_id}
    )

    data = resp.json()
    if resp.status_code == 403 and "error" in data.keys():
        raise RuntimeError(f"Failed to send message: {data['error']['message']}")
    resp.raise_for_status()

    return data

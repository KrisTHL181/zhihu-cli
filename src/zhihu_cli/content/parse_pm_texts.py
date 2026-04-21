import re
import sys
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from curl_cffi import requests


def extract_config(curl_text):
    """从 cURL 提取 Header 和基础 URL"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    url = url_match.group(1) if url_match else ""

    headers = {}
    for h in re.findall(r"-H\s+'([^']+)'", curl_text):
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    headers.pop("Accept-Encoding", None)
    return url, headers


def build_next_url(base_url, last_msg_id):
    """
    手动构造翻页 URL
    逻辑：保留原有的 query 参数（如 sender_id），添加/更新 after_id
    """
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)

    # 转换为单值字典并添加翻页参数
    params = {k: v[0] for k, v in query.items()}
    params["after_id"] = last_msg_id
    params["limit"] = "20"

    new_query = urlencode(params)
    # 构造不含原有 query 的路径，重新拼装
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def parse_page(data_dict):
    """
    解析单页数据
    返回: (消息列表, 最后一条消息ID, 用户名元组)
    """
    data = data_dict.get("data", {})
    messages = data.get("messages", [])
    if not messages:
        return [], None, None

    receiver_name = data.get("receiver", {}).get("name", "Unknown")
    sender_name = data.get("sender", {}).get("name", "Unknown")

    page_msgs = []
    for msg in messages:
        if msg["type"] != "message":
            continue
        username = sender_name if msg["user_type"] == "sender" else receiver_name
        content = msg.get("text", "")
        dt_str = datetime.fromtimestamp(msg["created_time"]).strftime("%Y-%m-%d %H:%M:%S")
        page_msgs.append(f"({dt_str}) {username}: {content}")

    # 获取当前页最后一条消息的 ID 用于翻页
    last_id = messages[-1].get("id")
    return page_msgs, last_id, (receiver_name, sender_name)


def main():
    print("--- 请粘贴知乎私信接口的 cURL 命令 (手动翻页版) ---")
    curl_input = sys.stdin.read()
    if not curl_input.strip():
        return

    # 1. 初始化
    current_url, headers = extract_config(curl_input)
    if not current_url:
        print("❌ 无法解析 URL")
        return

    all_messages = []
    is_end = False
    page_num = 1

    print("🚀 开始手动拼接请求抓取...")

    try:
        while not is_end:
            print(f"📥 正在抓取第 {page_num} 页... URL: {current_url}")
            resp = requests.get(current_url, headers=headers, impersonate="chrome110", timeout=15)

            if resp.status_code != 200:
                print(f"❌ 请求失败: {resp.status_code}")
                break

            data = resp.json()
            page_msgs, last_id, names = parse_page(data)

            if not page_msgs:
                print("🏁 没有更多消息了。")
                break

            all_messages.extend(page_msgs)

            # 2. 判断是否结束并构造下一页 URL
            paging = data.get("paging", {})
            is_end = paging.get("is_end", True)

            if not is_end and last_id:
                current_url = build_next_url(current_url, last_id)
                page_num += 1
                time.sleep(0.6)  # 模拟真人操作频率
            else:
                is_end = True

        # 3. 打印结果
        if all_messages:
            # 知乎返回是按时间倒序的，我们需要整体反转回正常的时间线
            all_messages.reverse()
            print("\n" + "=" * 60)
            for m in all_messages:
                print(m)
            print("=" * 60)
            print(f"✅ 抓取完成，共 {len(all_messages)} 条。")

    except Exception as e:
        print(f"💥 出错: {e}")


if __name__ == "__main__":
    main()

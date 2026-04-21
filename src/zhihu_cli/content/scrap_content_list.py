import json
import re
import sys
import time
from datetime import datetime

from curl_cffi import requests

from zhihu_cli.content.handlers.cache_manager import cache_manager


def load_headers(quick_mode: bool = False):
    """Load headers from cache or via cURL paste"""
    if quick_mode:
        headers = cache_manager.load_headers()
        if headers:
            print("[Success] Loaded cached headers from .cache/headers.json")
            return headers

    print("\n--- Please paste cURL from any Zhihu Article Page ---")
    print("Tip: Press Ctrl+D (Unix) or Ctrl+Z+Enter (Win) to finish\n")

    curl_input = sys.stdin.read()
    if not curl_input.strip():
        return None

    base_url, headers, offset_match, after_id_match = extract_config_from_curl(curl_input)
    if not headers:
        return None

    headers.pop("Accept-Encoding", None)
    cache_manager.save_headers(headers)
    print("[Success] Headers configured and cached.")
    return headers


def extract_config_from_curl(curl_text):
    """从 cURL 中提取 URL 和 Headers"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    full_url = url_match.group(1) if url_match else ""
    base_url = full_url.split("?")[0]

    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    # 提取初始分页参数
    offset_match = re.search(r"[?&]offset=(\d+)", full_url)
    after_id_match = re.search(r"[?&]after_id=([^&]+)", full_url)

    # 移除 Accept-Encoding，让 curl_cffi 自己处理
    headers.pop("Accept-Encoding", None)

    return base_url, headers, offset_match, after_id_match


def parse_item(item):
    """解析单条动态数据"""
    target = item.get("target", {})
    verb = item.get("verb", "")
    created_time = item.get("created_time", 0)

    # 动态类型
    item_type = target.get("type", "unknown")
    item_id = target.get("id", "")

    # 提取作者信息
    author = target.get("author", {})
    author_name = author.get("name", "未知用户")
    author_url = author.get("url", "")

    # 提取标题/摘要
    title = target.get("title", "")
    excerpt = target.get("excerpt_title", "")
    content_text = title or excerpt

    # 提取互动数据
    reaction = target.get("reaction", {})
    statistics = reaction.get("statistics", {})
    reaction_relation = target.get("reaction_relation", {})

    # 统计数据
    like_count = statistics.get("like_count", 0) or statistics.get("up_vote_count", 0)
    comment_count = statistics.get("comment_count", 0)
    repin_count = target.get("repin_count", 0)

    # 用户是否已点赞
    is_liked = reaction_relation.get("like", 0) == 1

    # 构造URL
    if target.get("url"):
        url = target.get("url")
    elif item_type == "pin":
        url = f"https://www.zhihu.com/pin/{item_id}"
    elif item_type == "article":
        url = f"https://zhuanlan.zhihu.com/p/{item_id}"
    elif item_type == "answer":
        url = f"https://www.zhihu.com/answer/{item_id}"
    else:
        url = f"https://www.zhihu.com/{item_type}/{item_id}"

    # 时间格式化
    if created_time:
        try:
            time_str = datetime.fromtimestamp(created_time).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_str = str(created_time)
    else:
        time_str = "未知时间"

    return {
        "verb": verb,
        "type": item_type,
        "id": item_id,
        "title": content_text or "无标题内容",
        "url": url,
        "created_time": created_time,
        "created_time_str": time_str,
        "author": {"name": author_name, "url": author_url, "avatar": author.get("avatar_url", "")},
        "stats": {
            "like_count": like_count,
            "comment_count": comment_count,
            "repin_count": repin_count,
            "is_liked": is_liked,
        },
    }


def fetch_user_activities():
    print("=" * 60)
    print("📝 知乎用户动态抓取工具")
    print("=" * 60)

    headers = load_headers(quick_mode=True)
    if not headers:
        return

    print("\n请从浏览器开发者工具复制动态流的 cURL 命令")
    print("步骤：")
    print("  1. F12 打开开发者工具")
    print("  2. 切换到 Network 标签")
    print("  3. 刷新页面，找到动态流请求 (通常包含 'people' 或 'activities')")
    print("  4. 右键 -> Copy -> Copy as cURL")
    print("  5. 粘贴到这里 (按 Ctrl+D 或 Ctrl+Z 结束输入)\n")

    curl_input = sys.stdin.read()
    if not curl_input:
        print("❌ 未检测到输入内容")
        return

    base_url, _, offset_match, after_id_match = extract_config_from_curl(curl_input)

    if not base_url:
        print("❌ 无法解析URL，请检查cURL命令格式")
        return

    # 移除可能导致问题的headers
    headers.pop("Content-Length", None)

    print("\n✅ 成功解析配置")
    print(f"  基础URL: {base_url[:80]}...")
    print(f"  Headers: {len(headers)} 个")

    all_data = []

    # 分页参数
    limit = 20
    offset = int(offset_match.group(1)) if offset_match else 0
    after_id = after_id_match.group(1) if after_id_match else None

    # 判断分页方式
    use_after_id = bool(after_id_match)

    print("\n🚀 开始抓取用户动态...")
    print(f"  分页方式: {'after_id' if use_after_id else 'offset'}")
    print(f"  初始参数: {'after_id=' + after_id if use_after_id else 'offset=' + str(offset)}")

    page = 1
    is_end = False
    max_retries = 3

    while not is_end:
        # 构造请求URL
        if use_after_id and after_id:
            request_url = f"{base_url}?limit={limit}&after_id={after_id}"
        else:
            request_url = f"{base_url}?limit={limit}&offset={offset}"

        retry_count = 0
        success = False

        while retry_count < max_retries and not success:
            try:
                print(f"\n  📄 第 {page} 页请求中...", end=" ")
                resp = requests.get(request_url, headers=headers, impersonate="chrome110", timeout=15)

                if resp.status_code == 200:
                    res_json = resp.json()
                    items = res_json.get("data", [])
                    paging = res_json.get("paging", {})

                    if not items:
                        print("⚠️ 未获取到数据")
                        is_end = True
                        break

                    # 解析每条动态
                    page_count = 0
                    for item in items:
                        parsed = parse_item(item)
                        all_data.append(parsed)
                        page_count += 1

                    print(f"✅ 获取 {page_count} 条")

                    # 更新分页参数
                    is_end = paging.get("is_end", True)

                    if use_after_id:
                        # 使用 after_id 分页
                        after_id = paging.get("next", "").split("after_id=")[-1].split("&")[0] if not is_end else None
                    else:
                        # 使用 offset 分页
                        offset = paging.get("next", "").split("offset=")[-1].split("&")[0] if not is_end else offset
                        if not isinstance(offset, int) and offset and offset.isdigit():
                            offset = int(offset)

                    success = True

                elif resp.status_code == 403:
                    print("❌ 403 禁止访问，可能需要登录")
                    is_end = True
                    break
                else:
                    print(f"❌ 状态码: {resp.status_code}")
                    retry_count += 1
                    if retry_count < max_retries:
                        print(f"     等待后重试 ({retry_count}/{max_retries})...")
                        time.sleep(3)
                    else:
                        is_end = True
                        break

            except requests.exceptions.Timeout:
                print("⏰ 请求超时")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"     重试 ({retry_count}/{max_retries})...")
                    time.sleep(3)
                else:
                    is_end = True
                    break

            except Exception as e:
                raise e
                print(f"❌ 异常: {str(e)[:100]}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(3)
                else:
                    is_end = True
                    break

        if not success:
            break

        page += 1
        time.sleep(2)  # 请求间隔

    # --- 保存数据 ---
    if all_data:
        output_file = "zhihu_user_activities.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=4, ensure_ascii=False)

        # 生成统计报告
        print("\n" + "=" * 60)
        print("✅ 抓取完成！")
        print(f"📁 数据已保存至: {output_file}")
        print(f"📊 总计获取动态数: {len(all_data)}")

        # 按类型统计
        type_stats = {}
        for item in all_data:
            t = item["type"]
            type_stats[t] = type_stats.get(t, 0) + 1

        print("\n📈 内容类型统计:")
        for t, count in sorted(type_stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {t}: {count} 条")

        # 显示前几条数据预览
        print("\n📋 最新内容预览:")
        for i, item in enumerate(all_data[:5], 1):
            print(f"  {i}. [{item['created_time_str']}] {item['type']} - {item['title'][:50]}...")

    else:
        print("\n⚠️ 未抓取到有效内容，请检查：")
        print("  1. cURL 命令是否正确")
        print("  2. 是否需要登录（cookie 是否有效）")
        print("  3. 用户是否有公开动态")


if __name__ == "__main__":
    fetch_user_activities()

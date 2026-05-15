import json
import re
import sys
import time
from datetime import datetime
from typing import Any

from curl_cffi.requests.exceptions import Timeout as RequestsTimeout

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session


def load_headers(quick_mode: bool = False) -> dict[str, str] | None:
    """从文件加载缓存的 headers，或通过粘贴 cURL 获取"""
    if quick_mode:
        headers = cache_manager.load_headers()
        if headers:
            print("[Success] Loaded cached headers from .cache/headers.json")
            return headers

    print("\n--- Please paste cURL from any Zhihu Articles API request ---")
    print("Tip: Press Ctrl+D (Unix) or Ctrl+Z+Enter (Win) to finish\n")

    curl_input = sys.stdin.read()
    if not curl_input.strip():
        return None

    base_url, headers, offset_match, after_id_match = extract_config_from_curl(curl_input)
    if not headers:
        return None

    # 移除可能导致问题的头部
    headers.pop("Accept-Encoding", None)
    cache_manager.save_headers(headers)
    print("[Success] Headers configured and cached.")
    return headers


def extract_config_from_curl(curl_text: str) -> tuple[str, dict[str, str], re.Match[str] | None, re.Match[str] | None]:
    """从 cURL 命令中提取 URL 和 Headers"""
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


def fmt_time(ts: int | float | None) -> str:
    if ts:
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)
    return "未知时间"


def parse_article(item: dict[str, Any]) -> dict[str, Any]:
    """解析单篇文章数据（直接对应 API 返回的 data 中的每一项）"""
    article_id = item.get("id", "")
    title = item.get("title", "无标题")
    excerpt = item.get("excerpt", "")
    content_preview = excerpt or (item.get("content", "")[:200] if item.get("content") else "")

    # 统计数据
    voteup_count = item.get("voteup_count", 0)
    comment_count = item.get("comment_count", 0)

    # 时间戳
    created = item.get("created", 0)
    updated = item.get("updated", 0)

    # 作者信息
    author = item.get("author", {})
    author_name = author.get("name", "未知用户")

    # 文章链接
    url = item.get("url", "")
    if not url and article_id:
        url = f"https://zhuanlan.zhihu.com/p/{article_id}"

    return {
        "id": article_id,
        "title": title,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {"voteup_count": voteup_count, "comment_count": comment_count},
        "author": author_name,
        "headline": author.get("headline", ""),
        "comment_permission": item.get("comment_permission", ""),
    }


def fetch_user_articles() -> None:
    print("=" * 60)
    print("📝 知乎用户文章列表抓取工具")
    print("=" * 60)

    headers = load_headers(quick_mode=True)
    if not headers:
        return

    print("\n请从浏览器开发者工具复制文章列表 API 的 cURL 命令")
    print("步骤：")
    print("  1. 打开用户主页，点击「文章」标签")
    print("  2. F12 打开开发者工具 -> Network 标签")
    print("  3. 刷新页面，找到请求 URL 包含 '/articles?include=...' 的请求")
    print("  4. 右键 -> Copy -> Copy as cURL")
    print("  5. 粘贴到这里 (按 Ctrl+D 或 Ctrl+Z 结束输入)\n")

    curl_input = sys.stdin.read()
    if not curl_input:
        print("❌ 未检测到输入内容")
        return

    base_url, headers, offset_match, after_id_match = extract_config_from_curl(curl_input)

    if not base_url:
        print("❌ 无法解析URL，请检查cURL命令格式")
        return

    # 移除可能干扰的头部
    headers.pop("Content-Length", None)

    print("\n✅ 成功解析配置")
    print(f"  基础URL: {base_url[:80]}...")
    print(f"  Headers: {len(headers)} 个")

    all_articles = []

    # 分页参数（文章 API 使用 offset 分页）
    limit = 20
    offset = int(offset_match.group(1)) if offset_match else 0
    # 注意：文章 API 不使用 after_id，但为了兼容保留变量
    use_after_id = bool(after_id_match) if after_id_match else False

    print("\n🚀 开始抓取文章列表...")
    print(f"  分页方式: {'after_id' if use_after_id else 'offset'}")
    print(f"  初始 offset: {offset}")

    page = 1
    is_end = False
    max_retries = 3

    while not is_end:
        # 构造请求 URL
        if use_after_id and after_id_match:
            # 理论上文章 API 不用 after_id，但保留逻辑
            request_url = f"{base_url}?limit={limit}&after_id={after_id_match.group(1)}"
        else:
            request_url = f"{base_url}?limit={limit}&offset={offset}"

        retry_count = 0
        success = False

        while retry_count < max_retries and not success:
            try:
                print(f"\n  📄 第 {page} 页请求中 (offset={offset})...", end=" ")
                resp = session.get(request_url, headers=headers, timeout=15)

                if resp.status_code == 200:
                    res_json = resp.json()
                    items = res_json.get("data", [])
                    paging = res_json.get("paging", {})

                    if not items:
                        print("⚠️ 未获取到数据")
                        is_end = True
                        break

                    # 解析每篇文章
                    page_count = 0

                    for item in items:
                        parsed = parse_article(item)
                        all_articles.append(parsed)
                        page_count += 1

                    print(f"✅ 获取 {page_count} 条")

                    # 更新分页参数
                    is_end = paging.get("is_end", True)
                    if not is_end:
                        next_url = paging.get("next", "")
                        # 从 next URL 中提取新的 offset
                        offset_match_next = re.search(r"[?&]offset=(\d+)", next_url)
                        if offset_match_next:
                            offset = int(offset_match_next.group(1))
                        else:
                            # 如果无法解析，按 limit 递增
                            offset += limit
                    else:
                        break

                    success = True

                elif resp.status_code == 403:
                    print("❌ 403 禁止访问，可能需要登录或更新 Cookies")
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

            except RequestsTimeout:
                print("⏰ 请求超时")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"     重试 ({retry_count}/{max_retries})...")
                    time.sleep(3)
                else:
                    is_end = True
                    break

            except Exception as e:
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
        time.sleep(1.5)  # 请求间隔，避免触发风控

    # --- 保存数据 ---
    if all_articles:
        output_file = "zhihu_articles.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_articles, f, indent=4, ensure_ascii=False)

        # 生成统计报告
        print("\n" + "=" * 60)
        print("✅ 抓取完成！")
        print(f"📁 数据已保存至: {output_file}")
        print(f"📊 总计获取文章数: {len(all_articles)}")

        # 显示前几条预览
        print("\n📋 最新文章预览:")
        for i, art in enumerate(all_articles[:5], 1):
            print(
                f"  {i}. [{art['created_time']}] {art['title'][:50]}... (👍{art['stats']['voteup_count']} 💬{art['stats']['comment_count']})"
            )

    else:
        print("\n⚠️ 未抓取到有效内容，请检查：")
        print("  1. cURL 命令是否正确（必须是文章列表 API）")
        print("  2. 是否需要登录（Cookie 是否有效）")
        print("  3. 用户是否公开发布了文章")


if __name__ == "__main__":
    fetch_user_articles()

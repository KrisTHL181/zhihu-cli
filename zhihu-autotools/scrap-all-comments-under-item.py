import sys
import json
import re
import time
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

from bs4 import BeautifulSoup
from curl_cffi import requests
from html2markdown import PageToMarkdown


CACHE_FILE = "headers.json"
ZHIHU_ARTICLE_PATTERN = r"https?://zhuanlan\.zhihu\.com/p/(\d+)"
ZHIHU_QUESTION_PATTERN = r'https?://(?:www\.)?zhihu\.com/question/(\d+)'
ZHIHU_QUESTION_WITH_ANSWER_PATTERN = r'https?://(?:www\.)?zhihu\.com/question/(\d+)(?:/answer/(\d+))?'
ZHIHU_PIN_PATTERN = r'https?://(?:www\.)?zhihu\.com/pin/([^/?#]+)'
CURL_HEADER_PATTERN = r"-H\s+['\"]([^'\"]+)['\"]"
CURL_URL_PATTERN = r"curl\s+'([^']+)'"

md_converter = PageToMarkdown()


def save_headers(headers: Dict[str, str]) -> None:
    """保存 Header 到本地文件"""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(headers, f, indent=2, ensure_ascii=False)
    print(f"✅ Headers 已缓存至 {CACHE_FILE}")


def load_headers() -> Optional[Dict[str, str]]:
    """从本地文件读取 Header"""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def extract_config_from_curl(curl_text: str) -> tuple[str, Dict[str, str]]:
    """从 cURL 提取 URL 和 Headers"""
    url_match = re.search(CURL_URL_PATTERN, curl_text)
    url = url_match.group(1) if url_match else ""

    headers = {}
    header_matches = re.findall(CURL_HEADER_PATTERN, curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    # 移除干扰字段
    headers.pop('Accept-Encoding', None)
    headers.pop('Content-Length', None)
    return url, headers


def parse_item(item_type: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    """从 initialData 中提取文章信息"""
    item = entities.get(item_type, {})
    if not item:
        raise ValueError(f"No {item} data found in entities")

    q_id = next(iter(item))
    q_data = item[q_id]
    content = q_data.get("content", q_data.get("detail", ""))
    if isinstance(content, list): # 知乎想法的API特殊
        content = content[0].get("content")

    return {
        "id": q_id,
        "title": q_data.get("title", ""),
        "url": q_data.get("url", ""),
        "author": q_data.get("name"),
        "created_time": q_data.get("created", q_data.get("createdTime", 0)),
        "updated_time": q_data.get("updated", q_data.get("updatedTime", 0)),
        "content": md_converter.convert(content),
    }


def fetch_page_data(session: requests.Session, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """获取知乎文章页面并解析 initialData"""
    resp = session.get(url, headers=headers, impersonate="chrome110", timeout=15)
    if resp.status_code == 403:
        raise PermissionError("403 Forbidden: Headers 可能已过期，请重新提供 cURL")
    if resp.status_code != 200:
        raise RuntimeError(f"页面请求失败: {resp.status_code}")

    soup = BeautifulSoup(resp.text, 'html.parser')
    script_tag = soup.find('script', id='js-initialData')
    if not script_tag or not script_tag.string:
        raise ValueError("无法找到 js-initialData 脚本标签")

    initial_data = json.loads(script_tag.string)
    return initial_data['initialState']['entities']


def print_item_info(item: Dict[str, Any]) -> None:
    """打印内容基本信息"""
    created = datetime.fromtimestamp(item['created_time']).strftime('%Y-%m-%d %H:%M:%S')
    print(f"📌 标题: {item['title']}")
    print(f"🔗 链接: {item['url']}")
    print(f"⏰ 创建时间: {created}")
    print(f"📝 内容:\n{item['content']}\n")
    print("=" * 50)


def convert_content(content: str) -> str:
    """统一内容转换逻辑"""
    converted = md_converter.convert(content)
    return converted if converted.strip() else content


def fetch_child_comments(
    session: requests.Session,
    parent_comment: Dict[str, Any],
    headers: Dict[str, str]
) -> None:
    """递归获取子评论（含翻页）"""
    child_offset = parent_comment.get("child_comment_next_offset")
    if not child_offset:
        return

    child_api_url = f"https://www.zhihu.com/api/v4/comment_v5/comment/{parent_comment['id']}/child_comment"
    child_next_url = f"{child_api_url}?limit=20&offset={child_offset}"

    while child_next_url:
        resp = session.get(child_next_url, headers=headers, impersonate="chrome110", timeout=15)
        if resp.status_code != 200:
            break
        child_json = resp.json()
        for child in child_json.get("data", []):
            c_author = child.get("author", {}).get("name", "匿名用户")
            c_like = child.get("like_count", 0)
            c_dislike = child.get("dislike_count", 0)
            c_content = convert_content(child.get("content", ""))
            print(f"    - 作者: {c_author} | 赞: {c_like} | 踩: {c_dislike}")
            print(f"      {c_content}\n")
        paging = child_json.get("paging", {})
        child_next_url = paging.get("next") if not paging.get("is_end") else None
        if child_next_url:
            time.sleep(0.5)


def fetch_and_print_comments(
    session: requests.Session,
    item_type: str,
    id: str,
    headers: Dict[str, str]
) -> None:
    """获取并打印所有根评论及子评论"""
    next_url = f"https://www.zhihu.com/api/v4/comment_v5/{item_type}/{id}/root_comment?order_by=score&limit=20&offset="
    comment_id = 1

    while next_url:
        resp = session.get(next_url, headers=headers, impersonate="chrome110", timeout=15)
        if resp.status_code != 200:
            print(f"❌ API 请求失败: {resp.status_code}")
            break

        res_json = resp.json()
        comments: List[Dict] = res_json.get("data", [])
        for comment in comments:
            author = comment.get("author", {}).get("name", "匿名用户")
            like = comment.get("like_count", 0)
            dislike = comment.get("dislike_count", 0)
            content = convert_content(comment.get("content", ""))

            print(f"\n[{comment_id}] 作者: {author} | 赞: {like} | 踩: {dislike}")
            print("-" * 20)
            print(content)

            # 子评论（直接展示 + 翻页）
            if comment.get("child_comments"):
                print("\n  ↳ 子评论:")
                for child in comment["child_comments"]:
                    c_author = child.get("author", {}).get("name", "匿名用户")
                    c_like = child.get("like_count", 0)
                    c_dislike = child.get("dislike_count", 0)
                    c_content = convert_content(child.get("content", ""))
                    print(f"    - 作者: {c_author} | 赞: {c_like} | 踩: {c_dislike}")
                    print(f"      {c_content}\n")
                fetch_child_comments(session, comment, headers)

            print("-" * 20)
            comment_id += 1

        paging = res_json.get("paging", {})
        if paging.get("is_end"):
            break
        next_url = paging.get("next")
        if next_url:
            next_url = next_url.replace("http://", "https://")
        time.sleep(1)

def get_type(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    通过正则捕获判断知乎链接类型并返回对应的ID
    
    Args:
        url: 知乎链接字符串
        
    Returns:
        tuple: (type, id) 
            - type: 'article', 'question', 'pin' 或 None
            - id: 匹配到的ID，如果没有匹配返回 None
    """
    match = re.search(ZHIHU_ARTICLE_PATTERN, url)
    if match:
        return ('articles', match.group(1))
    
    # 检查问题类型
    match = re.search(ZHIHU_QUESTION_WITH_ANSWER_PATTERN, url)
    if match:
        return ('answers', f"{match.group(1)}/{match.group(2)}")

    match = re.search(ZHIHU_QUESTION_PATTERN, url)
    if match:
        return ('questions', match.group(1))
    
    # 检查想法类型
    match = re.search(ZHIHU_PIN_PATTERN, url)
    if match:
        return ('pins', match.group(1))
    
    # 无法识别
    return (None, None)


def main() -> None:
    print("--- 💡 请粘贴 [知乎文章链接] 或 [cURL 命令] ---", file=sys.stderr)
    print("(直接输入链接将使用上次缓存的 Headers)\n", file=sys.stderr)

    user_input = sys.stdin.read().strip()
    if not user_input:
        return

    current_url: str = ""
    headers: Optional[Dict[str, str]] = None

    # 输入识别
    if user_input.startswith("curl"):
        current_url, headers = extract_config_from_curl(user_input)
        if headers:
            save_headers(headers)
        url_type = get_type(current_url)[0]
    else:
        url_type = get_type(user_input)[0]
        headers = load_headers()
        if url_type is None:
            print("❌ 无法识别的链接类型，请确保链接是知乎的文章、问题或想法", file=sys.stderr)
            return
        current_url = user_input

    assert headers is not None, "Headers 不应为空"

    print(f"🚀 开始请求: {current_url}\n\n{'='*60}\n\n", file=sys.stderr)

    try:
        with requests.Session() as session:
            entities = fetch_page_data(session, current_url, headers)
            article = parse_item(url_type, entities)
            print_item_info(article)
            fetch_and_print_comments(session, url_type, article["id"], headers)

    except Exception as e:
        print(f"💥 出错: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

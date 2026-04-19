import shutil
import sys
import json
import re
import time
import os
from datetime import datetime
import argparse
from bs4 import BeautifulSoup
from curl_cffi import requests
from .utils.html2markdown import PageToMarkdown
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import track

from .handlers.cache_manager import cache_manager


def save_headers(headers):
    """保存 Header 到缓存"""
    cache_manager.save_headers(headers)
    print("✅ Headers 已缓存至 .cache/headers.json")

def load_headers():
    """从缓存读取 Header"""
    return cache_manager.load_headers()

def extract_config(curl_text):
    """从 cURL 提取 Header 和基础 URL"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    url = url_match.group(1) if url_match else ""
    
    headers = {}
    # 匹配 -H 'Key: Value' 或 -H "Key: Value"
    header_matches = re.findall(r"-H\s+['\"]([^'\"]+)['\"]", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    
    # 移除可能引起压缩错误或长度不匹配的 header
    headers.pop('Accept-Encoding', None)
    headers.pop('Content-Length', None)
    return url, headers

def parse_question(entities: dict) -> dict:
    question = entities["questions"]
    q_id = next(iter(question))
    q_data = question[q_id]

    return {
        "id": q_id,
        "title": q_data.get("title", ""),
        "url": q_data.get("url", ""),
        "created_time": q_data.get("created", 0),
        "updated_time": q_data.get("updatedTime", 0),
        "answer_count": q_data.get("answerCount", 0),
        "detail": PageToMarkdown().convert(q_data.get("detail", "")),
    }

def get_request_data(url=None):
    if url:
        user_input = url
    else:
        print("--- 💡 请粘贴 [知乎问题链接] 或 [cURL 命令] ---", file=sys.stderr)
        print("(直接输入链接将使用上次缓存的 Headers)\n", file=sys.stderr)
        user_input = sys.stdin.read().strip()
    
    if not user_input:
        return None, None

    current_url = ""
    headers = {}

    # 1. 识别输入类型
    if user_input.startswith("curl"):
        current_url, headers = extract_config(user_input)
        if headers:
            save_headers(headers)
    elif "zhihu.com/question/" in user_input:
        # 提取 URL（防止粘贴带参数的长链接）
        match = re.search(r'(https://www\.zhihu\.com/question/\d+)', user_input)
        current_url = match.group(1) if match else user_input
        headers = load_headers()
        if not headers:
            print("❌ 本地无缓存，请先粘贴一次 cURL 以初始化 Headers", file=sys.stderr)
            return None, None
        print("使用缓存的 Headers 进行请求...", file=sys.stderr)
    else:
        print("❌ 无法识别的输入，请输入正确的 cURL 或知乎链接", file=sys.stderr)
        return None, None

    return current_url, headers

def get_question(session, current_url, headers):
    resp = session.get(current_url, headers=headers, impersonate="chrome110", timeout=15)
    
    if resp.status_code == 403:
        print("❌ 403 Forbidden: 缓存的 Headers 可能已过期，请重新粘贴 cURL")
        return

    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # 提取初始数据
    script_tag = soup.find('script', id='js-initialData')
    if not script_tag:
        print("❌ 无法解析页面数据，请检查链接或 Headers 是否有效", file=sys.stderr)
        return
        
    initial_data = json.loads(script_tag.string)
    page_data = initial_data['initialState']['entities']
    return parse_question(page_data)

def scrape_answers(session, question_data, headers):
    # 1. API 翻页逻辑
    next_url = f"https://www.zhihu.com/api/v4/questions/{question_data['id']}/answers?include=data%5B%2A%5D.content%2Cfavlists_count%2Cvoteup_count%2Ccomment_count%2Cauthor.name&limit=5&offset=0&sort_by=default&platform=desktop"
    
    is_end = False
    answer_num = 1

    try:
        while not is_end and next_url:
            resp = session.get(next_url, headers=headers, impersonate="chrome110", timeout=15)
            if resp.status_code != 200:
                print(f"❌ API 请求失败: {resp.status_code}")
                break

            res_json = resp.json()
            answers = res_json.get("data", [])

            for ans in answers:
                author = ans.get("author", {}).get("name", "匿名用户")
                vote = ans.get("voteup_count", 0)
                content = PageToMarkdown().convert(ans.get("content", ""))

                yield answer_num, author, vote, content
                answer_num += 1

            paging = res_json.get("paging", {})
            is_end = paging.get("is_end", True)
            next_url = paging.get("next")
            if next_url:
                next_url = next_url.replace("http://", "https://")
            
            if not is_end:
                time.sleep(1)

    except Exception as e:
        print(f"💥 出错: {e}")

def get_best_pager() -> str:
    if shutil.which("less"):
        return "less -R"

    return "more"

def main(url: str, reading_mode: bool = False, no_cache: bool = False):
    current_url, headers = get_request_data(url)
    if not current_url or not headers:
        return
    
    session = requests.Session()
    question_data = get_question(session, current_url, headers)
    if not question_data:
        return

    if not reading_mode:
        print(f"📌 知乎问题: {question_data['title']}")
        print(f"🔗 链接: {question_data['url']}")
        print(f"⏰ 创建时间: {datetime.fromtimestamp(question_data['created_time']).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📝 详情:\n{question_data['detail']}\n")

        for answer_num, author, vote, content in scrape_answers(session, question_data, headers):
            print(f"\n[{answer_num}] 作者: {author} | 赞同: {vote}")
            print("-" * 20)
            print(content)
            print("-" * 20)
        return

    sys.stdin = open('/dev/tty') # 刷新已经被消费掉的stdin
    console = Console()

    answers = cache_manager.get_cached_question(question_data["id"]) if not no_cache else None

    if answers is None:
        answers = []
        for item in track(scrape_answers(session, question_data, headers),
                        total=question_data["answer_count"],
                        description="抓取答案中..."):
            answers.append(item)

        if not no_cache:
            cache_manager.save_question(question_data["id"], answers)
            print("问题回答数据已缓存！", file=sys.stderr)

    os.environ["PAGER"] = get_best_pager()

    with console.pager(styles=True, links=True):
        console.print(f"📌 知乎问题: {question_data['title']}")
        console.print(f"🔗 链接: {question_data['url']}")
        console.print(f"⏰ 创建时间: {datetime.fromtimestamp(question_data['created_time']).strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"📝 详情:\n{question_data['detail']}\n")

        for num, author, vote, content in answers:
            ans_md = f"## [{num}] 作者: {author} | 赞同: {vote}\n"
            ans_md += f"{content}\n\n---"

            console.print(Markdown(ans_md))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape all answers under a Zhihu question")
    parser.add_argument("--url", help="输入 cURL 命令或知乎问题链接（可通过管道传入）")
    parser.add_argument("--reading-mode", action='store_true', help='使用基于 Rich 的阅读模式')
    parser.add_argument("--no-cache", action="store_true", help="强制重新抓取而不是缓存回答")
    args = parser.parse_args()

    main(args.url, args.reading_mode, args.no_cache)

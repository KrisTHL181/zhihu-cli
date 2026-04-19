import sys
import json
import time
import re
import os
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from curl_cffi import requests
from .handlers.cache_manager import cache_manager

def load_headers(quick_mode: bool = False):
    """从文件加载缓存的 headers，或通过粘贴 cURL 获取"""
    if quick_mode:
        headers = cache_manager.load_headers()
        if headers:
            print("[Success] Loaded cached headers from .cache/headers.json")
            return headers

    print("\n--- Please paste cURL from any Zhihu Answers API request ---")
    print("Tip: Press Ctrl+D (Unix) or Ctrl+Z+Enter (Win) to finish\n")
    
    curl_input = sys.stdin.read()
    if not curl_input.strip(): return None
    
    full_url, headers = extract_config_from_curl(curl_input)
    if not headers: return None
    
    # 移除可能导致问题的头部
    headers.pop('Accept-Encoding', None)
    cache_manager.save_headers(headers)
    print("[Success] Headers configured and cached.")
    return headers

def extract_config_from_curl(curl_text):
    """从 cURL 命令中提取完整 URL 和 Headers"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    full_url = url_match.group(1) if url_match else ""
    
    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    
    # 移除 Accept-Encoding，让 curl_cffi 自己处理
    headers.pop('Accept-Encoding', None)
    
    return full_url, headers

def fmt_time(ts):
    if ts:
        try:
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except:
            return str(ts)
    return '未知时间'

def parse_answer(item):
    """解析单条回答数据（直接对应 API 返回的 data 中的每一项）"""
    answer_id = item.get('id', '')
    # 回答本身没有标题，但有关联的问题
    question = item.get('question', {})
    question_title = question.get('title', '无标题')
    question_id = question.get('id', '')
    
    excerpt = item.get('excerpt', '')
    content_preview = excerpt or (item.get('content', '')[:200] if item.get('content') else '')
    
    # 统计数据
    voteup_count = item.get('voteup_count', 0)
    comment_count = item.get('comment_count', 0)
    
    # 时间戳
    created = item.get('created_time', 0)
    updated = item.get('updated_time', 0)
    
    # 作者信息
    author = item.get('author', {})
    author_name = author.get('name', '未知用户')

    # 回答链接
    url = item.get('url', '')
    if not url and answer_id:
        url = f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
    
    return {
        "id": answer_id,
        "question_title": question_title,
        "question_id": question_id,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {
            "voteup_count": voteup_count,
            "comment_count": comment_count
        },
        "author": author_name,
        "headline": author.get('headline', ''),
        "comment_permission": item.get('comment_permission', '')
    }

def fetch_user_answers():
    print("=" * 60)
    print("📝 知乎用户回答列表抓取工具")
    print("=" * 60)
    
    headers = load_headers(quick_mode=True)
    if not headers:
        return

    print("\n请从浏览器开发者工具复制回答列表 API 的 cURL 命令")
    print("步骤：")
    print("  1. 打开用户主页，点击「回答」标签")
    print("  2. F12 打开开发者工具 -> Network 标签")
    print("  3. 刷新页面，找到请求 URL 包含 '/answers?include=...' 的请求")
    print("  4. 右键 -> Copy -> Copy as cURL")
    print("  5. 粘贴到这里 (按 Ctrl+D 或 Ctrl+Z 结束输入)\n")
    
    curl_input = sys.stdin.read()
    if not curl_input:
        print("❌ 未检测到输入内容")
        return
    
    full_url, headers = extract_config_from_curl(curl_input)
    
    if not full_url:
        print("❌ 无法解析URL，请检查cURL命令格式")
        return
    
    # 解析原始 URL 的查询参数，保留 include 等重要参数
    parsed = urlparse(full_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    # 将值列表转换为字符串（通常每个参数只有一个值）
    for k, v in query_params.items():
        if isinstance(v, list) and len(v) == 1:
            query_params[k] = v[0]
    
    print(f"\n✅ 成功解析配置")
    print(f"  基础URL: {base_url}")
    print(f"  Headers: {len(headers)} 个")
    print(f"  查询参数: {list(query_params.keys())}")
    
    all_answers = []
    
    # 分页参数（回答 API 使用 offset 分页）
    limit = int(query_params.get('limit', 20))
    offset = int(query_params.get('offset', 0))
    
    print(f"\n🚀 开始抓取回答列表...")
    print(f"  分页方式: offset")
    print(f"  初始 offset: {offset}, limit: {limit}")
    
    page = 1
    is_end = False
    max_retries = 3
    
    while not is_end:
        # 构造请求 URL：更新 offset 参数
        query_params['offset'] = offset
        new_query = urlencode(query_params, doseq=True)
        request_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', new_query, ''))
        
        retry_count = 0
        success = False
        
        while retry_count < max_retries and not success:
            try:
                print(f"\n  📄 第 {page} 页请求中 (offset={offset})...", end=' ')
                resp = requests.get(request_url, headers=headers, impersonate="chrome110", timeout=15)

                if resp.status_code == 200:
                    res_json = resp.json()
                    items = res_json.get('data', [])
                    paging = res_json.get('paging', {})

                    if not items:
                        print("⚠️ 未获取到数据")
                        is_end = True
                        break
                    
                    # 解析每条回答
                    page_count = 0
                    for item in items:
                        parsed_ans = parse_answer(item)
                        all_answers.append(parsed_ans)
                        page_count += 1
                    
                    print(f"✅ 获取 {page_count} 条")
                    
                    # 更新分页参数
                    is_end = paging.get('is_end', True)
                    if not is_end:
                        next_url = paging.get('next', '')
                        # 从 next URL 中提取新的 offset
                        next_parsed = urlparse(next_url)
                        next_params = parse_qs(next_parsed.query)
                        if 'offset' in next_params:
                            offset = int(next_params['offset'][0])
                        else:
                            # 如果无法解析，按 limit 递增
                            offset += limit
                    else:
                        break
                    
                    success = True
                    
                elif resp.status_code == 403:
                    print(f"❌ 403 禁止访问，可能需要登录或更新 Cookies")
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
                print(f"⏰ 请求超时")
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
    if all_answers:
        output_file = "zhihu_answers.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_answers, f, indent=4, ensure_ascii=False)
        
        # 生成统计报告
        print("\n" + "=" * 60)
        print(f"✅ 抓取完成！")
        print(f"📁 数据已保存至: {output_file}")
        print(f"📊 总计获取回答数: {len(all_answers)}")

        # 显示前几条预览
        print("\n📋 最新回答预览:")
        for i, ans in enumerate(all_answers[:5], 1):
            print(f"  {i}. [{ans['created_time']}] {ans['question_title'][:50]}... (👍{ans['stats']['voteup_count']} 💬{ans['stats']['comment_count']})")
        
    else:
        print("\n⚠️ 未抓取到有效内容，请检查：")
        print("  1. cURL 命令是否正确（必须是回答列表 API）")
        print("  2. 是否需要登录（Cookie 是否有效）")
        print("  3. 用户是否公开发布了回答")

if __name__ == "__main__":
    fetch_user_answers()
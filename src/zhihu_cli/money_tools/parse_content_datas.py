import sys
import json
import re
import os
import time
from datetime import datetime
from curl_cffi import requests

def convert_percent(data) -> float:
    if data is None: return 0.0
    # 兼容处理字符串百分比或直接数值
    return round(float(data.rstrip("%")) / 100, 2)

def get_date(d):
    # 1. 优先取 p_date
    if d.get("p_date"):
        return d["p_date"]
    
    # 2. 依次检查不同的嵌套结构
    # 利用 dict.get('', {}) 确保即使找不到 key 也能返回空字典，避免报错
    if d.get('answer'):
        ts = d['answer'].get('created_time')
    elif d.get('pin'):
        ts = d['pin'].get('create_time')
    elif d.get('article'):
        ts = d['article'].get('created_time')
    else:
        ts = None

    if ts:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    return None

def extract_config(curl_text):
    """从 cURL 中提取 Header 和 Base URL"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    base_url = url_match.group(1).split('?')[0] if url_match else ""
    
    headers = {}
    for h in re.findall(r"-H\s+'([^']+)'", curl_text):
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    headers.pop('Accept-Encoding', None)
    return base_url, headers

def run_batch_daily_analysis():
    # 1. 确保目录存在
    os.makedirs("./content_metrics", exist_ok=True)

    # 2. 读取 ID 列表
    try:
        with open("all_assets_list.json", "r", encoding="utf-8") as f:
            answer_ids = json.load(f)
        print(f"📋 已加载 {len(answer_ids)} 个待分析资产（IDs）")
    except FileNotFoundError:
        print("❌ 错误：找不到 all_assets_list.json，请先运行资产盘点脚本。")
        return

    # 3. 获取 cURL 配置
    print("\n--- 请粘贴任意一个【daily 或 aggr 接口】的 cURL 命令 (用于同步 Header 签名) ---")
    curl_input = sys.stdin.read()
    if not curl_input: return
    base_url, headers = extract_config(curl_input)

    # 4. 循环收割数据
    success_count = 0
    for i, token in enumerate(answer_ids):
        print(f"\n[任务 {i+1}/{len(answer_ids)}] 正在处理 ID: {token} ...")
        
        params = {
            'type': token['type'],
            'token': token['id'],
            'start': '2026-01-06', # 建议对齐你的收益数据起点
            'end': datetime.now().strftime("%Y-%m-%d")
        }

        try:
            resp = requests.get(base_url, headers=headers, params=params, impersonate="chrome110", timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                clean_data = []
                if base_url.endswith("aggr"):
                    data = [data] # aggr has only 1 day
                for d in data:
                    clean_data.append({
                        "type": token['type'],
                        "date": get_date(d),
                        "pv": d.get('pv', 0),
                        "upvote": d.get('upvote', 0),
                        "collect": d.get('collect', 0),
                        "comment": d.get('comment', 0),
                        "share": d.get('share', 0),
                        "finish_read_percent": convert_percent(d['advanced'].get("finish_read_percent", "0.0%")),
                        "positive_interact_percent": convert_percent(d['advanced'].get("positive_interact_percent", "0.0%")),
                        "follower_translate": d['advanced'].get("follower_translate", "0")
                    })
                
                output_file = f"./content_metrics/metrics_full_{token['type']}_{token['id']}.json"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(clean_data, f, indent=4)
                
                print(f"  ✅ 已保存: {output_file}")
                success_count += 1
            else:
                print(f"  ❌ 抓取失败 (Code: {resp.status_code})。可能是 token 对应内容已删除或签名过期。")
            
        except Exception as e:
            print(f"  ⚠️ 异常: {e}")

        # 5. 频率控制（极其重要，避免被知乎反爬封禁）
        time.sleep(1.2)

    print("\n" + "="*40)
    print(f"🏁 批量收割结束！")
    print(f"📁 成功抓取: {success_count} / {len(answer_ids)}")
    print(f"📂 所有数据存储在: ./content_metrics/")
    print("="*40)

if __name__ == "__main__":
    run_batch_daily_analysis()

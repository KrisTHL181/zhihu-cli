import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from io import StringIO

from curl_cffi import requests

# 常量配置
DB_FILE = "zhihu_income_report.json"
DEFAULT_START_DATE = "2026-01-06"


def extract_headers_and_cookies(curl_text):
    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    base_url = url_match.group(1).split("?")[0] if url_match else None
    return base_url, headers


def load_existing_data():
    """读取现有数据，返回 (旧数据列表, 下一次抓取的起始日期)"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                data = json.load(f)
                details = data.get("details", [])
                if details:
                    # 假设数据是按日期排序的，找到最新的一天
                    # 如果之前是 reverse=True (降序)，则第一条是最新
                    last_date_str = max(item["date"] for item in details)
                    last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
                    next_start = last_dt + timedelta(days=1)
                    return details, next_start
        except Exception as e:
            print(f"[!] 读取旧文件失败: {e}，将重新抓取")

    return [], datetime.strptime(DEFAULT_START_DATE, "%Y-%m-%d")


def run_task():
    # 1. 加载历史数据
    existing_details, start_dt = load_existing_data()
    end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if start_dt >= end_dt:
        print(f"✅ 数据已是最新 (最后记录: {start_dt - timedelta(days=1)})，无需更新。")
        return

    # 2. 获取接口信息
    print(f"--- 增量模式：将从 {start_dt.strftime('%Y-%m-%d')} 开始抓取 ---")
    print("--- 请粘贴完整的 cURL 命令 (回车后 Ctrl+D/Ctrl+Z 结束) ---")
    curl_input = sys.stdin.read()
    if not curl_input:
        return

    base_url, headers = extract_headers_and_cookies(curl_input)
    if not base_url:
        print("❌ 未能识别 cURL 中的 URL，请检查格式。")
        return
    headers.pop("Accept-Encoding", None)

    new_income_data = []

    # 3. 抓取逻辑
    current_dt = start_dt
    while current_dt < end_dt:
        batch_end = min(current_dt + timedelta(days=30), end_dt - timedelta(days=1))
        # 如果 batch_end 小于 current_dt，说明已经追平了
        if batch_end < current_dt:
            batch_end = current_dt

        params = {"start_date": current_dt.strftime("%Y-%m-%d"), "end_date": batch_end.strftime("%Y-%m-%d")}

        print(f"\n[任务] 抓取中: {params['start_date']} -> {params['end_date']}")

        try:
            resp = requests.get(base_url, headers=headers, params=params, impersonate="chrome110", timeout=15)

            if resp.status_code == 200 and resp.text.strip():
                f = StringIO(resp.text.strip())
                reader = csv.reader(f)

                count = 0
                for row in reader:
                    if not row or "日期" in row[0]:
                        continue

                    salt_grains = int(row[1]) if len(row) > 1 else 0
                    yuan = round(salt_grains / 100.0, 2)

                    new_income_data.append({"date": row[0], "income_salt": salt_grains, "income_yuan": yuan})
                    count += 1
                print(f"  [成功] 新提取 {count} 天数据")
            else:
                print(f"  [跳过] 状态码: {resp.status_code}，可能该时段无数据")

        except Exception as e:
            print(f"  [异常] {e}")

        if batch_end >= end_dt - timedelta(days=1):
            break
        current_dt = batch_end + timedelta(days=1)
        time.sleep(1.5)

    # 4. 合并与去重保存
    all_details = existing_details + new_income_data
    # 按照日期去重（以防万一）并排序
    unique_data = {item["date"]: item for item in all_details}
    sorted_details = sorted(unique_data.values(), key=lambda x: x["date"], reverse=True)

    total_yuan = sum(item["income_yuan"] for item in sorted_details)

    if new_income_data or existing_details:
        output = {
            "summary": {
                "total_days": len(sorted_details),
                "total_income_yuan": round(total_yuan, 2),
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "details": sorted_details,
        }

        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=4)

        print("\n" + "=" * 40)
        print("✅ 更新完成！")
        print(f"📈 新增天数: {len(new_income_data)} 天")
        print(f"📊 总计天数: {len(sorted_details)} 天")
        print(f"💰 总计收益: {round(total_yuan, 2)} 元")
        print(f"📁 文件已同步: {DB_FILE}")
        print("=" * 40)
    else:
        print("\n[!] 未能获取任何新数据。")


if __name__ == "__main__":
    run_task()

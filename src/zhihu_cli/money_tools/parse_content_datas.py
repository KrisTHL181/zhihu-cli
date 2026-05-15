import json
import os
import time
from datetime import datetime
from typing import Any, cast

from curl_cffi import requests
from user_agents import parse

from zhihu_cli.content.handlers.cache_manager import cache_manager

DAILY_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/member/daily"
AGGR_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/member/aggr"


def _get_browser(ua: str) -> requests.BrowserTypeLiteral:
    family = parse(ua).browser.family.lower()
    if family in requests.impersonate.REAL_TARGET_MAP:
        return cast(requests.BrowserTypeLiteral, family)
    return "chrome"


def convert_percent(data: str | None) -> float:
    if data is None:
        return 0.0
    return round(float(data.rstrip("%")) / 100, 2)


def get_date(d: dict[str, Any]) -> str | None:
    if d.get("p_date"):
        return d["p_date"]

    if d.get("answer"):
        ts = d["answer"].get("created_time")
    elif d.get("pin"):
        ts = d["pin"].get("create_time")
    elif d.get("article"):
        ts = d["article"].get("created_time")
    else:
        ts = None

    if ts:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return None


def run_batch_daily_analysis(use_aggr: bool = False) -> None:
    os.makedirs("./content_metrics", exist_ok=True)

    try:
        with open("all_assets_list.json", encoding="utf-8") as f:
            answer_ids = json.load(f)
        print(f"📋 已加载 {len(answer_ids)} 个待分析资产（IDs）")
    except FileNotFoundError:
        print("❌ 错误：找不到 all_assets_list.json，请先运行资产盘点脚本。")
        return

    headers = cache_manager.load_headers()
    if not headers:
        print("❌ 未找到缓存的鉴权凭证，请先运行: zhihu auth paste")
        return
    headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}

    ua = headers.get("User-Agent", "")
    browser = _get_browser(ua)
    base_url = AGGR_URL if use_aggr else DAILY_URL

    success_count = 0
    for i, token in enumerate(answer_ids):
        print(f"\n[任务 {i + 1}/{len(answer_ids)}] 正在处理 ID: {token} ...")

        params = {
            "type": token["type"],
            "token": token["id"],
            "start": "2026-01-06",
            "end": datetime.now().strftime("%Y-%m-%d"),
        }

        try:
            resp = requests.get(base_url, headers=headers, params=params, impersonate=browser, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                clean_data = []
                if use_aggr:
                    data = [data]
                for d in data:
                    clean_data.append(
                        {
                            "type": token["type"],
                            "date": get_date(d),
                            "pv": d.get("pv", 0),
                            "upvote": d.get("upvote", 0),
                            "collect": d.get("collect", 0),
                            "comment": d.get("comment", 0),
                            "share": d.get("share", 0),
                            "finish_read_percent": convert_percent(d["advanced"].get("finish_read_percent", "0.0%")),
                            "positive_interact_percent": convert_percent(
                                d["advanced"].get("positive_interact_percent", "0.0%")
                            ),
                            "follower_translate": d["advanced"].get("follower_translate", "0"),
                        }
                    )

                output_file = f"./content_metrics/metrics_full_{token['type']}_{token['id']}.json"
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(clean_data, f, indent=4)

                print(f"  ✅ 已保存: {output_file}")
                success_count += 1
            else:
                print(f"  ❌ 抓取失败 (Code: {resp.status_code})。可能是 token 对应内容已删除或签名过期。")

        except Exception as e:
            print(f"  ⚠️ 异常: {e}")

        time.sleep(1.2)

    print("\n" + "=" * 40)
    print("🏁 批量收割结束！")
    print(f"📁 成功抓取: {success_count} / {len(answer_ids)}")
    print("📂 所有数据存储在: ./content_metrics/")
    print("=" * 40)


if __name__ == "__main__":
    run_batch_daily_analysis()

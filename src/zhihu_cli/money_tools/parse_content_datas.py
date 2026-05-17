import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session

DAILY_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/member/daily"
AGGR_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/member/aggr"


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


def generate_assets_file(output_path: Path) -> list[dict[str, str]]:
    """Scrape all user creations and save to output_path. Returns the asset list."""
    headers = cache_manager.load_headers()
    if not headers:
        print("❌ 未找到鉴权凭证，请先运行: zhihu auth paste")
        return []

    base_url = "https://www.zhihu.com/api/v4/creators/creations/v2/all"
    all_assets: list[dict[str, str]] = []
    offset = 0
    limit = 20

    print("🔍 正在扫描你的知乎创作内容...")
    while True:
        params = {
            "start": 0,
            "end": 0,
            "limit": limit,
            "offset": offset,
            "need_co_creation": 1,
            "sort_type": "created",
        }
        try:
            resp = session.get(base_url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                print(f"  ⚠️ HTTP {resp.status_code}，已中断")
                break

            data = resp.json()
            for item in data.get("data", []):
                asset_type = item.get("type")
                asset_id = item.get("data", {}).get("id")
                if asset_id and asset_type in ("answer", "pin", "article"):
                    all_assets.append(
                        {
                            "id": asset_id,
                            "type": asset_type,
                            "title": item.get("data", {}).get("title", ""),
                        }
                    )

            paging = data.get("paging", {})
            totals = paging.get("totals", 0)
            offset += limit
            print(f"  {min(offset, totals)}/{totals} — 已收集 {len(all_assets)} 条")

            if paging.get("is_end", True) or offset >= totals:
                break
        except Exception as e:
            print(f"  ⚠️ 异常: {e}")
            break
        time.sleep(1.2)

    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_assets, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存 {len(all_assets)} 条资产 → {output_path}")
    return all_assets


def run_batch_daily_analysis(use_aggr: bool = False) -> None:
    data_dir = Path.home() / ".zhihu-cli" / "exports"
    metrics_dir = data_dir / "content_metrics"
    os.makedirs(metrics_dir, exist_ok=True)
    assets_file = data_dir / "all_assets_list.json"

    answer_ids = []
    if assets_file.exists():
        with open(assets_file, encoding="utf-8") as f:
            answer_ids = json.load(f)
        print(f"📋 已加载 {len(answer_ids)} 个待分析资产")
    else:
        print("❌ 找不到 all_assets_list.json")
        print("   这个文件是你知乎创作内容的资产清单，metrics 命令通过它知道要分析哪些内容。")
        print()
        try:
            choice = input("  ▶ 是否现在自动生成？[Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("", "y", "yes"):
            print()
            answer_ids = generate_assets_file(assets_file)
            if not answer_ids:
                return
        else:
            print()
            print("  请手动运行: zhihu scrape creations")
            print("  生成后再执行: zhihu tools income metrics")
            return

    headers = cache_manager.load_headers()
    if not headers:
        print("❌ 未找到缓存的鉴权凭证，请先运行: zhihu auth paste")
        return
    headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}

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
            resp = session.get(base_url, headers=headers, params=params, timeout=15)

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

                output_file = metrics_dir / f"metrics_full_{token['type']}_{token['id']}.json"
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

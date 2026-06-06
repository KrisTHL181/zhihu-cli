import json
import os
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.waterfall import stream_handler

DAILY_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/content/daily"
AGGR_URL: str = "https://www.zhihu.com/api/v4/creators/analysis/realtime/content/aggr"


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
    if not cache_manager.load_headers():
        print("No cached headers found. Run: zhihu auth paste")
        return []

    initial_url = (
        "https://www.zhihu.com/api/v4/creators/creations/v2/all"
        "?start=0&end=0&limit=20&offset=0&need_co_creation=1&sort_type=created"
    )

    def parse_creations(data: dict[str, Any]) -> Iterable[dict[str, str]]:
        for item in data.get("data", []):
            asset_type = item.get("type")
            asset_id = item.get("data", {}).get("id")
            if asset_id and asset_type in ("answer", "pin", "article"):
                yield {
                    "id": asset_id,
                    "type": asset_type,
                    "title": item.get("data", {}).get("title", ""),
                    "created_time": item.get("data", {}).get("created_time", 0),
                }

    print("Scanning your Zhihu creations...")
    all_assets: list[dict[str, str]] = []
    for i, item in enumerate(stream_handler(initial_url, parse_creations)):
        all_assets.append(item)
        if (i + 1) % 20 == 0:
            print(f"  Collected {len(all_assets)} items...")

    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_assets, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(all_assets)} assets → {output_path}")
    return all_assets


def _extract_daily_item(d: dict[str, Any] | None, content_type: str = "") -> dict | None:
    """Extract metrics from a single daily item (used for aggr yesterday/today)."""
    if not d:
        return None
    advanced = d.get("advanced") or {}
    return {
        "date": d.get("p_date"),
        "pv": d.get("pv", 0),
        "show": d.get("show", 0),
        "play": d.get("play", 0),
        "upvote": d.get("reaction", d.get("upvote", 0)) if content_type == "pin" else d.get("upvote", 0),
        "like": d.get("like", 0),
        "collect": d.get("collect", 0),
        "comment": d.get("comment", 0),
        "share": d.get("share", 0),
        "finish_read_percent": convert_percent(advanced.get("finish_read_percent", "0.0%")),
        "positive_interact_percent": convert_percent(advanced.get("positive_interact_percent", "0.0%")),
        "follower_translate": advanced.get("follower_translate", 0),
    }


def run_batch_daily_analysis(use_aggr: bool = False) -> None:
    data_dir = Path.home() / ".zhihu-cli" / "exports"
    metrics_dir = data_dir / "content_metrics"
    os.makedirs(metrics_dir, exist_ok=True)
    assets_file = data_dir / "all_assets_list.json"

    answer_ids = []
    if assets_file.exists():
        with open(assets_file, encoding="utf-8") as f:
            answer_ids = json.load(f)
        print(f"Loaded {len(answer_ids)} assets to analyze")
    else:
        print("all_assets_list.json not found")
        print("  This file is the asset inventory of your Zhihu creations.")
        print()
        try:
            choice = input("  Generate it now? [Y/n]: ").strip().lower()
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
            print("  Run: zhihu scrape creations")
            print("  Then: zhihu tools creator metrics")
            return

    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return
    headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}

    base_url = AGGR_URL if use_aggr else DAILY_URL

    success_count = 0
    for i, token in enumerate(answer_ids):
        print(f"\n[Task {i + 1}/{len(answer_ids)}] Processing ID: {token} ...")

        created_ts = token.get("created_time", 0)
        start_date = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
        params = {
            "type": token["type"],
            "token": token["id"],
            "start": start_date,
            "end": datetime.now().strftime("%Y-%m-%d"),
        }

        try:
            resp = session.get(base_url, headers=headers, params=params, timeout=15)

            if resp.status_code == 200:
                data = resp.json()

                if use_aggr:
                    advanced = data.get("advanced") or {}
                    clean_data = {
                        "type": token["type"],
                        "totals": {
                            "pv": data.get("pv", 0),
                            "show": data.get("show", 0),
                            "play": data.get("play", 0),
                            "upvote": data.get("reaction", data.get("upvote", 0))
                            if token["type"] == "pin"
                            else data.get("upvote", 0),
                            "like": data.get("like", 0),
                            "collect": data.get("collect", 0),
                            "comment": data.get("comment", 0),
                            "share": data.get("share", 0),
                        },
                        "advanced": {
                            "finish_read_percent": convert_percent(advanced.get("finish_read_percent", "0.0%")),
                            "positive_interact_percent": convert_percent(
                                advanced.get("positive_interact_percent", "0.0%")
                            ),
                            "follower_translate": advanced.get("follower_translate", 0),
                        },
                        "yesterday": _extract_daily_item(data.get("yesterday"), token["type"]),
                        "today": _extract_daily_item(data.get("today"), token["type"]),
                    }
                    entries_label = "aggregated"
                else:
                    clean_data = []
                    for d in data:
                        advanced = d.get("advanced") or {}
                        clean_data.append(
                            {
                                "type": token["type"],
                                "date": get_date(d),
                                "pv": d.get("pv", 0),
                                "show": d.get("show", 0),
                                "play": d.get("play", 0),
                                "upvote": d.get("reaction", d.get("upvote", 0))
                                if token["type"] == "pin"
                                else d.get("upvote", 0),
                                "like": d.get("like", 0),
                                "collect": d.get("collect", 0),
                                "comment": d.get("comment", 0),
                                "share": d.get("share", 0),
                                "finish_read_percent": convert_percent(advanced.get("finish_read_percent", "0.0%")),
                                "positive_interact_percent": convert_percent(
                                    advanced.get("positive_interact_percent", "0.0%")
                                ),
                                "follower_translate": advanced.get("follower_translate", "0"),
                            }
                        )
                    entries_label = f"{len(clean_data)} entries"

                output_file = metrics_dir / f"metrics_full_{token['type']}_{token['id']}.json"
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(clean_data, f, indent=4)

                print(f"  Saved: {output_file} ({entries_label} records)")
                success_count += 1
            else:
                print(f"  Fetch failed (Code: {resp.status_code})")

        except Exception as e:
            print(f"  Exception: {e}")

        time.sleep(1.2)

    print("\n" + "=" * 40)
    print("Batch harvest finished!")
    print(f"Successful fetches: {success_count} / {len(answer_ids)}")
    print(f"Data stored in: {metrics_dir}")
    print("=" * 40)


if __name__ == "__main__":
    run_batch_daily_analysis()

"""Fetch and persist creator score detail (创作分明细) from Zhihu API."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session

DB_FILE: str = str(Path.home() / ".zhihu-cli" / "exports" / "creator_score_detail.json")
DEFAULT_START_DATE: str = "2025-01-01"
SCORE_URL: str = "https://www.zhihu.com/api/v4/creators/creator_score_detail"


def _date_to_ts(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    else:
        dt = dt.replace(hour=0, minute=0, second=0)
    return int(dt.timestamp())


def load_existing_data() -> tuple[list[dict[str, Any]], str]:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                data = json.load(f)
                details = data.get("details", [])
                if details:
                    last_date_str = max(item["p_date"] for item in details)
                    last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
                    next_start = last_dt + timedelta(days=1)
                    return details, next_start.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"[!] Failed to read existing data: {e}, will re-fetch")

    return [], DEFAULT_START_DATE


def run_task() -> None:
    existing_details, start_date = load_existing_data()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if start_date > today_str:
        print(f"Data is already up to date (last record: {start_date}), no update needed.")
        return

    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return

    print(f"--- Fetching creator score detail from {start_date} ---")

    start_ts = _date_to_ts(start_date)
    end_ts = _date_to_ts(today_str, end_of_day=True)

    new_records: list[dict[str, Any]] = []
    offset = 0
    limit = 20

    while True:
        params = {
            "start_at": start_ts,
            "end_at": end_ts,
            "limit": limit,
            "offset": offset,
        }

        print(f"  Fetching offset={offset}...", end=" ")

        try:
            resp = session.get(SCORE_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            detail_list = data.get("data", {}).get("detail", [])
            if not detail_list:
                print("no more data")
                break

            for item in detail_list:
                new_records.append(
                    {
                        "p_date": item["p_date"],
                        "score_type": item["score_type"],
                        "score_type_int": item["score_type_int"],
                        "change_score": item["change_score"],
                        "score": item["score"],
                        "reason": item["reason"],
                        "detail": item["detail"],
                    }
                )

            print(f"got {len(detail_list)} records")

            paging = data.get("paging", {})
            if paging.get("is_end", True):
                break

            offset += limit
            time.sleep(1.0)

        except Exception as e:
            print(f"error: {e}")
            break

    # Merge and deduplicate by (p_date, score_type_int)
    all_details = existing_details + new_records
    seen: set[tuple[str, int]] = set()
    unique_details: list[dict[str, Any]] = []
    for item in all_details:
        key = (item["p_date"], item["score_type_int"])
        if key not in seen:
            seen.add(key)
            unique_details.append(item)

    sorted_details = sorted(unique_details, key=lambda x: (x["p_date"], x["score_type_int"]), reverse=True)

    if new_records:
        output = {
            "summary": {
                "total_records": len(sorted_details),
                "unique_dates": len({d["p_date"] for d in sorted_details}),
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "details": sorted_details,
        }

        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # Summary by score type
        type_totals: dict[str, int] = {}
        for d in sorted_details:
            st = d["score_type"]
            type_totals[st] = type_totals.get(st, 0) + d["change_score"]

        print("\n" + "=" * 50)
        print("Updated!")
        print(f"New records: {len(new_records)}")
        print(f"Total records: {len(sorted_details)}")
        print(f"Unique dates: {len({d['p_date'] for d in sorted_details})}")
        print("\nScore change by type:")
        for st, total in sorted(type_totals.items(), key=lambda x: -x[1]):
            print(f"  {st}: +{total:,}")
        print(f"\nFile synced: {DB_FILE}")
        print("=" * 50)
    else:
        print("\n[!] No new data fetched.")


if __name__ == "__main__":
    run_task()

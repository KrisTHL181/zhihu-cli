import csv
import json
import os
import time
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session

DB_FILE: str = str(Path.home() / ".zhihu-cli" / "exports" / "zhihu_income_report.json")
DEFAULT_START_DATE: str = "2026-01-06"
BASE_URL: str = "https://www.zhihu.com/api/v4/creators/text/income/income/detail/download"


def load_existing_data() -> tuple[list[dict[str, Any]], datetime]:
    """Load existing data. Returns (old_data_list, next_fetch_start_date)."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                data = json.load(f)
                details = data.get("details", [])
                if details:
                    last_date_str = max(item["date"] for item in details)
                    last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
                    next_start = last_dt + timedelta(days=1)
                    return details, next_start
        except Exception as e:
            print(f"[!] Failed to read existing data: {e}, will re-fetch")

    return [], datetime.strptime(DEFAULT_START_DATE, "%Y-%m-%d")


def run_task() -> None:
    existing_details, start_dt = load_existing_data()
    end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if start_dt >= end_dt:
        print(f"Data is already up to date (last record: {start_dt - timedelta(days=1)}), no update needed.")
        return

    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return
    headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}

    print(f"--- Incremental mode: fetching from {start_dt.strftime('%Y-%m-%d')} ---")

    new_income_data = []

    current_dt = start_dt
    while current_dt < end_dt:
        batch_end = min(current_dt + timedelta(days=30), end_dt - timedelta(days=1))
        if batch_end < current_dt:
            batch_end = current_dt

        params = {"start_date": current_dt.strftime("%Y-%m-%d"), "end_date": batch_end.strftime("%Y-%m-%d")}

        print(f"\n[Task] Fetching: {params['start_date']} -> {params['end_date']}")

        try:
            resp = session.get(BASE_URL, headers=headers, params=params, timeout=15)

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
                print(f"  [Success] Extracted {count} days of data")
            else:
                print(f"  [Skip] Status code: {resp.status_code}")

        except Exception as e:
            print(f"  [Exception] {e}")

        if batch_end >= end_dt - timedelta(days=1):
            break
        current_dt = batch_end + timedelta(days=1)
        time.sleep(1.5)

    all_details = existing_details + new_income_data
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
        print("Updated!")
        print(f"New days: {len(new_income_data)}")
        print(f"Total days: {len(sorted_details)}")
        print(f"Total income: {round(total_yuan, 2)} CNY")
        print(f"File synced: {DB_FILE}")
        print("=" * 40)
    else:
        print("\n[!] No new data fetched.")


if __name__ == "__main__":
    run_task()

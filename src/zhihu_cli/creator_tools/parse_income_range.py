"""Fetch per-content income range data (单篇内容盐粒明细) from Zhihu API."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.wait import wait

DB_FILE: str = str(Path.home() / ".zhihu-cli" / "exports" / "creator_income_items.json")
RANGE_URL: str = "https://www.zhihu.com/api/v4/creators/text/income/income/range"
PAGE_SIZE: int = 20
MAX_DAYS: int = 30  # API rejects ranges longer than ~30 days


def _fetch_batch(
    start_date: str,
    end_date: str,
    order_field: str,
    order_sort: str,
) -> list[dict[str, Any]]:
    """Fetch all pages for a single date range batch."""
    items: list[dict[str, Any]] = []
    page = 1

    while True:
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "order_field": order_field,
            "order_sort": order_sort,
            "page": page,
            "page_size": PAGE_SIZE,
        }

        print(f"    page={page}...", end=" ")

        try:
            resp = session.get(RANGE_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            batch_items = data.get("data", [])
            total = data.get("total", 0)

            if not batch_items:
                print("no more data")
                break

            for item in batch_items:
                items.append(
                    {
                        "content_id": item["content_id"],
                        "content_token": item.get("content_token", ""),
                        "content_title": item["content_title"],
                        "content_type": item["content_type"],
                        "content_publish_at": item.get("content_publish_at", 0),
                        "content_publish_date": item.get("content_publish_date", ""),
                        "current_read": item.get("current_read", 0),
                        "current_interaction": item.get("current_interaction", 0),
                        "current_income": item.get("current_income", 0),
                        "total_read": item.get("total_read", 0),
                        "total_interaction": item.get("total_interaction", 0),
                        "total_income": item.get("total_income", 0),
                    }
                )

            print(f"got {len(batch_items)} items (total={total})")

            if page * PAGE_SIZE >= total:
                break

            page += 1
            wait(1.0)

        except Exception as e:
            print(f"error: {e}")
            break

    return items


def run_task(
    start_date: str | None = None,
    end_date: str | None = None,
    order_field: str = "content_publish_at",
    order_sort: str = "desc",
    json_output: bool = False,
) -> None:
    headers = cache_manager.load_headers()
    if not headers:
        print("No cached headers found. Run: zhihu auth paste")
        return

    yesterday = datetime.now() - timedelta(days=1)
    if end_date is None:
        end_date = yesterday.strftime("%Y-%m-%d")
    if start_date is None:
        start_date = cache_manager.get_start_date()

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    print(f"--- Fetching per-content income: {start_date} → {end_date} ---")

    all_items: list[dict[str, Any]] = []
    batch_start = start_dt

    while batch_start <= end_dt:
        batch_end = min(batch_start + timedelta(days=MAX_DAYS - 1), end_dt)
        b_start = batch_start.strftime("%Y-%m-%d")
        b_end = batch_end.strftime("%Y-%m-%d")

        print(f"  Batch: {b_start} → {b_end}")
        batch_items = _fetch_batch(b_start, b_end, order_field, order_sort)
        all_items.extend(batch_items)

        batch_start = batch_end + timedelta(days=1)
        if batch_start > end_dt:
            break
        wait(1.0)

    if not all_items:
        print("\n[!] No data fetched.")
        return

    # Deduplicate by content_id (keep last occurrence in case of overlap)
    seen: dict[str, dict[str, Any]] = {}
    for item in all_items:
        seen[item["content_id"]] = item
    unique_items = list(seen.values())

    # Sort by content_publish_at descending
    unique_items.sort(key=lambda x: x["content_publish_at"], reverse=True)

    total_income_salt = sum(item["total_income"] for item in unique_items)
    total_read = sum(item["total_read"] for item in unique_items)
    total_interaction = sum(item["total_interaction"] for item in unique_items)
    unique_dates = sorted({item["content_publish_date"] for item in unique_items}, reverse=True)
    answer_count = sum(1 for item in unique_items if item["content_type"] == "answer")
    article_count = sum(1 for item in unique_items if item["content_type"] == "article")
    pin_count = sum(1 for item in unique_items if item["content_type"] == "pin")

    output = {
        "params": {
            "start_date": start_date,
            "end_date": end_date,
            "order_field": order_field,
            "order_sort": order_sort,
        },
        "summary": {
            "total_items": len(unique_items),
            "answer_count": answer_count,
            "article_count": article_count,
            "pin_count": pin_count,
            "total_income_salt": total_income_salt,
            "total_income_yuan": round(total_income_salt / 100.0, 2),
            "total_read": total_read,
            "total_interaction": total_interaction,
            "unique_dates": len(unique_dates),
            "date_range": f"{unique_dates[-1]} → {unique_dates[0]}" if unique_dates else "",
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "items": unique_items,
    }

    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    if json_output:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # Summary table
    print()
    print("=" * 60)
    print(f"  Per-Content Income: {start_date} → {end_date}")
    print("=" * 60)
    print(
        f"  Total items:      {len(unique_items):>6}  (answers: {answer_count}, articles: {article_count}, pins: {pin_count})"
    )
    print(f"  Total income:     {total_income_salt:>6} 盐粒 = ¥{total_income_salt / 100:.2f}")
    print(f"  Total reads:      {total_read:>6}")
    print(f"  Total interactions:{total_interaction:>6}")
    print(f"  Unique dates:     {len(unique_dates):>6}")
    print("-" * 60)
    print(f"  {'Title':<36s} {'Type':<8s} {'Income':>8s} {'Reads':>8s}")
    print("-" * 60)
    for item in unique_items[:30]:
        title = item["content_title"][:34] + ".." if len(item["content_title"]) > 36 else item["content_title"]
        print(f"  {title:<36s} {item['content_type']:<8s} {item['total_income']:>7d}盐 {item['total_read']:>7d}阅")
    if len(unique_items) > 30:
        print(f"  ... and {len(unique_items) - 30} more items")
    print("=" * 60)
    print(f"\nFile saved: {DB_FILE}")


if __name__ == "__main__":
    run_task()

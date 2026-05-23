import json
from collections import defaultdict


def get_monthly_income_data(file_path: str) -> dict:
    """Return monthly income data as a dict.

    Returns {"monthly": {YYYY-MM: amount, ...}, "cumulative_total": float}
    or {"error": str} on failure.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        monthly_stats = defaultdict(float)

        for entry in data.get("details", []):
            month = entry["date"][:7]
            monthly_stats[month] += entry["income_yuan"]

        return {
            "monthly": dict(sorted(monthly_stats.items())),
            "cumulative_total": sum(monthly_stats.values()),
        }

    except FileNotFoundError:
        return {"error": "zhihu_income_report.json not found"}
    except Exception as e:
        return {"error": str(e)}


def analyze_monthly_income(file_path: str) -> None:
    result = get_monthly_income_data(file_path)
    if "error" in result:
        print(f"Error: {result['error']}")
        return

    monthly: dict = result["monthly"]
    cumulative: float = result["cumulative_total"]

    print(f"{'Month':<10} | {'Income (CNY)':<10}")
    print("-" * 25)

    for month, amount in monthly.items():
        print(f"{month:<10} | {amount:>10.2f}")

    print("-" * 25)
    print(f"Cumulative total: {cumulative:>14.2f}")


if __name__ == "__main__":
    from pathlib import Path

    analyze_monthly_income(str(Path.home() / ".zhihu-cli" / "exports" / "zhihu_income_report.json"))

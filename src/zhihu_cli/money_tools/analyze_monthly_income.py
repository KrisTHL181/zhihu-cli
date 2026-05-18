import json
from collections import defaultdict


def analyze_monthly_income(file_path: str) -> None:
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        monthly_stats = defaultdict(float)

        for entry in data.get("details", []):
            # Extract year-month from date (YYYY-MM)
            month = entry["date"][:7]
            monthly_stats[month] += entry["income_yuan"]

        # Print results
        print(f"{'Month':<10} | {'Income (CNY)':<10}")
        print("-" * 25)

        for month in sorted(monthly_stats.keys()):
            print(f"{month:<10} | {monthly_stats[month]:>10.2f}")

        print("-" * 25)
        print(f"Cumulative total: {sum(monthly_stats.values()):>14.2f}")

    except FileNotFoundError:
        print("Error: zhihu_income_report.json not found")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    from pathlib import Path

    analyze_monthly_income(str(Path.home() / ".zhihu-cli" / "exports" / "zhihu_income_report.json"))

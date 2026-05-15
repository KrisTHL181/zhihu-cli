import json
from collections import defaultdict


def analyze_monthly_income(file_path: str) -> None:
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        # 使用字典存储月份收入，默认值为 0.0
        monthly_stats = defaultdict(float)

        # 遍历明细数据
        for entry in data.get("details", []):
            # 提取日期中的年份和月份 (YYYY-MM)
            month = entry["date"][:7]
            monthly_stats[month] += entry["income_yuan"]

        # 打印结果
        print(f"{'月份':<10} | {'收入 (CNY)':<10}")
        print("-" * 25)

        # 按月份排序输出
        for month in sorted(monthly_stats.keys()):
            print(f"{month:<10} | {monthly_stats[month]:>10.2f}")

        print("-" * 25)
        print(f"累计总计: {sum(monthly_stats.values()):>14.2f}")

    except FileNotFoundError:
        print("错误：找不到 zhihu_income_report.json 文件")
    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    from pathlib import Path

    analyze_monthly_income(str(Path.home() / ".zhihu-cli" / "exports" / "zhihu_income_report.json"))

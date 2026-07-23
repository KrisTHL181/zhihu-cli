"""Monthly income aggregation and display.

Provides the data layer and styled terminal output for
``zhihu tools creator income monthly``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta


def _next_weekday(d: date, weekday: int) -> date:
    """Return the next occurrence of *weekday* (0=Mon … 6=Sun) on or after *d*."""
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


def _get_payment_date(month_str: str) -> date:
    """Return the actual payment date for income earned in *month_str* (YYYY-MM).

    Rule: paid on the 15th of the following month.  If the 15th falls on
    a Saturday or Sunday it is pushed to the following Monday.
    """
    year, month = map(int, month_str.split("-"))
    # First day of the following month
    if month == 12:
        pay_year, pay_month = year + 1, 1
    else:
        pay_year, pay_month = year, month + 1

    pay_day = 15
    d = date(pay_year, pay_month, pay_day)
    # Saturday (5) → next Monday; Sunday (6) → next Monday
    if d.weekday() == 5:  # Saturday
        d = _next_weekday(d, 0)  # next Monday
    elif d.weekday() == 6:  # Sunday
        d = _next_weekday(d, 0)  # next Monday
    return d


def _get_highlight_month(today: date) -> str:
    """Return the YYYY-MM to highlight with the arrow.

    Normally the payment landing this month is for the *previous* month's
    income (paid on the 15th, adjusted for weekends).  But if today is
    already past that payment date the money has arrived — the arrow shifts
    to the *current* month (income still accumulating, paid next month).
    """
    # Previous month (whose income is paid this month)
    if today.month == 1:
        prev_month = f"{today.year - 1}-12"
    else:
        prev_month = f"{today.year}-{today.month - 1:02d}"

    pay_date = _get_payment_date(prev_month)
    if today > pay_date:
        # Payment already arrived — highlight the current (in-progress) month
        return today.strftime("%Y-%m")
    return prev_month


def get_monthly_income_data(file_path: str) -> dict:
    """Return monthly income data as a dict.

    :param file_path: Path to ``zhihu_income_report.json``.
    :returns: ``{"monthly": {YYYY-MM: amount, ...}, "cumulative_total": float}``
              or ``{"error": str}`` on failure.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        monthly_stats: defaultdict[str, float] = defaultdict(float)

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
    """Print a styled monthly income summary table.

    Each row shows month, total income, payment date, and an arrow (→)
    next to the month whose payment arrives in the current calendar month.

    :param file_path: Path to ``zhihu_income_report.json``.
    """
    from zhihu_cli.output import arrow, f_bold, f_green, f_label, f_meta, f_num, heading, print_table

    result = get_monthly_income_data(file_path)
    if "error" in result:
        from zhihu_cli.output import error

        error(result["error"])
        return

    monthly: dict[str, float] = result["monthly"]
    cumulative: float = result["cumulative_total"]
    today = date.today()
    highlight_month = _get_highlight_month(today)

    heading("Monthly Income Summary")

    columns = ["Month", "Income (CNY)", "Pay Date"]
    rows: list[list[str]] = []

    for month_str, amount in monthly.items():
        pay_date = _get_payment_date(month_str)
        pay_label = pay_date.strftime("%Y-%m-%d")
        day_name = pay_date.strftime("%A")

        if month_str == highlight_month:
            is_current = month_str == today.strftime("%Y-%m")
            tag = " ← due this month" if not is_current else " ← next payment"
            label = f"{arrow('')} {f_bold(month_str)}"
            income = f_green(f"{amount:>10.2f}")
            pay = f"{pay_label}  {f_meta(f'({day_name})')}  {f_green(tag)}"
        else:
            label = f"   {month_str}"
            income = f"{amount:>10.2f}"
            pay = f"{pay_label}  {f_meta(f'({day_name})')}"

        rows.append([label, income, pay])

    print_table(title=None, columns=columns, rows=rows)

    # Cumulative total footer
    from zhihu_cli.output import divider

    divider()
    print(f"  {f_label('Cumulative total:')} {f_num(f'{cumulative:,.2f}')} CNY")

    # Arrow legend
    if highlight_month in monthly:
        pay_d = _get_payment_date(highlight_month)
        if highlight_month == today.strftime("%Y-%m"):
            print(
                f"\n  {f_meta('→')} {highlight_month} income (in progress) — "
                f"expected {f_green(pay_d.strftime('%Y-%m-%d'))}"
            )
        else:
            print(f"\n  {f_meta('→')} {highlight_month} income expected {f_green(pay_d.strftime('%Y-%m-%d'))}")

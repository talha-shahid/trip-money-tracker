"""Pay-period helper: periods run from `start_day` of one month to the day before it next month."""
from __future__ import annotations

from datetime import date, timedelta


def pay_period(d: date, start_day: int = 10) -> tuple[date, date]:
    if not 1 <= start_day <= 28:
        raise ValueError("start_day must be between 1 and 28")
    if d.day >= start_day:
        start = d.replace(day=start_day)
    else:
        prev_month_last = d.replace(day=1) - timedelta(days=1)
        start = prev_month_last.replace(day=start_day)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=start_day)  # same day, next month
    return start, nxt - timedelta(days=1)


def period_from_text(text: str, start_day: int = 10) -> tuple[date, date]:
    """'2026-10' -> the pay period that STARTS in October (10 Oct to 9 Nov); 'YYYY-MM-DD:YYYY-MM-DD' -> as given."""
    text = text.strip()
    if ":" in text:
        a, b = text.split(":", 1)
        return date.fromisoformat(a), date.fromisoformat(b)
    year, month = (int(x) for x in text.split("-")[:2])
    return pay_period(date(year, month, start_day), start_day)

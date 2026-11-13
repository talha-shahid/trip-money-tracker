from datetime import date

from tripmoney.period import pay_period


def test_period_runs_10th_to_9th():
    assert pay_period(date(2026, 10, 10)) == (date(2026, 10, 10), date(2026, 11, 9))
    assert pay_period(date(2026, 10, 26)) == (date(2026, 10, 10), date(2026, 11, 9))
    assert pay_period(date(2026, 11, 9)) == (date(2026, 10, 10), date(2026, 11, 9))
    assert pay_period(date(2026, 11, 10)) == (date(2026, 11, 10), date(2026, 12, 9))


def test_period_crosses_the_year_boundary():
    assert pay_period(date(2026, 12, 20)) == (date(2026, 12, 10), date(2027, 1, 9))
    assert pay_period(date(2027, 1, 3)) == (date(2026, 12, 10), date(2027, 1, 9))


def test_sept_21_scan_falls_outside_the_october_period():
    start, end = pay_period(date(2026, 10, 15))
    assert not (start <= date(2026, 9, 21) <= end)

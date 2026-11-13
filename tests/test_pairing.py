from decimal import Decimal

from helpers import mv
from tripmoney.config import load_settings
from tripmoney.domain import Status
from tripmoney.engine import FLAG_UNPAIRED, price, summarize

TABLE = load_settings().rate_table


def run(ms, gap=60):
    return price(ms, TABLE, max_gap_minutes=gap)


def test_two_20ft_empties_are_one_trip():
    a, b = run([mv("1", size=20, at="2026-10-22 23:15"), mv("2", size=20, at="2026-10-22 23:15")])
    assert (a.trip_count, a.amount, a.pair_role) == (1, Decimal(57), "first")
    assert (b.trip_count, b.amount, b.pair_role) == (0, Decimal(0), "second")
    assert a.pair_id == b.pair_id and not a.flags and not b.flags


def test_three_boxes_make_one_pair_and_one_flagged_lone_box():
    out = run([mv(str(i), size=20, at="2026-10-21 00:31") for i in (1, 2, 3)])
    assert [a.pair_role for a in out] == ["first", "second", "lone"]
    assert out[2].trip_count == 1 and out[2].amount == Decimal(57) and out[2].flags == [FLAG_UNPAIRED]
    assert summarize(out).trips == 2


def test_far_apart_boxes_are_not_paired():
    out = run([mv("1", size=20, at="2026-10-21 00:31"), mv("2", size=20, at="2026-10-21 23:55")])
    assert [a.pair_role for a in out] == ["lone", "lone"]


def test_gap_limit_is_configurable():
    ms = [mv("1", size=20, at="2026-10-21 10:00"), mv("2", size=20, at="2026-10-21 11:30")]
    assert [a.pair_role for a in run(ms, gap=60)] == ["lone", "lone"]
    assert [a.pair_role for a in run(ms, gap=120)] == ["first", "second"]


def test_different_vessel_booking_driver_or_truck_never_pair():
    base = dict(size=20, at="2026-10-21 10:00")
    for other in (dict(vessel="V2"), dict(booking="B2"), dict(driver="D2"), dict(truck="T2")):
        out = run([mv("1", **base), mv("2", **base, **other)])
        assert [a.pair_role for a in out] == ["lone", "lone"], other


def test_booking_number_groups_even_if_customer_text_differs():
    base = dict(size=20, at="2026-10-21 10:00", booking="CUL123")
    out = run([mv("1", customer="China United", **base), mv("2", customer="CHINA UNITED.", **base)])
    assert [a.pair_role for a in out] == ["first", "second"]


def test_date_only_boxes_pair_on_the_same_day_only():
    same = run([mv("1", size=20, at="2026-10-21 00:00", has_time=False),
                mv("2", size=20, at="2026-10-21 00:00", has_time=False)])
    other = run([mv("1", size=20, at="2026-10-21 00:00", has_time=False),
                 mv("2", size=20, at="2026-10-22 00:00", has_time=False)])
    assert [a.pair_role for a in same] == ["first", "second"]
    assert [a.pair_role for a in other] == ["lone", "lone"]


def test_40ft_and_full_boxes_are_never_paired():
    out = run([mv("1", size=40), mv("2", size=40), mv("3", size=20, status=Status.FULL),
               mv("4", size=20, status=Status.FULL)])
    assert all(a.pair_id is None for a in out) and sum(a.trip_count for a in out) == 4

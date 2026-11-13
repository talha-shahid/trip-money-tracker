from decimal import Decimal

import pytest

from helpers import mv
from tripmoney.config import load_settings
from tripmoney.domain import Direction, Status
from tripmoney.engine import FLAG_NO_RULE, FLAG_UNVERIFIED, price

TABLE = load_settings().rate_table


@pytest.mark.parametrize("kw, route, amount", [
    (dict(size=40, status=Status.FULL, direction=Direction.MLS_JIP), "MLS-JIP,OW,FUL", 70),
    (dict(size=20, status=Status.FULL, direction=Direction.MLS_JIP), "MLS-JIP,OW,FUL", 70),
    (dict(size=40, status=Status.FULL, direction=Direction.JIP_MLS), "JIP-MLS,OW,FUL", 70),
    (dict(size=20, status=Status.FULL, direction=Direction.JIP_MLS), "JIP-MLS,OW,FUL", 70),
    (dict(size=40, status=Status.EMPTY, direction=Direction.MLS_JIP), "MLS-JIP,OW,MT4", 56),
    (dict(size=40, status=Status.EMPTY, direction=Direction.MLS_JIP, ctype="REEFER"), "MLS-JIP,OW,MT4", 56),
])
def test_single_container_rules(kw, route, amount):
    (a,) = price([mv("1", **kw)], TABLE)
    assert (a.route_code, a.amount, a.trip_count, a.flags) == (route, Decimal(amount), 1, [])


def test_jip_to_mls_empty_uses_spec_rate_and_is_flagged_unverified():
    for size in (20, 40):
        (a,) = price([mv("1", size=size, status=Status.EMPTY, direction=Direction.JIP_MLS)], TABLE)
        assert (a.system_code, a.amount, a.trip_count) == ("MLS-OW-MTY", Decimal(59), 1)
        assert a.flags == [FLAG_UNVERIFIED]


def test_unknown_size_gets_no_rule_and_is_flagged():
    (a,) = price([mv("1", size=45)], TABLE)
    assert (a.rule_id, a.amount, a.trip_count, a.flags) == (None, Decimal(0), 0, [FLAG_NO_RULE])


def test_output_order_matches_input_order():
    ms = [mv("b", size=40), mv("a", size=40, status=Status.FULL), mv("c", size=40)]
    assert [a.movement_id for a in price(ms, TABLE)] == ["b", "a", "c"]


def test_effective_dates_select_the_right_rate():
    from datetime import date
    from tripmoney.rules import RateTable, Rule

    base = dict(route_code="X", system_code="X", spec_code="", direction=Direction.MLS_JIP,
                status=Status.FULL, size_ft=None)
    table = RateTable([
        Rule(id="new", amount=Decimal(80), effective_from=date(2026, 11, 1), **base),
        Rule(id="old", amount=Decimal(70), effective_to=date(2026, 10, 31), **base),
    ], version="t")
    old, new = price([mv("1", status=Status.FULL, at="2026-10-31 09:00"),
                      mv("2", status=Status.FULL, at="2026-11-01 09:00")], table)
    assert (old.amount, new.amount) == (Decimal(70), Decimal(80))

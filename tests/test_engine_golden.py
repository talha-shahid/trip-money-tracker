"""The real trip sheet is the ground truth: the engine must reproduce it from raw box attributes."""
from decimal import Decimal

from helpers import load_golden
from tripmoney.config import load_settings
from tripmoney.engine import price, summarize


def _priced():
    rows, movements = load_golden()
    settings = load_settings()
    return rows, price(movements, settings.rate_table, max_gap_minutes=settings.pairing_max_gap_minutes)


def test_every_row_matches_the_sheet():
    rows, out = _priced()
    assert len(rows) == len(out) == 80
    for r, a in zip(rows, out):
        assert a.trip_count == int(r["expected_trip_no"]), r
        assert a.amount == Decimal(r["expected_amount"]), r
        if r["expected_route"]:  # second box of a pair has no route typed in the sheet
            assert a.route_code == r["expected_route"], r


def test_totals_match_the_sheet_summary_block():
    _, out = _priced()
    s = summarize(out)
    assert (s.trips, s.amount) == (64, Decimal(3894))
    assert {k: (v.trips, v.amount) for k, v in s.by_route.items()} == {
        "MLS-JIP,OW,MT2": (16, Decimal(912)),
        "MLS-JIP,OW,FUL": (7, Decimal(490)),
        "MLS-JIP,OW,MT4": (27, Decimal(1512)),
        "JIP-MLS,OW,FUL": (14, Decimal(980)),
    }


def test_golden_data_needs_no_human_review():
    _, out = _priced()
    assert all(not a.flags for a in out)

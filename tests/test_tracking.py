from datetime import datetime

import pytest

from fixtures_io import page_inputs
from helpers import mv
from tripmoney import tracking
from tripmoney.config import load_settings
from tripmoney.db import init_db, make_engine, make_session_factory, repo
from tripmoney.domain import Direction, Status
from tripmoney.ingest.pipeline import ingest_pages

SETTINGS = load_settings()


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def box(s, id, direction, at, **kw):
    row = repo.add_movement(s, mv(id, direction=direction, at=at, **kw))
    row.container_no = "TEST0000001"
    return row


def test_in_then_out_is_a_completed_stay(session):
    box(session, "1", Direction.JIP_MLS, "2026-10-10 08:00", status=Status.FULL)
    box(session, "2", Direction.MLS_JIP, "2026-10-15 20:00", status=Status.EMPTY)
    h = tracking.container_history(session, "TEST0000001")
    assert [e.kind for e in h.events] == ["IN", "OUT"] and h.current == tracking.LEFT_DEPOT
    (stay,) = h.stays(datetime(2026, 11, 1))
    assert stay[2] == 5.5 and stay[1] is not None and not h.anomalies


def test_box_still_in_depot_ages_up_to_now(session):
    box(session, "1", Direction.JIP_MLS, "2026-10-10 12:00", status=Status.FULL)
    h = tracking.container_history(session, "TEST0000001")
    assert h.current == tracking.IN_DEPOT and h.days_in_depot(datetime(2026, 10, 20, 12)) == 10.0
    assert [(x.container_no, d) for x, d in tracking.depot_inventory(session, datetime(2026, 10, 20, 12))] == [("TEST0000001", 10.0)]


def test_two_ins_in_a_row_point_to_a_missing_scan(session):
    box(session, "1", Direction.JIP_MLS, "2026-10-10 08:00")
    box(session, "2", Direction.JIP_MLS, "2026-10-12 08:00")
    assert len(tracking.container_history(session, "TEST0000001").anomalies) == 1


def test_terminal_receipt_does_not_count_as_a_depot_event(session):
    box(session, "1", Direction.MLS_JIP, "2026-10-10 08:00")
    row = box(session, "2", Direction.MLS_JIP, "2026-10-10 10:00")
    row.source_doc_type = "dpworld"
    h = tracking.container_history(session, "TEST0000001")
    assert h.current == tracking.AT_TERMINAL and not h.anomalies  # OUT then terminal: no "OUT twice"


def test_unknown_container_has_no_history(session):
    assert tracking.container_history(session, "NOPE") is None


def test_real_scans_give_a_depot_inventory_and_search(session):
    for pack in ("pack1", "pack2"):
        ingest_pages(session, page_inputs(pack), SETTINGS)
    inv = {h.container_no: days for h, days in tracking.depot_inventory(session, datetime(2026, 10, 30))}
    assert len(inv) == 19 and "TSSU5340034" in inv and "CAAU4073093" not in inv  # 8 + 11 IN pages, none seen leaving
    assert inv["TSSU5340034"] == 5.1  # IN 24 Oct 22:04 -> 30 Oct 00:00
    assert [h.container_no for h in tracking.search(session, "ZGXU3098707")] == ["ZGXU3098707"]
    assert {h.container_no for h in tracking.search(session, "OOCL")} == {"OOLU9710696", "OOCU6915335"}
    assert tracking.search(session, "696249")[0].container_no == "CRXU8681568"  # by EIR number

from datetime import datetime

import pytest
from sqlalchemy import select

from fixtures_io import page_inputs
from tripmoney import review
from tripmoney.config import load_settings
from tripmoney.db import AuditLog, Name, init_db, make_engine, make_session_factory, repo
from tripmoney.db import models as m
from tripmoney.ingest.pipeline import ingest_pages

SETTINGS = load_settings()


@pytest.fixture()
def s():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as session:
        for pack in ("pack1", "pack2"):
            ingest_pages(session, page_inputs(pack), SETTINGS)
        yield session


def mv_of(s, no) -> m.Movement:
    return s.scalar(select(m.Movement).where(m.Movement.container_no == no))


def open_codes(mv):
    return {f.code for f in mv.flags if not f.resolved}


def test_filling_in_the_smeared_customer_closes_its_flag_and_teaches_the_name(s):
    mv = mv_of(s, "ARKU2395678")
    assert "MISSING_CUSTOMER" in open_codes(mv)
    diff = review.edit_movement(s, mv.id, {"customer": "Arkas"}, SETTINGS)
    assert diff["customer"] == ("", "Arkas") and "MISSING_CUSTOMER" not in open_codes(mv)
    assert s.scalar(select(Name).where(Name.canonical == "Arkas")) is not None
    assert s.scalar(select(AuditLog).where(AuditLog.action == "edit", AuditLog.entity_id == mv.id)) is not None


def test_changing_size_reprices_the_trip(s):
    mv = mv_of(s, "BEAU2897625")  # lone 20' empty = 57
    assert mv.assignment.route_code == "MLS-JIP,OW,MT2"
    review.edit_movement(s, mv.id, {"size_ft": "40"}, SETTINGS)
    assert (mv.assignment.route_code, int(mv.assignment.amount)) == ("MLS-JIP,OW,MT4", 56)


def test_container_number_is_validated_and_repaired(s):
    mv = mv_of(s, "TGCU0162377")
    review.edit_movement(s, mv.id, {"container_no": "TGCU0I62377"}, SETTINGS)  # I for 1
    assert mv.container_no == "TGCU0162377" and "CHECK_DIGIT" not in open_codes(mv)
    review.edit_movement(s, mv.id, {"container_no": "TGCU0162378"}, SETTINGS)  # wrong check digit
    assert "CHECK_DIGIT" in open_codes(mv)
    with pytest.raises(review.EditError):
        review.edit_movement(s, mv.id, {"container_no": "TGCU01"}, SETTINGS)


@pytest.mark.parametrize("field, bad", [("size_ft", "30"), ("status", "HALF"), ("direction", "A-B"),
                                        ("container_type", "BOAT"), ("occurred_at", "yesterday")])
def test_bad_values_are_rejected(s, field, bad):
    with pytest.raises(review.EditError):
        review.edit_movement(s, mv_of(s, "TGCU0162377").id, {field: bad}, SETTINGS)


def test_blank_required_fields_mean_leave_as_is(s):
    mv = mv_of(s, "TGCU0162377")
    assert review.edit_movement(s, mv.id, {"size_ft": "", "status": "", "occurred_at": ""}, SETTINGS) == {}


def test_moving_a_box_to_another_driver_reprices_both(s):
    mv = mv_of(s, "ARKU2412440")
    from tripmoney.db.repo import summary
    before = summary(s, driver_key="2000000002").trips
    review.edit_movement(s, mv.id, {"driver_key": "2000000001"}, SETTINGS)
    assert mv.driver.key == "2000000001" and summary(s, driver_key="2000000002").trips == before - 1


def test_approve_resolves_flags_and_leaves_the_queue(s):
    mv = mv_of(s, "SEGU5160443")
    assert mv in s.scalars(repo.open_flags_query()).all()
    review.approve(s, mv.id)
    assert mv.review_status == "approved" and not open_codes(mv)
    assert mv not in s.scalars(repo.open_flags_query()).all()


def test_cannot_approve_an_incomplete_page(s):
    mv = mv_of(s, "TGCU0162377")
    mv.size_ft = None
    with pytest.raises(review.EditError):
        review.approve(s, mv.id)


def test_bulk_approve_takes_only_pages_whose_sole_warning_is_that_flag(s):
    before = repo.count_needing_review(s)
    n = review.approve_by_flag(s, "UNPAIRED_20FT", SETTINGS)
    assert n > 0 and repo.count_needing_review(s) == before - n
    assert "UNPAIRED_20FT" in open_codes(mv_of(s, "ARKU2412440")) or mv_of(s, "ARKU2412440").review_status != "approved"
    assert mv_of(s, "SEGU5160443").review_status != "approved"  # has other warnings, untouched


def test_driver_details(s):
    d = review.set_driver_details(s, "2000000001", name="  Sample   Driver One ", emp_no="4000001", sponsor="SRL")
    assert (d.name, d.emp_no, d.sponsor) == ("Sample Driver One", "4000001", "SRL")
    with pytest.raises(review.EditError):
        review.set_driver_details(s, "missing")


def test_driver_can_be_found_by_visible_digits_or_name_but_never_ambiguously(s):
    assert review.find_driver(s, "***001").key == "2000000001" and review.find_driver(s, "002").key == "2000000002"
    assert review.find_driver(s, "2000000002").key == "2000000002"
    with pytest.raises(review.EditError, match="no driver"):
        review.find_driver(s, "999")
    review.set_driver_details(s, "001", name="Same Name")
    review.set_driver_details(s, "002", name="Other Name")
    assert review.find_driver(s, "same name").key == "2000000001"


# ---- void / restore -----------------------------------------------------------------------------------------

def trips(s, key="2000000001"):
    sm = repo.summary(s, driver_key=key)
    return sm.trips, int(sm.amount)


def test_voiding_a_page_removes_its_money_and_takes_it_out_of_every_list(s):
    mv = mv_of(s, "MRKU0318280")  # the DP World receipt: a 40' empty = 56
    before = trips(s)
    review.void_movement(s, mv.id, "copy of an EIR already counted", SETTINGS)
    assert trips(s) == (before[0] - 1, before[1] - 56) and mv.assignment is None
    assert mv not in s.scalars(repo.open_flags_query()).all()
    from tripmoney import tracking
    assert tracking.container_history(s, "MRKU0318280") is None
    from tripmoney.export import load_trip_rows
    rows, outside = load_trip_rows(s, "2000000001", None, None, {})
    assert "MRKU0318280" not in {r.container for r in rows} and mv not in outside
    assert s.scalar(select(AuditLog).where(AuditLog.action == "void", AuditLog.entity_id == mv.id)).detail.count("copy of an EIR")


def test_void_needs_a_reason_and_blocks_edits_and_approval(s):
    mv = mv_of(s, "MRKU0318280")
    with pytest.raises(review.EditError, match="reason"):
        review.void_movement(s, mv.id, "  ", SETTINGS)
    review.void_movement(s, mv.id, "not ours", SETTINGS)
    with pytest.raises(review.EditError, match="restore"):
        review.edit_movement(s, mv.id, {"customer": "X"}, SETTINGS)
    with pytest.raises(review.EditError, match="restore"):
        review.approve(s, mv.id)


def test_restore_brings_the_trip_back(s):
    mv = mv_of(s, "MRKU0318280")
    before = trips(s)
    review.void_movement(s, mv.id, "mistake", SETTINGS)
    review.restore_movement(s, mv.id, SETTINGS)
    assert trips(s) == before and mv.review_status == "unreviewed" and mv.assignment is not None
    with pytest.raises(review.EditError, match="not voided"):
        review.restore_movement(s, mv.id, SETTINGS)


def test_voiding_one_box_of_a_pair_leaves_its_partner_alone_and_flagged(s):
    partner, voided = mv_of(s, "SLVU3016562"), mv_of(s, "CAAU2443630")
    review.void_movement(s, voided.id, "duplicate scan", SETTINGS)
    assert partner.assignment.pair_role == "lone" and partner.assignment.trip_count == 1
    assert "UNPAIRED_20FT" in open_codes(partner)


# ---- adding a box by hand -----------------------------------------------------------------------------------

def valid_container():
    from tripmoney.iso6346 import check_digit
    return "TEST000001" + str(check_digit("TEST000001"))


HAND = dict(direction="MLS-JIP", status="EMPTY", size_ft="20", occurred_at="2026-10-21T23:58", container_type="GP",
            customer="CHINA UNITED", vessel="KR TASMAN", booking_no="CULTASMANRSGT231026", driver_key="2000000001",
            truck="RJA-3518")


def test_a_box_added_by_hand_is_priced_approved_and_can_complete_a_pair(s):
    lone = mv_of(s, "BEAU2897625")  # 20' empty, 23:55, no partner scanned
    assert "UNPAIRED_20FT" in open_codes(lone)
    before = trips(s)
    mv = review.add_movement_by_hand(s, {**HAND, "container_no": valid_container()}, SETTINGS)
    assert mv.review_status == "approved" and mv.source_doc_type == "manual" and mv.document_id is None
    assert trips(s) == before  # the two boxes are now ONE paired trip, not two
    assert "UNPAIRED_20FT" not in open_codes(lone) and lone.assignment.pair_id == mv.assignment.pair_id
    assert s.scalar(select(AuditLog).where(AuditLog.action == "add_by_hand")) is not None


@pytest.mark.parametrize("change, message", [
    ({"container_no": ""}, "please fill in"),
    ({"container_no": "TEST0000019"}, "check digit"),
    ({"direction": "SIDEWAYS"}, "direction"),
    ({"size_ft": "33"}, "size"),
    ({"occurred_at": "tomorrow"}, "date/time"),
])
def test_manual_entry_is_strictly_validated(s, change, message):
    fields = {**HAND, "container_no": valid_container(), **change}
    with pytest.raises(review.EditError, match=message):
        review.add_movement_by_hand(s, fields, SETTINGS)


def test_manual_entry_refuses_a_duplicate(s):
    fields = {**HAND, "container_no": valid_container()}
    review.add_movement_by_hand(s, fields, SETTINGS)
    with pytest.raises(review.EditError, match="already recorded"):
        review.add_movement_by_hand(s, fields, SETTINGS)

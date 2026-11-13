from decimal import Decimal

import pytest
from sqlalchemy import func, select

from fixtures_io import page_inputs
from tripmoney.config import load_settings
from tripmoney.db import Document, Movement, init_db, make_engine, make_session_factory, repo
from tripmoney.ingest.pipeline import ingest_pages

D1, D2 = "2000000001", "2000000002"  # dummy Iqama numbers baked into the sanitised fixtures
SETTINGS = load_settings()


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture()
def loaded(session):
    for pack in ("pack1", "pack2"):
        ingest_pages(session, page_inputs(pack), SETTINGS)
    return session


def by_container(s, no) -> Movement:
    return s.scalar(select(Movement).where(Movement.container_no == no))


def codes(mv) -> set[str]:
    return {f.code for f in mv.flags}


def test_every_page_becomes_a_movement(loaded):
    assert loaded.scalar(select(func.count()).select_from(Movement)) == 65
    assert loaded.scalar(select(func.count()).select_from(Document).where(Document.status == "parsed")) == 65


def test_each_pack_is_assigned_to_its_driver_and_truck(loaded):
    for key, truck, n in ((D1, "RJA3518", 28), (D2, "NSA6128", 37)):
        rows = loaded.scalars(select(Movement).join(Movement.driver).where(Movement.driver.has(key=key))).all()
        assert len(rows) == n and {r.truck.plate for r in rows} == {truck}


def test_money_per_driver(loaded):
    a, b = repo.summary(loaded, driver_key=D1), repo.summary(loaded, driver_key=D2)
    assert (a.trips, a.amount) == (26, Decimal(1572))
    assert (b.trips, b.amount) == (37, Decimal(2277))
    assert {k: (v.trips, v.amount) for k, v in a.by_route.items()} == {
        "JIP-MLS,OW,FUL": (4, Decimal(280)), "JIP-MLS,OW,MTY": (4, Decimal(236)), "MLS-JIP,OW,FUL": (3, Decimal(210)),
        "MLS-JIP,OW,MT2": (6, Decimal(342)), "MLS-JIP,OW,MT4": (9, Decimal(504))}


def test_two_pairs_of_20ft_empties_are_found_in_the_first_pack(loaded):
    pairs = {by_container(loaded, n).assignment.pair_id for n in ("CAAU2443630", "SLVU3016562", "SEGU1565865", "CXDU2362967")}
    assert len(pairs) == 2
    assert by_container(loaded, "CAAU2443630").assignment.trip_count + by_container(loaded, "SLVU3016562").assignment.trip_count == 1


def test_hand_corrected_page_goes_to_the_pack_driver_and_is_flagged(loaded):
    mv = by_container(loaded, "ARKU2412440")
    assert mv.driver.key == D2 and mv.truck.plate == "NSA6128"
    assert {"DRIVER_MISMATCH", "TRUCK_MISMATCH", "REPRINT_SUSPECTED"} <= codes(mv)


@pytest.mark.parametrize("container, expected", [
    ("SEGU5160443", "OUTSIDE_PERIOD"), ("OOCU9799286", "REPRINT_SUSPECTED"), ("ZGLU2093344", "REPRINT_SUSPECTED"),
    ("MRKU0318280", "THIRD_PARTY_RECEIPT"), ("ZGLU2093642", "THIRD_PARTY_RECEIPT"), ("ARKU2395678", "MISSING_CUSTOMER"),
    ("CRXU8681568", "RATE_UNVERIFIED"), ("BEAU2897625", "UNPAIRED_20FT"), ("OOLU9230830", "CONTAINER_REPAIRED")])
def test_flags_land_on_the_right_pages(loaded, container, expected):
    assert expected in codes(by_container(loaded, container))


def test_clean_pages_carry_no_flags(loaded):
    assert codes(by_container(loaded, "TGCU0162377")) == set()
    assert codes(by_container(loaded, "OOLU9230830") ) <= {"CONTAINER_REPAIRED"}


def test_review_queue_only_holds_pages_with_warnings(loaded):
    n = repo.count_needing_review(loaded)
    assert 0 < n < 65
    queued = {m.container_no for m in loaded.scalars(repo.open_flags_query())}
    assert "TGCU0162377" not in queued and "SEGU5160443" in queued


def test_names_are_shown_with_proper_spacing(loaded):
    mv = by_container(loaded, "TGBU5438551")
    assert (mv.customer, mv.vessel) == ("TRIDENT FREIGHT SAUDI L L L C", "ZHONG GU NAN JING")


def test_reading_the_same_file_again_adds_nothing(loaded):
    again = ingest_pages(loaded, page_inputs("pack1"), SETTINGS)
    assert (again.pages, again.movements) == (0, 0)
    assert loaded.scalar(select(func.count()).select_from(Movement)) == 65


def test_a_rescanned_copy_is_recognised_as_duplicate(loaded):
    copy = page_inputs("pack1")
    for p in copy:
        p.sha256 = "another-scan-of-the-same-sheets"
    rep = ingest_pages(loaded, copy, SETTINGS)
    assert (rep.duplicates, rep.movements) == (28, 0)
    assert loaded.scalar(select(func.count()).select_from(Movement)) == 65
    assert "DUPLICATE_SCAN" in codes(by_container(loaded, "TGCU0162377"))


def test_driver_can_be_forced(session):
    ingest_pages(session, page_inputs("pack1"), SETTINGS, driver_key="MANUAL-1")
    assert {m.driver.key for m in session.scalars(select(Movement))} == {"MANUAL-1"}


def test_driver_name_set_by_hand_is_not_overwritten(session):
    repo.get_or_create_driver(session, D1, name="Sample Driver One")
    ingest_pages(session, page_inputs("pack1"), SETTINGS)
    assert by_container(session, "CRXU8681568").driver.name == "Sample Driver One"


def test_ocr_text_and_fields_are_kept_for_rereading(loaded):
    doc = by_container(loaded, "CRXU8681568").document
    assert '"boxes"' in doc.ocr_json and '"CRXU8681568"' in doc.fields_json

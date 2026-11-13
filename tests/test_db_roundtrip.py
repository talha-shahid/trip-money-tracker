from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from helpers import load_golden, mv
from tripmoney.config import load_settings
from tripmoney.db import ReviewFlag, TripAssignment, init_db, make_engine, make_session_factory
from tripmoney.db import repo
from tripmoney.domain import Direction, Status


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_golden_sheet_roundtrips_through_the_database(session):
    settings = load_settings()
    _, movements = load_golden()
    for m in movements:
        repo.add_movement(session, m)
    live = repo.reprice(session, settings)
    saved = repo.summary(session)
    assert (live.trips, live.amount) == (saved.trips, saved.amount) == (64, Decimal(3894))
    assert session.scalar(select(func.count()).select_from(TripAssignment)) == 80
    assert repo.summary(session, driver_key="DRV-4890").amount == Decimal(3894)
    assert repo.summary(session, driver_key="nobody").trips == 0


def test_repricing_is_idempotent(session):
    settings = load_settings()
    for m in load_golden()[1]:
        repo.add_movement(session, m)
    for _ in range(3):
        repo.reprice(session, settings)
    assert session.scalar(select(func.count()).select_from(TripAssignment)) == 80
    assert repo.summary(session).amount == Decimal(3894)


def test_review_flags_are_stored_and_replaced_not_duplicated(session):
    settings = load_settings()
    repo.add_movement(session, mv("1", size=20, at="2026-10-21 00:31"))  # lone 20' empty
    repo.add_movement(session, mv("2", size=40, status=Status.EMPTY, direction=Direction.JIP_MLS))
    for _ in range(2):
        repo.reprice(session, settings)
    codes = sorted(session.scalars(select(ReviewFlag.code)).all())
    assert codes == ["RATE_UNVERIFIED", "UNPAIRED_20FT"]


def test_one_driver_can_be_repriced_without_touching_another(session):
    settings = load_settings()
    repo.add_movement(session, mv("1", driver="A", truck="TA"))
    repo.add_movement(session, mv("2", driver="B", truck="TB"))
    repo.reprice(session, settings, driver_key="A")
    assert session.scalar(select(func.count()).select_from(TripAssignment)) == 1
    repo.reprice(session, settings)
    assert session.scalar(select(func.count()).select_from(TripAssignment)) == 2


def test_duplicate_eir_numbers_are_rejected(session):
    repo.add_movement(session, mv("1"), eir_no="696249")
    with pytest.raises(IntegrityError):
        repo.add_movement(session, mv("2"), eir_no="696249")

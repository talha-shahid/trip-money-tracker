"""Persistence helpers: store movements, run the rate engine, bind results to the trip log."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import domain, engine
from ..config import Settings
from . import models as m

_CHUNK = 500
_RULE_FLAG_TEXT = {
    "UNPAIRED_20FT": "20' empty with no partner box: priced as a full trip, please confirm",
    "RATE_UNVERIFIED": "this rate comes from the spec only, not yet confirmed against a paid trip sheet",
    "NO_RULE": "no rate rule matches this box (check size / direction / status)",
}


def get_or_create_driver(s: Session, key: str, **fields) -> m.Driver:
    row = s.scalar(select(m.Driver).where(m.Driver.key == key))
    if row is None:
        row = m.Driver(key=key, **fields)
        s.add(row)
        s.flush()
    return row


def get_or_create_truck(s: Session, plate: str, driver: m.Driver | None = None) -> m.Truck:
    row = s.scalar(select(m.Truck).where(m.Truck.plate == plate))
    if row is None:
        row = m.Truck(plate=plate, driver=driver)
        s.add(row)
        s.flush()
    return row


def add_movement(s: Session, mv: domain.Movement, *, eir_no: str | None = None,
                 document_id: int | None = None, **extra) -> m.Movement:
    driver = get_or_create_driver(s, mv.driver_key) if mv.driver_key else None
    truck = get_or_create_truck(s, mv.truck, driver) if mv.truck else None
    row = m.Movement(
        document_id=document_id, eir_no=eir_no, container_no=mv.container_no, size_ft=mv.size_ft,
        container_type=mv.container_type, status=mv.status.value, direction=mv.direction.value,
        occurred_at=mv.occurred_at, has_time=mv.has_time, customer=mv.customer, vessel=mv.vessel,
        booking_no=mv.booking_no, driver=driver, truck=truck, **extra,
    )
    s.add(row)
    s.flush()
    return row


def to_domain(row: m.Movement) -> domain.Movement:
    return domain.Movement(
        id=str(row.id), container_no=row.container_no, size_ft=row.size_ft,
        status=domain.Status(row.status), direction=domain.Direction(row.direction),
        occurred_at=row.occurred_at, has_time=row.has_time, container_type=row.container_type,
        customer=row.customer, vessel=row.vessel, booking_no=row.booking_no,
        driver_key=row.driver.key if row.driver else "", truck=row.truck.plate if row.truck else "",
    )


def _driver_filter(q, driver_key: str | None):
    return q.join(m.Driver, m.Movement.driver_id == m.Driver.id).where(m.Driver.key == driver_key) if driver_key else q


def reprice(s: Session, settings: Settings, *, driver_key: str | None = None) -> engine.Summary:
    """(Re)run the rate engine over stored movements and replace their assignments and rule flags.

    Safe to run repeatedly: it never creates duplicates.
    """
    everything = list(s.scalars(_driver_filter(select(m.Movement), driver_key).order_by(m.Movement.id)))
    void_ids = [r.id for r in everything if r.review_status == "void"]  # "not a trip": never priced
    rows = [r for r in everything if r.is_complete and r.review_status != "void"]  # incomplete pages wait for review
    for i in range(0, len(void_ids), _CHUNK):
        s.execute(delete(m.TripAssignment).where(m.TripAssignment.movement_id.in_(void_ids[i:i + _CHUNK])))
    result = engine.price([to_domain(r) for r in rows], settings.rate_table,
                          max_gap_minutes=settings.pairing_max_gap_minutes)
    ids = [r.id for r in rows]
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i:i + _CHUNK]
        s.execute(delete(m.TripAssignment).where(m.TripAssignment.movement_id.in_(chunk)))
        s.execute(delete(m.ReviewFlag).where(m.ReviewFlag.movement_id.in_(chunk), m.ReviewFlag.source == "rules"))
    for row, a in zip(rows, result):
        s.add(m.TripAssignment(
            movement_id=row.id, rule_id=a.rule_id, route_code=a.route_code, system_code=a.system_code,
            amount=a.amount, trip_count=a.trip_count, pair_id=a.pair_id, pair_role=a.pair_role,
            verified=a.verified, rate_version=a.rate_version,
        ))
        for code in a.flags:
            s.add(m.ReviewFlag(movement_id=row.id, code=code, source="rules", severity="warn",
                               message=_RULE_FLAG_TEXT.get(code, "")))
    s.flush()
    s.expire_all()  # objects loaded before still point at the replaced assignment rows
    return engine.summarize(result)


def summary(s: Session, *, driver_key: str | None = None) -> engine.Summary:
    """Totals read back from the saved trip log (same shape as the sheet's summary block)."""
    q = _driver_filter(select(m.TripAssignment).join(m.Movement, m.TripAssignment.movement_id == m.Movement.id), driver_key)
    out = engine.Summary()
    for a in s.scalars(q.where(m.TripAssignment.trip_count > 0)):
        amount = Decimal(str(a.amount))
        t = out.by_route.setdefault(a.route_code, engine.RouteTotal())
        t.trips += a.trip_count
        t.amount += amount
        out.trips += a.trip_count
        out.amount += amount
    return out


def load_lexicon(s: Session):
    """Names from config/lexicon.yaml plus everything reviewers have corrected."""
    from ..ingest.lexicon import Lexicon

    lex = Lexicon.load()
    for n in s.scalars(select(m.Name)):
        lex.add(n.kind, n.canonical)
    return lex


def learn_name(s: Session, kind: str, canonical: str) -> None:
    from ..textnorm import alnum_key, clean_text

    key, canonical = alnum_key(canonical), clean_text(canonical)
    if not key or kind not in ("customer", "vessel", "line", "remark"):
        return
    row = s.scalar(select(m.Name).where(m.Name.kind == kind, m.Name.key == key))
    if row is None:
        s.add(m.Name(kind=kind, key=key, canonical=canonical))
    else:
        row.canonical = canonical


def open_flags_query(driver_key: str | None = None):
    """Movements a person still has to look at: an unresolved warning/error and not yet approved."""
    q = (select(m.Movement).join(m.ReviewFlag, m.ReviewFlag.movement_id == m.Movement.id)
         .where(m.ReviewFlag.resolved.is_(False), m.ReviewFlag.severity.in_(("warn", "error")),
                m.Movement.review_status.notin_(("approved", "void"))).distinct())
    return _driver_filter(q, driver_key)


def count_needing_review(s: Session, driver_key: str | None = None) -> int:
    return len(s.scalars(open_flags_query(driver_key)).all())

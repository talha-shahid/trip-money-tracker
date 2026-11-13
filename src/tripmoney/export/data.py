"""Reads the trip log for one driver and period into plain rows ready to write."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m


def display_name(driver: m.Driver) -> str:
    """The driver's name, or a masked key. The raw Iqama appears only in the sheet's IQAMA NO cell."""
    if driver.name:
        return driver.name
    k = driver.key or ""
    return "driver ***" + k[-3:] if k.isdigit() and len(k) > 4 else k


@dataclass
class TripRow:
    n: int
    when: datetime
    container: str  # as printed, including any trailing letter
    size_label: str  # e.g. "GPD | 20"
    route: str  # blank for the 2nd box of a pair and for unpriced boxes
    customer: str
    trip_no: int  # 1 paid trip, 0 not paid
    amount: Decimal | None  # None where the sheet shows BLANK
    vessel: str
    status: str  # Empty | Full
    eir_no: str
    check: str  # "OK" or "REVIEW: CODE, CODE"
    movement_id: int
    pair_role: str | None
    rule_id: str | None = None


def _needs_review(mv: m.Movement) -> list[str]:
    if mv.review_status == "approved":
        return []
    codes = [f.code for f in mv.flags if not f.resolved and f.severity in ("warn", "error")]
    if not mv.is_complete and "INCOMPLETE" not in codes:
        codes.append("INCOMPLETE")
    return sorted(set(codes))


def _order(rows: list[tuple[m.Movement, m.TripAssignment | None]]):
    """By time, with the second box of a pair directly under the first (as on the paper sheet)."""
    rows = sorted(rows, key=lambda r: (r[0].occurred_at or datetime.min, r[0].id))
    seconds = {a.pair_id: (mv, a) for mv, a in rows if a and a.pair_role == "second"}
    out, seen = [], set()
    for mv, a in rows:
        if mv.id in seen:
            continue
        out.append((mv, a))
        seen.add(mv.id)
        if a and a.pair_role == "first" and a.pair_id in seconds:
            sm, sa = seconds[a.pair_id]
            out.append((sm, sa))
            seen.add(sm.id)
    return out


def load_trip_rows(s: Session, driver_key: str, start: date | None, end: date | None, type_codes: dict[str, str]):
    """-> (rows inside the period, movements left out because they fall outside it)."""
    q = (select(m.Movement).join(m.Driver, m.Movement.driver_id == m.Driver.id)
         .where(m.Driver.key == driver_key, m.Movement.review_status != "void")
         .order_by(m.Movement.occurred_at, m.Movement.id))
    inside, outside = [], []
    for mv in s.scalars(q):
        d = mv.occurred_at.date() if mv.occurred_at else None
        if d is not None and ((start and d < start) or (end and d > end)):
            outside.append(mv)
        else:
            inside.append((mv, mv.assignment))
    rows = []
    for i, (mv, a) in enumerate(_order(inside), 1):
        paid = bool(a and a.trip_count > 0 and a.route_code)
        second = bool(a and a.pair_role == "second")
        size = f"{type_codes.get(mv.container_type, mv.container_type)} | {mv.size_ft}" if mv.size_ft else mv.container_type
        review = _needs_review(mv)
        rows.append(TripRow(
            n=i, when=mv.occurred_at, container=mv.container_no + (mv.container_suffix or ""), size_label=size,
            route=a.route_code if paid and not second else "",
            customer=mv.customer, trip_no=a.trip_count if a else 0,
            amount=Decimal(str(a.amount)) if (a and paid) else None, vessel=mv.vessel,
            status=(mv.status or "").title(), eir_no=mv.eir_no or "",
            check="OK" if not review else "REVIEW: " + ", ".join(review), movement_id=mv.id,
            pair_role=a.pair_role if a else None, rule_id=a.rule_id if a else None))
    return rows, outside

"""Container tracking: the IN/OUT history of every box, who is in the depot now, and how long."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import models as m
from .textnorm import alnum_key

IN_DEPOT, LEFT_DEPOT, AT_TERMINAL, UNKNOWN = "IN DEPOT", "LEFT DEPOT", "AT TERMINAL", "UNKNOWN"


@dataclass
class Event:
    movement_id: int
    when: datetime
    kind: str  # "IN" (received into the depot) | "OUT" (released from it) | "TERMINAL" (third-party port receipt)
    status: str | None  # FULL | EMPTY
    size_ft: int | None
    customer: str
    vessel: str
    eir_no: str
    driver: str
    truck: str
    note: str = ""


@dataclass
class ContainerHistory:
    container_no: str
    events: list[Event] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    @property
    def last(self) -> Event | None:
        return self.events[-1] if self.events else None

    @property
    def current(self) -> str:
        if not self.last:
            return UNKNOWN
        return {"IN": IN_DEPOT, "OUT": LEFT_DEPOT, "TERMINAL": AT_TERMINAL}[self.last.kind]

    def days_in_depot(self, as_of: datetime) -> float | None:
        """Days since the latest IN, if the box is still in the depot."""
        if self.current != IN_DEPOT:
            return None
        return round((as_of - self.last.when).total_seconds() / 86400, 1)

    def stays(self, as_of: datetime) -> list[tuple[Event, Event | None, float]]:
        """(IN, matching OUT or None, days) for every completed or ongoing stay."""
        out, pending = [], None
        for e in self.events:
            if e.kind == "IN":
                pending = e
            elif e.kind == "OUT" and pending is not None:
                out.append((pending, e, round((e.when - pending.when).total_seconds() / 86400, 1)))
                pending = None
        if pending is not None:
            out.append((pending, None, round((as_of - pending.when).total_seconds() / 86400, 1)))
        return out


def _kind(mv: m.Movement) -> str:
    if mv.source_doc_type in ("dpworld", "rsgt"):
        return "TERMINAL"
    return "IN" if mv.direction == "JIP-MLS" else "OUT"


def _event(mv: m.Movement) -> Event:
    return Event(mv.id, mv.occurred_at, _kind(mv), mv.status, mv.size_ft, mv.customer, mv.vessel, mv.eir_no or "",
                 (mv.driver.name or "") if mv.driver else "", mv.truck.plate if mv.truck else "",
                 "third-party terminal receipt" if _kind(mv) == "TERMINAL" else "")


def _build(container_no: str, rows: list[m.Movement]) -> ContainerHistory:
    h = ContainerHistory(container_no, [_event(r) for r in sorted(rows, key=lambda r: (r.occurred_at, r.id))])
    prev = None
    for e in h.events:
        if prev and prev.kind == e.kind and e.kind in ("IN", "OUT"):
            h.anomalies.append(f"{e.kind} twice in a row ({prev.when:%d %b %H:%M} and {e.when:%d %b %H:%M}): a scan is missing")
        if e.kind != "TERMINAL":
            prev = e
    return h


def container_history(s: Session, container_no: str) -> ContainerHistory | None:
    key = alnum_key(container_no)[:11]
    rows = list(s.scalars(select(m.Movement).where(m.Movement.container_no == key, m.Movement.occurred_at.is_not(None),
                                                                  m.Movement.review_status != "void")))
    return _build(key, rows) if rows else None


def _all_histories(s: Session) -> list[ContainerHistory]:
    groups: dict[str, list[m.Movement]] = {}
    for r in s.scalars(select(m.Movement).where(m.Movement.occurred_at.is_not(None), m.Movement.container_no != "",
                                                       m.Movement.review_status != "void")):
        groups.setdefault(r.container_no, []).append(r)
    return [_build(k, v) for k, v in sorted(groups.items())]


def depot_inventory(s: Session, as_of: datetime | None = None) -> list[tuple[ContainerHistory, float]]:
    """Boxes whose latest event is an IN, oldest first: (history, days in depot)."""
    as_of = as_of or datetime.now()
    rows = [(h, h.days_in_depot(as_of)) for h in _all_histories(s) if h.current == IN_DEPOT]
    return sorted(rows, key=lambda r: -r[1])


def search(s: Session, text: str, limit: int = 100) -> list[ContainerHistory]:
    """Find boxes by (part of) container number, customer, vessel or EIR number."""
    t = text.strip()
    if not t:
        return []
    like, key = f"%{t}%", f"%{alnum_key(t)}%"
    q = select(m.Movement.container_no).where(or_(
        m.Movement.container_no.like(key), m.Movement.customer.ilike(like), m.Movement.vessel.ilike(like),
        m.Movement.eir_no == t), m.Movement.review_status != "void").distinct().limit(limit)
    out = []
    for no in s.scalars(q):
        h = container_history(s, no)
        if h:
            out.append(h)
    return out

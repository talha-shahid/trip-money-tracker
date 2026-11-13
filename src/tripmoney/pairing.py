"""Pairing of 20' empties: two boxes on one truck count as ONE paid trip."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from .domain import Movement
from .textnorm import alnum_key


@dataclass(frozen=True)
class PairResult:
    pair_id: str
    role: str  # "first" | "second" | "lone"


def _group_key(m: Movement):
    # Same driver/truck, same direction/size/status, same booking (or customer+vessel).
    return (m.driver_key, m.truck, m.direction, m.size_ft, m.status, alnum_key(m.booking_no) or f"{alnum_key(m.customer)}|{alnum_key(m.vessel)}")


def _gap_minutes(a: Movement, b: Movement) -> float:
    if not (a.has_time and b.has_time):  # date-only data: same day counts as together
        return 0.0 if a.occurred_at.date() == b.occurred_at.date() else float("inf")
    return abs((b.occurred_at - a.occurred_at).total_seconds()) / 60


def pair_movements(movements: Sequence[Movement], max_gap_minutes: int = 60) -> dict[str, PairResult]:
    """Greedy pairing of consecutive boxes (by time) inside each group.

    An odd box out is returned with role "lone" so the caller can flag it.
    """
    groups: dict = defaultdict(list)
    for idx, m in enumerate(movements):
        groups[_group_key(m)].append((m.occurred_at, idx, m))

    out: dict[str, PairResult] = {}
    for items in groups.values():
        items.sort(key=lambda t: (t[0], t[1]))
        i = 0
        while i < len(items):
            a = items[i][2]
            if i + 1 < len(items) and _gap_minutes(a, items[i + 1][2]) <= max_gap_minutes:
                b = items[i + 1][2]
                pid = f"{a.id}+{b.id}"
                out[a.id] = PairResult(pid, "first")
                out[b.id] = PairResult(pid, "second")
                i += 2
            else:
                out[a.id] = PairResult(a.id, "lone")
                i += 1
    return out

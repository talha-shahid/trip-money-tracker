"""Rule engine: movements in, priced assignments out (spec workflow step 3)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from .domain import Assignment, Movement
from .pairing import pair_movements
from .rules import RateTable, Rule

FLAG_NO_RULE = "NO_RULE"
FLAG_UNPAIRED = "UNPAIRED_20FT"
FLAG_UNVERIFIED = "RATE_UNVERIFIED"


def _assignment(m: Movement, rule: Rule, table: RateTable, *, trip_count: int, amount: Decimal,
                pair_id=None, pair_role=None) -> Assignment:
    flags = [] if rule.verified else [FLAG_UNVERIFIED]
    return Assignment(
        movement_id=m.id, rule_id=rule.id, route_code=rule.route_code, system_code=rule.system_code,
        amount=amount, trip_count=trip_count, pair_id=pair_id, pair_role=pair_role,
        verified=rule.verified, rate_version=table.version, flags=flags,
    )


def price(movements: Sequence[Movement], table: RateTable, *, max_gap_minutes: int = 60) -> list[Assignment]:
    """Assign a rate to every movement. Output order matches input order."""
    result: dict[str, Assignment] = {}
    paired: dict[str, list[tuple[Movement, Rule]]] = defaultdict(list)

    for m in movements:
        rule = table.find(m)
        if rule is None:
            result[m.id] = Assignment(m.id, None, None, None, Decimal(0), 0, verified=False,
                                      rate_version=table.version, flags=[FLAG_NO_RULE])
        elif rule.unit == "pair":
            paired[rule.id].append((m, rule))
        else:
            result[m.id] = _assignment(m, rule, table, trip_count=1, amount=rule.amount)

    for items in paired.values():
        info = pair_movements([m for m, _ in items], max_gap_minutes)
        for m, rule in items:
            p = info[m.id]
            if p.role == "second":
                result[m.id] = _assignment(m, rule, table, trip_count=0, amount=Decimal(0),
                                           pair_id=p.pair_id, pair_role="second")
            else:
                a = _assignment(m, rule, table, trip_count=1, amount=rule.amount,
                                pair_id=p.pair_id, pair_role=p.role)
                if p.role == "lone":
                    a.flags.append(FLAG_UNPAIRED)  # priced as a trip, but a human should look
                result[m.id] = a

    return [result[m.id] for m in movements]


@dataclass
class RouteTotal:
    trips: int = 0
    amount: Decimal = Decimal(0)


@dataclass
class Summary:
    by_route: dict[str, RouteTotal] = field(default_factory=dict)
    trips: int = 0
    amount: Decimal = Decimal(0)


def summarize(assignments: Sequence[Assignment]) -> Summary:
    """Same numbers as the trip sheet's summary block: trips and amount per route + totals."""
    s = Summary()
    for a in assignments:
        if a.trip_count <= 0 or a.route_code is None:
            continue
        t = s.by_route.setdefault(a.route_code, RouteTotal())
        t.trips += a.trip_count
        t.amount += a.amount
        s.trips += a.trip_count
        s.amount += a.amount
    return s

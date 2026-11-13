"""Rate rules and the ordered rate table."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .domain import Direction, Movement, Status


@dataclass(frozen=True)
class Rule:
    id: str
    route_code: str
    system_code: str
    spec_code: str
    amount: Decimal
    unit: str = "container"  # "container" | "pair"
    direction: Direction | None = None  # None = any
    status: Status | None = None
    size_ft: int | None = None
    verified: bool = True
    effective_from: date = date.min
    effective_to: date | None = None
    note: str = ""

    def matches(self, m: Movement) -> bool:
        d = m.occurred_at.date()
        if d < self.effective_from:
            return False
        if self.effective_to is not None and d > self.effective_to:
            return False
        if self.direction is not None and m.direction != self.direction:
            return False
        if self.status is not None and m.status != self.status:
            return False
        if self.size_ft is not None and m.size_ft != self.size_ft:
            return False
        return True


class RateTable:
    """Ordered rules; the first match wins."""

    def __init__(self, rules, version: str = "unversioned"):
        self.rules = tuple(rules)
        self.version = version
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids in rate table")

    def find(self, m: Movement) -> Rule | None:
        for rule in self.rules:
            if rule.matches(m):
                return rule
        return None

"""Plain data types shared by the rate engine, the DB layer and (later) OCR."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Direction(str, Enum):
    MLS_JIP = "MLS-JIP"  # depot -> port  (EIR "OUT")
    JIP_MLS = "JIP-MLS"  # port -> depot  (EIR "IN")


class Status(str, Enum):
    FULL = "FULL"
    EMPTY = "EMPTY"


@dataclass
class Movement:
    """One container movement as read from an EIR (or a trip-sheet row)."""

    id: str
    container_no: str
    size_ft: int
    status: Status
    direction: Direction
    occurred_at: datetime
    has_time: bool = True  # False when only the date is known
    container_type: str = "GP"
    customer: str = ""
    vessel: str = ""
    booking_no: str = ""
    driver_key: str = ""
    truck: str = ""


@dataclass
class Assignment:
    """Result of pricing one movement."""

    movement_id: str
    rule_id: str | None
    route_code: str | None
    system_code: str | None
    amount: Decimal
    trip_count: int  # 1 = paid trip, 0 = no payment (e.g. 2nd box of a pair)
    pair_id: str | None = None
    pair_role: str | None = None  # "first" | "second" | "lone"
    verified: bool = True
    rate_version: str = ""
    flags: list[str] = field(default_factory=list)

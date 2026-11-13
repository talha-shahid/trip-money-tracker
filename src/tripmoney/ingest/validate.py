"""Cross-page checks: who drove, is the date in the pay period, is this a reprint."""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from ..period import pay_period
from .parsed import INFO, WARN, ParsedPage


@dataclass(frozen=True)
class Resolved:
    driver_key: str  # Iqama number (stable id) or a fallback
    driver_name: str
    iqama: str
    truck: str


def _mask(iqama: str) -> str:
    return "***" + iqama[-3:] if iqama else "none"


def resolve_pack(pages: list[ParsedPage], *, min_support: int = 3, forced_key: str | None = None) -> list[Resolved]:
    """Decide the driver and truck of every page in one scanned pack.

    A driver is whoever's Iqama number appears on at least `min_support` pages. A page showing a
    different Iqama (a hand-corrected sheet, a misread digit) is assigned to the pack's main driver
    and flagged. Terminal receipts carry only a truck plate, so they are matched by plate.
    """
    counts = Counter(p.iqama for p in pages if p.iqama)
    drivers = [iq for iq, n in counts.most_common() if n >= min_support] or [iq for iq, _ in counts.most_common(1)]
    info: dict[str, tuple[str, str]] = {}
    for iq in drivers:
        names = Counter(p.driver_name for p in pages if p.iqama == iq and p.driver_name)
        trucks = Counter(p.truck for p in pages if p.iqama == iq and p.truck)
        info[iq] = (names.most_common(1)[0][0] if names else "", trucks.most_common(1)[0][0] if trucks else "")
    main = drivers[0] if drivers else ""

    out: list[Resolved] = []
    for p in pages:
        if forced_key:
            iq = forced_key
            name, truck = next(iter(info.values()), ("", ""))
        else:
            iq = p.iqama if p.iqama in info else next((d for d in drivers if info[d][1] and info[d][1] == p.truck), main)
            name, truck = info.get(iq, ("", ""))
            if p.iqama and p.iqama not in info:
                p.flag("DRIVER_MISMATCH", WARN, f"page shows Iqama {_mask(p.iqama)} but belongs to this pack's driver; "
                       "possibly hand-corrected", "validator")
            if not iq:
                iq = "UNKNOWN"
                p.flag("NO_DRIVER", "error", "no driver could be identified for this page", "validator")
        if p.truck and truck and p.truck != truck:
            p.flag("TRUCK_MISMATCH", WARN, f"page shows truck '{p.truck_raw}', using this driver's truck {truck}", "validator")
        out.append(Resolved(iq, name, iq if iq != "UNKNOWN" else "", truck or p.truck))
    return out


def validate_pack(pages: list[ParsedPage], *, pay_period_start_day: int = 10) -> None:
    dated = [p.occurred_at for p in pages if p.occurred_at]
    ref = None
    if dated:
        mid = sorted(dated)[len(dated) // 2]
        ref = pay_period(mid.date(), pay_period_start_day)
    for p in pages:
        if ref and p.occurred_at and not (ref[0] <= p.occurred_at.date() <= ref[1]):
            p.flag("OUTSIDE_PERIOD", WARN, f"{p.occurred_at:%d %b %Y} is outside this pack's pay period "
                   f"({ref[0]:%d %b} to {ref[1]:%d %b})", "validator")
        if p.occurred_at and p.printed_at:
            gap = p.printed_at - p.occurred_at
            if gap > timedelta(hours=24):
                p.flag("REPRINT_SUSPECTED", WARN, f"printed {gap.total_seconds() / 3600:.0f} h after the move: "
                       "likely a reprint or duplicate copy", "validator")
            elif gap < -timedelta(hours=1):
                p.flag("TIME_MISMATCH", WARN, "move time is later than the print time: check AM/PM", "validator")

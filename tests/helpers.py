import csv
from datetime import datetime
from pathlib import Path

from tripmoney.domain import Direction, Movement, Status

FIXTURE = Path(__file__).parent / "fixtures" / "golden_trip_sheet.csv"


def mv(id, *, size=40, status=Status.EMPTY, direction=Direction.MLS_JIP, at="2026-10-20 10:00",
       customer="ACME", vessel="V1", booking="", driver="D1", truck="T1", has_time=True, ctype="GP"):
    return Movement(id=id, container_no=f"TEST{id}", size_ft=size, status=status, direction=direction,
                    occurred_at=datetime.fromisoformat(at), has_time=has_time, container_type=ctype,
                    customer=customer, vessel=vessel, booking_no=booking, driver_key=driver, truck=truck)


def load_golden():
    with FIXTURE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    movements = [
        Movement(id=f"g{r['sr']}", container_no=r["container_no"], size_ft=int(r["size_ft"]),
                 status=Status(r["status"]), direction=Direction(r["direction"]),
                 occurred_at=datetime.fromisoformat(r["date"]), has_time=False,
                 container_type=r["type_code"], customer=r["customer"], vessel=r["vessel"],
                 driver_key=r["driver_key"], truck=r["truck"])
        for r in rows
    ]
    return rows, movements

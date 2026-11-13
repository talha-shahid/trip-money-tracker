"""End-to-end demo: prices the real trip sheet through the engine AND the database,
then checks the result against what the sheet itself says.

    python scripts/demo.py

Writes data/demo.db (recreated on every run). Exit code is 0 on MATCH, 1 otherwise.
"""
import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from tripmoney.config import load_settings
from tripmoney.db import init_db, make_engine, make_session_factory, repo
from tripmoney.domain import Direction, Movement, Status

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "golden_trip_sheet.csv"
DB = ROOT / "data" / "demo.db"


def main() -> int:
    settings = load_settings()
    with FIXTURE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    DB.unlink(missing_ok=True)
    engine = make_engine(f"sqlite:///{DB.as_posix()}")
    init_db(engine)
    with make_session_factory(engine)() as s:
        for r in rows:
            repo.add_movement(s, Movement(
                id=f"sheet-{r['sr']}", container_no=r["container_no"], size_ft=int(r["size_ft"]),
                status=Status(r["status"]), direction=Direction(r["direction"]),
                occurred_at=datetime.fromisoformat(r["date"]), has_time=False,
                container_type=r["type_code"], customer=r["customer"], vessel=r["vessel"],
                driver_key=r["driver_key"], truck=r["truck"]))
        repo.reprice(s, settings)
        saved = repo.summary(s)  # read back from the database, not from memory
        s.commit()

    exp_trips = sum(int(r["expected_trip_no"]) for r in rows)
    exp_amount = sum(Decimal(r["expected_amount"]) for r in rows)

    print(f"{'Route code':<18}{'Trips':>6}{'Amount':>9}")
    for route, t in sorted(saved.by_route.items()):
        print(f"{route:<18}{t.trips:>6}{t.amount:>9.2f}")
    print(f"{'TOTAL':<18}{saved.trips:>6}{saved.amount:>9.2f}")
    print(f"{'Sheet says':<18}{exp_trips:>6}{exp_amount:>9.2f}")
    ok = (saved.trips, saved.amount) == (exp_trips, exp_amount)
    print("MATCH" if ok else "MISMATCH")
    print(f"Saved {len(rows)} movements to {DB.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

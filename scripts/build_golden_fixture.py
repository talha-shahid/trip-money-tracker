"""Builds tests/fixtures/golden_trip_sheet.csv from a MAMAS driver trip-sheet .xlsx.

Only what the rate engine needs is copied (no Iqama / employee numbers). The driver is
replaced by a neutral key. The sheet's own route, trip no and amount are kept as the
EXPECTED values the engine must reproduce.

Direction is not a column in the sheet, so it is taken from the typed route
(MLS-JIP / JIP-MLS). Second boxes of a 20' pair have no route typed; they inherit the
previous row's direction, which is how the EIR's IN/OUT would label them anyway.

Usage: python scripts/build_golden_fixture.py path/to/trip_sheet.xlsx
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden_trip_sheet.csv"


def build(xlsx: str) -> int:
    ws = load_workbook(xlsx, data_only=True).active
    truck = str(ws["B5"].value)
    rows, prev_direction = [], None
    for r in ws.iter_rows(min_row=7, values_only=True):
        sr, date, cont, size, _waybill, route, customer, trip_no, amount, vessel, status = r[:11]
        if not cont:
            continue
        type_code, size_ft = (p.strip() for p in str(size).split("|"))
        if route:
            prev_direction = str(route).split(",")[0]
        rows.append({
            "sr": sr,
            "date": datetime.strptime(date, "%d-%b-%y").date().isoformat(),
            "container_no": cont, "type_code": type_code, "size_ft": size_ft,
            "customer": customer, "vessel": vessel, "status": str(status).upper(),
            "direction": prev_direction, "driver_key": f"DRV-{truck}", "truck": truck,
            "expected_route": route or "", "expected_trip_no": trip_no,
            "expected_amount": 0 if amount == "BLANK" else amount,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    print(f"wrote {build(sys.argv[1])} rows -> {OUT}")

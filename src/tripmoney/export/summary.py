"""Period summary workbook (all drivers), CSV of the trip log, and the one-call export of everything."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import xlsxwriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import models as m
from .data import TripRow, display_name, load_trip_rows
from .trip_sheet import _num, write_trip_sheet


@dataclass
class DriverBlock:
    driver: m.Driver
    truck: str
    rows: list[TripRow]
    excluded: list[m.Movement]

    @property
    def trips(self) -> int:
        return sum(r.trip_no for r in self.rows)

    @property
    def amount(self) -> Decimal:
        return sum((r.amount or Decimal(0) for r in self.rows), Decimal(0))

    @property
    def to_review(self) -> int:
        return sum(1 for r in self.rows if r.check != "OK")


def _truck_of(s: Session, d: m.Driver) -> str:
    t = s.scalar(select(m.Truck).where(m.Truck.driver_id == d.id).order_by(m.Truck.id))
    return t.plate if t else ""


def driver_blocks(s: Session, settings: Settings, start: date | None, end: date | None,
                  driver_key: str | None = None) -> list[DriverBlock]:
    q = select(m.Driver).order_by(m.Driver.name, m.Driver.key)
    if driver_key:
        q = q.where(m.Driver.key == driver_key)
    out = []
    for d in s.scalars(q):
        rows, excluded = load_trip_rows(s, d.key, start, end, settings.type_codes or {})
        if rows or excluded:
            out.append(DriverBlock(d, _truck_of(s, d), rows, excluded))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "driver"


def write_period_summary(path: str | Path, blocks: list[DriverBlock], settings: Settings,
                         period: tuple[date | None, date | None]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path))
    f = lambda **k: wb.add_format({"font_name": "Arial", "font_size": 10, **k})  # noqa: E731
    bold, head, cell, plain = f(bold=True), f(bold=True, bg_color="#D9D9D9", border=1), f(border=1), f()
    ws = wb.add_worksheet("Summary")
    label = f"{period[0]:%d %b %Y} to {period[1]:%d %b %Y}" if period[0] and period[1] else "all dates"
    ws.write("A1", f"Trip money summary: {label}", f(bold=True, font_size=14))
    ws.write_row("A3", ["DRIVER", "TRUCK", "TRIPS", "AMOUNT", "TO REVIEW", "OUTSIDE PERIOD"], head)
    for i, b in enumerate(blocks):
        x = 4 + i
        ws.write_row(f"A{x}", [display_name(b.driver), b.truck], cell)
        ws.write_number(f"C{x}", b.trips, cell)
        ws.write_number(f"D{x}", _num(b.amount), cell)
        ws.write_number(f"E{x}", b.to_review, cell)
        ws.write_number(f"F{x}", len(b.excluded), cell)
    end = 3 + len(blocks)
    tot = end + 1
    ws.write(f"A{tot}", "TOTAL", bold)
    for col, val in zip("CDEF", (sum(b.trips for b in blocks), _num(sum((b.amount for b in blocks), Decimal(0))),
                                 sum(b.to_review for b in blocks), sum(len(b.excluded) for b in blocks))):
        ws.write_formula(f"{col}{tot}", f"=SUM({col}4:{col}{max(end, 4)})", bold, val)
    ws.set_column("A:A", 32); ws.set_column("B:F", 16)

    rt = wb.add_worksheet("By route")
    routes = [r.route_code for r in settings.rate_table.rules]
    rt.write_row("A1", ["DRIVER"] + [f"{r} (trips)" for r in routes] + [f"{r} (amount)" for r in routes], head)
    for i, b in enumerate(blocks, 2):
        rt.write(f"A{i}", display_name(b.driver), cell)
        for j, route in enumerate(routes):
            rows = [r for r in b.rows if r.route == route]
            rt.write_number(i - 1, 1 + j, sum(r.trip_no for r in rows), cell)
            rt.write_number(i - 1, 1 + len(routes) + j, _num(sum((r.amount or Decimal(0) for r in rows), Decimal(0))), cell)
    rt.set_column("A:A", 32); rt.set_column(1, 2 * len(routes), 22)
    wb.close()
    return path


def write_csv(path: str | Path, blocks: list[DriverBlock]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel opens UTF-8 correctly
        w = csv.writer(fh)
        w.writerow(["driver", "truck", "sr", "date", "time", "container", "size", "trip_from", "customer", "trip_no",
                    "amount", "vessel", "status", "eir_no", "check"])
        for b in blocks:
            for r in b.rows:
                w.writerow([display_name(b.driver), b.truck, r.n, f"{r.when:%Y-%m-%d}", f"{r.when:%H:%M}",
                            r.container, r.size_label, r.route, r.customer, r.trip_no,
                            "" if r.amount is None else _num(r.amount), r.vessel, r.status, r.eir_no, r.check])
    return path


def export_all(s: Session, settings: Settings, out_dir: str | Path, start: date | None, end: date | None,
               driver_key: str | None = None) -> list[Path]:
    """One trip sheet per driver + a summary workbook + a CSV, for the given pay period."""
    out_dir = Path(out_dir)
    tag = f"{start:%Y-%m-%d}_to_{end:%Y-%m-%d}" if start and end else "all"
    blocks = driver_blocks(s, settings, start, end, driver_key)
    files, used = [], set()
    for b in blocks:
        base = _slug(display_name(b.driver))
        if base in used:  # two drivers with the same (or no) name must not overwrite each other
            base = f"{base}_{b.driver.key[-3:]}"
        used.add(base)
        files.append(write_trip_sheet(out_dir / f"{base}_{tag}.xlsx", b.driver, b.truck, b.rows, settings,
                                      period=(start, end), excluded=b.excluded))
    files.append(write_period_summary(out_dir / f"summary_{tag}.xlsx", blocks, settings, (start, end)))
    files.append(write_csv(out_dir / f"trips_{tag}.csv", blocks))
    return files

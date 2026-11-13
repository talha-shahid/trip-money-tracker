"""Writes the per-driver trip sheet in the MAMAS Transport Division layout.

Same columns, header block, tie-out cells and summary block as the paper-era sheet, but self-contained:
rates come from a ROUTE sheet inside the workbook (no links to other files), and every total is a live
formula, so changing a route code or a rate in Excel recalculates everything. Cached values are stored
too, so the numbers also show in viewers that do not recalculate.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import xlsxwriter

from ..config import Settings
from ..db import models as m
from .data import TripRow, display_name

FIRST = 7  # first data row (Excel row number), as in the original sheet


def _num(d: Decimal | None):
    if d is None:
        return "BLANK"
    return int(d) if d == d.to_integral_value() else float(d)


def write_trip_sheet(path: str | Path, driver: m.Driver, truck: str, rows: list[TripRow], settings: Settings, *,
                     period: tuple[date | None, date | None] = (None, None),
                     excluded: list[m.Movement] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path))
    wb.set_calc_mode("auto")
    f = lambda **k: wb.add_format({"font_name": "Arial", "font_size": 10, **k})  # noqa: E731
    title, bold, plain = f(bold=True, font_size=14), f(bold=True), f()
    head = f(bold=True, bg_color="#D9D9D9", border=1)
    cell = f(border=1)
    datef, numf = f(border=1, num_format="dd-mmm-yy"), f(border=1, align="center")
    warn = f(border=1, font_color="#9C0006", bg_color="#FFC7CE")
    okf = f(bold=True, font_color="#006100")

    ws = wb.add_worksheet("Trip Sheet")
    rt = wb.add_worksheet("ROUTE")
    last = FIRST + max(len(rows), 1) - 1

    # ---- ROUTE sheet: the rate table the AMOUNT column looks up --------------------------------------
    rt.write_row(0, 0, ["TRIP FROM", "AMOUNT", "SYSTEM CODE", "PER", "VERIFIED", "NOTE"], head)
    rules = {r.route_code: r for r in settings.rate_table.rules}
    for i, r in enumerate(rules.values(), 1):
        rt.write_row(i, 0, [r.route_code, int(r.amount) if r.amount == r.amount.to_integral_value() else float(r.amount),
                            r.system_code, r.unit, "yes" if r.verified else "NO (spec only)", r.note], plain)
    rt.set_column("A:A", 18)
    rt.set_column("B:E", 14)
    rt.set_column("F:F", 70)
    rt.write(len(rules) + 2, 0, f"Rate table version {settings.rate_table.version}. Change an AMOUNT here and the "
             "Trip Sheet recalculates.", plain)

    # ---- header block --------------------------------------------------------------------------------
    ws.write("A1", "        MAMAS TRANSPORT DIVISION TRIP SHEET   ", title)
    if period[0] and period[1]:
        ws.write("F1", f"Period {period[0]:%d %b %Y} to {period[1]:%d %b %Y}", bold)
    trips = sum(r.trip_no for r in rows)
    amount = sum((r.amount or Decimal(0) for r in rows), Decimal(0))
    review_rows = sum(1 for r in rows if r.check != "OK")
    ws.write("A2", "IQAMA NO", bold); ws.write("B2", driver.iqama_no or "", bold)
    ws.write("C2", "Sponsor", bold); ws.write("D2", driver.sponsor or "", bold)
    ws.write("A3", "  NAME", bold); ws.write("B3", display_name(driver), bold)
    ws.write("C3", "Trips", bold); ws.write_formula("D3", "=SUM(H:H)", bold, trips)
    ws.write_formula("E3", '=IF(D3=O5,"ok","need check")', okf, "ok")
    ws.write("A4", "EMP NO", bold); ws.write("B4", driver.emp_no or "", bold)
    ws.write("C4", "Amount", bold); ws.write_formula("D4", "=SUM(I:I)", bold, _num(amount))
    ws.write_formula("E4", '=IF(D4=P5,"ok","need check")', okf, "ok")
    ws.write("A5", "  TRUCK NO", bold); ws.write("B5", truck, bold)
    ws.write("C5", "To review", bold)
    ws.write_formula("D5", '=COUNTIF(M7:M2000,"REVIEW*")', bold, review_rows)
    # What the system calculated, to spot hand edits made later in Excel. These sit in J:L on purpose:
    # the totals are SUM(H:H) / SUM(I:I) over whole columns, so nothing numeric may live in H or I.
    ws.write("J3", "System trips", plain); ws.write("K3", trips, plain)
    ws.write_formula("L3", '=IF(D3=K3,"ok","differs")', okf, "ok")
    ws.write("J4", "System amount", plain); ws.write("K4", _num(amount), plain)
    ws.write_formula("L4", '=IF(D4=K4,"ok","differs")', okf, "ok")

    for c, h in enumerate(["SR # NO", "DATE", "CONT NO", "Cont Size", "WAYBILL NO", "TRIP FROM", "CUSTOMER",
                           "Trip No", "AMOUNT ", "VESSEL", "STATUS", "EIR NO", "CHECK"]):
        ws.write(5, c, h, head)

    # ---- trips ---------------------------------------------------------------------------------------
    for i, r in enumerate(rows):
        x = FIRST + i  # Excel row number
        ws.write_number(x - 1, 0, r.n, cell)
        ws.write_datetime(x - 1, 1, r.when, datef)
        ws.write(x - 1, 2, r.container, cell)
        ws.write(x - 1, 3, r.size_label, cell)
        ws.write_blank(x - 1, 4, None, cell)
        ws.write(x - 1, 5, r.route, cell)
        ws.write(x - 1, 6, r.customer, cell)
        ws.write_formula(x - 1, 7, f"=COUNTA(F{x})", numf, 1 if r.route else 0)
        ws.write_formula(x - 1, 8, f'=IFERROR(ROUND(IF(H{x}=0,"blank",VLOOKUP(F{x},ROUTE!$A$1:$C$50,2,0)),0),"BLANK")',
                         numf, _num(r.amount) if r.route else "BLANK")
        ws.write(x - 1, 9, r.vessel, cell)
        ws.write(x - 1, 10, r.status, cell)
        ws.write(x - 1, 11, r.eir_no, cell)
        ws.write(x - 1, 12, r.check, cell if r.check == "OK" else warn)
    ws.autofilter(5, 0, last - 1, 12)
    ws.freeze_panes(6, 0)

    # ---- summary block (N:P) and route x customer table (R:T), as on the original -------------------
    by_route = Counter()
    amt_route: Counter = Counter()
    for r in rows:
        if r.route:
            by_route[r.route] += r.trip_no
            amt_route[r.route] += r.amount or Decimal(0)
    ws.write("N5", "Total", bold)
    ws.write_formula("O5", "=SUM(O8:O2000)", bold, trips)
    ws.write_formula("P5", "=SUM(P8:P2000)", bold, _num(amount))
    ws.write_formula("O6", '=B3&"  TRIP SUMMARY"', plain, f"{display_name(driver)}  TRIP SUMMARY")
    ws.write_row("N7", ["TRIP FROM", "Qty", "amount"], bold)
    for i, route in enumerate(rules):
        x = 8 + i
        ws.write(f"N{x}", route, plain)
        ws.write_formula(f"O{x}", f"=COUNTIF(F:F,N{x})", plain, by_route.get(route, 0))
        ws.write_formula(f"P{x}", f"=SUMIF(F:F,N{x},I:I)", plain, _num(amt_route.get(route, Decimal(0))))
    ws.write_row("R7", ["TRIP FROM", "CUSTOMER", "Trips"], bold)
    pairs = Counter((r.route, r.customer) for r in rows if r.route)
    for i, ((route, cust), n) in enumerate(sorted(pairs.items())):
        x = 8 + i
        ws.write(f"R{x}", route, plain)
        ws.write(f"S{x}", cust, plain)
        ws.write_formula(f"T{x}", f"=COUNTIFS(F:F,R{x},G:G,S{x})", plain, n)

    for col, w in {"A": 9, "B": 11, "C": 15, "D": 11, "E": 12, "F": 19, "G": 30, "H": 8, "I": 10, "J": 26,
                   "K": 9, "L": 9, "M": 34, "N": 17, "O": 8, "P": 10, "R": 19, "S": 30, "T": 8}.items():
        ws.set_column(f"{col}:{col}", w)

    # ---- boxes left out of the period ----------------------------------------------------------------
    if excluded:
        ex = wb.add_worksheet("Outside period")
        ex.write_row(0, 0, ["DATE", "CONT NO", "CUSTOMER", "EIR NO", "WHY"], head)
        for i, mv in enumerate(excluded, 1):
            ex.write_datetime(i, 0, mv.occurred_at, datef)
            ex.write_row(i, 1, [mv.container_no + (mv.container_suffix or ""), mv.customer, mv.eir_no or "",
                                "dated outside the pay period: not included in the totals"], plain)
        ex.set_column("A:E", 22)
    wb.close()
    return path

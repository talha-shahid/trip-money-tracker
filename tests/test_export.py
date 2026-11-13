from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from fixtures_io import page_inputs
from helpers import load_golden
from tripmoney.config import load_settings
from tripmoney.db import init_db, make_engine, make_session_factory, repo
from tripmoney.db import models as m
from tripmoney.export import driver_blocks, export_all
from tripmoney.ingest.pipeline import ingest_pages
from tripmoney.period import period_from_text

SETTINGS = load_settings()


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture()
def golden(session, tmp_path):
    rows, movements = load_golden()
    for mv in movements:
        repo.add_movement(session, mv)
    session.query(m.Driver).one().name = "Test Driver"
    repo.reprice(session, SETTINGS)
    files = export_all(session, SETTINGS, tmp_path, *period_from_text("2026-10"))
    xlsx = next(f for f in files if f.name.startswith("Test_Driver"))
    return rows, load_workbook(xlsx, data_only=True), files


def test_files_are_created(golden):
    _, _, files = golden
    assert sorted(f.suffix for f in files) == [".csv", ".xlsx", ".xlsx"]


def test_header_totals_and_tie_outs_match_the_original_sheet(golden):
    _, wb, _ = golden
    ws = wb["Trip Sheet"]
    assert (ws["D3"].value, ws["D4"].value) == (64, 3894)
    assert (ws["E3"].value, ws["E4"].value, ws["L3"].value, ws["L4"].value) == ("ok", "ok", "ok", "ok")
    assert (ws["O5"].value, ws["P5"].value) == (64, 3894)
    assert ws["B3"].value == "Test Driver" and ws["B5"].value == "4890"


def test_summary_block_per_route(golden):
    _, wb, _ = golden
    ws = wb["Trip Sheet"]
    got = {ws[f"N{r}"].value: (ws[f"O{r}"].value, ws[f"P{r}"].value) for r in range(8, 13)}
    assert got == {"MLS-JIP,OW,FUL": (7, 490), "JIP-MLS,OW,FUL": (14, 980), "MLS-JIP,OW,MT2": (16, 912),
                   "MLS-JIP,OW,MT4": (27, 1512), "JIP-MLS,OW,MTY": (0, 0)}


def test_every_container_row_matches_the_paid_sheet(golden):
    rows, wb, _ = golden
    ws = wb["Trip Sheet"]
    expected = {r["container_no"]: r for r in rows}
    seen = 0
    for x in range(7, 7 + len(rows)):
        cont = ws[f"C{x}"].value
        e = expected[cont]
        assert (ws[f"F{x}"].value or "") == e["expected_route"], cont
        assert ws[f"H{x}"].value == int(e["expected_trip_no"]), cont
        assert ws[f"I{x}"].value == ("BLANK" if e["expected_trip_no"] == "0" else int(e["expected_amount"])), cont
        seen += 1
    assert seen == 80


def test_second_box_of_a_pair_sits_directly_under_the_first(golden):
    rows, wb, _ = golden
    ws = wb["Trip Sheet"]
    for x in range(7, 7 + len(rows)):
        if not ws[f"F{x}"].value:  # a second box
            assert ws[f"F{x - 1}"].value == "MLS-JIP,OW,MT2" and ws[f"J{x}"].value == ws[f"J{x - 1}"].value


def test_rate_table_is_inside_the_workbook(golden):
    _, wb, _ = golden
    rt = wb["ROUTE"]
    assert {rt.cell(r, 1).value: rt.cell(r, 2).value for r in range(2, 7)}["MLS-JIP,OW,MT4"] == 56


def test_totals_in_workbook_equal_sum_of_rows(golden):
    rows, wb, _ = golden
    ws = wb["Trip Sheet"]
    col = lambda c: [ws[f"{c}{x}"].value for x in range(7, 7 + len(rows))]  # noqa: E731
    assert sum(col("H")) == ws["D3"].value and sum(v for v in col("I") if isinstance(v, (int, float))) == ws["D4"].value


def test_csv_has_one_line_per_container(golden):
    _, _, files = golden
    csv_file = next(f for f in files if f.suffix == ".csv")
    assert len(csv_file.read_text(encoding="utf-8-sig").strip().splitlines()) == 81


def test_boxes_outside_the_period_are_listed_not_counted(session, tmp_path):
    for pack in ("pack1",):
        ingest_pages(session, page_inputs(pack), SETTINGS)
    d = session.query(m.Driver).one()
    start, end = period_from_text("2026-10")
    blocks = driver_blocks(session, SETTINGS, start, end)
    assert len(blocks[0].excluded) == 1 and blocks[0].excluded[0].container_no == "SEGU5160443"
    assert len(blocks[0].rows) == 27
    files = export_all(session, SETTINGS, tmp_path, start, end)
    wb = load_workbook(next(f for f in files if f.name.startswith("DRIVERONE")), data_only=True)
    assert "Outside period" in wb.sheetnames
    # 26 trips / 1,572 for the whole pack, minus the one excluded 21-Sep box (a 59 trip)
    assert (wb["Trip Sheet"]["D3"].value, wb["Trip Sheet"]["D4"].value) == (25, 1513)


def test_unreviewed_pages_are_marked_in_the_check_column(session, tmp_path):
    ingest_pages(session, page_inputs("pack1"), SETTINGS)
    start, end = period_from_text("2026-10")
    files = export_all(session, SETTINGS, tmp_path, start, end)
    ws = load_workbook(next(f for f in files if f.name.startswith("DRIVERONE")), data_only=True)["Trip Sheet"]
    checks = [ws[f"M{x}"].value for x in range(7, 34)]
    assert checks.count("OK") < len(checks) and any("UNPAIRED_20FT" in c for c in checks)
    assert ws["D5"].value == sum(1 for c in checks if c != "OK") > 0


def test_summary_workbook_adds_up(session, tmp_path):
    for pack in ("pack1", "pack2"):
        ingest_pages(session, page_inputs(pack), SETTINGS)
    start, end = period_from_text("2026-10")
    files = export_all(session, SETTINGS, tmp_path, start, end)
    ws = load_workbook(next(f for f in files if f.name.startswith("summary")), data_only=True)["Summary"]
    assert ws["A6"].value == "TOTAL"
    assert ws["C6"].value == ws["C4"].value + ws["C5"].value
    assert ws["D6"].value == ws["D4"].value + ws["D5"].value


def test_two_drivers_with_the_same_name_do_not_overwrite_each_other(session, tmp_path):
    for pack in ("pack1", "pack2"):  # the sanitised fixtures give both drivers the same name
        ingest_pages(session, page_inputs(pack), SETTINGS)
    start, end = period_from_text("2026-10")
    files = export_all(session, SETTINGS, tmp_path, start, end)
    assert len({f.name for f in files}) == len(files) == 4


def test_unnamed_driver_never_leaks_the_iqama_into_file_names_or_cells(session, tmp_path):
    ingest_pages(session, page_inputs("pack1"), SETTINGS)
    session.query(m.Driver).one().name = None
    session.flush()
    files = export_all(session, SETTINGS, tmp_path, *period_from_text("2026-10"))
    assert not any("2000000001" in f.name for f in files)
    ws = load_workbook(next(f for f in files if f.name.startswith("driver")), data_only=True)["Trip Sheet"]
    assert ws["B2"].value == "2000000001" and ws["B3"].value == "driver ***001"  # Iqama only in its own cell
    assert "2000000001" not in next(f for f in files if f.suffix == ".csv").read_text(encoding="utf-8-sig")

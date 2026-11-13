"""All 65 real scanned pages (sanitised OCR output) against the hand-keyed reference."""
from collections import Counter

import pytest

from fixtures_io import FIX, load_pages
from tripmoney.evaluate import FIELDS, evaluate, load_gold
from tripmoney.ingest.extract import extract_page


@pytest.fixture(scope="module")
def report():
    return evaluate(load_pages(), load_gold(FIX / "gold_eir_pages.csv"))


def test_overall_accuracy(report):
    assert report.pages == 65
    assert report.overall >= 0.995, report.text()


@pytest.mark.parametrize("field", ["doc_type", "direction", "eir_no", "container_no", "occurred_at", "size_ft",
                                   "status", "container_type", "seal_no", "booking_no", "vessel"])
def test_money_critical_fields_are_perfect(report, field):
    assert report.ok[field] == report.total[field] == 65, [m for m in report.mismatches if m[2] == field]


def test_only_the_known_hard_pages_are_wrong(report):
    # one smeared customer cell, one hand-overwritten truck number; both are flagged for a human
    assert {(p, g, f) for p, g, f, *_ in report.mismatches} <= {("pack2", "29", "customer"), ("pack2", "33", "truck")}


def test_every_container_number_passes_its_check_digit():
    for pages in load_pages().values():
        for boxes, w in pages:
            p = extract_page(boxes, w)
            assert "CHECK_DIGIT" not in {f.code for f in p.flags}, p.container_no


def test_page_types_are_recognised():
    kinds = Counter(extract_page(b, w).doc_type for pages in load_pages().values() for b, w in pages)
    assert kinds == {"sr_eir": 63, "dpworld": 1, "rsgt": 1}


def test_unrecognised_page_is_flagged_not_guessed():
    from tripmoney.ocr.engine import Box
    p = extract_page([Box("hello world", 0, 0, 100, 20, 0.99)], 1653)
    assert p.doc_type == "unknown" and [f.code for f in p.flags] == ["UNKNOWN_DOC"]


def test_gold_columns_are_the_evaluated_fields():
    assert set(FIELDS) <= set(load_gold(FIX / "gold_eir_pages.csv")[0])


def test_remarks_are_read_cleanly_and_stamps_are_ignored():
    from tripmoney.ingest.lexicon import Lexicon

    lex, remarks = Lexicon.load(), {}
    for pack, pages in load_pages().items():
        for i, (boxes, w) in enumerate(pages, 1):
            remarks[(pack, i)] = extract_page(boxes, w, lex).remarks
    assert remarks[("pack1", 1)] == "MINOR WASHING DUSTY" and remarks[("pack1", 3)] == "FULL BOX"
    assert remarks[("pack1", 6)] == "FULL BOX/ MINOR" and remarks[("pack2", 7)] == "FULL / MINOR"
    assert remarks[("pack2", 33)] == ""  # the page with a rubber stamp over the remarks cell

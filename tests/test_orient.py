"""Upside-down / sideways pages are straightened. A fake OCR engine 'reads' a page only when a red marker
painted on the page sits in the bottom-right corner (the page's correct orientation for this test)."""
import dataclasses

import pytest
from sqlalchemy import select

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from fixtures_io import load_pages  # noqa: E402
from tripmoney.config import load_settings  # noqa: E402
from tripmoney.db import init_db, make_engine, make_session_factory  # noqa: E402
from tripmoney.db import models as m  # noqa: E402
from tripmoney.ingest.orient import read_oriented  # noqa: E402
from tripmoney.ingest.pipeline import ingest_pdf  # noqa: E402
from tripmoney.ocr.engine import Box  # noqa: E402

JUNK = [Box(f"lorem{i}", 10, 10 + 40 * i, 200, 40 + 40 * i, 0.9) for i in range(8)]


def red_at_bottom_right(img) -> bool:
    return img[-1, -1, 0] > 200 and img[-1, -1, 1] < 80


class MarkerOcr:
    def __init__(self, good):
        self.good, self.calls = good, 0

    def read(self, image):
        self.calls += 1
        return self.good if red_at_bottom_right(image) else JUNK


def upright_page():
    """A 200 x 1653 px 'scan' (the width real pages have at 200 dpi) with a red marker bottom-right."""
    img = np.full((200, 1653, 3), 255, np.uint8)
    img[-10:, -10:] = (255, 0, 0)
    return img


GOOD = load_pages()["pack1"][0][0]


def test_an_upright_page_is_read_once():
    ocr = MarkerOcr(GOOD)
    boxes, img, turned = read_oriented(ocr, upright_page())
    assert (turned, ocr.calls, boxes is GOOD) == (0, 1, True)


@pytest.mark.parametrize("quarter_turns_applied, undone_by", [(2, 180), (1, 270), (3, 90)])
def test_a_turned_page_is_straightened(quarter_turns_applied, undone_by):
    scanned = np.ascontiguousarray(np.rot90(upright_page(), quarter_turns_applied))
    ocr = MarkerOcr(GOOD)
    boxes, img, turned = read_oriented(ocr, scanned)
    assert turned == undone_by and red_at_bottom_right(img) and boxes is GOOD and img.shape[1] == 1653


def test_a_page_that_looks_like_a_receipt_but_reads_empty_is_still_turned():
    """The real-world case: OCR reads every line upright, so the layout is recognised but the fields are empty."""
    class LinesRightWayUp:
        calls = 0

        def read(self, image):
            self.calls += 1
            return GOOD if red_at_bottom_right(image) else [Box(b.text, b.x0, 2000 - b.y1, b.x1, 2000 - b.y0, b.conf) for b in GOOD]

    ocr = LinesRightWayUp()
    scanned = np.ascontiguousarray(np.rot90(upright_page(), 2))
    assert read_oriented(ocr, scanned)[2] == 180


def test_a_blank_page_is_not_turned_three_times():
    class Blank:
        calls = 0

        def read(self, image):
            self.calls += 1
            return []

    ocr = Blank()
    assert read_oriented(ocr, upright_page())[2] == 0 and ocr.calls == 1


def test_a_page_that_never_reads_is_returned_as_scanned():
    class Never:
        calls = 0

        def read(self, image):
            self.calls += 1
            return JUNK

    ocr = Never()
    boxes, img, turned = read_oriented(ocr, upright_page())
    assert turned == 0 and ocr.calls == 4 and boxes is JUNK


def test_a_pdf_with_upside_down_pages_is_read_and_the_image_is_stored_upright(tmp_path):
    doc = pymupdf.open()
    for _ in range(2):  # two pages whose red marker is top-left, i.e. upside down for this test
        pg = doc.new_page(width=595, height=842)  # A4: 1653 px wide at 200 dpi, like a real scan
        pg.draw_rect(pymupdf.Rect(0, 0, 20, 20), color=(1, 0, 0), fill=(1, 0, 0))
    pdf = tmp_path / "upside_down.pdf"
    doc.save(pdf)
    st = dataclasses.replace(load_settings(), home=tmp_path)
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        rep = ingest_pdf(s, pdf, st, MarkerOcr(GOOD))
        s.commit()
        assert rep.pages == 2 and rep.unknown == 0 and rep.movements == 1 and rep.duplicates == 1  # same page twice
        doc_row = s.scalars(select(m.Document).order_by(m.Document.id)).first()
        assert doc_row.status == "parsed"
        codes = {f.code for f in s.scalars(select(m.Movement)).first().flags}
        assert "PAGE_ROTATED" in codes
        pix = pymupdf.Pixmap(str(st.pages_dir / doc_row.image_path))
        r, g, b = pix.pixel(pix.width - 1, pix.height - 1)
        assert r > 200 and g < 90 and b < 90  # the stored review image is the straightened one

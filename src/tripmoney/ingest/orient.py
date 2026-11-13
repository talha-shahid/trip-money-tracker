"""Straighten pages that were scanned upside down or sideways.

OCR reads each text line the right way up even when the whole page is turned, so the page still *looks* like a known
receipt while its geometry (where each label and value sits) is wrong and every field comes out empty. Orientation is
therefore judged by how completely the key fields can be read: if the page as scanned reads fully it is kept, otherwise
it is turned 180, 90 and 270 degrees and read again, and the best-reading orientation wins. Blank or nearly blank pages
are left alone.
"""
from __future__ import annotations

from ..ocr.engine import Box, OcrEngine
from .extract import extract_page

MIN_BOXES_TO_TRY = 5  # fewer text lines than this = blank / cover page, do not waste three more OCR passes
FULL = 5


def reading_score(boxes: list[Box], width: int) -> int:
    """How many of the five fields that decide the money were read: container (valid), direction, date, size, status."""
    p = extract_page(boxes, width)
    if p.doc_type == "unknown":
        return 0
    codes = {f.code for f in p.flags}
    return sum((bool(p.container_no) and "CHECK_DIGIT" not in codes, bool(p.direction), p.occurred_at is not None,
                p.size_ft is not None, bool(p.status)))


def read_oriented(ocr: OcrEngine, rgb) -> tuple[list[Box], object, int]:
    """-> (boxes, image as finally used, degrees turned counter-clockwise: 0 / 90 / 180 / 270)."""
    boxes = ocr.read(rgb)
    if len(boxes) < MIN_BOXES_TO_TRY:
        return boxes, rgb, 0
    best = (reading_score(boxes, rgb.shape[1]), boxes, rgb, 0)
    if best[0] == FULL:
        return best[1:]
    import numpy as np

    for quarter_turns in (2, 1, 3):
        turned = np.ascontiguousarray(np.rot90(rgb, quarter_turns))
        found = ocr.read(turned)
        score = reading_score(found, turned.shape[1])
        if score > best[0]:  # strictly better, so a page that reads no better turned is left as scanned
            best = (score, found, turned, quarter_turns * 90)
            if score == FULL:
                break
    return best[1:]

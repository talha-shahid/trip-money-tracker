"""Loads the sanitised OCR pages (real scan geometry, dummy driver data) and the hand-keyed gold CSV."""
import gzip
import json
from pathlib import Path

from tripmoney.ingest.pipeline import PageInput
from tripmoney.ocr.engine import Box

FIX = Path(__file__).parent / "fixtures"
PACK_FILES = {"pack1": "pack1.pdf", "pack2": "pack2.pdf"}


def load_pages() -> dict[str, list[tuple[list[Box], int]]]:
    raw = json.load(gzip.open(FIX / "ocr_pages.json.gz", "rt", encoding="utf-8"))
    packs: dict[str, list] = {}
    for key in sorted(raw):
        pack = key.rsplit("-", 1)[0]
        d = raw[key]
        packs.setdefault(pack, []).append(([Box(**b) for b in d["boxes"]], d["w"]))
    return packs


def page_inputs(pack: str) -> list[PageInput]:
    return [PageInput(PACK_FILES[pack], pack, i, w, 2323, boxes)
            for i, (boxes, w) in enumerate(load_pages()[pack], 1)]

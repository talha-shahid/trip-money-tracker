"""Label-anchored reading of fixed-layout forms.

Instead of fixed pixel zones, find each printed label ("CONTAINER NUMBER", "EIR No.", ...) in the OCR
output and read the value printed beneath it: from the label downwards, up to the next label below
and up to the next label on the same row. Skewed or slightly torn scans move labels and values
together, so this keeps working where fixed zones would not.
"""
from __future__ import annotations

import difflib

from ..ocr.engine import Box
from ..textnorm import alnum_key

REF_WIDTH = 1653  # page width (px) at 200 dpi, the scale all caps below are expressed in


def match_label(key: str, vocab: dict[str, str]) -> str | None:
    if key in vocab:
        return vocab[key]
    if len(key) < 4:
        return None
    best = difflib.get_close_matches(key, vocab.keys(), n=1, cutoff=0.86)
    return vocab[best[0]] if best else None


def _reading_order(boxes: list[Box]) -> list[Box]:
    """Top-to-bottom lines, left-to-right inside a line (a wrapped booking number must read in order)."""
    lines: list[list[Box]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if lines and b.cy - lines[-1][0].cy <= 0.6 * max(b.h, 1):
            lines[-1].append(b)
        else:
            lines.append([b])
    return [b for line in lines for b in sorted(line, key=lambda b: b.x0)]


class Layout:
    def __init__(self, boxes: list[Box], vocab: dict[str, str], page_w: int, prefixes: dict[str, str] | None = None):
        self.scale = (page_w or REF_WIDTH) / REF_WIDTH
        prefixes = prefixes or {}
        self.labels: list[tuple[str, Box]] = []
        label_ids: set[int] = set()
        for b in boxes:
            key = alnum_key(b.text)
            if not key:
                continue
            name = next((n for p, n in prefixes.items() if key.startswith(p)), None) or match_label(key, vocab)
            if name:
                self.labels.append((name, b))
                label_ids.add(id(b))
        self.values = [b for b in boxes if id(b) not in label_ids and alnum_key(b.text) and b.conf >= 0.3]
        self.table_right = max((b.x1 for b in boxes), default=page_w) + 5

    def label(self, name: str) -> Box | None:
        return next((b for n, b in self.labels if n == name), None)

    def region(self, name: str, cap: float) -> tuple[float, float, float, float] | None:
        lab = self.label(name)
        if lab is None:
            return None
        s = self.scale
        others = [(n, b) for n, b in self.labels if b is not lab]
        row = [b for _, b in others if abs(b.cy - lab.cy) <= 0.6 * max(lab.h, 20 * s) and b.x0 > lab.x0 + 20 * s]
        right = min((b.x0 for b in row), default=self.table_right)
        below = [b for _, b in others if b.y0 > lab.y1 - 2 and lab.x0 - 25 * s <= b.cx < right]
        bottom = min((b.y0 for b in below), default=lab.y1 + cap * s)
        bottom = min(bottom, lab.y1 + cap * s)
        return lab.x0 - 30 * s, lab.y1 - 3 * s, right - 4 * s, bottom - 2 * s

    def read(self, name: str, cap: float = 120, join: str = " ", min_conf: float = 0.0,
             exclude: tuple[str, ...] = ()) -> tuple[str, float]:
        """Text and (minimum) confidence printed under the label; ('', 0) if absent.

        `min_conf` / `exclude` drop noise such as a rubber stamp lying over a free-text cell.
        """
        reg = self.region(name, cap)
        if reg is None:
            return "", 0.0
        x0, y0, x1, y1 = reg
        hits = [b for b in self.values if y0 <= b.cy <= y1 and x0 <= b.cx <= x1 and b.conf >= min_conf
                and not any(e in alnum_key(b.text) for e in exclude)]
        if not hits:
            return "", 0.0
        return join.join(b.text.strip() for b in _reading_order(hits)), min(b.conf for b in hits)

    def right_of(self, name: str) -> tuple[str, float]:
        """Value printed to the right of a 'Label: value' row (terminal receipts)."""
        lab = self.label(name)
        if lab is None:
            return "", 0.0
        hits = [b for b in self.values if abs(b.cy - lab.cy) <= 0.7 * max(lab.h, 20 * self.scale) and b.x0 >= lab.x1 - 5]
        hits.sort(key=lambda b: b.x0)
        return (" ".join(b.text.strip() for b in hits), min(b.conf for b in hits)) if hits else ("", 0.0)

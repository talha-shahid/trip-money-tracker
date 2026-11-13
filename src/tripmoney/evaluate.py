"""Measure reading accuracy against a hand-keyed reference (gold) CSV."""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .ingest.extract import extract_page
from .ingest.lexicon import Lexicon
from .ingest.validate import resolve_pack
from .ocr.engine import Box
from .textnorm import alnum_key

FIELDS = ["doc_type", "direction", "eir_no", "container_no", "occurred_at", "size_ft", "status", "container_type",
          "customer", "vessel", "booking_no", "seal_no", "truck"]
_EXACT = {"doc_type", "direction", "occurred_at", "size_ft", "status", "container_type"}


def load_gold(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class EvalReport:
    pages: int = 0
    ok: Counter = field(default_factory=Counter)
    total: Counter = field(default_factory=Counter)
    mismatches: list[tuple] = field(default_factory=list)  # (pack, page, field, expected, got)

    @property
    def overall(self) -> float:
        t = sum(self.total.values())
        return sum(self.ok.values()) / t if t else 0.0

    def text(self) -> str:
        lines = [f"pages: {self.pages}"]
        lines += [f"{f:15s} {self.ok[f]:3d}/{self.total[f]:<3d} {100 * self.ok[f] / max(self.total[f], 1):6.1f}%"
                  for f in FIELDS]
        lines.append(f"overall field accuracy: {100 * self.overall:.1f}%")
        if self.mismatches:
            lines.append("\nmismatches (pack | page | field | expected | read):")
            lines += [" | ".join(str(x) for x in mm) for mm in self.mismatches]
        return "\n".join(lines)


def _value(p, name: str) -> str:
    v = getattr(p, name)
    if name == "occurred_at":
        return v.strftime("%Y-%m-%dT%H:%M") if v else ""
    if name == "container_no":
        return p.container_no + p.container_suffix
    return "" if v is None else str(v)


def evaluate(packs: dict[str, list[tuple[list[Box], int]]], gold: list[dict], lexicon: Lexicon | None = None) -> EvalReport:
    """`packs` maps a pack name to its pages as (OCR boxes, page width). The truck is compared after the
    pack-level driver resolution, exactly as it is stored."""
    rep = EvalReport()
    parsed: dict[tuple[str, int], object] = {}
    for pack, pages in packs.items():
        pp = [extract_page(b, w, lexicon) for b, w in pages]
        known = [p for p in pp if p.doc_type != "unknown"]
        for p, r in zip(known, resolve_pack(known)):
            p.truck = r.truck
        parsed.update({(pack, i + 1): p for i, p in enumerate(pp)})
    for g in gold:
        p = parsed.get((g["pack"], int(g["page"])))
        if p is None:
            continue
        rep.pages += 1
        for f in FIELDS:
            exp, got = g[f], _value(p, f)
            same = exp == got if f in _EXACT or f == "occurred_at" else alnum_key(exp) == alnum_key(got)
            rep.total[f] += 1
            if same:
                rep.ok[f] += 1
            else:
                rep.mismatches.append((g["pack"], g["page"], f, exp, got))
    return rep

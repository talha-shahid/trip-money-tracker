"""Known customer / vessel / shipping-line names.

OCR drops spaces ("TRIDENTFREIGHTSAUDILLLC"), so names are matched on letters+digits only and shown in
their proper form. Reviewers' corrections are added here, so the system improves with use.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import yaml

from ..config import find_config
from ..textnorm import alnum_key, clean_text

KINDS = ("customer", "vessel", "line", "remark")


class Lexicon:
    def __init__(self, entries: dict[str, list[str]] | None = None):
        self._by_kind: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        for kind, names in (entries or {}).items():
            for n in names or []:
                self.add(kind, n)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Lexicon":
        p = Path(path or find_config("lexicon.yaml"))
        return cls(yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {})

    def add(self, kind: str, canonical: str) -> None:
        key = alnum_key(canonical)
        if kind in self._by_kind and key:
            self._by_kind[kind][key] = clean_text(canonical)

    def names(self, kind: str) -> list[str]:
        return sorted(self._by_kind[kind].values())

    def canonical(self, kind: str, raw: str) -> str:
        """Best known spelling for `raw`; unknown names are returned cleaned but otherwise unchanged."""
        text = clean_text(raw)
        key = alnum_key(text)
        known = self._by_kind.get(kind, {})
        if not key:
            return ""
        if key in known:
            return known[key]
        hit = difflib.get_close_matches(key, known, n=1, cutoff=0.9)
        return known[hit[0]] if hit else text

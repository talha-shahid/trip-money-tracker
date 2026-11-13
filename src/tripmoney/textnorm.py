"""Small text helpers shared by OCR clean-up, pairing and tracking."""
from __future__ import annotations

import re


def alnum_key(s: str | None) -> str:
    """Upper-case letters and digits only. OCR drops or adds spaces, so comparisons use this."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def clean_text(s: str | None) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s.strip(" .,:;|_-")

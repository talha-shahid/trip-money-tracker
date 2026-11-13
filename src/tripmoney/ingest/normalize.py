"""Turn raw OCR strings into clean values. Every function is pure and tolerant of OCR noise."""
from __future__ import annotations

import difflib
import re
from datetime import datetime

from ..iso6346 import check_digit, is_valid
from ..textnorm import alnum_key, clean_text

_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
           "OCTOBER", "NOVEMBER", "DECEMBER"]


def _month_no(word: str) -> int | None:
    w = re.sub(r"[^A-Za-z0]", "", word).upper().replace("0", "O")
    if len(w) < 3:
        return None
    for i, m in enumerate(_MONTHS, 1):  # full name or 3+ letter prefix ("Oct", "Sept")
        if m.startswith(w) or w.startswith(m[:3]) and difflib.SequenceMatcher(None, w, m).ratio() > 0.6:
            return i
    return None


def parse_eir_datetime(text: str) -> datetime | None:
    """'October25,20264:37AM' -> 2026-10-25 04:37 (OCR drops the spaces)."""
    m = re.search(r"([A-Za-z0]{3,9})\s*(\d{1,2})\s*[,.]?\s*(20\d{2})\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*([AaPp])", text or "")
    if not m:
        return None
    month = _month_no(m.group(1))
    if not month:
        return None
    hour, minute, ap = int(m.group(4)), int(m.group(5)), m.group(6).upper()
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if ap == "P" else 0)
    try:
        return datetime(int(m.group(3)), month, int(m.group(2)), hour, minute)
    except ValueError:
        return None


def parse_printed(text: str) -> datetime | None:
    """'19-0ct-2620:48' -> 2026-10-19 20:48."""
    m = re.search(r"(\d{1,2})\s*-\s*([A-Za-z0]{3,4})\s*-\s*(\d{2})\s*(\d{2})\s*:\s*(\d{2})", text or "")
    if not m:
        return None
    month = _month_no(m.group(2))
    if not month:
        return None
    try:
        return datetime(2000 + int(m.group(3)), month, int(m.group(1)), int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def parse_dmy_datetime(text: str) -> datetime | None:
    """DP World: '21-10-202621:03' -> 2026-10-21 21:03."""
    m = re.search(r"(\d{2})\s*-\s*(\d{2})\s*-\s*(20\d{2})\s*(\d{2})\s*:\s*(\d{2})", text or "")
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5))) if m else None
    except ValueError:
        return None


def parse_iso_datetime(text: str) -> datetime | None:
    """Red Sea Gateway: '2026-10-18 16:38:31'."""
    m = re.search(r"(20\d{2})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*(\d{2})\s*:\s*(\d{2})", text or "")
    try:
        return datetime(*(int(g) for g in m.groups())) if m else None
    except ValueError:
        return None


def size_from_text(text: str) -> int | None:
    t = re.sub(r"[Oo]", "0", text or "")
    m = re.search(r"(\d{2})\s*[fF']?", t)
    if not m:
        return None
    n = int(m.group(1))
    return n if n in (20, 40, 45) else min((20, 40, 45), key=lambda s: abs(s - n)) if 15 <= n <= 50 else None


def size_from_iso(iso: str) -> int | None:
    """ISO 6346 type code: first character is the length (2 = 20', 4 = 40', L = 45')."""
    c = (iso or "").strip().upper()[:1]
    return {"2": 20, "4": 40, "L": 45}.get(c)


def status_from_text(text: str) -> str | None:
    k = alnum_key(text)
    if not k:
        return None
    if k.startswith(("EMP", "MTY")) or difflib.SequenceMatcher(None, k, "EMPTY").ratio() >= 0.6:
        return "EMPTY"
    if k.startswith("FUL") or difflib.SequenceMatcher(None, k, "FULL").ratio() >= 0.6:
        return "FULL"
    return None


_TYPES = {"GENERALPURPOSE": "GP", "TANK": "TANK", "REEFER": "REEFER", "OPENTOP": "OPEN_TOP",
          "FLATRACK": "FLAT_RACK", "HIGHCUBE": "HC", "PLATFORM": "FLAT_RACK"}


def ctype_from_text(text: str) -> str | None:
    k = alnum_key(text)
    if not k:
        return None
    hit = difflib.get_close_matches(k, _TYPES, n=1, cutoff=0.6)
    return _TYPES[hit[0]] if hit else None


def ctype_from_iso(iso: str) -> str | None:
    c = (iso or "").strip().upper()[2:3]
    return {"G": "GP", "R": "REEFER", "U": "OPEN_TOP", "T": "TANK", "P": "FLAT_RACK", "B": "BULK"}.get(c)


def normalize_plate(text: str) -> str:
    """'RJA-3518', '3518RJA' and 'RJA 3518' all become 'RJA3518' (letters first, then digits)."""
    k = alnum_key(text)
    letters = "".join(c for c in k if c.isalpha())
    digits = "".join(c for c in k if c.isdigit())
    return letters + digits


_NOT_AVAILABLE = {"", "NA", "VN", "NIA", "NLA", "NONE", "NIL"}  # "N/A" is often read as "V/N" or "N/A"-like


def reference_no(text: str) -> str:
    """Booking / BL / release numbers: letters+digits only, N/A -> '', and a 0 between two letters is an O."""
    k = alnum_key(text)
    return "" if k in _NOT_AVAILABLE else re.sub(r"(?<=[A-Z])0(?=[A-Z])", "O", k)


def clean_party(text: str) -> str:
    t = clean_text(text)
    return "" if alnum_key(t) in _NOT_AVAILABLE else t


def iqama_digits(text: str) -> str:
    d = re.sub(r"\D", "", (text or "").replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1"))
    return d if len(d) == 10 else ""


# ---- container numbers ---------------------------------------------------------------------------

_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A"}
_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8", "T": "7", "A": "4"}
_ALT = {"O": "0DQ", "0": "ODQ", "I": "1L", "1": "IL", "L": "1I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2",
        "2": "Z", "G": "6C", "6": "G", "D": "0", "Q": "0", "U": "VY", "V": "UY", "M": "N", "N": "MH", "H": "N",
        "C": "GO", "E": "F", "F": "E", "P": "R", "R": "P", "T": "7", "7": "T", "A": "4", "4": "A", "W": "VM"}


def repair_container(raw: str, guess: bool = True) -> tuple[str, str, bool, bool]:
    """-> (core 11 chars, suffix, repaired, valid).

    Position rules (4 letters + 7 digits) fix most O/0 and I/1 mix-ups; if the ISO 6346 check digit
    still fails, a single-character substitution is accepted only when it is the one unique fix
    (`guess=False` turns that off, for numbers typed by a person).
    """
    k = alnum_key(raw)
    core, suffix = k[:11], k[11:]
    if len(core) < 11:
        return core, suffix, False, False
    fixed = "".join(_TO_LETTER.get(c, c) if i < 4 and c.isdigit() else
                    _TO_DIGIT.get(c, c) if i >= 4 and c.isalpha() else c for i, c in enumerate(core))
    if is_valid(fixed):
        return fixed, suffix, fixed != core, True
    if not guess:  # a person typed this: apply position rules only, never guess a different number
        return fixed, suffix, fixed != core, False
    found = set()
    for i, c in enumerate(fixed):
        for alt in _ALT.get(c, ""):
            if (i < 4) != alt.isalpha():
                continue
            cand = fixed[:i] + alt + fixed[i + 1:]
            if is_valid(cand):
                found.add(cand)
    if len(found) == 1:
        return found.pop(), suffix, True, True
    return fixed, suffix, fixed != core, False

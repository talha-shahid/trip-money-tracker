"""ISO 6346 container-number check digit (used later to catch OCR mistakes)."""
from __future__ import annotations

import re
import string

_VALUES: dict[str, int] = {}
_v = 10
for _ch in string.ascii_uppercase:
    if _v % 11 == 0:  # values 11, 22, 33 are skipped
        _v += 1
    _VALUES[_ch] = _v
    _v += 1


def split_container_no(raw: str) -> tuple[str, str]:
    """('ZGXU3098707A') -> ('ZGXU3098707', 'A'). Some depot numbers carry a stray trailing letter."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    return cleaned[:11], cleaned[11:]


def check_digit(first10: str) -> int:
    total = sum((_VALUES[c] if c.isalpha() else int(c)) * (2 ** i) for i, c in enumerate(first10))
    return (total % 11) % 10


def is_valid(raw: str) -> bool:
    core, _suffix = split_container_no(raw)
    if not re.fullmatch(r"[A-Z]{4}\d{7}", core):
        return False
    return check_digit(core[:10]) == int(core[10])

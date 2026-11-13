"""The structured result of reading one scanned page."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ERROR, WARN, INFO = "error", "warn", "info"


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str = WARN
    message: str = ""
    source: str = "ocr"  # ocr (reading problems) | validator (cross-page checks)


@dataclass
class ParsedPage:
    doc_type: str  # sr_eir | dpworld | rsgt | unknown
    direction: str | None = None  # "MLS-JIP" (EIR OUT) | "JIP-MLS" (EIR IN)
    eir_no: str = ""
    container_no: str = ""  # 11-character ISO 6346 number
    container_suffix: str = ""  # stray trailing letter some depot numbers carry
    occurred_at: datetime | None = None
    size_ft: int | None = None
    status: str | None = None  # FULL | EMPTY
    container_type: str | None = None
    grade: str = ""
    customer: str = ""
    shipping_line: str = ""
    vessel: str = ""
    booking_no: str = ""
    bl_no: str = ""
    cro_no: str = ""
    seal_no: str = ""
    remarks: str = ""
    driver_name: str = ""
    iqama: str = ""
    truck: str = ""  # normalised plate, e.g. RJA3518
    truck_raw: str = ""
    printed_at: datetime | None = None
    printed_by: str = ""
    conf: float = 0.0  # lowest confidence among the fields that decide the money
    raw: dict[str, str] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)

    def flag(self, code: str, severity: str = WARN, message: str = "", source: str = "ocr") -> None:
        if all(f.code != code for f in self.flags):
            self.flags.append(Flag(code, severity, message, source))

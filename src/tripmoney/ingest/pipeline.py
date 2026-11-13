"""Ingestion: scanned pages in, validated movements and priced trips out (spec workflow steps 1-4)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import models as m
from ..db import repo
from ..ocr.engine import Box, OcrEngine, boxes_to_json
from .extract import extract_page
from .lexicon import Lexicon
from .parsed import ParsedPage
from .orient import read_oriented
from .pdfio import iter_pages, jpeg_bytes, sha256_file
from .validate import resolve_pack, validate_pack


@dataclass
class PageInput:
    filename: str
    sha256: str
    page_no: int
    width: int
    height: int
    boxes: list[Box]
    image_path: str | None = None
    pack: str = ""
    rotated: int = 0  # degrees the page had to be turned to be read


@dataclass
class IngestReport:
    pages: int = 0
    movements: int = 0
    duplicates: int = 0
    unknown: int = 0
    skipped_files: list[str] = field(default_factory=list)
    flags: Counter = field(default_factory=Counter)
    drivers: dict[str, str] = field(default_factory=dict)  # key -> name
    needs_review: int = 0


def parsed_to_json(p: ParsedPage) -> str:
    d = asdict(p)
    for k in ("occurred_at", "printed_at"):
        d[k] = d[k].isoformat() if d[k] else None
    return json.dumps(d)


def ingest_pages(s: Session, pages: list[PageInput], settings: Settings, lexicon: Lexicon | None = None, *,
                 driver_key: str | None = None, reprice: bool = True) -> IngestReport:
    lex = lexicon or repo.load_lexicon(s)
    report = IngestReport()
    fresh = [p for p in pages if not s.scalar(select(m.Document.id).where(m.Document.sha256 == p.sha256,
                                                                          m.Document.page_no == p.page_no))]
    by_pack: dict[str, list[PageInput]] = defaultdict(list)
    for p in fresh:
        by_pack[p.sha256].append(p)

    for pack_pages in by_pack.values():
        parsed = [extract_page(p.boxes, p.width, lex) for p in pack_pages]
        sr = [x for x in parsed if x.doc_type != "unknown"]
        resolved_iter = iter(resolve_pack(sr, forced_key=driver_key))
        validate_pack(sr, pay_period_start_day=settings.pay_period_start_day)
        for pi, pp in zip(pack_pages, parsed):
            report.pages += 1
            doc = m.Document(pack=pi.filename, filename=pi.filename, sha256=pi.sha256, page_no=pi.page_no,
                             width=pi.width, height=pi.height, image_path=pi.image_path, doc_type=pp.doc_type,
                             ocr_json=boxes_to_json(pi.boxes, pi.width, pi.height), fields_json=parsed_to_json(pp))
            s.add(doc)
            s.flush()
            if pp.doc_type == "unknown":
                doc.status = "unknown"
                report.unknown += 1
                continue
            if pi.rotated:
                pp.flag("PAGE_ROTATED", "info", f"scanned turned by {pi.rotated} degrees; straightened automatically")
            res = next(resolved_iter)
            dup = _find_duplicate(s, pp)
            if dup is not None:
                doc.status = "duplicate"
                report.duplicates += 1
                s.add(m.ReviewFlag(movement_id=dup.id, code="DUPLICATE_SCAN", severity="info", source="validator",
                                   message=f"page {pi.page_no} of {pi.filename} is a second scan of this EIR"))
                continue
            doc.status = "parsed"
            drv = repo.get_or_create_driver(s, res.driver_key, name=res.driver_name or None,
                                            iqama_no=res.iqama or None)
            if res.driver_name and not drv.name:
                drv.name = res.driver_name
            truck = repo.get_or_create_truck(s, res.truck, drv) if res.truck else None
            mv = m.Movement(
                document_id=doc.id, source_doc_type=pp.doc_type, eir_no=pp.eir_no or None, container_no=pp.container_no,
                container_suffix=pp.container_suffix, size_ft=pp.size_ft, container_type=pp.container_type or "GP",
                grade=pp.grade or None, status=pp.status, direction=pp.direction, occurred_at=pp.occurred_at,
                customer=pp.customer, shipping_line=pp.shipping_line or None, vessel=pp.vessel,
                booking_no=pp.booking_no, bl_no=pp.bl_no or None, cro_no=pp.cro_no or None, seal_no=pp.seal_no or None,
                remarks=pp.remarks or None, printed_at=pp.printed_at, printed_by=pp.printed_by or None,
                confidence=pp.conf, driver=drv, truck=truck)
            s.add(mv)
            s.flush()
            for f in pp.flags:
                s.add(m.ReviewFlag(movement_id=mv.id, code=f.code, severity=f.severity, message=f.message, source=f.source))
                if f.severity != "info":  # info flags (e.g. an auto-corrected digit) need no human
                    report.flags[f.code] += 1
            report.movements += 1
            report.drivers[drv.key] = drv.name or ""
    s.add(m.AuditLog(action="ingest", entity="document", detail=f"{report.pages} pages, {report.movements} movements"))
    s.flush()
    if reprice:
        repo.reprice(s, settings)
    s.flush()
    report.needs_review = repo.count_needing_review(s)
    return report


def _find_duplicate(s: Session, p: ParsedPage) -> m.Movement | None:
    if not p.direction:
        return None
    if p.eir_no:
        return s.scalar(select(m.Movement).where(m.Movement.direction == p.direction, m.Movement.eir_no == p.eir_no))
    if p.container_no and p.occurred_at:
        return s.scalar(select(m.Movement).where(m.Movement.container_no == p.container_no,
                                                 m.Movement.occurred_at == p.occurred_at,
                                                 m.Movement.direction == p.direction))
    return None


def ingest_pdf(s: Session, path: str | Path, settings: Settings, ocr: OcrEngine, lexicon: Lexicon | None = None, *,
               driver_key: str | None = None, progress: Callable[[int, int], None] | None = None) -> IngestReport:
    """Read one scanned PDF (one driver's pack). Re-ingesting the same file is a no-op."""
    path = Path(path)
    sha = sha256_file(path)
    if s.scalar(select(func.count()).select_from(m.Document).where(m.Document.sha256 == sha)):
        return IngestReport(skipped_files=[path.name])
    settings.pages_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PageInput] = []
    for img in iter_pages(path):
        rel = f"{sha[:12]}-{img.page_no:03d}.jpg"
        boxes, rgb, turned = read_oriented(ocr, img.rgb)
        (settings.pages_dir / rel).write_bytes(img.jpeg if not turned else jpeg_bytes(rgb))
        pages.append(PageInput(path.name, sha, img.page_no, rgb.shape[1], rgb.shape[0], boxes, rel, rotated=turned))
        if progress:
            progress(img.page_no, img.total)
    return ingest_pages(s, pages, settings, lexicon, driver_key=driver_key)

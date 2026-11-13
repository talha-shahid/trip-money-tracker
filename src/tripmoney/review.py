"""Human review operations: edit a movement, approve it, approve in bulk. Everything is written to the audit log."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import models as m
from .db import repo
from .ingest import normalize as nz
from .iso6346 import is_valid

EDITABLE = ("container_no", "size_ft", "status", "direction", "container_type", "occurred_at", "customer", "vessel",
            "booking_no", "bl_no", "seal_no", "remarks", "shipping_line", "driver_key", "truck")
TYPES = ("GP", "TANK", "REEFER", "OPEN_TOP", "FLAT_RACK", "HC")
_NAME_KIND = {"customer": "customer", "vessel": "vessel", "shipping_line": "line"}
_TEXT = ("customer", "vessel", "booking_no", "bl_no", "seal_no", "remarks", "shipping_line")
_NULLABLE = {"bl_no", "seal_no", "remarks", "shipping_line"}
_MISSING = {"MISSING_DIRECTION": "direction", "MISSING_OCCURRED_AT": "occurred_at", "MISSING_SIZE_FT": "size_ft",
            "MISSING_STATUS": "status", "MISSING_CONTAINER": "container_no", "MISSING_CUSTOMER": "customer"}


class EditError(ValueError):
    pass


def audit(s: Session, action: str, entity_id: int | None, detail: dict | str = "", actor: str = "user",
          entity: str = "movement") -> None:
    s.add(m.AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id,
                     detail=detail if isinstance(detail, str) else json.dumps(detail, default=str)))


def _clean(field: str, raw: str):
    raw = (raw or "").strip()
    if field == "container_no":
        core, suffix, _, _ = nz.repair_container(raw, guess=False)
        if len(core) != 11:
            raise EditError("container number must be 4 letters + 7 digits")
        return core, suffix
    if field == "size_ft":
        if raw not in ("20", "40", "45"):
            raise EditError("size must be 20, 40 or 45")
        return int(raw)
    if field == "status":
        if raw.upper() not in ("FULL", "EMPTY"):
            raise EditError("status must be FULL or EMPTY")
        return raw.upper()
    if field == "direction":
        if raw not in ("MLS-JIP", "JIP-MLS"):
            raise EditError("direction must be MLS-JIP (OUT) or JIP-MLS (IN)")
        return raw
    if field == "container_type":
        if raw.upper() not in TYPES:
            raise EditError(f"container type must be one of {', '.join(TYPES)}")
        return raw.upper()
    if field == "occurred_at":
        try:
            return datetime.fromisoformat(raw)
        except ValueError as e:
            raise EditError("date/time must look like 2026-10-25T04:37") from e
    if field == "truck":
        return nz.normalize_plate(raw)
    if field in ("booking_no", "bl_no"):
        return nz.reference_no(raw)
    if field == "seal_no":
        return "".join(ch for ch in raw.upper() if ch.isalnum())
    return " ".join(raw.split())


def edit_movement(s: Session, movement_id: int, changes: dict[str, str], settings: Settings, actor: str = "user") -> dict:
    """Apply a reviewer's corrections. Returns {field: (old, new)} for what actually changed."""
    mv = s.get(m.Movement, movement_id)
    if mv is None:
        raise EditError("movement not found")
    if mv.review_status == "void":
        raise EditError("this page is voided: restore it before editing")
    old_driver = mv.driver.key if mv.driver else None
    diff: dict[str, tuple] = {}
    for field, raw in changes.items():
        if field not in EDITABLE or raw is None:
            continue
        if field == "driver_key":
            key = raw.strip()
            if key and key != old_driver:
                diff["driver"] = (old_driver, key)
                mv.driver = repo.get_or_create_driver(s, key)
            continue
        if field == "truck":
            plate = _clean("truck", raw)
            if plate and (not mv.truck or mv.truck.plate != plate):
                diff["truck"] = (mv.truck.plate if mv.truck else None, plate)
                mv.truck = repo.get_or_create_truck(s, plate, mv.driver)
            continue
        if not raw.strip() and field not in _TEXT:
            continue  # a blank required field in the form means "leave as is"
        value = _clean(field, raw)
        if field == "container_no":
            core, suffix = value
            if (mv.container_no, mv.container_suffix or "") != (core, suffix):
                diff[field] = (mv.container_no + (mv.container_suffix or ""), core + suffix)
                mv.container_no, mv.container_suffix = core, suffix
        elif field in _TEXT:
            if (getattr(mv, field) or "") != value:
                diff[field] = (getattr(mv, field), value)
                setattr(mv, field, value if (value or field not in _NULLABLE) else None)
                if field in _NAME_KIND and value:
                    repo.learn_name(s, _NAME_KIND[field], value)
        elif getattr(mv, field) != value:
            diff[field] = (getattr(mv, field), value)
            setattr(mv, field, value)
    _refresh_flags(s, mv)
    if diff:
        audit(s, "edit", mv.id, {k: [str(a), str(b)] for k, (a, b) in diff.items()}, actor)
    s.flush()
    for key in {old_driver, mv.driver.key if mv.driver else None}:
        if key:
            repo.reprice(s, settings, driver_key=key)
    return diff


def _refresh_flags(s: Session, mv: m.Movement) -> None:
    """After an edit, close the 'missing X' / check-digit flags the edit has fixed."""
    by_code = {f.code: f for f in mv.flags}
    for code, field in _MISSING.items():
        if code in by_code and getattr(mv, field) not in (None, ""):
            by_code[code].resolved = True
    cd = by_code.get("CHECK_DIGIT")
    if mv.container_no and not is_valid(mv.container_no):
        if cd is None:
            s.add(m.ReviewFlag(movement_id=mv.id, code="CHECK_DIGIT", severity="error", source="validator",
                               message="container number fails the ISO 6346 check digit"))
        else:
            cd.resolved = False
    elif cd is not None:
        cd.resolved = True


def approve(s: Session, movement_id: int, actor: str = "user") -> None:
    mv = s.get(m.Movement, movement_id)
    if mv is None:
        raise EditError("movement not found")
    if mv.review_status == "void":
        raise EditError("this page is voided: restore it before approving")
    if not mv.is_complete:
        raise EditError("fill in size, status, direction and date/time before approving")
    for f in mv.flags:
        f.resolved = True
    mv.review_status = "approved"
    audit(s, "approve", mv.id, "", actor)
    s.flush()


def approve_by_flag(s: Session, code: str, settings: Settings, driver_key: str | None = None, actor: str = "user") -> int:
    """Accept every pending movement whose only open warnings are `code` (e.g. all lone 20' boxes)."""
    n = 0
    for mv in s.scalars(repo.open_flags_query(driver_key)):
        open_codes = {f.code for f in mv.flags if not f.resolved and f.severity in ("warn", "error")}
        if open_codes == {code} and mv.is_complete:
            approve(s, mv.id, actor)
            n += 1
    audit(s, "bulk_approve", None, {"flag": code, "driver": driver_key, "count": n}, actor, entity="batch")
    return n


def find_driver(s: Session, ref: str) -> m.Driver:
    """A driver by exact key, by the last digits shown in the UI (``636``), or by name."""
    ref = ref.strip().lstrip("*")
    rows = list(s.scalars(select(m.Driver)))
    for match in (lambda d: d.key == ref, lambda d: len(ref) >= 3 and d.key.endswith(ref),
                  lambda d: bool(d.name) and d.name.casefold() == ref.casefold()):
        hits = [d for d in rows if match(d)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise EditError(f"'{ref}' matches more than one driver; use more digits or the full name")
    raise EditError(f"no driver matches '{ref}'")


def set_driver_details(s: Session, key: str, *, name: str | None = None, emp_no: str | None = None,
                       sponsor: str | None = None, actor: str = "user") -> m.Driver:
    d = find_driver(s, key)
    before = (d.name, d.emp_no, d.sponsor)
    if name is not None:
        d.name = " ".join(name.split()) or None
    if emp_no is not None:
        d.emp_no = emp_no.strip() or None
    if sponsor is not None:
        d.sponsor = sponsor.strip() or None
    audit(s, "driver_details", d.id, {"before": before, "after": (d.name, d.emp_no, d.sponsor)}, actor, entity="driver")
    return d


def void_movement(s: Session, movement_id: int, reason: str, settings: Settings, actor: str = "user") -> None:
    """Mark a page as 'not a trip' (a copy, a non-paid receipt, a scan of something else).

    It is no longer priced, exported, queued or counted in container history, and the reason is audited.
    """
    reason = " ".join((reason or "").split())
    if len(reason) < 3:
        raise EditError("please give a short reason; it goes into the audit trail")
    mv = s.get(m.Movement, movement_id)
    if mv is None:
        raise EditError("movement not found")
    mv.review_status = "void"
    audit(s, "void", mv.id, {"reason": reason, "container": mv.container_no}, actor)
    s.flush()
    if mv.driver:
        repo.reprice(s, settings, driver_key=mv.driver.key)


def restore_movement(s: Session, movement_id: int, settings: Settings, actor: str = "user") -> None:
    mv = s.get(m.Movement, movement_id)
    if mv is None:
        raise EditError("movement not found")
    if mv.review_status != "void":
        raise EditError("this page is not voided")
    mv.review_status = "unreviewed"
    audit(s, "restore", mv.id, {"container": mv.container_no}, actor)
    s.flush()
    if mv.driver:
        repo.reprice(s, settings, driver_key=mv.driver.key)


def add_movement_by_hand(s: Session, fields: dict[str, str], settings: Settings, actor: str = "user") -> m.Movement:
    """A box with no usable scan (lost, torn). Entered by a person, so it is approved at once, but strictly validated."""
    need = ("container_no", "direction", "status", "size_ft", "occurred_at", "driver_key")
    missing = [f for f in need if not (fields.get(f) or "").strip()]
    if missing:
        raise EditError("please fill in: " + ", ".join(x.replace("_", " ") for x in missing))
    core, suffix = _clean("container_no", fields["container_no"])
    if not is_valid(core):
        raise EditError(f"{core} fails the container check digit: please re-check the number")
    direction, status, size = (_clean(k, fields[k]) for k in ("direction", "status", "size_ft"))
    when = _clean("occurred_at", fields["occurred_at"])
    ctype = _clean("container_type", fields["container_type"]) if (fields.get("container_type") or "").strip() else "GP"
    dup = s.scalar(select(m.Movement.id).where(m.Movement.container_no == core, m.Movement.occurred_at == when,
                                               m.Movement.direction == direction, m.Movement.review_status != "void"))
    if dup:
        raise EditError(f"this box is already recorded at that time (#{dup})")
    driver = repo.get_or_create_driver(s, fields["driver_key"].strip())
    plate = _clean("truck", fields.get("truck") or "")
    eir = "".join(ch for ch in (fields.get("eir_no") or "") if ch.isdigit()) or None
    if eir and s.scalar(select(m.Movement.id).where(m.Movement.direction == direction, m.Movement.eir_no == eir)):
        raise EditError(f"EIR {eir} already exists for this direction")
    mv = m.Movement(
        source_doc_type="manual", eir_no=eir, container_no=core, container_suffix=suffix, size_ft=size, status=status,
        direction=direction, occurred_at=when, container_type=ctype, driver=driver,
        truck=repo.get_or_create_truck(s, plate, driver) if plate else None,
        customer=_clean("customer", fields.get("customer") or ""), vessel=_clean("vessel", fields.get("vessel") or ""),
        booking_no=_clean("booking_no", fields.get("booking_no") or ""),
        seal_no=_clean("seal_no", fields.get("seal_no") or "") or None, remarks=_clean("remarks", fields.get("remarks") or "") or None,
        review_status="approved")
    s.add(mv)
    s.flush()
    for kind, v in (("customer", mv.customer), ("vessel", mv.vessel)):
        if v:
            repo.learn_name(s, kind, v)
    audit(s, "add_by_hand", mv.id, {"container": core, "direction": direction}, actor)
    repo.reprice(s, settings, driver_key=driver.key)
    return mv

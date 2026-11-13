"""Read one OCR'd page into a ParsedPage: classify the layout, pull the fields, normalise, validate."""
from __future__ import annotations

from ..ocr.engine import Box
from ..textnorm import alnum_key
from . import normalize as nz
from .layout import Layout
from .lexicon import Lexicon
from .parsed import ERROR, INFO, WARN, ParsedPage

SR_VOCAB = {
    "DATETIME": "date_time", "CONTAINERNUMBER": "container_number", "EIR": "eir", "EIRNO": "eir_no",
    "VESSEL": "vessel", "CUSTOMER": "customer", "SHIPPINGLINE": "shipping_line", "YARDLOCATION": "yard",
    "BLNUMBER": "bl_no", "BOOKINGNO": "booking_no", "CONTAINERTYPE": "ctype", "GRADE": "grade",
    "CONTAINERSIZE": "size", "CENTRALRELEASEORDERNUMBER": "cro", "SEALNUMBER": "seal", "EMPTYFULL": "status",
    "CONTAINER": "spec1", "SPECIFICATION": "spec2", "TAREWEIGHT": "tare", "MAXPAYLOAD": "payload",
    "REMARKS": "remarks", "OFFICIALUSEONLY": "official", "RECEIVEDINSPECTEDBY": "received", "NAME": "name",
    "SIGNATURE": "signature", "MARKEDDAMAGESDEFICIENCIES": "damages", "DRIVERINFORMATION": "driver_hdr",
    "DRIVERNAME": "driver_name", "TRUCKNUMBER": "truck", "DRIVERRESIDENTIDNUMBER": "iqama",
    "TRANSPORTERNAME": "transporter",
}
DPW_VOCAB = {
    "VEHICLEID": "vehicle", "CABIN": "cabin", "ENTRYTIME": "entry", "ISSUEDATE": "issue", "CONTAINER": "container",
    "LINEID": "line", "CONTAINERTYPE": "ctype", "FREIGHTKIND": "freight", "GROSSWEIGHT": "gross",
    "IMCOCODE": "imco", "REFERENCENO": "ref", "SEALNO": "seal", "REEFERTEMP": "reefer", "DAMAGE": "damage",
    "TRANSACTIONTYPE": "trans", "BUNDLESPAYLOADIFANY": "bundle",
}
RSGT_VOCAB = {
    "BATNO": "bat", "TRUCKINDATETIME": "truck_in", "TRUCKOUTDATETIME": "truck_out", "TRUCKLICENSENO": "plate",
    "CONTAINER": "container", "LINEID": "line", "ISO": "iso", "FREIGHTKIND": "freight", "GROSSWEIGHT": "gross",
    "VESSELNAME": "vessel", "VOYAGENO": "voyage", "DATEOFARRIVAL": "arrival", "SEALNO": "seal",
    "BILLOFLADINGNO": "bl", "TRANSACTIONTYPE": "trans", "DAMAGE": "damage", "EMPTYRETURN": "empty_return",
    "EXAMINATIONTYPE": "exam", "EQUIPMENTGRADE": "grade", "ENTRYGATEASST": "gate",
}


def classify(boxes: list[Box]) -> str:
    k = alnum_key(" ".join(b.text for b in boxes))
    if "REDSEAGATEWAY" in k:
        return "rsgt"
    if "DPWORLD" in k or "CONTAINERRECEIPTEIR" in k:
        return "dpworld"
    if "EQUIPMENTINTERCHANGERECEIPT" in k or ("EIRNO" in k and "SQUARE" in k):
        return "sr_eir"
    return "unknown"


def _set_container(p: ParsedPage, raw: str, conf: float) -> None:
    core, suffix, repaired, valid = nz.repair_container(raw)
    p.container_no, p.container_suffix = core, suffix
    if repaired and valid:
        p.flag("CONTAINER_REPAIRED", INFO, f"OCR read '{raw}', corrected using the ISO 6346 check digit")
    if not core:
        p.flag("MISSING_CONTAINER", ERROR, "no container number found")
    elif not valid:
        p.flag("CHECK_DIGIT", ERROR, f"container number '{core}' fails the ISO 6346 check digit")


def _finish(p: ParsedPage, confs: dict[str, float]) -> ParsedPage:
    for name, label in (("direction", "direction (IN/OUT)"), ("occurred_at", "date/time"), ("size_ft", "size"),
                        ("status", "empty/full")):
        if getattr(p, name) in (None, ""):
            p.flag(f"MISSING_{name.upper()}", ERROR, f"{label} could not be read")
    seen = [c for c in confs.values() if c > 0]
    p.conf = round(min(seen), 3) if seen else 0.0
    if p.conf and p.conf < 0.75:
        p.flag("LOW_CONFIDENCE", WARN, f"OCR confidence {p.conf:.2f} on a key field")
    return p


def parse_sr_eir(boxes: list[Box], w: int, lex: Lexicon) -> ParsedPage:
    lay = Layout(boxes, SR_VOCAB, w, prefixes={"PRINTEDBY": "printed"})
    p = ParsedPage("sr_eir")
    t: dict[str, str] = {}
    c: dict[str, float] = {}
    spec = {"date_time": 110, "container_number": 200, "eir": 150, "eir_no": 150, "vessel": 110, "customer": 110,
            "shipping_line": 110, "bl_no": 110, "booking_no": 110, "ctype": 110, "grade": 110, "size": 110,
            "cro": 110, "seal": 110, "status": 110, "tare": 110, "remarks": 200, "driver_name": 110,
            "truck": 110, "iqama": 110, "transporter": 110, "printed": 110}
    joined = {"container_number", "eir_no", "bl_no", "booking_no", "cro", "seal"}
    for name, cap in spec.items():
        noisy = name == "remarks"  # free text, often crossed by the depot's own rubber stamp
        t[name], c[name] = lay.read(name, cap, "" if name in joined else " ", min_conf=0.85 if noisy else 0.0,
                                    exclude=("SQUARE", "ROOT", "OPERATIONS", "LOGISTICS", "DEPT") if noisy else ())
    p.raw = {k: v for k, v in t.items() if v}

    word = alnum_key(t["eir"])
    p.direction = "JIP-MLS" if word.startswith("IN") else "MLS-JIP" if word.startswith(("OUT", "0UT", "OU")) else None
    p.eir_no = "".join(ch for ch in t["eir_no"] if ch.isdigit())
    _set_container(p, t["container_number"], c["container_number"])
    p.occurred_at = nz.parse_eir_datetime(t["date_time"])
    p.size_ft = nz.size_from_text(t["size"])
    p.status = nz.status_from_text(t["status"])
    p.container_type = nz.ctype_from_text(t["ctype"])
    if t["ctype"] and p.container_type is None:
        p.flag("UNKNOWN_TYPE", WARN, f"container type '{t['ctype']}' not recognised")
    p.grade = nz.clean_party(t["grade"])
    p.customer = lex.canonical("customer", nz.clean_party(t["customer"]))
    p.shipping_line = lex.canonical("line", nz.clean_party(t["shipping_line"]))
    p.vessel = lex.canonical("vessel", nz.clean_party(t["vessel"]))
    p.booking_no = nz.reference_no(t["booking_no"])
    p.bl_no = nz.reference_no(t["bl_no"])
    p.cro_no = nz.reference_no(t["cro"])
    if not t["customer"]:
        p.flag("MISSING_CUSTOMER", WARN, "customer could not be read (smeared or torn?)")
    p.seal_no = alnum_key(t["seal"])
    p.remarks = lex.canonical("remark", nz.clean_party(t["remarks"]))
    if len(alnum_key(p.remarks)) < 3:  # a lone character is stamp residue, not a remark
        p.remarks = ""
    p.driver_name = nz.clean_party(t["driver_name"]).upper()
    p.iqama = nz.iqama_digits(t["iqama"])
    p.truck_raw = t["truck"]
    p.truck = nz.normalize_plate(t["truck"])
    p.printed_at = nz.parse_printed(t["printed"])
    lab = lay.label("printed")
    if lab is not None and ":" in lab.text:
        p.printed_by = lab.text.split(":", 1)[1].strip()
    return _finish(p, {k: c[k] for k in ("eir", "container_number", "date_time", "size", "status")})


def parse_dpworld(boxes: list[Box], w: int, lex: Lexicon) -> ParsedPage:
    lay = Layout(boxes, DPW_VOCAB, w)
    p = ParsedPage("dpworld")
    t, c = {}, {}
    for name, cap in {"cabin": 90, "entry": 90, "issue": 90, "container": 90, "line": 90, "ctype": 90,
                      "freight": 90, "trans": 90, "seal": 90}.items():
        t[name], c[name] = lay.read(name, cap, "")
    p.raw = {k: v for k, v in t.items() if v}
    _set_container(p, t["container"], c["container"])
    p.occurred_at = nz.parse_dmy_datetime(t["entry"])
    iso = alnum_key(t["ctype"])
    p.size_ft, p.container_type = nz.size_from_iso(iso), nz.ctype_from_iso(iso)
    k = alnum_key(t["freight"])
    p.status = "EMPTY" if k.startswith("MTY") or k.startswith("EMP") else "FULL" if k.startswith(("FCL", "FUL", "LCL")) else None
    p.customer = lex.canonical("customer", nz.clean_party(t["line"]))
    p.truck_raw, p.truck = t["cabin"], nz.normalize_plate(t["cabin"])
    p.seal_no = alnum_key(t["seal"])
    trans = alnum_key(t["trans"])
    # Receive = the truck delivers the box to the terminal (depot -> port). Without the line we assume that.
    p.direction = "JIP-MLS" if trans.startswith("DELIVER") else "MLS-JIP"
    p.flag("THIRD_PARTY_RECEIPT", WARN, "DP World terminal receipt: confirm this is a separate paid trip")
    return _finish(p, {"container": c["container"], "entry": c["entry"], "freight": c["freight"], "ctype": c["ctype"]})


def parse_rsgt(boxes: list[Box], w: int, lex: Lexicon) -> ParsedPage:
    lay = Layout(boxes, RSGT_VOCAB, w)
    p = ParsedPage("rsgt")
    t, c = {}, {}
    for name in ("truck_in", "plate", "container", "line", "iso", "freight", "trans", "seal", "vessel", "voyage"):
        t[name], c[name] = lay.right_of(name)
    p.raw = {k: v for k, v in t.items() if v}
    _set_container(p, t["container"], c["container"])
    p.occurred_at = nz.parse_iso_datetime(t["truck_in"])
    iso = alnum_key(t["iso"])
    p.size_ft, p.container_type = nz.size_from_iso(iso), nz.ctype_from_iso(iso)
    k = alnum_key(t["freight"])
    p.status = "EMPTY" if k.startswith(("MTY", "EMP")) else "FULL" if k.startswith(("FCL", "FUL")) else None
    p.customer = lex.canonical("customer", nz.clean_party(t["line"]))
    p.truck_raw, p.truck = t["plate"], nz.normalize_plate(t["plate"])
    p.seal_no = alnum_key(t["seal"])
    trans = alnum_key(t["trans"])
    p.direction = "JIP-MLS" if trans.startswith("DELIVER") else "MLS-JIP"
    p.flag("THIRD_PARTY_RECEIPT", WARN, "Red Sea Gateway Terminal receipt: confirm this is a separate paid trip")
    return _finish(p, {"container": c["container"], "truck_in": c["truck_in"], "freight": c["freight"], "iso": c["iso"]})


def extract_page(boxes: list[Box], width: int, lexicon: Lexicon | None = None) -> ParsedPage:
    lex = lexicon or Lexicon()
    kind = classify(boxes)
    if kind == "sr_eir":
        return parse_sr_eir(boxes, width, lex)
    if kind == "dpworld":
        return parse_dpworld(boxes, width, lex)
    if kind == "rsgt":
        return parse_rsgt(boxes, width, lex)
    p = ParsedPage("unknown")
    p.flag("UNKNOWN_DOC", ERROR, "page layout not recognised")
    return p

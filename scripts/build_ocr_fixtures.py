"""Builds tests/fixtures/ocr_pages.json.gz from a folder of per-page OCR dumps (<pack>-<NN>.json).

Driver names, Iqama numbers and staff names are replaced with dummies so the fixture can live in the repo:
every driver pack gets one fake Iqama, and a stray Iqama (a hand-corrected sheet) gets another.
Usage: python scripts/build_ocr_fixtures.py path/to/ocr_dump_dir
"""
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

from tripmoney.ingest.extract import extract_page
from tripmoney.ingest.validate import resolve_pack
from tripmoney.ocr.engine import Box

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ocr_pages.json.gz"


def main(src: str) -> None:
    files = sorted(Path(src).glob("*-[0-9][0-9].json"))
    packs: dict[str, list[Path]] = {}
    for f in files:
        packs.setdefault(f.stem.rsplit("-", 1)[0], []).append(f)
    out: dict[str, dict] = {}
    for n, (pack, fs) in enumerate(sorted(packs.items()), 1):
        raw = [json.loads(f.read_text()) for f in fs]
        parsed = [extract_page([Box(**b) for b in d["boxes"]], d["w"]) for d in raw]
        known = [p for p in parsed if p.doc_type != "unknown"]
        main_iq = Counter(p.iqama for p in known if p.iqama).most_common(1)[0][0]
        fake_main, fake_stray = f"20000000{n:02d}", f"29000000{n:02d}"
        for d, p in zip(raw, parsed):
            for b in d["boxes"]:
                t = b["text"]
                if p.raw.get("iqama") and t == p.raw["iqama"]:
                    b["text"] = fake_main if p.iqama == main_iq else fake_stray
                elif p.raw.get("driver_name") and t == p.raw["driver_name"]:
                    b["text"] = "DRIVERONE" if p.iqama == main_iq else "STRAYDRIVER"
                elif re.match(r"PRINTEDBY", re.sub(r"[^A-Z]", "", t.upper())) and ":" in t:
                    b["text"] = t.split(":")[0] + ":staff"
        for i, d in enumerate(raw, 1):
            out[f"{pack}-{i:02d}"] = {"w": d["w"], "h": d["h"], "boxes": [
                {k: (round(v, 1) if isinstance(v, float) else v) for k, v in b.items()} for b in d["boxes"]]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {len(out)} pages -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])

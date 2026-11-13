"""Command-line interface: `tripmoney --help`."""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from . import review as rv
from . import tracking
from .config import BUNDLED, Settings, load_settings
from .db import init_db, make_engine, make_session_factory, repo
from .db import models as m
from .export import driver_blocks, export_all
from .period import pay_period, period_from_text


def _settings(args) -> Settings:
    st = load_settings()
    return dataclasses.replace(st, home=Path(args.home)) if args.home else st


def _session_factory(st: Settings):
    st.home.mkdir(parents=True, exist_ok=True)
    engine = make_engine(st.db_url)
    init_db(engine)
    return make_session_factory(engine)


def _period(s, st: Settings, text: str | None) -> tuple[date, date]:
    if text:
        return period_from_text(text, st.pay_period_start_day)
    latest = s.scalar(select(func.max(m.Movement.occurred_at)))
    return pay_period((latest or datetime.now()).date(), st.pay_period_start_day)


def _mask(key: str) -> str:
    return "***" + key[-3:] if key and key.isdigit() else key


# ---- commands -------------------------------------------------------------------------------------------

def cmd_init(args) -> int:
    st = _settings(args)
    _session_factory(st)
    cfg = Path("config")
    cfg.mkdir(exist_ok=True)
    for name in ("rates.yaml", "lexicon.yaml"):
        if not (cfg / name).exists():
            shutil.copy(BUNDLED / name, cfg / name)
            print(f"created config/{name}  (edit this copy)")
    print(f"database ready: {st.home / 'tripmoney.db'}")
    print("next:  tripmoney ingest scans.pdf   then   tripmoney serve")
    return 0


def cmd_ingest(args) -> int:
    from .ingest.pipeline import ingest_pdf
    from .ocr import CachedEngine, RapidOcrEngine

    st = _settings(args)
    ocr = CachedEngine(RapidOcrEngine(), st.home / "ocr_cache")
    factory = _session_factory(st)
    for pdf in args.pdf:
        print(f"\n{pdf}")
        with factory() as s:
            rep = ingest_pdf(s, pdf, st, ocr, driver_key=args.driver_key,
                             progress=lambda i, n: print(f"\r  reading page {i}/{n}", end="", flush=True))
            s.commit()
            if rep.skipped_files:
                print("  already ingested, skipped")
                continue
            print(f"\n  {rep.movements} movements, {rep.duplicates} duplicates, {rep.unknown} unreadable pages")
            print(f"  {rep.needs_review} movements need review" + (f"  ({', '.join(f'{k} x{v}' for k, v in rep.flags.most_common())})" if rep.flags else ""))
    return cmd_report(args)


def cmd_reprice(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        sm = repo.reprice(s, st)
        s.commit()
        print(f"{sm.trips} trips, {sm.amount} total")
    return 0


def cmd_report(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        start, end = _period(s, st, getattr(args, "period", None))
        blocks = driver_blocks(s, st, start, end, getattr(args, "driver", None))
        print(f"\nPay period {start:%d %b %Y} to {end:%d %b %Y}")
        if not blocks:
            print("no trips in this period")
            return 0
        print(f"{'driver':<28}{'truck':<10}{'trips':>6}{'amount':>10}{'review':>8}{'outside':>9}")
        for b in blocks:
            print(f"{(b.driver.name or _mask(b.driver.key))[:27]:<28}{b.truck:<10}{b.trips:>6}{b.amount:>10}{b.to_review:>8}{len(b.excluded):>9}")
        print(f"{'TOTAL':<38}{sum(b.trips for b in blocks):>6}{sum(b.amount for b in blocks):>10}")
    return 0


def cmd_review(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        rows = s.scalars(repo.open_flags_query(args.driver).order_by(m.Movement.id)).all()
        print(f"{len(rows)} movements need review")
        for mv in rows:
            codes = ", ".join(sorted(f.code for f in mv.flags if not f.resolved and f.severity != "info"))
            print(f"  #{mv.id:<4} {mv.container_no:<12} {mv.occurred_at or '?'!s:<20} {codes}")
    return 0


def cmd_approve(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        n = 0
        for i in args.id or []:
            rv.approve(s, i, actor="cli")
            n += 1
        if args.flag:
            n += rv.approve_by_flag(s, args.flag, st, args.driver, actor="cli")
        s.commit()
        print(f"approved {n} movements")
    return 0


def cmd_void(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        for i in args.id:
            rv.void_movement(s, i, args.reason, st, actor="cli")
        s.commit()
        print(f"voided {len(args.id)} movements (they are no longer priced or exported)")
    return 0


def cmd_restore(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        for i in args.id:
            rv.restore_movement(s, i, st, actor="cli")
        s.commit()
        print(f"restored {len(args.id)} movements")
    return 0


def cmd_export(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        start, end = _period(s, st, args.period)
        files = export_all(s, st, args.out, start, end, args.driver)
        for f in files:
            print(f)
        blocks = driver_blocks(s, st, start, end, args.driver)
        pending = sum(b.to_review for b in blocks)
        out_of = sum(len(b.excluded) for b in blocks)
        if pending:
            print(f"note: {pending} rows are still marked REVIEW in the CHECK column")
        if out_of:
            print(f"note: {out_of} boxes dated outside the pay period were left out (see 'Outside period' sheet)")
    return 0


def cmd_containers(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        if args.in_depot:
            inv = tracking.depot_inventory(s)
            print(f"{len(inv)} containers in the depot (oldest first)")
            for h, days in inv:
                e = h.last
                print(f"  {h.container_no}  {days:>6.1f} days  {e.status or '?':<6} {e.size_ft or '?'}'  {e.customer}")
            return 0
        found = [tracking.container_history(s, args.query)] if len(args.query.replace(' ', '')) >= 11 else tracking.search(s, args.query)
        for h in filter(None, found):
            print(f"\n{h.container_no}: {h.current}")
            for e in h.events:
                print(f"  {e.when:%Y-%m-%d %H:%M}  {e.kind:<8}{e.status or '?':<6}{e.customer:<32}{e.vessel}")
            for a in h.anomalies:
                print(f"  ! {a}")
        if not any(found):
            print("nothing found")
    return 0


def cmd_drivers(args) -> int:
    st = _settings(args)
    with _session_factory(st)() as s:
        if args.set:
            rv.set_driver_details(s, args.set, name=args.name, emp_no=args.emp_no, sponsor=args.sponsor, actor="cli")
            s.commit()
        print(f"{'key':<14}{'name':<30}{'emp no':<10}{'sponsor':<10}")
        for d in s.scalars(select(m.Driver).order_by(m.Driver.name)):
            print(f"{_mask(d.key):<14}{(d.name or '-')[:29]:<30}{d.emp_no or '-':<10}{d.sponsor or '-':<10}")
    return 0


def cmd_backup(args) -> int:
    st = _settings(args)
    src = st.home / "tripmoney.db"
    if not src.exists():
        print("no database yet")
        return 1
    out_dir = Path(args.out or st.home / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"tripmoney-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)  # consistent copy even while the app is running
    for old in sorted(out_dir.glob("tripmoney-*.db"))[:-args.keep]:
        old.unlink()
    print(dest)
    return 0


def cmd_evaluate(args) -> int:
    from .evaluate import evaluate, load_gold
    from .ingest.pdfio import iter_pages
    from .ocr import CachedEngine, RapidOcrEngine

    st = _settings(args)
    ocr = CachedEngine(RapidOcrEngine(), st.home / "ocr_cache")
    packs = {}
    for spec in args.pack:
        name, _, pdf = spec.partition("=")
        packs[name] = [(ocr.read(img.rgb), img.rgb.shape[1]) for img in iter_pages(pdf)]
    rep = evaluate(packs, load_gold(args.gold))
    print(rep.text())
    return 0 if rep.overall >= args.min_accuracy else 1


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print('the web app needs extra packages: pip install -e ".[web]"')
        return 1
    import os
    if args.host not in ("127.0.0.1", "localhost") and not os.environ.get("TRIPMONEY_PASSWORD"):
        print("refusing to listen on the network without a password: set TRIPMONEY_PASSWORD first")
        return 1
    if args.home:
        os.environ["TRIPMONEY_HOME"] = args.home
    print(f"open http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run("tripmoney.web.app:create_app", factory=True, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tripmoney", description="Trip Money & Container Tracking")
    p.add_argument("--home", help="data folder (default ./data, or $TRIPMONEY_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database and an editable config/ folder").set_defaults(fn=cmd_init)
    a = sub.add_parser("ingest", help="read scanned PDFs (one driver pack per file)")
    a.add_argument("pdf", nargs="+")
    a.add_argument("--driver-key", help="force every page to this driver key")
    a.set_defaults(fn=cmd_ingest)
    sub.add_parser("reprice", help="re-run the rate rules over everything").set_defaults(fn=cmd_reprice)
    a = sub.add_parser("report", help="trips and money per driver")
    a.add_argument("--period", help="2026-10 = the period starting 10 Oct; or 2026-10-10:2026-11-09")
    a.add_argument("--driver", help="driver key")
    a.set_defaults(fn=cmd_report)
    a = sub.add_parser("review", help="list movements waiting for a human")
    a.add_argument("--driver")
    a.set_defaults(fn=cmd_review)
    a = sub.add_parser("approve", help="approve movements by id, or all whose only warning is a given flag")
    a.add_argument("--id", type=int, action="append")
    a.add_argument("--flag", help="e.g. UNPAIRED_20FT")
    a.add_argument("--driver")
    a.set_defaults(fn=cmd_approve)
    a = sub.add_parser("void", help="mark pages as 'not a trip' (copies, unpaid receipts); needs a reason")
    a.add_argument("--id", type=int, action="append", required=True)
    a.add_argument("--reason", required=True)
    a.set_defaults(fn=cmd_void)
    a = sub.add_parser("restore", help="undo a void")
    a.add_argument("--id", type=int, action="append", required=True)
    a.set_defaults(fn=cmd_restore)
    a = sub.add_parser("export", help="Excel trip sheets + summary + CSV for a pay period")
    a.add_argument("--period")
    a.add_argument("--driver")
    a.add_argument("--out", default="exports")
    a.set_defaults(fn=cmd_export)
    a = sub.add_parser("containers", help="container history, or the depot inventory")
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--in-depot", action="store_true")
    a.set_defaults(fn=cmd_containers)
    a = sub.add_parser("drivers", help="list drivers; set name / employee no / sponsor")
    a.add_argument("--set", metavar="DRIVER", help="the last digits shown (e.g. 636), or the name")
    a.add_argument("--name")
    a.add_argument("--emp-no")
    a.add_argument("--sponsor")
    a.set_defaults(fn=cmd_drivers)
    a = sub.add_parser("backup", help="consistent copy of the database")
    a.add_argument("--out")
    a.add_argument("--keep", type=int, default=30, help="how many backups to keep")
    a.set_defaults(fn=cmd_backup)
    a = sub.add_parser("evaluate", help="measure reading accuracy against a hand-keyed CSV")
    a.add_argument("--gold", required=True)
    a.add_argument("--pack", action="append", required=True, metavar="NAME=FILE.pdf")
    a.add_argument("--min-accuracy", type=float, default=0.99)
    a.set_defaults(fn=cmd_evaluate)
    a = sub.add_parser("serve", help="start the web app")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    a.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except rv.EditError as e:
        print(f"error: {e}")
        return 1
    except RuntimeError as e:  # missing optional packages etc.
        print(f"error: {e}")
        return 1
    except BrokenPipeError:  # e.g. `tripmoney containers --in-depot | head`
        return 0


if __name__ == "__main__":
    sys.exit(main())

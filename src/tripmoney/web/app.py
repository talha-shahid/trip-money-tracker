"""The web app: upload scans, review flagged pages, see trips and containers, download Excel."""
from __future__ import annotations

import base64
import os
import re
import secrets
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import review as rv
from .. import tracking
from ..config import Settings, load_settings
from ..db import init_db, make_engine, make_session_factory, repo
from ..db import models as m
from ..export import driver_blocks, export_all, load_trip_rows
from ..period import pay_period, period_from_text
from .jobs import JobManager

HERE = Path(__file__).resolve().parent
MAX_UPLOAD = 200 * 1024 * 1024
FLAG_HELP = {
    "UNPAIRED_20FT": "A 20' empty with no partner box. Priced as a full trip; approve if that is right.",
    "RATE_UNVERIFIED": "This rate comes from the spec only and was never seen on a paid sheet.",
    "THIRD_PARTY_RECEIPT": "A port terminal receipt: confirm it is a separate paid trip, not a copy of an EIR.",
    "REPRINT_SUSPECTED": "Printed long after the move: a reprint or duplicate copy. Check it is not counted twice.",
    "OUTSIDE_PERIOD": "Dated outside this pack's pay period. Left out of the trip sheet until approved/edited.",
    "DRIVER_MISMATCH": "The page shows a different Iqama (hand-corrected?). Assigned to the pack's driver.",
    "TRUCK_MISMATCH": "The printed truck differs from the driver's truck.",
    "MISSING_CUSTOMER": "Customer could not be read (smeared or torn).",
    "CHECK_DIGIT": "The container number fails its check digit: a misread or a typo.",
    "TIME_MISMATCH": "The move time is later than the print time: check AM/PM.",
    "NO_RULE": "No rate rule matches (size / direction / status).",
}


def money(v) -> str:
    if v is None or v == "":
        return ""
    v = Decimal(str(v))
    return f"{int(v):,}" if v == v.to_integral_value() else f"{v:,.2f}"


def mask(key) -> str:
    key = key or ""
    return "***" + key[-3:] if key.isdigit() and len(key) > 4 else key


def create_app(settings: Settings | None = None, *, ocr_factory=None, inline_jobs: bool = False) -> FastAPI:
    st = settings or load_settings()
    st.home.mkdir(parents=True, exist_ok=True)
    engine = make_engine(st.db_url)
    init_db(engine)
    SF = make_session_factory(engine)
    jobs = JobManager(inline=inline_jobs)
    password = os.environ.get("TRIPMONEY_PASSWORD", "")
    app = FastAPI(title="Trip Money & Container Tracking", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters.update(money=money, mask=mask)
    tpl.env.globals.update(FLAG_HELP=FLAG_HELP, currency=st.currency)
    app.state.settings, app.state.jobs, app.state.session_factory = st, jobs, SF
    state = {"ocr": None}

    def get_ocr():
        if state["ocr"] is None:
            if ocr_factory is not None:
                state["ocr"] = ocr_factory()
            else:
                from ..ocr import CachedEngine, RapidOcrEngine
                state["ocr"] = CachedEngine(RapidOcrEngine(), st.home / "ocr_cache")
        return state["ocr"]

    # ---- security: optional password, same-origin posts, no external content ----------------------------
    def same_origin(request: Request) -> bool:
        """Browsers set Sec-Fetch-Site themselves and pages cannot forge it, so it is trusted first. Without it,
        fall back to Origin, which must name this host (a literal 'null' Origin is refused). Non-browser clients
        (curl, scripts) send neither and are allowed: this guards against malicious *web pages*, not scripts."""
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            return site in ("same-origin", "none")
        origin = request.headers.get("origin")
        return origin is None or urlsplit(origin).netloc == request.headers.get("host")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if password and request.url.path != "/healthz":
            ok = False
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("basic "):
                try:
                    _, _, given = base64.b64decode(auth[6:]).decode().partition(":")
                    ok = secrets.compare_digest(given.encode(), password.encode())
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return PlainTextResponse("password required", 401, headers={"WWW-Authenticate": 'Basic realm="tripmoney"'})
        if request.method == "POST" and not same_origin(request):
            return PlainTextResponse("cross-site request refused", 403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    def db():
        with SF() as s:
            yield s

    def render(request: Request, name: str, s: Session, **ctx):
        ctx["pending"] = repo.count_needing_review(s)
        return tpl.TemplateResponse(request, name, ctx)

    def period_of(s: Session, text: str | None) -> tuple[date, date]:
        if text:
            try:
                return period_from_text(text, st.pay_period_start_day)
            except (ValueError, IndexError):
                pass
        latest = s.scalar(select(func.max(m.Movement.occurred_at)))
        return pay_period((latest or datetime.now()).date(), st.pay_period_start_day)

    def period_text(p: tuple[date, date]) -> str:
        return f"{p[0]:%Y-%m-%d}:{p[1]:%Y-%m-%d}"

    def driver_or_404(s: Session, driver_id: int) -> m.Driver:
        d = s.get(m.Driver, driver_id)
        if d is None:
            raise HTTPException(404, "driver not found")
        return d

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---- dashboard ----------------------------------------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request, period: str | None = None, s: Session = Depends(db)):
        p = period_of(s, period)
        blocks = driver_blocks(s, st, *p)
        return render(request, "dashboard.html", s, blocks=blocks, period=p, period_text=period_text(p),
                      trips=sum(b.trips for b in blocks), amount=sum((b.amount for b in blocks), Decimal(0)),
                      jobs=jobs.recent(5), pages=s.scalar(select(func.count()).select_from(m.Document)),
                      movements=s.scalar(select(func.count()).select_from(m.Movement)))

    # ---- upload -------------------------------------------------------------------------------------------
    @app.post("/upload")
    async def upload(files: list[UploadFile] = File(...)):
        up = st.home / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        for f in files:
            data = await f.read()
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "file too large")
            if not data.startswith(b"%PDF"):
                raise HTTPException(400, f"{f.filename} is not a PDF")
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(f.filename or "scan.pdf").name) or "scan.pdf"
            path = up / f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
            path.write_bytes(data)
            jobs.submit(name, _ingest_job(path))
        return RedirectResponse("/jobs", 303)

    def _ingest_job(path: Path):
        def run(job):
            from ..ingest.pipeline import ingest_pdf

            with SF() as s:
                rep = ingest_pdf(s, path, st, get_ocr(),
                                 progress=lambda i, n: (setattr(job, "page", i), setattr(job, "total", n)))
                s.commit()
            if rep.skipped_files:
                return "already read earlier: skipped"
            return f"{rep.movements} movements, {rep.duplicates} duplicates, {rep.needs_review} need review"
        return run

    @app.get("/jobs")
    def jobs_page(request: Request, s: Session = Depends(db)):
        return render(request, "jobs.html", s, jobs=jobs.recent(30),
                      running=any(j.status in ("queued", "running") for j in jobs.jobs.values()))

    @app.get("/jobs.json")
    def jobs_json():
        return [{"id": j.id, "file": j.filename, "status": j.status, "page": j.page, "total": j.total, "message": j.message}
                for j in jobs.recent(30)]

    # ---- review -------------------------------------------------------------------------------------------
    def queue_ids(s: Session, driver_id: int | None = None, flag: str | None = None) -> list[int]:
        q = select(m.Movement.id).join(m.ReviewFlag, m.ReviewFlag.movement_id == m.Movement.id).where(
            m.ReviewFlag.resolved.is_(False), m.ReviewFlag.severity.in_(("warn", "error")),
            m.Movement.review_status.notin_(("approved", "void")))
        if driver_id:
            q = q.where(m.Movement.driver_id == driver_id)
        if flag:
            q = q.where(m.ReviewFlag.code == flag)
        return sorted(set(s.scalars(q)))

    @app.get("/review")
    def review_queue(request: Request, driver: int | None = None, flag: str | None = None, s: Session = Depends(db)):
        ids = queue_ids(s, driver, flag)
        rows = [s.get(m.Movement, i) for i in ids]
        counts: dict[str, int] = {}
        for mv in s.scalars(repo.open_flags_query()):
            for code in {f.code for f in mv.flags if not f.resolved and f.severity in ("warn", "error")}:
                counts[code] = counts.get(code, 0) + 1
        voided = s.scalars(select(m.Movement).where(m.Movement.review_status == "void").order_by(m.Movement.id)).all()
        return render(request, "review_queue.html", s, rows=rows, voided=voided, counts=sorted(counts.items(), key=lambda kv: -kv[1]),
                      drivers=s.scalars(select(m.Driver).order_by(m.Driver.name)).all(), driver=driver, flag=flag)

    @app.post("/review/bulk")
    def review_bulk(code: str = Form(...), driver: int | None = Form(None), s: Session = Depends(db)):
        key = s.get(m.Driver, driver).key if driver else None
        rv.approve_by_flag(s, code, st, key, actor="web")
        s.commit()
        return RedirectResponse("/review", 303)

    @app.post("/review/{mid}/void")
    def review_void(mid: int, reason: str = Form(""), s: Session = Depends(db)):
        try:
            rv.void_movement(s, mid, reason, st, actor="web")
            s.commit()
        except rv.EditError as e:
            s.rollback()
            return RedirectResponse(f"/review/{mid}?msg={e}", 303)
        return RedirectResponse(f"/review/{mid}?msg=voided", 303)

    @app.post("/review/{mid}/restore")
    def review_restore(mid: int, s: Session = Depends(db)):
        try:
            rv.restore_movement(s, mid, st, actor="web")
            s.commit()
        except rv.EditError as e:
            s.rollback()
            return RedirectResponse(f"/review/{mid}?msg={e}", 303)
        return RedirectResponse(f"/review/{mid}?msg=restored", 303)

    NEW_FIELDS = ("container_no", "direction", "status", "size_ft", "occurred_at", "container_type", "customer", "vessel",
                  "booking_no", "eir_no", "seal_no", "truck", "remarks")

    def new_form(request: Request, s: Session, values: dict, error: str = "", status: int = 200):
        ctx = dict(drivers=s.scalars(select(m.Driver).order_by(m.Driver.name)).all(), values=values, error=error,
                   types=rv.TYPES, pending=repo.count_needing_review(s))
        resp = tpl.TemplateResponse(request, "movement_new.html", ctx)
        resp.status_code = status
        return resp

    @app.get("/movements/new")
    def movement_new(request: Request, s: Session = Depends(db)):
        return new_form(request, s, {"container_type": "GP"})

    @app.post("/movements/new")
    async def movement_create(request: Request, s: Session = Depends(db)):
        form = await request.form()
        values = {k: str(form.get(k, "")) for k in NEW_FIELDS}
        values["driver_id"] = str(form.get("driver_id", ""))
        try:
            fields = dict(values)
            fields["driver_key"] = driver_or_404(s, int(values["driver_id"])).key if values["driver_id"] else ""
            mv = rv.add_movement_by_hand(s, fields, st, actor="web")
            s.commit()
        except rv.EditError as e:
            s.rollback()
            return new_form(request, s, values, str(e), 400)
        return RedirectResponse(f"/review/{mv.id}?msg=added", 303)

    @app.get("/rates")
    def rates_page(request: Request, s: Session = Depends(db)):
        return render(request, "rates.html", s, rules=st.rate_table.rules, version=st.rate_table.version, settings=st)

    @app.get("/review/{mid}")
    def review_item(request: Request, mid: int, driver: int | None = None, flag: str | None = None,
                    msg: str | None = None, s: Session = Depends(db)):
        mv = s.get(m.Movement, mid)
        if mv is None:
            raise HTTPException(404, "movement not found")
        ids = queue_ids(s, driver, flag)
        nxt = next((i for i in ids if i > mid), None)
        prv = next((i for i in reversed(ids) if i < mid), None)
        return render(request, "review_item.html", s, mv=mv, doc=mv.document, next_id=nxt, prev_id=prv, queue_len=len(ids),
                      drivers=s.scalars(select(m.Driver).order_by(m.Driver.name)).all(), msg=msg, driver=driver,
                      flag=flag, types=rv.TYPES, when=mv.occurred_at.strftime("%Y-%m-%dT%H:%M") if mv.occurred_at else "")

    @app.post("/review/{mid}")
    async def review_save(request: Request, mid: int, s: Session = Depends(db)):
        form = await request.form()
        action = form.get("action", "save")
        changes = {k: str(form.get(k, "")) for k in rv.EDITABLE if k != "driver_key"}
        did = form.get("driver_id")
        if did:
            changes["driver_key"] = driver_or_404(s, int(did)).key
        try:
            rv.edit_movement(s, mid, changes, st, actor="web")
            if action == "approve":
                rv.approve(s, mid, actor="web")
            s.commit()
        except rv.EditError as e:
            s.rollback()
            return RedirectResponse(f"/review/{mid}?msg={str(e)}", 303)
        if action == "approve":
            ids = queue_ids(s, None, None)
            nxt = next((i for i in ids if i > mid), ids[0] if ids else None)
            return RedirectResponse(f"/review/{nxt}" if nxt else "/review", 303)
        return RedirectResponse(f"/review/{mid}?msg=saved", 303)

    @app.get("/pages/{doc_id}.jpg")
    def page_image(doc_id: int, s: Session = Depends(db)):
        doc = s.get(m.Document, doc_id)
        if doc is None or not doc.image_path:
            raise HTTPException(404, "no image")
        path = (st.pages_dir / doc.image_path).resolve()
        if st.pages_dir.resolve() not in path.parents or not path.exists():
            raise HTTPException(404, "no image")
        return FileResponse(path, media_type="image/jpeg")

    # ---- trips, drivers, containers, audit -------------------------------------------------------------------
    @app.get("/trips")
    def trips_page(request: Request, driver: int | None = None, period: str | None = None, only_review: int = 0,
                   s: Session = Depends(db)):
        p = period_of(s, period)
        drivers = s.scalars(select(m.Driver).order_by(m.Driver.name)).all()
        sections = []
        for d in drivers:
            if driver and d.id != driver:
                continue
            rows, outside = load_trip_rows(s, d.key, *p, st.type_codes or {})
            if only_review:
                rows = [r for r in rows if r.check != "OK"]
            sections.append((d, rows, outside))
        return render(request, "trips.html", s, sections=sections, drivers=drivers, driver=driver, period=p,
                      period_text=period_text(p), only_review=only_review)

    @app.get("/drivers")
    def drivers_page(request: Request, s: Session = Depends(db)):
        return render(request, "drivers.html", s, drivers=s.scalars(select(m.Driver).order_by(m.Driver.name)).all())

    @app.post("/drivers/{did}")
    def drivers_save(did: int, name: str = Form(""), emp_no: str = Form(""), sponsor: str = Form(""),
                     s: Session = Depends(db)):
        d = driver_or_404(s, did)
        rv.set_driver_details(s, d.key, name=name, emp_no=emp_no, sponsor=sponsor, actor="web")
        s.commit()
        return RedirectResponse("/drivers", 303)

    @app.get("/containers")
    def containers_page(request: Request, q: str = "", s: Session = Depends(db)):
        found = tracking.search(s, q) if q else []
        inv = tracking.depot_inventory(s) if not q else []
        return render(request, "containers.html", s, q=q, found=found, inventory=inv)

    @app.get("/containers/{no}")
    def container_page(request: Request, no: str, s: Session = Depends(db)):
        h = tracking.container_history(s, no)
        if h is None:
            raise HTTPException(404, "container not found")
        return render(request, "container.html", s, h=h, stays=h.stays(datetime.now()))

    @app.get("/audit")
    def audit_page(request: Request, s: Session = Depends(db)):
        rows = s.scalars(select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(300)).all()
        return render(request, "audit.html", s, rows=rows)

    # ---- exports ------------------------------------------------------------------------------------------
    def _export(s: Session, period: str | None, driver_key: str | None = None):
        p = period_of(s, period)
        out = Path(tempfile.mkdtemp(prefix="tripmoney-export-", dir=st.home))
        return export_all(s, st, out, p[0], p[1], driver_key), p

    @app.get("/export/driver/{did}.xlsx")
    def export_driver(did: int, period: str | None = None, s: Session = Depends(db)):
        d = driver_or_404(s, did)
        files, _ = _export(s, period, d.key)
        f = next(x for x in files if x.suffix == ".xlsx" and not x.name.startswith("summary"))
        rv.audit(s, "export", d.id, f"trip sheet {f.name}", "web", "driver")
        s.commit()
        return FileResponse(f, filename=f.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/export/summary.xlsx")
    def export_summary(period: str | None = None, s: Session = Depends(db)):
        files, _ = _export(s, period)
        f = next(x for x in files if x.name.startswith("summary"))
        return FileResponse(f, filename=f.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/export/trips.csv")
    def export_csv(period: str | None = None, s: Session = Depends(db)):
        files, _ = _export(s, period)
        f = next(x for x in files if x.suffix == ".csv")
        return FileResponse(f, filename=f.name, media_type="text/csv")

    return app

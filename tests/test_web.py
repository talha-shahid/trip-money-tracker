import dataclasses
import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from fixtures_io import load_pages, page_inputs  # noqa: E402
from tripmoney.config import load_settings  # noqa: E402
from tripmoney.db import models as m  # noqa: E402
from tripmoney.ingest.pipeline import ingest_pages  # noqa: E402
from tripmoney.web.app import create_app  # noqa: E402


class FakeOcr:
    """Returns the stored OCR of the first pack's pages, in order, instead of running OCR."""

    def __init__(self):
        self.pages = load_pages()["pack1"]
        self.i = 0

    def read(self, image):
        boxes = self.pages[self.i][0]
        self.i += 1
        return boxes


@pytest.fixture()
def app(tmp_path):
    st = dataclasses.replace(load_settings(), home=tmp_path)
    app = create_app(st, ocr_factory=FakeOcr, inline_jobs=True)
    with app.state.session_factory() as s:
        for pack in ("pack1", "pack2"):
            ingest_pages(s, page_inputs(pack), st)
        s.commit()
    return app


@pytest.fixture()
def client(app):
    return TestClient(app, follow_redirects=False)


def mid(app, container):
    with app.state.session_factory() as s:
        return s.scalar(select(m.Movement.id).where(m.Movement.container_no == container))


PAGES = ["/", "/review", "/trips", "/drivers", "/containers", "/audit", "/jobs"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "<title>" in r.text


def test_dashboard_numbers(client):
    r = client.get("/")
    assert "62" in r.text and "3,790" in r.text and "Download Excel" in r.text


def test_iqama_numbers_are_never_shown_in_full(client, app):
    ids = [mid(app, "ARKU2412440"), mid(app, "TGCU0162377")]
    texts = [client.get(p).text for p in PAGES] + [client.get(f"/review/{i}").text for i in ids]
    assert not any("2000000001" in t or "2000000002" in t for t in texts)
    assert "***001" in client.get("/drivers").text


def test_review_queue_lists_flagged_pages_only(client):
    r = client.get("/review")
    assert "SEGU5160443" in r.text and "TGCU0162377" not in r.text and "Approve all" in r.text


def test_review_item_shows_flags_and_form(client, app):
    r = client.get(f"/review/{mid(app, 'ARKU2412440')}")
    assert r.status_code == 200 and "DRIVER_MISMATCH" in r.text and 'name="container_no"' in r.text and "Save &amp; approve" in r.text


def test_fixing_a_page_and_approving_it(client, app):
    i = mid(app, "ARKU2395678")
    r = client.post(f"/review/{i}", data={"customer": "Arkas", "action": "save"})
    assert r.status_code == 303 and r.headers["location"].endswith("msg=saved")
    page = client.get(f"/review/{i}").text
    assert "Arkas" in page and "MISSING_CUSTOMER" in page  # flag is shown struck-through once resolved
    r = client.post(f"/review/{i}", data={"action": "approve"})
    assert r.status_code == 303
    with app.state.session_factory() as s:
        assert s.get(m.Movement, i).review_status == "approved"
    assert "ARKU2395678" not in client.get("/review").text


def test_bad_input_is_reported_not_crashed(client, app):
    i = mid(app, "TGCU0162377")
    r = client.post(f"/review/{i}", data={"size_ft": "30", "action": "save"})
    assert r.status_code == 303 and "size must be" in r.headers["location"].replace("%20", " ")


def test_bulk_approve(client, app):
    before = client.get("/review").text.count("UNPAIRED_20FT")
    assert client.post("/review/bulk", data={"code": "UNPAIRED_20FT"}).status_code == 303
    assert client.get("/review").text.count("UNPAIRED_20FT") < before


def test_driver_name_can_be_set(client, app):
    with app.state.session_factory() as s:
        did = s.scalars(select(m.Driver.id)).first()
    assert client.post(f"/drivers/{did}", data={"name": "Sample Driver", "emp_no": "7", "sponsor": "SRL"}).status_code == 303
    assert "Sample Driver" in client.get("/drivers").text and "Sample Driver" in client.get("/").text


def test_trips_filter_only_review(client):
    all_rows = client.get("/trips").text.count("<tr>")
    few = client.get("/trips?only_review=1").text.count("<tr>")
    assert 0 < few < all_rows


def test_container_pages(client):
    assert "IN DEPOT" in client.get("/containers/TGCU0162377").text
    assert client.get("/containers/NOPE").status_code == 404
    assert "TSSU5340034" in client.get("/containers?q=kerry").text
    assert "In the depot now" in client.get("/containers").text


def test_audit_records_what_was_done(client, app):
    client.post(f"/review/{mid(app, 'ARKU2395678')}", data={"customer": "Arkas", "action": "save"})
    assert "edit" in client.get("/audit").text


def test_excel_downloads_are_real_workbooks(client, app):
    with app.state.session_factory() as s:
        did = s.scalars(select(m.Driver.id)).first()
    r = client.get(f"/export/driver/{did}.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    ws = load_workbook(io.BytesIO(r.content), data_only=True)["Trip Sheet"]
    assert ws["D3"].value > 0 and ws["E3"].value == "ok"
    r = client.get("/export/summary.xlsx")
    assert load_workbook(io.BytesIO(r.content))["Summary"]["A1"].value.startswith("Trip money summary")
    assert client.get("/export/trips.csv").text.count("\n") > 60
    assert client.get("/export/driver/9999.xlsx").status_code == 404


def test_scan_image_is_served_and_path_traversal_is_refused(client, app):
    st = app.state.settings
    st.pages_dir.mkdir(parents=True, exist_ok=True)
    (st.pages_dir / "x.jpg").write_bytes(b"\xff\xd8\xff fake")
    with app.state.session_factory() as s:
        docs = s.scalars(select(m.Document)).all()
        docs[0].image_path, docs[1].image_path = "x.jpg", "../../etc/passwd"
        a, b = docs[0].id, docs[1].id
        s.commit()
    assert client.get(f"/pages/{a}.jpg").headers["content-type"] == "image/jpeg"
    assert client.get(f"/pages/{b}.jpg").status_code == 404
    assert client.get("/pages/99999.jpg").status_code == 404


def test_security_headers(client):
    h = client.get("/").headers
    assert "default-src 'self'" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"


def test_cross_site_posts_are_refused(client, app):
    i = mid(app, "ARKU2395678")
    assert client.post(f"/review/{i}", data={"customer": "X"}, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post(f"/review/{i}", data={"customer": "X"}, headers={"origin": "http://testserver"}).status_code == 303


def test_password_protection(app, monkeypatch):
    monkeypatch.setenv("TRIPMONEY_PASSWORD", "s3cret")
    st = app.state.settings
    locked = TestClient(create_app(st, inline_jobs=True), follow_redirects=False)
    assert locked.get("/").status_code == 401
    assert locked.get("/", auth=("anyone", "wrong")).status_code == 401
    assert locked.get("/", auth=("anyone", "s3cret")).status_code == 200
    assert locked.get("/healthz").status_code == 200


def test_upload_reads_a_pdf_end_to_end(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("numpy")
    st = dataclasses.replace(load_settings(), home=tmp_path)
    app = create_app(st, ocr_factory=FakeOcr, inline_jobs=True)
    client = TestClient(app, follow_redirects=False)
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    pdf = doc.tobytes()
    r = client.post("/upload", files={"files": ("pack.pdf", pdf, "application/pdf")})
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    job = client.get("/jobs.json").json()[0]
    assert job["status"] == "done" and job["total"] == 3 and "3 movements" in job["message"]
    with app.state.session_factory() as s:
        assert s.scalar(select(m.Movement.id).where(m.Movement.container_no == "CRXU8681568")) is not None
        doc_id = s.scalars(select(m.Document.id)).first()
    assert client.get(f"/pages/{doc_id}.jpg").headers["content-type"] == "image/jpeg"
    # the same file again is skipped
    client.post("/upload", files={"files": ("again.pdf", pdf, "application/pdf")})
    assert "skipped" in client.get("/jobs.json").json()[0]["message"]


def test_upload_rejects_non_pdf(client):
    r = client.post("/upload", files={"files": ("evil.pdf", b"<html>", "application/pdf")})
    assert r.status_code == 400


# ---- void / restore / add by hand / rates ---------------------------------------------------------------------

def test_voiding_and_restoring_through_the_web(client, app):
    i = mid(app, "MRKU0318280")
    page = client.get(f"/review/{i}").text
    assert "Void this page" in page and "VOIDED" not in page
    assert "reason" in client.post(f"/review/{i}/void", data={"reason": " "}).headers["location"]
    r = client.post(f"/review/{i}/void", data={"reason": "copy of an EIR"})
    assert r.status_code == 303 and r.headers["location"].endswith("msg=voided")
    page = client.get(f"/review/{i}").text
    assert "VOIDED" in page and "Restore it" in page and 'name="container_no"' not in page  # no edit form while voided
    assert "MRKU0318280" in client.get("/review").text  # listed under 'Voided pages'
    assert "Voided pages" in client.get("/review").text
    assert "MRKU0318280" not in client.get("/trips").text
    assert client.get("/containers/MRKU0318280").status_code == 404
    assert client.post(f"/review/{i}/restore").headers["location"].endswith("msg=restored")
    assert "VOIDED" not in client.get(f"/review/{i}").text and "MRKU0318280" in client.get("/trips").text


def test_a_voided_page_cannot_be_edited_by_posting_to_it(client, app):
    i = mid(app, "MRKU0318280")
    client.post(f"/review/{i}/void", data={"reason": "not ours"})
    r = client.post(f"/review/{i}", data={"customer": "X", "action": "save"})
    assert "restore" in r.headers["location"]


def valid_container():
    from tripmoney.iso6346 import check_digit
    return "TEST000001" + str(check_digit("TEST000001"))


def test_add_a_box_by_hand_end_to_end(client, app):
    assert "Add a box by hand" in client.get("/movements/new").text
    with app.state.session_factory() as s:
        did = s.scalars(select(m.Driver.id)).first()
    form = {"container_no": valid_container(), "occurred_at": "2026-10-22T09:00", "direction": "MLS-JIP", "status": "FULL",
            "size_ft": "40", "driver_id": str(did), "customer": "Kerry", "truck": "RJA-3518"}
    r = client.post("/movements/new", data=form)
    assert r.status_code == 303 and r.headers["location"].endswith("msg=added")
    page = client.get(r.headers["location"]).text
    assert "Added by hand and approved" in page and "MLS-JIP,OW,FUL" in page
    again = client.post("/movements/new", data=form)  # duplicate
    assert again.status_code == 400 and "already recorded" in again.text and valid_container() in again.text  # form keeps what was typed


def test_add_by_hand_reports_a_bad_check_digit(client):
    r = client.post("/movements/new", data={"container_no": "TEST0000019", "occurred_at": "2026-10-22T09:00",
                                            "direction": "MLS-JIP", "status": "FULL", "size_ft": "40", "driver_id": "1"})
    assert r.status_code == 400 and "check digit" in r.text


def test_rates_page_shows_the_rules(client):
    t = client.get("/rates").text
    assert "MLS-JIP,OW,MT4" in t and "pair of boxes" in t and "spec only" in t and "config/rates.yaml" in t


def test_real_browser_form_posts_are_accepted(client, app):
    """Chrome sends 'Origin: null' for form posts under some referrer policies, but always sets Sec-Fetch-Site."""
    i = mid(app, "ARKU2395678")
    browser = {"sec-fetch-site": "same-origin", "origin": "null"}
    assert client.post(f"/review/{i}", data={"customer": "X", "action": "save"}, headers=browser).status_code == 303
    assert client.get("/").headers["referrer-policy"] == "same-origin"


def test_cross_site_requests_are_refused_however_they_announce_themselves(client, app):
    i = mid(app, "ARKU2395678")
    for headers in ({"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"},
                    {"sec-fetch-site": "cross-site", "origin": "http://testserver"},
                    {"origin": "null"}, {"origin": "http://evil.example"}):
        assert client.post(f"/review/{i}", data={"customer": "X"}, headers=headers).status_code == 403, headers
    assert client.post(f"/review/{i}", data={"customer": "X"}, headers={"sec-fetch-site": "none"}).status_code == 303

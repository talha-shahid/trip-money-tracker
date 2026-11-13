"""CLI commands that need no OCR, run against a database filled from the sanitised scans."""
import pytest

from fixtures_io import page_inputs
from tripmoney import cli
from tripmoney.config import load_settings
from tripmoney.db import init_db, make_engine, make_session_factory
from tripmoney.ingest.pipeline import ingest_pages


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    st = load_settings()
    import dataclasses
    st = dataclasses.replace(st, home=tmp_path / "data")
    st.home.mkdir()
    engine = make_engine(st.db_url)
    init_db(engine)
    with make_session_factory(engine)() as s:
        for pack in ("pack1", "pack2"):
            ingest_pages(s, page_inputs(pack), st)
        s.commit()
    return str(st.home)


def run(home, *args):
    return cli.main(["--home", home, *args])


def test_report_shows_both_drivers(home, capsys):
    assert run(home, "report", "--period", "2026-10") == 0
    out = capsys.readouterr().out
    assert "Pay period 10 Oct 2026 to 09 Nov 2026" in out and "TOTAL" in out and "62" in out and "3790" in out.replace(".00", "")  # 3,849 less the 59 dated 21 Sep


def test_review_lists_flagged_pages(home, capsys):
    run(home, "review")
    out = capsys.readouterr().out
    assert "SEGU5160443" in out and "OUTSIDE_PERIOD" in out and "TGCU0162377" not in out


def test_bulk_approve_then_review_is_shorter(home, capsys):
    run(home, "review"); first = int(capsys.readouterr().out.split()[0])
    assert run(home, "approve", "--flag", "UNPAIRED_20FT") == 0
    capsys.readouterr()
    run(home, "review"); second = int(capsys.readouterr().out.split()[0])
    assert second < first


def test_export_writes_files_and_warns(home, tmp_path, capsys):
    out = tmp_path / "out"
    assert run(home, "export", "--period", "2026-10", "--out", str(out)) == 0
    assert len(list(out.glob("*.xlsx"))) == 3 and len(list(out.glob("*.csv"))) == 1
    text = capsys.readouterr().out
    assert "still marked REVIEW" in text and "outside the pay period" in text


def test_containers_history_and_inventory(home, capsys):
    run(home, "containers", "TGCU0162377")
    assert "TGCU0162377: IN DEPOT" in capsys.readouterr().out
    run(home, "containers", "--in-depot")
    assert "19 containers in the depot" in capsys.readouterr().out
    run(home, "containers", "kerry")
    assert "TSSU5340034" in capsys.readouterr().out


def test_set_driver_name(home, capsys):
    run(home, "drivers", "--set", "2000000001", "--name", "Test Driver", "--emp-no", "42")
    assert "Test Driver" in capsys.readouterr().out


def test_backup_creates_a_readable_copy_and_prunes(home, tmp_path):
    import sqlite3
    for _ in range(3):
        assert run(home, "backup", "--out", str(tmp_path / "bk"), "--keep", "2") == 0
    files = sorted((tmp_path / "bk").glob("*.db"))
    assert len(files) == 2
    assert sqlite3.connect(files[-1]).execute("select count(*) from movements").fetchone()[0] == 65


def test_init_creates_editable_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--home", str(tmp_path / "d"), "init"]) == 0
    assert (tmp_path / "config" / "rates.yaml").exists() and (tmp_path / "d" / "tripmoney.db").exists()
    assert load_settings().rate_table.rules  # now reads the local copy


def test_reprice_and_unknown_command(home, capsys):
    assert run(home, "reprice") == 0
    with pytest.raises(SystemExit):
        cli.main(["nonsense"])


def test_void_and_restore_commands(home, capsys):
    import sqlite3
    db = sqlite3.connect(f"{home}/tripmoney.db")
    mid = db.execute("select id from movements where container_no='MRKU0318280'").fetchone()[0]
    assert run(home, "void", "--id", str(mid), "--reason", "copy of an EIR") == 0
    capsys.readouterr()
    run(home, "review")
    assert "MRKU0318280" not in capsys.readouterr().out
    assert run(home, "void", "--id", str(mid), "--reason", " ") == 1
    assert run(home, "restore", "--id", str(mid)) == 0
    assert "restored 1" in capsys.readouterr().out.splitlines()[-1]
    run(home, "containers", "MRKU0318280")
    assert "MRKU0318280" in capsys.readouterr().out

from __future__ import annotations

import warnings
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base

# SQLite stores NUMERIC as float. Amounts are whole or 2-decimal values and every sum is
# done in Python Decimal (see repo.summary), so the warning is noise here.
warnings.filterwarnings("ignore", message=".*does \\*not\\* support Decimal objects natively.*")


def make_engine(url: str = "sqlite:///data/tripmoney.db") -> Engine:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)

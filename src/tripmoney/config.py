"""Loads config/rates.yaml (or $TRIPMONEY_CONFIG) into a Settings object."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from .domain import Direction, Status
from .rules import RateTable, Rule

BUNDLED = Path(__file__).resolve().parent / "defaults"


def find_config(name: str) -> Path:
    """An editable ./config/<name> in the working folder wins; otherwise the copy bundled with the package.

    `tripmoney init` creates ./config/ for you to edit.
    """
    local = Path("config") / name
    return local if local.exists() else BUNDLED / name


@dataclass(frozen=True)
class Settings:
    rate_table: RateTable
    pairing_max_gap_minutes: int = 60
    pay_period_start_day: int = 10
    currency: str = ""
    home: Path = Path("data")
    type_codes: dict = None  # container type -> abbreviation used in trip sheets

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.home / 'tripmoney.db').resolve().as_posix()}"

    @property
    def pages_dir(self) -> Path:
        return self.home / "pages"


def _any_to_none(value):
    return None if value in (None, "any") else value


def _rule(raw: dict) -> Rule:
    kwargs = dict(
        id=raw["id"],
        route_code=raw["route_code"],
        system_code=raw["system_code"],
        spec_code=raw.get("spec_code", ""),
        amount=Decimal(str(raw["amount"])),
        unit=raw.get("unit", "container"),
        verified=bool(raw.get("verified", True)),
        note=(raw.get("note") or "").strip(),
    )
    if (v := _any_to_none(raw.get("direction"))) is not None:
        kwargs["direction"] = Direction(v)
    if (v := _any_to_none(raw.get("status"))) is not None:
        kwargs["status"] = Status(v)
    if (v := _any_to_none(raw.get("size_ft"))) is not None:
        kwargs["size_ft"] = int(v)
    if raw.get("effective_from"):
        kwargs["effective_from"] = raw["effective_from"]
    if raw.get("effective_to"):
        kwargs["effective_to"] = raw["effective_to"]
    if kwargs["unit"] not in ("container", "pair"):
        raise ValueError(f"rule {kwargs['id']}: unit must be 'container' or 'pair'")
    return Rule(**kwargs)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("TRIPMONEY_CONFIG") or find_config("rates.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    table = RateTable([_rule(r) for r in raw["rules"]], version=str(raw.get("version", "unversioned")))
    return Settings(
        rate_table=table,
        pairing_max_gap_minutes=int(raw.get("pairing_max_gap_minutes", 60)),
        pay_period_start_day=int(raw.get("pay_period_start_day", 10)),
        currency=str(raw.get("currency", "")),
        home=Path(os.environ.get("TRIPMONEY_HOME") or "data"),
        type_codes={str(k): str(v) for k, v in (raw.get("type_codes") or {}).items()},
    )

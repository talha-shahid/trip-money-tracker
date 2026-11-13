"""SQLAlchemy schema. SQLite today; nothing here is SQLite-specific, so Postgres works later."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Driver(Base):
    __tablename__ = "drivers"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # Iqama number when known (stable), else a name key
    name: Mapped[str | None] = mapped_column(String(200))
    iqama_no: Mapped[str | None] = mapped_column(String(20))  # government ID: mask when displaying
    emp_no: Mapped[str | None] = mapped_column(String(20))
    sponsor: Mapped[str | None] = mapped_column(String(50))


class Truck(Base):
    __tablename__ = "trucks"
    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(20), unique=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"))
    driver: Mapped[Driver | None] = relationship()


class Document(Base):
    """One scanned page."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("sha256", "page_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(300), default="")  # source file name
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    page_no: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    image_path: Mapped[str | None] = mapped_column(String(400))
    doc_type: Mapped[str | None] = mapped_column(String(30))  # sr_eir | dpworld | rsgt | unknown
    status: Mapped[str] = mapped_column(String(20), default="new")  # parsed | duplicate | unknown
    ocr_json: Mapped[str | None] = mapped_column(Text)  # OCR boxes, so pages can be re-read without re-OCR
    fields_json: Mapped[str | None] = mapped_column(Text)  # what the parser read, as extracted
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Movement(Base):
    __tablename__ = "movements"
    __table_args__ = (UniqueConstraint("direction", "eir_no"),)  # same EIR number twice = duplicate scan
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    source_doc_type: Mapped[str | None] = mapped_column(String(30))
    eir_no: Mapped[str | None] = mapped_column(String(20))
    container_no: Mapped[str] = mapped_column(String(16), index=True)  # 11-character ISO 6346 number
    container_suffix: Mapped[str] = mapped_column(String(4), default="")
    size_ft: Mapped[int | None] = mapped_column(Integer)
    container_type: Mapped[str] = mapped_column(String(16), default="GP")
    grade: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(8))  # FULL | EMPTY
    direction: Mapped[str | None] = mapped_column(String(8))  # MLS-JIP | JIP-MLS
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    has_time: Mapped[bool] = mapped_column(Boolean, default=True)
    customer: Mapped[str] = mapped_column(String(120), default="")
    shipping_line: Mapped[str | None] = mapped_column(String(120))
    vessel: Mapped[str] = mapped_column(String(120), default="")
    booking_no: Mapped[str] = mapped_column(String(60), default="")
    bl_no: Mapped[str | None] = mapped_column(String(60))
    cro_no: Mapped[str | None] = mapped_column(String(60))
    seal_no: Mapped[str | None] = mapped_column(String(30))
    remarks: Mapped[str | None] = mapped_column(String(200))
    printed_at: Mapped[datetime | None] = mapped_column(DateTime)
    printed_by: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[float | None] = mapped_column(Float)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"))
    truck_id: Mapped[int | None] = mapped_column(ForeignKey("trucks.id"))
    review_status: Mapped[str] = mapped_column(String(12), default="unreviewed")  # unreviewed | approved
    document: Mapped[Document | None] = relationship()
    driver: Mapped[Driver | None] = relationship()
    truck: Mapped[Truck | None] = relationship()
    assignment: Mapped["TripAssignment | None"] = relationship(back_populates="movement", uselist=False)
    flags: Mapped[list["ReviewFlag"]] = relationship(back_populates="movement")

    @property
    def is_complete(self) -> bool:
        return None not in (self.size_ft, self.status, self.direction, self.occurred_at) and bool(self.container_no)


class TripAssignment(Base):
    """The rate code bound to a movement (spec workflow step 4)."""
    __tablename__ = "trip_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    movement_id: Mapped[int] = mapped_column(ForeignKey("movements.id"), unique=True)
    rule_id: Mapped[str | None] = mapped_column(String(40))
    route_code: Mapped[str | None] = mapped_column(String(40))
    system_code: Mapped[str | None] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    trip_count: Mapped[int] = mapped_column(Integer, default=0)
    pair_id: Mapped[str | None] = mapped_column(String(80))
    pair_role: Mapped[str | None] = mapped_column(String(8))
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    rate_version: Mapped[str] = mapped_column(String(40), default="")
    assigned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    movement: Mapped[Movement] = relationship(back_populates="assignment")


class ReviewFlag(Base):
    """Anything a human should look at: from the rate rules, OCR, or the validators."""
    __tablename__ = "review_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    movement_id: Mapped[int] = mapped_column(ForeignKey("movements.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(8), default="warn")  # info | warn | error
    message: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(12), default="rules")  # rules | ocr | validator
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    movement: Mapped[Movement] = relationship(back_populates="flags")


class Name(Base):
    """Known customer / vessel / line spellings, learned from reviewer corrections."""
    __tablename__ = "names"
    __table_args__ = (UniqueConstraint("kind", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(120))
    canonical: Mapped[str] = mapped_column(String(120))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actor: Mapped[str] = mapped_column(String(60), default="system")
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(30), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(Text, default="")

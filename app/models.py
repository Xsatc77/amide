import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Medium(str, enum.Enum):
    LYOPHILIZED = "Lyophilized"
    LIQUID = "Liquid"
    AUTOINJECTOR = "Autoinjector"
    INHALER = "Inhaler"
    PILL = "Pill"
    DROPS = "Drops"
    SALVE = "Salve"


class InventoryItem(Base):
    """One line of stock: e.g. "BPC-157, 5 vials of 10 mg, lyophilized"."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_inventory_count_nonneg"),
        CheckConstraint("vial_size_mg IS NULL OR vial_size_mg > 0", name="ck_inventory_vial_size_pos"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="ck_inventory_cost_nonneg"),
        CheckConstraint("coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0", name="ck_inventory_coa_vial_size_pos"),
        CheckConstraint("coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)",
                        name="ck_inventory_coa_purity_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    count: Mapped[int] = mapped_column(Integer, default=1)
    vial_size_mg: Mapped[float | None] = mapped_column(Float)
    medium: Mapped[Medium | None] = mapped_column(
        Enum(Medium, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])
    )
    # Money is stored as integer cents to avoid floating-point rounding.
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(String(200))
    lot_number: Mapped[str | None] = mapped_column(String(100))
    order_date: Mapped[date | None] = mapped_column(Date)
    shipped_date: Mapped[date | None] = mapped_column(Date)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    # Filename (not path) of the uploaded COA inside config.COA_DIR.
    coa_filename: Mapped[str | None] = mapped_column(String(100))
    # What the lab actually measured, per the COA (vs. the labeled vial_size_mg).
    coa_vial_size_mg: Mapped[float | None] = mapped_column(Float)
    coa_purity_pct: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100


# ---------------------------------------------------------------- protocols

class LabeledEnum(str, enum.Enum):
    """A str enum whose members carry a display label: MEMBER = (value, label)."""

    def __new__(cls, value: str, label: str):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj


def _enum_column(cls):
    return Enum(cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])


class DoseUnit(LabeledEnum):
    MG = ("mg", "mg")
    MCG = ("mcg", "mcg")
    IU = ("IU", "IU")


class Frequency(LabeledEnum):
    DAILY = ("daily", "Daily")
    EOD = ("eod", "Every other day")
    EVERY_N_DAYS = ("every_n_days", "Every N days")
    WEEKDAYS = ("weekdays", "Specific days")
    WEEKLY = ("weekly", "Weekly")
    AS_NEEDED = ("as_needed", "As needed")


class TimeOfDay(LabeledEnum):
    AM = ("am", "AM")
    PM = ("pm", "PM")
    BEDTIME = ("bedtime", "Bedtime")
    ANY = ("any", "Any time")


class Route(LabeledEnum):
    SUBQ = ("subq", "SubQ")
    IM = ("im", "IM")
    ORAL = ("oral", "Oral")
    NASAL = ("nasal", "Nasal")
    TOPICAL = ("topical", "Topical")
    OTHER = ("other", "Other")


class PeptideSource(LabeledEnum):
    CARD = ("card", "Peptide card")
    STARTER = ("starter", "Starter list")
    CUSTOM = ("custom", "Added by you")


# Weekday letters used in ProtocolItem.weekdays, Monday first (R = Thursday, U = Sunday).
WEEKDAY_LETTERS = "MTWRFSU"
WEEKDAY_NAMES = dict(zip(WEEKDAY_LETTERS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")))


class Peptide(Base):
    """A library entry. Dose ranges are optional reference values, not recommendations."""

    __tablename__ = "peptides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120, collation="NOCASE"), unique=True)
    aliases: Mapped[str | None] = mapped_column(String(300))
    card_number: Mapped[int | None] = mapped_column(Integer)
    dose_low: Mapped[float | None] = mapped_column(Float)
    dose_mid: Mapped[float | None] = mapped_column(Float)
    dose_high: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit | None] = mapped_column(_enum_column(DoseUnit))
    typical_frequency: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[PeptideSource] = mapped_column(_enum_column(PeptideSource), default=PeptideSource.CUSTOM)

    # From the owner's peptide cards (read-only in the app; set by app.library.loader).
    card_class: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    evidence_level: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(200))
    card_details: Mapped[dict | None] = mapped_column(JSON)
    # Filename inside config.CARDS_DIR.
    card_image: Mapped[str | None] = mapped_column(String(100))


class GoalPeptide(Base):
    """One peptide in a goal's suggested stack."""

    __tablename__ = "goal_peptides"

    goal: Mapped[str] = mapped_column(String(40), primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    peptide: Mapped[Peptide] = relationship()


class Protocol(Base):
    __tablename__ = "protocols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # Set when the owner presses End; the protocol is Ended from then on.
    ended_on: Mapped[date | None] = mapped_column(Date)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    titration_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    goals: Mapped[list["ProtocolGoal"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    items: Mapped[list["ProtocolItem"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="ProtocolItem.position")

    @property
    def goal_slugs(self) -> list[str]:
        return [g.goal for g in self.goals]


class ProtocolGoal(Base):
    __tablename__ = "protocol_goals"

    protocol_id: Mapped[int] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"), primary_key=True)
    goal: Mapped[str] = mapped_column(String(40), primary_key=True)


class ProtocolItem(Base):
    """One peptide within a protocol, with the owner's dose and schedule."""

    __tablename__ = "protocol_items"
    __table_args__ = (
        CheckConstraint("dose IS NULL OR dose > 0", name="ck_protocol_item_dose_pos"),
        CheckConstraint("every_n_days IS NULL OR every_n_days >= 2", name="ck_protocol_item_every_n_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    protocol_id: Mapped[int] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"))
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    dose: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit), default=DoseUnit.MG)
    frequency: Mapped[Frequency] = mapped_column(_enum_column(Frequency), default=Frequency.DAILY)
    every_n_days: Mapped[int | None] = mapped_column(Integer)
    weekdays: Mapped[str | None] = mapped_column(String(7))
    time_of_day: Mapped[TimeOfDay] = mapped_column(_enum_column(TimeOfDay), default=TimeOfDay.ANY)
    route: Mapped[Route] = mapped_column(_enum_column(Route), default=Route.SUBQ)
    inventory_item_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(String(300))

    peptide: Mapped[Peptide] = relationship()
    inventory_item: Mapped[InventoryItem | None] = relationship()
    steps: Mapped[list["TitrationStep"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="TitrationStep.start_week")


class TitrationStep(Base):
    __tablename__ = "titration_steps"
    __table_args__ = (
        CheckConstraint("start_week >= 1", name="ck_titration_start_week"),
        CheckConstraint("end_week IS NULL OR end_week >= start_week", name="ck_titration_end_week"),
        CheckConstraint("dose > 0", name="ck_titration_dose_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    protocol_item_id: Mapped[int] = mapped_column(ForeignKey("protocol_items.id", ondelete="CASCADE"))
    start_week: Mapped[int] = mapped_column(Integer)
    end_week: Mapped[int | None] = mapped_column(Integer)
    dose: Mapped[float] = mapped_column(Float)


# ---------------------------------------------------------------- accounts

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32))  # as typed, for display
    username_key: Mapped[str] = mapped_column(String(32), unique=True)  # lower-case, for matching
    password_hash: Mapped[str] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last 30-second step a code was accepted for: a code can't be used twice.
    totp_last_step: Mapped[int | None] = mapped_column(Integer)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    notice_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow().replace(tzinfo=None))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def initial(self) -> str:
        return self.username[:1].upper()


class LoginSession(Base):
    """A browser's session: legal notice acceptance, then (optionally) a signed-in user.
    Timestamps are naive UTC."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # random token, also the cookie value
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notice_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    twofa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User | None] = relationship()

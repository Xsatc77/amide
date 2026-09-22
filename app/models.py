import enum
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100

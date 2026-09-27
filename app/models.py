import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LabeledEnum(str, enum.Enum):
    """A str enum whose members carry a display label: MEMBER = (value, label)."""

    def __new__(cls, value: str, label: str):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj


def _enum_column(cls):
    # Calculate length needed for the longest enum value
    max_length = max(len(m.value) for m in cls)
    # Use at least 20, but round up to handle future additions
    length = max(20, max_length + 5)
    return Enum(cls, native_enum=False, length=length, values_callable=lambda e: [m.value for m in e])


class DoseUnit(LabeledEnum):
    MG = ("mg", "mg")
    MCG = ("mcg", "mcg")
    IU = ("IU", "IU")


class DispensingMethod(LabeledEnum):
    SYRINGE = ("syringe", "Syringe")
    PEN = ("pen", "Peptide pen")


class InjectionSite(LabeledEnum):
    ABDOMEN_L = ("abdomen_l", "Left abdomen")
    ABDOMEN_R = ("abdomen_r", "Right abdomen")
    THIGH_L = ("thigh_l", "Left thigh")
    THIGH_R = ("thigh_r", "Right thigh")
    ARM_L = ("arm_l", "Left upper arm")
    ARM_R = ("arm_r", "Right upper arm")
    GLUTE_L = ("glute_l", "Left glute")
    GLUTE_R = ("glute_r", "Right glute")


class DoseStatus(LabeledEnum):
    ON_TIME = ("on_time", "On time")
    LATE = ("late", "Logged late")
    MISSED = ("missed", "Missed")  # never written to the DB -- inferred at read time; kept here so
                                   # DoseLog.status and a computed "missed" display value share one type
    SKIPPED = ("skipped", "Skipped")


class Medium(str, enum.Enum):
    LYOPHILIZED = "Lyophilized"
    LIQUID = "Liquid"
    AUTOINJECTOR = "Autoinjector"
    INHALER = "Inhaler"
    PILL = "Pill"
    DROPS = "Drops"
    SALVE = "Salve"


class Category(str, enum.Enum):
    MEDICINE = "Medicine"
    BAC_WATER = "BAC Water"
    SUPPLY = "Supply"


class StorageLocation(LabeledEnum):
    FRIDGE = ("fridge", "Fridge")
    FREEZER = ("freezer", "Freezer")
    ROOM_TEMP = ("room_temp", "Room temperature")


class Colorway(LabeledEnum):
    LIGHT = ("light", "Light")
    DARK = ("dark", "Dark")
    TEQUILA_SUNRISE = ("tequila_sunrise", "Tequila Sunrise")
    FIREWORKS = ("fireworks", "Fireworks")
    SOLARIN = ("solarin", "Solarin")
    BRICKS = ("bricks", "The Bricks")
    RETRO = ("retro", "Retro")
    GREENSLEEVES = ("greensleeves", "Greensleeves")
    HIGH_CONTRAST = ("high_contrast", "High Contrast")


class ShareCategory(LabeledEnum):
    INVENTORY = ("inventory", "Inventory")
    PERSONAL_DATA = ("personal_data", "Personal data")


class BiologicalSex(str, enum.Enum):
    MALE = "Male"
    FEMALE = "Female"


class JournalSideEffect(str, enum.Enum):
    INJECTION_SITE_REACTION = "Injection site reaction"
    HEADACHE = "Headache"
    NAUSEA = "Nausea"
    FATIGUE = "Fatigue"
    BLOATING = "Bloating / water retention"
    GI_UPSET = "GI upset"
    JOINT_PAIN = "Joint pain"
    INSOMNIA = "Insomnia"
    APPETITE_CHANGE = "Appetite change"
    FLUSHING_DIZZINESS = "Flushing / dizziness"


class LabMarker(str, enum.Enum):
    TOTAL_TESTOSTERONE = "Total Testosterone"
    FREE_TESTOSTERONE = "Free Testosterone"
    ESTRADIOL = "Estradiol"
    LH = "LH"
    FSH = "FSH"
    SHBG = "SHBG"
    PROLACTIN = "Prolactin"
    IGF_1 = "IGF-1"
    CORTISOL = "Cortisol"
    FASTING_GLUCOSE = "Fasting Glucose"
    HBA1C = "HbA1c"
    FASTING_INSULIN = "Fasting Insulin"
    TOTAL_CHOLESTEROL = "Total Cholesterol"
    LDL = "LDL"
    HDL = "HDL"
    TRIGLYCERIDES = "Triglycerides"
    TSH = "TSH"
    FREE_T3 = "Free T3"
    FREE_T4 = "Free T4"
    ALT = "ALT"
    AST = "AST"
    CREATININE = "Creatinine"
    EGFR = "eGFR"
    BUN = "BUN"
    HEMOGLOBIN = "Hemoglobin"
    HEMATOCRIT = "Hematocrit"
    WBC = "WBC"
    PLATELETS = "Platelets"
    HS_CRP = "hs-CRP"
    VITAMIN_D = "Vitamin D"
    FERRITIN = "Ferritin"
    OTHER = "Other"


class ActivityLevel(LabeledEnum):
    SEDENTARY = ("1.2", "Sedentary — little or no exercise")
    LIGHTLY_ACTIVE = ("1.375", "Lightly active — 1-3 days/week")
    MODERATELY_ACTIVE = ("1.55", "Moderately active — 3-5 days/week")
    VERY_ACTIVE = ("1.725", "Very active — 6-7 days/week")
    EXTRA_ACTIVE = ("1.9", "Extra active — physical job or 2x/day training")


class MacroGoal(LabeledEnum):
    AGGRESSIVE_LOSS = ("-1000", "Aggressive fat loss")
    MODERATE_LOSS = ("-500", "Moderate fat loss")
    SLOW_LOSS = ("-250", "Slow fat loss")
    MAINTAIN = ("0", "Maintain current weight")
    SLOW_GAIN = ("250", "Slow muscle gain")
    MODERATE_GAIN = ("500", "Moderate muscle gain")
    AGGRESSIVE_GAIN = ("1000", "Aggressive muscle gain")


class DietPreset(LabeledEnum):
    BALANCED = ("balanced", "Balanced (30/40/30)")
    HIGH_PROTEIN = ("high_protein", "High protein (40/30/30)")
    LOW_CARB = ("low_carb", "Low carb (40/15/45)")
    KETO = ("keto", "Ketogenic (20/5/75)")
    CUSTOM = ("custom", "Custom ratio")


class Vendor(Base):
    """A supplier. Shared across all users (like the peptide library), not scoped per owner --
    what a user says they bought from a vendor stays private per Inventory sharing; the vendor
    itself doesn't. `created_by_id` is provenance only, never an access-control field."""

    __tablename__ = "vendors"
    __table_args__ = (UniqueConstraint("name", name="uq_vendor_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200, collation="NOCASE"))
    website: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    supplier: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    recommended: Mapped[bool | None] = mapped_column(Boolean)
    price_list_filename: Mapped[str | None] = mapped_column(String(120))
    price_list_url: Mapped[str | None] = mapped_column(String(500))
    price_list_updated_at: Mapped[date | None] = mapped_column(Date)

    contacts: Mapped[list["VendorContact"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
    payment_methods: Mapped[list["VendorPaymentMethod"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")


class ContactMethodType(Base):
    __tablename__ = "contact_method_types"
    __table_args__ = (UniqueConstraint("name", name="uq_contact_method_type_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60, collation="NOCASE"))


class PaymentMethodType(Base):
    __tablename__ = "payment_method_types"
    __table_args__ = (UniqueConstraint("name", name="uq_payment_method_type_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60, collation="NOCASE"))


class VendorContact(Base):
    """One contact entry for a vendor: a method type plus the actual handle/number/address. A
    vendor can have several (two phone numbers, an email AND a Telegram handle, etc.)."""
    __tablename__ = "vendor_contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    method_type_id: Mapped[int] = mapped_column(ForeignKey("contact_method_types.id", ondelete="RESTRICT"))
    value: Mapped[str] = mapped_column(String(200))

    vendor: Mapped["Vendor"] = relationship(back_populates="contacts")
    method_type: Mapped["ContactMethodType"] = relationship()


class VendorPaymentMethod(Base):
    """A payment type this vendor accepts -- a flag row, no value (unlike VendorContact)."""
    __tablename__ = "vendor_payment_methods"
    __table_args__ = (UniqueConstraint("vendor_id", "method_type_id", name="uq_vendor_payment_method"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    method_type_id: Mapped[int] = mapped_column(ForeignKey("payment_method_types.id", ondelete="RESTRICT"))

    vendor: Mapped["Vendor"] = relationship(back_populates="payment_methods")
    method_type: Mapped["PaymentMethodType"] = relationship()


class VendorFavorite(Base):
    """Per-user favorite marker. Row exists = favorited -- insert/delete, no boolean to toggle,
    mirroring Share's existence-is-the-grant pattern."""
    __tablename__ = "vendor_favorites"
    __table_args__ = (UniqueConstraint("user_id", "vendor_id", name="uq_vendor_favorite"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)


class InventoryItem(Base):
    """One line of stock: e.g. "BPC-157, 5 vials of 10 mg, lyophilized"."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_inventory_count_nonneg"),
        CheckConstraint("vial_size_mg IS NULL OR vial_size_mg > 0", name="ck_inventory_vial_size_pos"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="ck_inventory_cost_nonneg"),
        CheckConstraint("volume_ml IS NULL OR volume_ml > 0", name="ck_inventory_volume_pos"),
        CheckConstraint("units_per_package IS NULL OR units_per_package > 0", name="ck_inventory_units_pkg_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    count: Mapped[int] = mapped_column(Integer, default=1)
    # The amount value; what it's measured in is vial_size_unit (mg/mcg/IU). Column name kept for
    # backward compatibility even though it's no longer always mg.
    vial_size_mg: Mapped[float | None] = mapped_column(Float)
    vial_size_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit), default=DoseUnit.MG)
    medium: Mapped[Medium | None] = mapped_column(
        Enum(Medium, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])
    )
    # Liquid: how much liquid is in the container (concentration = vial_size_mg / volume_ml when the unit is mg).
    volume_ml: Mapped[float | None] = mapped_column(Float)
    # Autoinjector: doses/clicks per pen. Pill: pills per bottle.
    units_per_package: Mapped[int | None] = mapped_column(Integer)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer)  # None -> User.low_stock_default
    storage: Mapped[StorageLocation | None] = mapped_column(_enum_column(StorageLocation))
    # Money is stored as integer cents to avoid floating-point rounding.
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    # Free-text vendor name, kept in sync with vendor_id's Vendor.name so existing display code needs
    # no changes; vendor_id is the source of truth once set.
    vendor: Mapped[str | None] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    category: Mapped[Category] = mapped_column(_enum_column(Category), default=Category.MEDICINE)
    reconstituted_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100

    order_items: Mapped[list["OrderItem"]] = relationship(
        back_populates="inventory_item", cascade="all, delete-orphan")

    sales: Mapped[list["Sale"]] = relationship(
        back_populates="inventory_item", order_by="Sale.sale_date.desc()", cascade="all, delete-orphan")

    @property
    def available_count(self) -> int:
        """Medicine/BAC Water: received quantity (from checked-in orders only) minus
        reconstituted/sold. Supply: the plain count column. A short or damaged receipt caught at
        check-in never inflates this -- only OrderItem.received_quantity counts, never the
        original quantity ordered."""
        if self.category == Category.SUPPLY:
            return self.count
        arrived = sum(
            (li.received_quantity or 0) for li in self.order_items if li.order.arrival_date is not None)
        return arrived - self.reconstituted_count - self.sold_count


class Order(Base):
    """One shipment/vendor order, possibly containing several items (see OrderItem). Filling in
    arrival_date is the single combined "checked in" action -- see OrderItem.received_quantity."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("tax_cents IS NULL OR tax_cents >= 0", name="ck_order_tax_nonneg"),
        CheckConstraint("shipping_cents IS NULL OR shipping_cents >= 0", name="ck_order_shipping_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_date: Mapped[date] = mapped_column(Date)
    shipped_date: Mapped[date | None] = mapped_column(Date)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    tracking_site: Mapped[str | None] = mapped_column(String(500))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    vendor: Mapped[str | None] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    shipping_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    @property
    def tax(self) -> float | None:
        return None if self.tax_cents is None else self.tax_cents / 100

    @property
    def shipping(self) -> float | None:
        return None if self.shipping_cents is None else self.shipping_cents / 100


def _allocate_across_lines(total_cents: int | None, lines: list["OrderItem"]) -> dict[int, int]:
    """Splits `total_cents` across `lines` weighted by each line's own cost_cents, using largest-remainder
    rounding so the parts sum exactly back to `total_cents` -- no dropped or invented cent. Lines with no
    cost_cents (None or 0), or when total_weight is 0, get 0. Keyed by each line's persisted `id` (these
    are always already-committed rows when this runs, since allocation is computed at display time, never
    during the same transaction that creates them)."""
    if not total_cents or not lines:
        return {li.id: 0 for li in lines}
    weights = [(li, li.cost_cents or 0) for li in lines]
    total_weight = sum(w for _, w in weights)
    if total_weight == 0:
        return {li.id: 0 for li in lines}
    shares: dict[int, int] = {}
    remainders: list[tuple[float, "OrderItem"]] = []
    allocated = 0
    for li, w in weights:
        exact = total_cents * w / total_weight
        floor = int(exact)
        shares[li.id] = floor
        allocated += floor
        remainders.append((exact - floor, li))
    leftover = total_cents - allocated
    remainders.sort(key=lambda r: r[0], reverse=True)
    for i in range(leftover):
        shares[remainders[i][1].id] += 1
    return shares


class OrderItem(Base):
    """One line of an Order: a quantity of one InventoryItem, with its own cost/lot/expiration/COA
    (different peptides, different lots of the same peptide, and BAC Water can each carry their
    own COA/lot/expiration even within one shipment). received_quantity is null until the parent
    Order is checked in; check-in fills it in for every line at once (pre-filled to `quantity`,
    editable down for anything short or damaged) -- nothing here counts toward
    InventoryItem.available_count until then."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_pos"),
        CheckConstraint(
            "received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)",
            name="ck_order_item_received_range"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="ck_order_item_cost_nonneg"),
        CheckConstraint("coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0", name="ck_order_item_coa_vial_size_pos"),
        CheckConstraint("coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)",
                        name="ck_order_item_coa_purity_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    received_quantity: Mapped[int | None] = mapped_column(Integer)
    received_note: Mapped[str | None] = mapped_column(String(300))
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    lot_number: Mapped[str | None] = mapped_column(String(100))
    expiration_date: Mapped[date | None] = mapped_column(Date)
    coa_filename: Mapped[str | None] = mapped_column(String(100))
    coa_vial_size_mg: Mapped[float | None] = mapped_column(Float)
    coa_purity_pct: Mapped[float | None] = mapped_column(Float)

    order: Mapped["Order"] = relationship(back_populates="items")
    inventory_item: Mapped["InventoryItem"] = relationship(back_populates="order_items")

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100

    @property
    def allocated_shipping_cents(self) -> int:
        return _allocate_across_lines(self.order.shipping_cents, self.order.items).get(self.id, 0)

    @property
    def allocated_tax_cents(self) -> int:
        return _allocate_across_lines(self.order.tax_cents, self.order.items).get(self.id, 0)

    @property
    def total_cost(self) -> float | None:
        """This line's own cost plus its share of the order's shipping/tax, for a per-vial cost
        that accounts for what the whole shipment actually cost -- None if this line has no cost
        at all (nothing to allocate onto)."""
        if self.cost_cents is None:
            return None
        return (self.cost_cents + self.allocated_shipping_cents + self.allocated_tax_cents) / 100


class Sale(Base):
    """One sale transaction out of a Medicine or BAC Water InventoryItem's available_count. Pure
    history -- InventoryItem.sold_count (incremented alongside each Sale) is what available_count
    actually subtracts, the same relationship Order has to arrived count. A Medicine sale that
    bundles BAC Water creates two independent Sale rows (one per item, same sale_date) -- there is
    no link between them; each item's history is tracked on its own for accurate per-item cost
    tracking. Sales are append-only: no edit or delete route exists."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_quantity_pos"),
        CheckConstraint("price_cents >= 0", name="ck_sale_price_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    sale_date: Mapped[date] = mapped_column(Date)
    price_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    inventory_item: Mapped["InventoryItem"] = relationship(back_populates="sales")

    @property
    def price(self) -> float:
        return self.price_cents / 100


class ActiveVial(Base):
    """A reconstituted, opened vial -- created from an InventoryItem, decrementing its count by 1.
    Doses remaining is a static snapshot computed once at creation (see app.calculator.reconstitution);
    it never depletes in this pass, since there is no dose-logging feature yet to draw it down."""

    __tablename__ = "active_vials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    concentration_mg_ml: Mapped[float] = mapped_column(Float)
    water_ml: Mapped[float] = mapped_column(Float)
    dose_value: Mapped[float] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit))
    doses_total: Mapped[int] = mapped_column(Integer)
    dispensing_method: Mapped[DispensingMethod] = mapped_column(_enum_column(DispensingMethod), default=DispensingMethod.SYRINGE)
    volume_remaining_ml: Mapped[float] = mapped_column(Float)
    date_mixed: Mapped[date] = mapped_column(Date)
    discard_by: Mapped[date] = mapped_column(Date)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_discard_prompt_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow().replace(tzinfo=None))

    inventory_item: Mapped["InventoryItem"] = relationship()


# ---------------------------------------------------------------- protocols
# (LabeledEnum, _enum_column and DoseUnit are defined above, near Medium, since InventoryItem needs them too.)

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


class DoseLog(Base):
    """One due-item-on-one-date outcome: logged (on time or late) or skipped. A day that passes
    with nothing logged is "missed" -- inferred at read time by diffing occurrences() against
    existing rows here, never written as its own row (see app.calendar.schedule.missed_items).
    Denormalizes peptide/dose/route/time_of_day at log time rather than trusting
    protocol_item_id to keep meaning -- editing a saved Protocol clears and rebuilds all its
    ProtocolItem rows (see app.protocols' save_protocol), so a hard FK there would silently lose
    history on every edit. protocol_item_id is kept as a nullable, best-effort deep link only."""

    __tablename__ = "dose_logs"
    __table_args__ = (
        CheckConstraint("dose_value IS NULL OR dose_value > 0", name="ck_dose_log_dose_pos"),
        CheckConstraint("volume_ml IS NULL OR volume_ml > 0", name="ck_dose_log_volume_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    protocol_id: Mapped[int] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"), index=True)
    protocol_item_id: Mapped[int | None] = mapped_column(ForeignKey("protocol_items.id", ondelete="SET NULL"))
    active_vial_id: Mapped[int | None] = mapped_column(ForeignKey("active_vials.id", ondelete="SET NULL"))
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="RESTRICT"))
    peptide_name: Mapped[str] = mapped_column(String(120))
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit))
    route: Mapped[str] = mapped_column(String(20))
    scheduled_date: Mapped[date] = mapped_column(Date)
    scheduled_time_of_day: Mapped[TimeOfDay] = mapped_column(_enum_column(TimeOfDay))
    status: Mapped[DoseStatus] = mapped_column(_enum_column(DoseStatus))
    logged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    injection_site: Mapped[InjectionSite | None] = mapped_column(_enum_column(InjectionSite))
    volume_ml: Mapped[float | None] = mapped_column(Float)

    protocol: Mapped["Protocol"] = relationship()
    active_vial: Mapped["ActiveVial | None"] = relationship()


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
    email: Mapped[str | None] = mapped_column(String(320))
    timezone: Mapped[str | None] = mapped_column(String(64))
    colorway: Mapped[Colorway | None] = mapped_column(_enum_column(Colorway))
    default_discard_days: Mapped[int | None] = mapped_column(Integer)
    low_stock_default: Mapped[int | None] = mapped_column(Integer)  # None -> 5 at render time
    shipment_delay_days: Mapped[int | None] = mapped_column(Integer)  # None -> 21 at render time
    sex: Mapped[BiologicalSex | None] = mapped_column(_enum_column(BiologicalSex))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_in: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[ActivityLevel | None] = mapped_column(_enum_column(ActivityLevel))
    macro_goal: Mapped[MacroGoal | None] = mapped_column(_enum_column(MacroGoal))
    diet_preset: Mapped[DietPreset | None] = mapped_column(_enum_column(DietPreset))
    custom_protein_pct: Mapped[int | None] = mapped_column(Integer)
    custom_carb_pct: Mapped[int | None] = mapped_column(Integer)
    custom_fat_pct: Mapped[int | None] = mapped_column(Integer)
    water_goal_oz: Mapped[int | None] = mapped_column(Integer)

    @property
    def initial(self) -> str:
        return self.username[:1].upper()


class Share(Base):
    """A one-directional grant: owner_id lets grantee_id view one category of their data.
    Revocation is just deleting the row. No accept/pending flow -- the owner's grant is final."""

    __tablename__ = "shares"
    __table_args__ = (UniqueConstraint("owner_id", "grantee_id", "category", name="uq_share_owner_grantee_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    grantee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[ShareCategory] = mapped_column(_enum_column(ShareCategory))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class BodyMeasurement(Base):
    """One weigh-in/measurement session. Every field nullable -- log just weight some days, a full
    tape-measure session on others. Bilateral parts store both sides; the silhouette/charts show
    their average (Task 5), the entry's own detail view shows both raw numbers."""
    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint("weight_lbs IS NULL OR weight_lbs > 0", name="ck_body_measurement_weight_pos"),
        CheckConstraint("systolic IS NULL OR systolic > 0", name="ck_body_measurement_systolic_pos"),
        CheckConstraint("diastolic IS NULL OR diastolic > 0", name="ck_body_measurement_diastolic_pos"),
        CheckConstraint("neck_in IS NULL OR neck_in > 0", name="ck_body_measurement_neck_pos"),
        CheckConstraint("waist_in IS NULL OR waist_in > 0", name="ck_body_measurement_waist_pos"),
        CheckConstraint("hips_in IS NULL OR hips_in > 0", name="ck_body_measurement_hips_pos"),
        CheckConstraint("biceps_l_in IS NULL OR biceps_l_in > 0", name="ck_body_measurement_biceps_l_pos"),
        CheckConstraint("biceps_r_in IS NULL OR biceps_r_in > 0", name="ck_body_measurement_biceps_r_pos"),
        CheckConstraint("forearm_l_in IS NULL OR forearm_l_in > 0", name="ck_body_measurement_forearm_l_pos"),
        CheckConstraint("forearm_r_in IS NULL OR forearm_r_in > 0", name="ck_body_measurement_forearm_r_pos"),
        CheckConstraint("quad_l_in IS NULL OR quad_l_in > 0", name="ck_body_measurement_quad_l_pos"),
        CheckConstraint("quad_r_in IS NULL OR quad_r_in > 0", name="ck_body_measurement_quad_r_pos"),
        CheckConstraint("calf_l_in IS NULL OR calf_l_in > 0", name="ck_body_measurement_calf_l_pos"),
        CheckConstraint("calf_r_in IS NULL OR calf_r_in > 0", name="ck_body_measurement_calf_r_pos"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    measured_at: Mapped[date] = mapped_column(Date, index=True)
    weight_lbs: Mapped[float | None] = mapped_column(Float)
    systolic: Mapped[int | None] = mapped_column(Integer)
    diastolic: Mapped[int | None] = mapped_column(Integer)
    neck_in: Mapped[float | None] = mapped_column(Float)
    waist_in: Mapped[float | None] = mapped_column(Float)
    hips_in: Mapped[float | None] = mapped_column(Float)
    biceps_l_in: Mapped[float | None] = mapped_column(Float)
    biceps_r_in: Mapped[float | None] = mapped_column(Float)
    forearm_l_in: Mapped[float | None] = mapped_column(Float)
    forearm_r_in: Mapped[float | None] = mapped_column(Float)
    quad_l_in: Mapped[float | None] = mapped_column(Float)
    quad_r_in: Mapped[float | None] = mapped_column(Float)
    calf_l_in: Mapped[float | None] = mapped_column(Float)
    calf_r_in: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JournalEntry(Base):
    """One row per user per calendar day. Auto-created by the first quick note of the day if no
    full entry exists yet (mood/energy/sleep_quality/side effects left null/empty); a full-form
    save on a day that already has a row edits it in place rather than creating a duplicate."""
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("owner_id", "entry_date", name="uq_journal_entry_owner_date"),
        CheckConstraint("mood IS NULL OR mood BETWEEN 1 AND 5", name="ck_journal_entry_mood_range"),
        CheckConstraint("energy IS NULL OR energy BETWEEN 1 AND 5", name="ck_journal_entry_energy_range"),
        CheckConstraint("sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5", name="ck_journal_entry_sleep_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    mood: Mapped[int | None] = mapped_column(Integer)
    energy: Mapped[int | None] = mapped_column(Integer)
    sleep_quality: Mapped[int | None] = mapped_column(Integer)
    side_effects_other: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    side_effects: Mapped[list["JournalEntrySideEffect"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan")
    quick_notes: Mapped[list["JournalQuickNote"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalQuickNote.noted_at")


class JournalEntrySideEffect(Base):
    """One flag row per checked side-effect tag -- a fixed enum, not a user-addable lookup table."""
    __tablename__ = "journal_entry_side_effects"
    __table_args__ = (
        UniqueConstraint("entry_id", "side_effect", name="uq_journal_entry_side_effect"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    side_effect: Mapped[JournalSideEffect] = mapped_column(_enum_column(JournalSideEffect))

    entry: Mapped["JournalEntry"] = relationship(back_populates="side_effects")


class JournalQuickNote(Base):
    """A single dashboard quick-capture note, timestamped to when it was written. Displayed
    underneath the day's main `notes` field, never merged into it."""
    __tablename__ = "journal_quick_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    text: Mapped[str] = mapped_column(Text)

    entry: Mapped["JournalEntry"] = relationship(back_populates="quick_notes")


class LabPanel(Base):
    """One draw/visit's worth of results, optionally with the lab's own report attached."""
    __tablename__ = "lab_panels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    drawn_at: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    report_filename: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    results: Mapped[list["LabResult"]] = relationship(
        back_populates="panel", cascade="all, delete-orphan")


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (
        CheckConstraint("range_low IS NULL OR range_high IS NULL OR range_low <= range_high",
                        name="ck_lab_result_range_order"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("lab_panels.id", ondelete="CASCADE"), index=True)
    marker: Mapped[LabMarker] = mapped_column(_enum_column(LabMarker))
    marker_other: Mapped[str | None] = mapped_column(String(80))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    range_low: Mapped[float | None] = mapped_column(Float)
    range_high: Mapped[float | None] = mapped_column(Float)

    panel: Mapped["LabPanel"] = relationship(back_populates="results")

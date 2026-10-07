import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive_utcnow() -> datetime:
    """UTC without a timezone, the way the app's sessions and ingest times are kept."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


class SupplyType(LabeledEnum):
    """What a Supply item is, so reconstitution can find the right one to use up."""
    RECON_SYRINGE = ("reconstitution_syringe", "Reconstitution Syringe")
    DOSING_SYRINGE = ("dosing_syringe", "Dosing Syringe")
    ALCOHOL_PAD = ("alcohol_prep_pad", "Alcohol Prep Pad")
    PEN_VIAL = ("peptide_pen_vial", "Peptide Pen Vial")
    PEN_NEEDLE = ("peptide_pen_needle", "Peptide Pen Needle")
    STERILE_VIAL = ("sterile_vial_10ml", "10mL Sterile Vial")
    PEPTIDE_FILTER = ("peptide_filter", "Peptide Filter")


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
    wallets: Mapped[list["VendorWallet"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan", order_by="VendorWallet.id")


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


WALLET_COINS = ("BTC", "ETH", "USDC", "USDT")
BODY_PHOTO_ANGLES = ("front", "side", "back", "other")
FOOD_MEALS = ("breakfast", "lunch", "dinner", "snack")
FOOD_MEAL_LABELS = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snacks"}


class VendorWallet(Base):
    """A crypto address a vendor accepts payment at: the coin, the address, the network it lives on (USDT on
    Tron is not USDT on Ethereum) and, optionally, a photo of the vendor's QR code. Shared like the vendor."""
    __tablename__ = "vendor_wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    coin: Mapped[str] = mapped_column(String(8))
    address: Mapped[str] = mapped_column(String(200))
    network: Mapped[str | None] = mapped_column(String(60))
    qr_filename: Mapped[str | None] = mapped_column(String(120))

    vendor: Mapped["Vendor"] = relationship(back_populates="wallets")


class VendorFavorite(Base):
    """Per-user favorite marker. Row exists = favorited -- insert/delete, no boolean to toggle,
    mirroring Share's existence-is-the-grant pattern."""
    __tablename__ = "vendor_favorites"
    __table_args__ = (UniqueConstraint("user_id", "vendor_id", name="uq_vendor_favorite"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)


class Warehouse(LabeledEnum):
    US = ("us", "US")
    CHINA = ("china", "China")


class WarehouseSource(LabeledEnum):
    FILENAME = ("filename", "Filename")
    TEXT = ("text", "List text")
    ASSUMED = ("assumed", "Assumed")
    MANUAL = ("manual", "Set manually")


class PriceList(Base):
    """One imported vendor price list. Reference data (like the peptide library), not scoped to a user."""

    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("source_filename", name="uq_price_list_source_filename"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(String(200))  # kept even if the vendor row is later deleted
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), index=True)
    warehouse: Mapped[Warehouse] = mapped_column(_enum_column(Warehouse), default=Warehouse.CHINA)
    warehouse_source: Mapped[WarehouseSource] = mapped_column(
        _enum_column(WarehouseSource), default=WarehouseSource.ASSUMED)
    list_date: Mapped[date] = mapped_column(Date)
    source_filename: Mapped[str] = mapped_column(String(300))
    shipping_note: Mapped[str | None] = mapped_column(Text)  # the vendor's own wording, not interpreted
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["PriceListItem"]] = relationship(
        back_populates="price_list", cascade="all, delete-orphan")


class PriceListItem(Base):
    """One product line of a price list. A pack is a kit (exactly 10 vials) or a box (fewer). Per-vial cost is
    pack_price / pack_size (computed, not stored)."""

    __tablename__ = "price_list_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.id", ondelete="CASCADE"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))
    product_name: Mapped[str | None] = mapped_column(String(300))
    peptide_id: Mapped[int | None] = mapped_column(ForeignKey("peptides.id", ondelete="SET NULL"), index=True)
    vial_amount: Mapped[float] = mapped_column(Float)
    vial_unit: Mapped[str] = mapped_column(String(10))  # mg | mcg | IU | ml | mg/ml
    pack_size: Mapped[int | None] = mapped_column(Integer)  # vials in the pack the price buys
    pack_price: Mapped[float | None] = mapped_column(Float)
    pack_type: Mapped[str | None] = mapped_column(String(10))  # "kit" (exactly 10 vials) | "box" (fewer) | None
    extra_prices: Mapped[dict | None] = mapped_column(JSON)  # other price columns by header, e.g. {"10kits+": 173}
    flags: Mapped[list | None] = mapped_column(JSON)

    price_list: Mapped["PriceList"] = relationship(back_populates="items")


class PriceAlertIgnore(Base):
    """A product the administrator marked "not a peptide": it never raises a new-peptide alert. Shared, like
    the price data itself."""

    __tablename__ = "price_alert_ignores"
    __table_args__ = (UniqueConstraint("product_key", name="uq_price_alert_ignore_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_key: Mapped[str] = mapped_column(String(300))  # the matcher's name_key of the product
    product_name: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchasingUnit(LabeledEnum):
    INDIVIDUAL = ("individual", "Individual vial")
    KIT_OF_10 = ("kit_of_10", "Kit of 10 vials")


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
    # Only meaningful for Category.MEDICINE (same gate vial_size_mg/medium already use) -- drives
    # an as-needed protocol item's flat course-total quantity (1 vial vs. a 10-vial kit).
    purchasing_unit: Mapped["PurchasingUnit"] = mapped_column(_enum_column(PurchasingUnit), default=PurchasingUnit.INDIVIDUAL)
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
    local_seller: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")      # picked up in person: no shipping wait
    category: Mapped[Category] = mapped_column(_enum_column(Category), default=Category.MEDICINE)
    reconstituted_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    # Supply items only: which supply this is (alcohol pad, recon syringe, ...). BAC Water only: 1 = used first, 4 = last, none = after all ranked.
    supply_type: Mapped[SupplyType | None] = mapped_column(_enum_column(SupplyType))
    bac_priority: Mapped[int | None] = mapped_column(Integer)
    # IU vials (and IU doses): how many IU are in 1 mg of this product (about 3 for HGH). Empty until it is entered in the calculator.
    iu_per_mg: Mapped[float | None] = mapped_column(Float)
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

    def _checked_in_lines(self):
        return [li for li in self.order_items if li.order.arrival_date is not None and (li.received_quantity or 0) > 0]

    @property
    def next_expiration(self) -> date | None:
        """The earliest expiration date among the lines that were checked in (None when none has one): what to use first."""
        return min((li.expiration_date for li in self._checked_in_lines() if li.expiration_date), default=None)

    @property
    def first_arrival(self) -> date | None:
        """When the oldest checked-in stock arrived (FIFO)."""
        return min((li.order.arrival_date for li in self._checked_in_lines()), default=None)


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
    delivered_date: Mapped[date | None] = mapped_column(Date)   # the package reached the door; check-in (arrival_date) comes after
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
    # A BAC Water vial (opened bottle) has no concentration or doses: those four are empty for it, and water_ml is the bottle's volume.
    concentration_mg_ml: Mapped[float | None] = mapped_column(Float)
    water_ml: Mapped[float] = mapped_column(Float)
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit | None] = mapped_column(_enum_column(DoseUnit))
    doses_total: Mapped[int | None] = mapped_column(Integer)
    dispensing_method: Mapped[DispensingMethod] = mapped_column(_enum_column(DispensingMethod), default=DispensingMethod.SYRINGE)
    # The unit the vial is measured in (mg, mcg or IU): concentration_mg_ml is then per mL in THIS unit (the column name is historic).
    vial_unit: Mapped[str] = mapped_column(String(10), default="mg", server_default="mg")
    iu_per_mg: Mapped[float | None] = mapped_column(Float)     # bridges IU and mass doses for this vial
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
    """In the order they happen in a day; the calendar and Today list doses in this order and show only the ones in use."""
    FASTING = ("fasting", "Fasting")
    WAKING = ("waking", "Waking")                        # right after waking
    AM = ("am", "AM")                                    # first half of the day
    PRE_WORKOUT = ("pre_workout", "Pre-workout")         # up to an hour before
    POST_WORKOUT = ("post_workout", "Post-workout")      # up to an hour after
    PM = ("pm", "PM")                                    # second half of the day
    BEFORE_BED = ("before_bed", "Before bed")            # up to an hour before
    BEDTIME = ("bedtime", "Bedtime")
    ANY = ("any", "Any time")


TIME_ORDER = {m: n for n, m in enumerate(TimeOfDay)}


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
    SHEET = ("sheet", "Reference sheet")


class DosingTierLevel(str, enum.Enum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class StackRelation(str, enum.Enum):
    WORKS_WITH = "works_with"
    AVOID = "avoid"


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

    # Reference sheet columns
    half_life_text: Mapped[str | None] = mapped_column(String(100))
    bioavailability_text: Mapped[str | None] = mapped_column(Text)
    tmax_text: Mapped[str | None] = mapped_column(String(100))
    route_summary: Mapped[str | None] = mapped_column(String(100))
    storage_before_text: Mapped[str | None] = mapped_column(Text)
    storage_after_text: Mapped[str | None] = mapped_column(Text)
    storage_temperature_text: Mapped[str | None] = mapped_column(String(100))
    legal_status_text: Mapped[str | None] = mapped_column(Text)
    cost_estimate_text: Mapped[str | None] = mapped_column(Text)
    usage_tips: Mapped[list | None] = mapped_column(JSON)
    sheet_sections: Mapped[dict | None] = mapped_column(JSON)
    sheet_sections_simple: Mapped[dict | None] = mapped_column(JSON)
    tags: Mapped[list | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    # Standard vial size from library card for course totals fallback when no inventory is linked.
    normally_supplied_amount: Mapped[float | None] = mapped_column(Float)
    normally_supplied_unit: Mapped[DoseUnit | None] = mapped_column(_enum_column(DoseUnit))
    # Comma-separated available specifications from price lists (e.g., "5mg, 10mg, 15mg, 20mg")
    # Used to prioritize commonly-available peptides in Protocol Builder selector.
    library_specifications: Mapped[str | None] = mapped_column(Text)

    dosing_tiers: Mapped[list["PeptideDosingTier"]] = relationship(
        back_populates="peptide", cascade="all, delete-orphan")
    cycle: Mapped["PeptideCycle | None"] = relationship(back_populates="peptide", cascade="all, delete-orphan")
    stack_relations: Mapped[list["PeptideStackRelation"]] = relationship(
        back_populates="peptide", cascade="all, delete-orphan")
    monitoring_tests: Mapped[list["PeptideMonitoringTest"]] = relationship(
        back_populates="peptide", cascade="all, delete-orphan")


class PeptideDosingTier(Base):
    """One row per level (Beginner/Intermediate/Advanced) for a peptide's community dosing guide."""
    __tablename__ = "peptide_dosing_tiers"
    __table_args__ = (UniqueConstraint("peptide_id", "level", name="uq_peptide_dosing_tier_level"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    level: Mapped[DosingTierLevel] = mapped_column(_enum_column(DosingTierLevel))
    dose_text: Mapped[str | None] = mapped_column(String(100))
    frequency_text: Mapped[str | None] = mapped_column(String(100))
    time_of_day: Mapped[TimeOfDay | None] = mapped_column(_enum_column(TimeOfDay))

    peptide: Mapped["Peptide"] = relationship(back_populates="dosing_tiers")


class PeptideCycle(Base):
    __tablename__ = "peptide_cycles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), unique=True)
    on_weeks: Mapped[int | None] = mapped_column(Integer)
    off_weeks: Mapped[int | None] = mapped_column(Integer)
    max_cycles_per_year: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    peptide: Mapped["Peptide"] = relationship(back_populates="cycle")


class PeptideStackRelation(Base):
    __tablename__ = "peptide_stack_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    partner_name: Mapped[str] = mapped_column(String(120))
    relation: Mapped[StackRelation] = mapped_column(_enum_column(StackRelation))
    note: Mapped[str] = mapped_column(Text)

    peptide: Mapped["Peptide"] = relationship(back_populates="stack_relations")


class PeptideMonitoringTest(Base):
    __tablename__ = "peptide_monitoring_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(120))
    when_text: Mapped[str | None] = mapped_column(String(200))
    why_text: Mapped[str | None] = mapped_column(Text)
    target_text: Mapped[str | None] = mapped_column(String(200))

    peptide: Mapped["Peptide"] = relationship(back_populates="monitoring_tests")


class PeptideNote(Base):
    """A person's private note or saved article about a library peptide (Peptide Learning)."""

    __tablename__ = "peptide_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    cycle_offs: Mapped[list["ProtocolItemCycleOff"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="ProtocolItemCycleOff.start_week")


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


class ProtocolItemCycleOff(Base):
    """A week-range, relative to the protocol's start_date, during which this item is never due --
    regardless of its frequency or any titration step that would otherwise apply. "On" is simply
    "not covered by any row here"; there is no separate "on" row type."""
    __tablename__ = "protocol_item_cycle_offs"
    __table_args__ = (
        CheckConstraint("start_week >= 1", name="ck_cycle_off_start_week"),
        CheckConstraint("end_week >= start_week", name="ck_cycle_off_end_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    protocol_item_id: Mapped[int] = mapped_column(ForeignKey("protocol_items.id", ondelete="CASCADE"))
    start_week: Mapped[int] = mapped_column(Integer)
    end_week: Mapped[int] = mapped_column(Integer)


class DoseReminder(Base):
    """A reminder already sent, so a dose is reminded once per day."""
    __tablename__ = "dose_reminders"
    __table_args__ = (UniqueConstraint("user_id", "protocol_item_id", "for_date", name="uq_dose_reminder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    protocol_item_id: Mapped[int] = mapped_column(Integer)
    for_date: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # What the Shop-this-protocol plan assumes shipping costs per vendor order, in cents (empty = the app's default: $60 China, $30 US).
    shop_china_shipping_cents: Mapped[int | None] = mapped_column(Integer)
    shop_us_shipping_cents: Mapped[int | None] = mapped_column(Integer)
    auto_print_labels: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")      # open the vial labels when an order is checked in
    label_size: Mapped[str] = mapped_column(String(10), default="5160", server_default="5160")
    calendar_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)      # secret address of the private iCal feed; none = feed off
    ntfy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")      # push the dose reminders to ntfy
    ntfy_topic: Mapped[str | None] = mapped_column(String(100))
    low_stock_default: Mapped[int | None] = mapped_column(Integer)  # None -> 5 at render time
    shipment_delay_days: Mapped[int | None] = mapped_column(Integer)  # None -> 21 at render time
    photo_2fa_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    sex: Mapped[BiologicalSex | None] = mapped_column(_enum_column(BiologicalSex))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_in: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[ActivityLevel | None] = mapped_column(_enum_column(ActivityLevel))
    macro_goal: Mapped[MacroGoal | None] = mapped_column(_enum_column(MacroGoal))
    diet_preset: Mapped[DietPreset | None] = mapped_column(_enum_column(DietPreset))
    life_stage: Mapped[str | None] = mapped_column(String(20))  # a key of app.measurements.tdee.LIFE_STAGES; women only
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
    photo_unlocked_until: Mapped[datetime | None] = mapped_column(DateTime)

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
        CheckConstraint("heart_rate_bpm IS NULL OR heart_rate_bpm > 0", name="ck_body_measurement_heart_rate_pos"),
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
    heart_rate_bpm: Mapped[int | None] = mapped_column(Integer)
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


class WaterLog(Base):
    """One "+ Log water" tap from the Dashboard's Water goal panel. Several rows accumulate per
    day -- the panel sums today's rows against the goal rather than this table holding one
    running total, so a day's history stays a normal append-only log like DoseLog."""
    __tablename__ = "water_logs"
    __table_args__ = (
        CheckConstraint("ounces > 0", name="ck_water_log_ounces_pos"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    logged_at: Mapped[date] = mapped_column(Date, index=True)
    ounces: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BodyPhoto(Base):
    """A private progress photo, owner-only. `filename` is a random name of a JPEG in config.BODY_PHOTO_DIR."""
    __tablename__ = "body_photos"
    __table_args__ = (
        CheckConstraint("angle IS NULL OR angle IN ('front', 'side', 'back', 'other')", name="ck_body_photo_angle"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    taken_on: Mapped[date] = mapped_column(Date)
    angle: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Food(Base):
    """A food with its numbers for one serving. Starter foods (owner_id NULL) ship with the app and are read-only."""
    __tablename__ = "foods"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", "serving", name="uq_food_owner_name_serving"),
        Index("uq_food_starter_name_serving", "name", "serving", unique=True, sqlite_where=text("owner_id IS NULL")),
        CheckConstraint("source IN ('starter', 'mine')", name="ck_food_source"),
        CheckConstraint("(source = 'starter' AND owner_id IS NULL) OR (source = 'mine' AND owner_id IS NOT NULL)",
                        name="ck_food_owner_source"),
        CheckConstraint("calories >= 0 AND protein_g >= 0 AND carb_g >= 0 AND fat_g >= 0 AND fiber_g >= 0",
                        name="ck_food_nonnegative"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    serving: Mapped[str] = mapped_column(String(60))
    serving_g: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carb_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    fiber_g: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FoodLog(Base):
    """One thing eaten. The name, serving and numbers are a snapshot taken when it was logged, so changing or deleting
    the food later never rewrites history."""
    __tablename__ = "food_logs"
    __table_args__ = (
        CheckConstraint("meal IN ('breakfast', 'lunch', 'dinner', 'snack')", name="ck_food_log_meal"),
        CheckConstraint("servings > 0 AND servings <= 50", name="ck_food_log_servings"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    eaten_on: Mapped[date] = mapped_column(Date, index=True)
    meal: Mapped[str] = mapped_column(String(10))
    food_id: Mapped[int | None] = mapped_column(ForeignKey("foods.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    serving: Mapped[str] = mapped_column(String(60))
    servings: Mapped[float] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carb_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    fiber_g: Mapped[float] = mapped_column(Float)
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
    custom_effects: Mapped[list["JournalEntryCustomEffect"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan")
    quick_notes: Mapped[list["JournalQuickNote"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalQuickNote.noted_at")


class JournalCustomEffect(Base):
    """A side effect a person added to their own list, offered as a tick box on every journal entry."""
    __tablename__ = "journal_custom_effects"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_journal_custom_effect"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))


class JournalEntryCustomEffect(Base):
    """A custom side effect ticked on one entry. The name is kept as written, so removing it from the list never changes past days."""
    __tablename__ = "journal_entry_custom_effects"
    __table_args__ = (UniqueConstraint("entry_id", "name", name="uq_journal_entry_custom_effect"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))

    entry: Mapped["JournalEntry"] = relationship(back_populates="custom_effects")


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
    qualifier: Mapped[str | None] = mapped_column(String(1))      # "<" or ">" when the lab reported "less than" / "greater than" this value
    unit: Mapped[str | None] = mapped_column(String(20))
    range_low: Mapped[float | None] = mapped_column(Float)
    range_high: Mapped[float | None] = mapped_column(Float)

    panel: Mapped["LabPanel"] = relationship(back_populates="results")


# ---------------------------------------------------------------- exercise

class WorkoutSource(str, enum.Enum):
    PDF = "pdf"
    MANUAL = "manual"


class WeightUnit(LabeledEnum):
    LB = ("lb", "lb")
    KG = ("kg", "kg")


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[WorkoutSource] = mapped_column(_enum_column(WorkoutSource))
    source_pdf_filename: Mapped[str | None] = mapped_column(String(100))
    started_on: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    days: Mapped[list["WorkoutPlanDay"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", passive_deletes=True,
        order_by="WorkoutPlanDay.position")


class WorkoutPlanDay(Base):
    __tablename__ = "workout_plan_days"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("workout_plans.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    weekdays: Mapped[str | None] = mapped_column(String(7))  # subset of WEEKDAY_LETTERS, e.g. "MWF"

    plan: Mapped["WorkoutPlan"] = relationship(back_populates="days")
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="WorkoutExercise.position")


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    sets_text: Mapped[str | None] = mapped_column(String(50))
    reps_text: Mapped[str | None] = mapped_column(String(50))
    rest_text: Mapped[str | None] = mapped_column(String(50))
    # The database exercise this plan row was matched to (see app/workouts/exercise_match.py). A person-confirmed
    # match survives edits; an automatic one is recomputed when the name changes.
    db_exercise: Mapped[str | None] = mapped_column(String(200))
    db_exercise_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class WorkoutLog(Base):
    """One completed instance of a WorkoutPlanDay, on a specific calendar date."""
    __tablename__ = "workout_logs"
    __table_args__ = (UniqueConstraint("plan_day_id", "log_date", name="uq_workout_log_day_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_day_id: Mapped[int | None] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="SET NULL"))
    # Snapshots, so a log reads the same after its plan day or plan is edited or removed.
    day_label: Mapped[str | None] = mapped_column(String(200))
    plan_name: Mapped[str | None] = mapped_column(String(200))
    log_date: Mapped[date] = mapped_column(Date, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    plan_day: Mapped["WorkoutPlanDay | None"] = relationship()
    exercise_logs: Mapped[list["WorkoutExerciseLog"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True)


class WorkoutExerciseLog(Base):
    """One exercise within a logged workout. Everything its calorie estimate used is stored here, so the log keeps
    reading the same after the plan or the exercise database changes, and history can be followed per exercise."""
    __tablename__ = "workout_exercise_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_log_id: Mapped[int] = mapped_column(ForeignKey("workout_logs.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("workout_exercises.id", ondelete="SET NULL"))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_value: Mapped[float | None] = mapped_column(Float)
    weight_unit: Mapped[WeightUnit | None] = mapped_column(_enum_column(WeightUnit))
    reps_value: Mapped[int | None] = mapped_column(Integer)    # reps per set, an exact whole number

    name: Mapped[str | None] = mapped_column(String(200))        # the exercise as logged
    db_exercise: Mapped[str | None] = mapped_column(String(200)) # its canonical name in the exercise database
    area: Mapped[str | None] = mapped_column(String(60))
    equipment: Mapped[str | None] = mapped_column(String(60))
    sets: Mapped[int | None] = mapped_column(Integer)
    duration_min: Mapped[float | None] = mapped_column(Float)
    speed_mph: Mapped[float | None] = mapped_column(Float)
    grade_pct: Mapped[float | None] = mapped_column(Float)
    watts: Mapped[float | None] = mapped_column(Float)
    implements: Mapped[int | None] = mapped_column(Integer)
    style: Mapped[str | None] = mapped_column(String(40))
    sec_per_rep: Mapped[float | None] = mapped_column(Float)
    rest_min: Mapped[float | None] = mapped_column(Float)
    met: Mapped[float | None] = mapped_column(Float)
    body_weight_lb: Mapped[float | None] = mapped_column(Float)
    gross_kcal: Mapped[float | None] = mapped_column(Float)
    net_kcal: Mapped[float | None] = mapped_column(Float)
    volume_lb: Mapped[float | None] = mapped_column(Float)
    compendium_code: Mapped[str | None] = mapped_column(String(10))
    kcal_note: Mapped[str | None] = mapped_column(String(200))   # why there is no estimate, when there is none


class FitnessTestExerciseName(str, enum.Enum):
    MAX_PUSHUPS = "max_pushups"
    MAX_SITUPS = "max_situps"
    MAX_BODYWEIGHT_SQUATS = "max_bodyweight_squats"
    PLANK_HOLD_SECONDS = "plank_hold_seconds"


class FitnessTestResult(Base):
    __tablename__ = "fitness_test_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[FitnessTestExerciseName] = mapped_column(_enum_column(FitnessTestExerciseName))
    value: Mapped[float] = mapped_column(Float)
    tested_at: Mapped[date] = mapped_column(Date, index=True)


INGEST_KINDS = ("pdf", "image", "xlsx", "text")
INGEST_STATUSES = ("received", "imported", "needs_review", "ignored", "duplicate", "failed", "undone", "rejected")


class IngestToken(Base):
    """A secret the price-list watcher presents. Only the SHA-256 hash is kept; the secret is shown once."""
    __tablename__ = "ingest_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(60))
    prefix: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class IngestSource(Base):
    """A chat group price lists are posted in, mapped to a vendor by the administrator."""
    __tablename__ = "ingest_sources"
    __table_args__ = (
        UniqueConstraint("platform", "chat_id", name="uq_ingest_source_chat"),
        CheckConstraint("default_warehouse IS NULL OR default_warehouse IN ('us', 'china')", name="ck_ingest_source_warehouse"),
        CheckConstraint("state IN ('active', 'gone')", name="ck_ingest_source_state"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")
    chat_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    default_warehouse: Mapped[str | None] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(10), default="active")
    state_reason: Mapped[str | None] = mapped_column(String(200))
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    alert_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
    topics_only: Mapped[bool] = mapped_column(Boolean, default=False)           # follow only the ticked topics of a forum group
    skip_words: Mapped[str | None] = mapped_column(String(1200))                # lists mentioning these words are set aside
    follow_words: Mapped[str | None] = mapped_column(String(1200), default="price, prices, pricing, pricelist, warehouse")   # a new topic named like these starts ticked


class IngestTopic(Base):
    """A topic (named thread) of a forum group. New topics start ticked only when named like the group's follow words."""
    __tablename__ = "ingest_topics"
    __table_args__ = (UniqueConstraint("source_id", "topic_id", name="uq_ingest_topic"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("ingest_sources.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)


class IngestItem(Base):
    """One file (or one typed message) received from a source. Items of one list share a `group_key`."""
    __tablename__ = "ingest_items"
    __table_args__ = (
        UniqueConstraint("source_id", "message_id", "file_hash", name="uq_ingest_item_message_file"),
        CheckConstraint("kind IN ('pdf', 'image', 'xlsx', 'text')", name="ck_ingest_item_kind"),
        CheckConstraint("status IN ('received', 'imported', 'needs_review', 'ignored', 'duplicate', 'failed', 'undone', 'rejected')",
                        name="ck_ingest_item_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("ingest_sources.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(64))
    album_id: Mapped[str | None] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(String(160), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    filename: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))
    file_hash: Mapped[str] = mapped_column(String(64))
    stored_file: Mapped[str | None] = mapped_column(String(64))
    caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="received")
    reason: Mapped[str | None] = mapped_column(String(300))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    warehouse: Mapped[str | None] = mapped_column(String(10))
    list_date: Mapped[date | None] = mapped_column(Date)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id", ondelete="SET NULL"))
    rows_found: Mapped[int | None] = mapped_column(Integer)
    rows_matched: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    topic_id: Mapped[str | None] = mapped_column(String(32))
    topic_title: Mapped[str | None] = mapped_column(String(200))


class DashboardDismissal(Base):
    """A person dismissed one dashboard alert (identified by a key such as 'newlist:12')."""
    __tablename__ = "dashboard_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "alert_key", name="uq_dashboard_dismissal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alert_key: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utcnow)

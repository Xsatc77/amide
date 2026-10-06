"""What a backup is made of: sections, the tables and files in each, and the rules for sharing and loading them.

A section is a named group of tables (plus attached files) that is exported and loaded together. Person sections are
filtered to one person's rows with the SQL in `Tbl.where`; installation sections hold shared data (every row). This
registry is data, not logic: export, load and restore all read it, and a guard test fails when a table in the schema
is neither in a section nor on the explicit NOT_BACKED_UP list, so a future table cannot be forgotten.

Rows are exchanged as the raw SQLite values (ints, floats, text, NULL), column by column, so dates, enums and JSON
round-trip exactly and a backup from an older schema loads into a newer one by column name."""

from dataclasses import dataclass

from sqlalchemy import Table
from sqlalchemy.schema import sort_tables

from app import config
from app.db import Base

PERSON, INSTALLATION = "person", "installation"
NOT_BACKED_UP = {"sessions", "alembic_version"}

# File directories attached to rows (and the library's own files), by key.
FILE_DIRS = {"coa": "COA_DIR", "price_lists": "PRICE_LIST_DIR", "lab_reports": "LAB_REPORT_DIR",
             "workout_pdfs": "WORKOUT_PDF_DIR", "wallet_qr": "WALLET_QR_DIR",
             "body_photos": "BODY_PHOTO_DIR"}

PROFILE_COLUMNS = ("sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset", "life_stage",
                   "custom_protein_pct", "custom_carb_pct", "custom_fat_pct", "water_goal_oz", "timezone", "colorway",
                   "default_discard_days", "low_stock_default", "shipment_delay_days")

_ITEMS = "SELECT id FROM inventory_items WHERE owner_id = :uid"
_PROTOCOLS = "SELECT id FROM protocols WHERE owner_id = :uid"
_PROTOCOL_ITEMS = f"SELECT id FROM protocol_items WHERE protocol_id IN ({_PROTOCOLS})"
_PLANS = "SELECT id FROM workout_plans WHERE owner_id = :uid"
_DAYS = f"SELECT id FROM workout_plan_days WHERE plan_id IN ({_PLANS})"
_ENTRIES = "SELECT id FROM journal_entries WHERE owner_id = :uid"


@dataclass(frozen=True)
class Tbl:
    name: str
    where: str | None = None                 # SQL for a person's rows (uses :uid); None = every row
    file: tuple[str, str] | None = None      # (column holding a stored file name, key of FILE_DIRS)
    share_drop: bool = False                 # left out of share files entirely
    share_null: tuple[str, ...] = ()         # columns blanked in share files (a blanked file column drops the file)
    columns: tuple[str, ...] | None = None   # only these columns are exchanged (the profile)
    reference: bool = False                  # a lookup list: an existing row with the same key is reused, not skipped


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    level: str
    tables: tuple[Tbl, ...]
    shareable: bool = False
    library_files: bool = False              # the library's card images and cards.json travel with this section
    help: str = ""


SECTIONS: dict[str, Section] = {s.key: s for s in (
    Section("profile", "Profile and preferences", PERSON, (
        Tbl("users", "id = :uid", columns=PROFILE_COLUMNS),),
        help="Body profile, goals, timezone and display preferences. Never your password, 2FA or email."),
    Section("inventory", "Inventory", PERSON, (
        Tbl("inventory_items", "owner_id = :uid"),
        Tbl("orders", f"id IN (SELECT order_id FROM order_items WHERE inventory_item_id IN ({_ITEMS}))"),
        Tbl("order_items", f"inventory_item_id IN ({_ITEMS})", file=("coa_filename", "coa")),
        Tbl("sales", f"inventory_item_id IN ({_ITEMS})", share_drop=True),
        Tbl("active_vials", "owner_id = :uid", share_drop=True)),
        shareable=True, help="Items, orders and order lines with their COA files, sales and vials in use."),
    Section("protocols", "Protocols and dose logs", PERSON, (
        Tbl("protocols", "owner_id = :uid"),
        Tbl("protocol_goals", f"protocol_id IN ({_PROTOCOLS})"),
        Tbl("protocol_items", f"protocol_id IN ({_PROTOCOLS})"),
        Tbl("titration_steps", f"protocol_item_id IN ({_PROTOCOL_ITEMS})"),
        Tbl("protocol_item_cycle_offs", f"protocol_item_id IN ({_PROTOCOL_ITEMS})"),
        Tbl("dose_logs", "owner_id = :uid", share_drop=True)),
        shareable=True, help="Protocols with their items, titration and cycle-off weeks, and your dose logs."),
    Section("workouts", "Workouts", PERSON, (
        Tbl("workout_plans", "owner_id = :uid", file=("source_pdf_filename", "workout_pdfs"),
            share_null=("source_pdf_filename",)),
        Tbl("workout_plan_days", f"plan_id IN ({_PLANS})"),
        Tbl("workout_exercises", f"day_id IN ({_DAYS})"),
        Tbl("workout_logs", "owner_id = :uid", share_drop=True),
        Tbl("workout_exercise_logs", "workout_log_id IN (SELECT id FROM workout_logs WHERE owner_id = :uid)",
            share_drop=True),
        Tbl("fitness_test_results", "owner_id = :uid", share_drop=True)),
        shareable=True, help="Plans, days and exercises, your logged workouts and fitness tests."),
    Section("measurements", "Measurements and water", PERSON, (
        Tbl("body_measurements", "owner_id = :uid"),
        Tbl("water_logs", "owner_id = :uid")), help="Weigh-ins, tape measurements, blood pressure and water logs."),
    Section("body_photos", "Body photos", PERSON, (
        Tbl("body_photos", "owner_id = :uid", file=("filename", "body_photos")),),
        help="Your progress photos with their image files. Never offered in a Share file."),
    Section("journal", "Journal", PERSON, (
        Tbl("journal_entries", "owner_id = :uid"),
        Tbl("journal_entry_side_effects", f"entry_id IN ({_ENTRIES})"),
        Tbl("journal_quick_notes", f"entry_id IN ({_ENTRIES})")), help="Daily entries, side effects and quick notes."),
    Section("labs", "Labs", PERSON, (
        Tbl("lab_panels", "owner_id = :uid", file=("report_filename", "lab_reports")),
        Tbl("lab_results", "panel_id IN (SELECT id FROM lab_panels WHERE owner_id = :uid)")),
        help="Lab panels, results and attached lab reports."),
    Section("accounts", "Accounts", INSTALLATION, (
        Tbl("users"), Tbl("shares")),
        help="Every account, with password hashes and 2FA secrets, and sharing grants. Whole-installation backups only."),
    Section("vendors", "Vendors", INSTALLATION, (
        Tbl("contact_method_types", reference=True), Tbl("payment_method_types", reference=True),
        Tbl("vendors", file=("price_list_filename", "price_lists"), share_null=("created_by_id",)),
        Tbl("vendor_contacts"), Tbl("vendor_payment_methods"),
        Tbl("vendor_wallets", file=("qr_filename", "wallet_qr"), share_drop=True),
        Tbl("vendor_favorites", share_drop=True)),
        shareable=True, help="Vendors with contacts, accepted payment methods, wallets and attached price-list files."),
    Section("price_lists", "Price lists", INSTALLATION, (
        Tbl("price_lists"), Tbl("price_list_items"), Tbl("price_alert_ignores", share_drop=True)),
        shareable=True, help="Imported vendor price lists and their items."),
    Section("library", "Library", INSTALLATION, (
        Tbl("peptides"), Tbl("goal_peptides"), Tbl("peptide_cycles"), Tbl("peptide_dosing_tiers"),
        Tbl("peptide_monitoring_tests"), Tbl("peptide_stack_relations")),
        shareable=True, library_files=True, help="Peptide library entries, cycles, tiers, and card images."),
)}

# Order sections are loaded in: shared data first, so person rows can point at it, and inventory before protocols.
LOAD_ORDER = ("library", "vendors", "price_lists", "profile", "inventory", "protocols", "workouts", "measurements",
              "body_photos", "journal", "labs")
PERSON_SECTIONS = tuple(k for k in LOAD_ORDER if SECTIONS[k].level == PERSON)
SHARED_SECTIONS = tuple(k for k in LOAD_ORDER if SECTIONS[k].level == INSTALLATION)
SHAREABLE = tuple(k for k in LOAD_ORDER if SECTIONS[k].shareable)

# A row that points outside the sections being loaded finds its target again by this natural key.
# (table, column) -> (target table, key columns)
REFS = {
    ("inventory_items", "vendor_id"): ("vendors", ("name",)),
    ("orders", "vendor_id"): ("vendors", ("name",)),
    ("protocol_items", "peptide_id"): ("peptides", ("name",)),
    ("protocol_items", "inventory_item_id"): ("inventory_items", ("name", "vial_size_mg", "vial_size_unit")),
    ("dose_logs", "peptide_id"): ("peptides", ("name",)),
    ("price_lists", "vendor_id"): ("vendors", ("name",)),
    ("price_list_items", "peptide_id"): ("peptides", ("name",)),
}
# Shared tables merged by name when an administrator adds a shared section (an existing match is skipped).
MERGE_KEYS = {
    "contact_method_types": ("name",), "payment_method_types": ("name",), "vendors": ("name",),
    "peptides": ("name",), "price_lists": ("vendor_id", "warehouse", "list_date"),
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def file_dir(key: str):
    """The directory a file key refers to, read from config at call time."""
    return getattr(config, FILE_DIRS[key])


def ordered(tbls: tuple[Tbl, ...]) -> list[Tbl]:
    """The section's tables, parents before children (foreign keys among them decide)."""
    by_name = {t.name: t for t in tbls}
    return [by_name[t.name] for t in sort_tables([table(n) for n in by_name]) if t.name in by_name]


def person_columns(tbl: Tbl) -> list[str]:
    """Columns that point at a user (the owner, or who created it): they always become the signed-in person."""
    return [fk.parent.name for fk in table(tbl.name).foreign_keys if fk.column.table.name == "users"]


def all_table_names() -> set[str]:
    return {t.name for s in SECTIONS.values() for t in s.tables}


def uncovered_tables() -> set[str]:
    """Schema tables that are neither in a section nor deliberately left out (must be empty)."""
    return set(Base.metadata.tables) - all_table_names() - NOT_BACKED_UP

REFERENCE_TABLES = {t.name for s in SECTIONS.values() for t in s.tables if t.reference}

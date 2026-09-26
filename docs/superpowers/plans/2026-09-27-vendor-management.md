# Vendor Management (Phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the full Vendor Management feature described in the spec — structured contact/
payment info, recommend flag, per-user favorites, a reference price-list attachment with a
staleness prompt, a standalone Vendors page, and a New Order flow that recalls prior prices per
vendor+item with zero extra data entry.

**Architecture:** two new "type" tables (`ContactMethodType`/`PaymentMethodType`, global,
pick-or-create like `Vendor`/`Peptide`), two new child tables (`VendorContact`,
`VendorPaymentMethod`), one new per-user join table (`VendorFavorite`), five new columns on
`Vendor`, one retired column (`contact_info`, backfilled into `notes`), a new
`app/routers/vendors.py` + `app/templates/vendors/`, an extended `app/uploads.py` for price-list
files, and changes to the existing New Order flow in `app/routers/inventory.py` +
`app/templates/inventory/list.html`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite) + Jinja2 + vanilla JS (same as the rest
of the app).

**Spec:** `docs/superpowers/specs/2026-09-27-vendor-management-design.md`

## Global Constraints

- `VendorFavorite` is strictly per-user — every list/detail query must filter by the signed-in
  user's own id; a favorite is never global.
- Purchase History and per-line price recall are both scoped identically: the signed-in user's own
  orders, plus orders belonging to anyone who has shared `ShareCategory.INVENTORY` with them —
  never a global cross-user lookup, never the Dashboard's single-viewer-switch pattern.
- `Vendor` stays a global, shared table (like `Peptide`) — never owner-scoped.
- Retiring `Vendor.contact_info` must backfill any non-null value into `notes` (appended, never
  overwritten) as part of the migration — no silent data loss.
- Price-list uploads must accept `.doc`/`.docx` in addition to the existing COA image/PDF types —
  a genuinely different allowed-type set from `app.uploads.ALLOWED_TYPES`, not a re-use of it.
- Follow existing conventions: `resolve_vendor`'s pick-or-create shape for the two new type tables;
  `_visible_items`/`_visible_active_vials`'s blend-and-tag-by-owner-name shape for Purchase
  History; migrations via `op.batch_alter_table` for SQLite compatibility.
- This plan's prior features have repeatedly found the same three recurring test-fixture bugs:
  (1) inserting a `Peptide`/seeded-name row unconditionally collides with a real seeded row under a
  unique constraint — reuse or check-first; (2) an unfiltered `select(User.id)` picks the wrong
  user under full-suite test-order pollution — pin via `User.username_key == "tester"`; (3) a
  POSIX-only `%-d` strftime flag crashes on Windows — use `{{ x.strftime('%b') }} {{ x.day }}`
  instead. Every task below should watch for and proactively avoid all three.

## Review Focus

1. A vendor with no `VendorFavorite` row for the CURRENT user must never render as favorited just
   because a different user favorited it.
2. Per-line price recall must scope to `Order.vendor_id == <this vendor>` AND the matched item name
   — never a different vendor's price for the same peptide name, and never a price from an order
   the signed-in user has no Inventory-share visibility into.
3. Retiring `Vendor.contact_info` must not silently drop data — the migration's backfill into
   `notes` needs its own test.
4. The staleness prompt's "No" (replace) path must only ever touch the ONE vendor being ordered
   from — never create a duplicate `Vendor` row, never affect any other vendor's price list.
5. The New Order form's "new vendor" branch must create exactly one `Vendor` row per submission,
   even on a validation-error round-trip (re-submitting the same form after fixing one field must
   not create two vendors) — mirrors this app's existing idempotent-resubmission conventions
   elsewhere (e.g. dose-log duplicate guards).

---

### Task 1: Migration + models (type tables, child tables, Vendor columns, contact_info retirement)

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0016_vendor_management.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `ContactMethodType`, `PaymentMethodType`, `VendorContact`, `VendorPaymentMethod`,
  `VendorFavorite` models; `Vendor.supplier`/`.contact_name`/`.recommended`/
  `.price_list_filename`/`.price_list_url`/`.price_list_updated_at`; `Vendor.contact_info` removed.
  Every later task consumes these exact names.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_migrations.py`'s existing `test_0015_adds_dashboard_thresholds` (or
`test_0014_adds_dose_logging`) for the exact `_cfg`/`command`/`sqlite3` helper shape this file
already uses, and write a new test in the same style:

```python
def test_0016_adds_vendor_management(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0015")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-27')")
        c.execute("insert into vendors(name,contact_info,notes,created_at) values "
                  "('Acme Peptides','old-contact-info','existing notes','2026-09-27')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        # New tables exist.
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"contact_method_types", "payment_method_types", "vendor_contacts",
               "vendor_payment_methods", "vendor_favorites"} <= tables
        # Seeded defaults present.
        contact_names = {r[0] for r in c.execute("select name from contact_method_types")}
        assert {"Email", "WhatsApp", "Telegram", "Phone"} <= contact_names
        payment_names = {r[0] for r in c.execute("select name from payment_method_types")}
        assert {"Credit Card", "Cash", "Crypto", "Alibaba"} <= payment_names
        # Vendor gained the new columns and lost contact_info, with a backfill into notes.
        vendor_cols = {r[1] for r in c.execute("pragma table_info(vendors)")}
        assert {"supplier", "contact_name", "recommended", "price_list_filename",
               "price_list_url", "price_list_updated_at"} <= vendor_cols
        assert "contact_info" not in vendor_cols
        name, notes = c.execute(
            "select name, notes from vendors where name='Acme Peptides'").fetchone()
        assert "old-contact-info" in notes and "existing notes" in notes
    command.downgrade(cfg, "0015")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"contact_method_types", "payment_method_types", "vendor_contacts",
                    "vendor_payment_methods", "vendor_favorites"} & tables)
        vendor_cols = {r[1] for r in c.execute("pragma table_info(vendors)")}
        assert "contact_info" in vendor_cols
        assert "supplier" not in vendor_cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -k test_0016 -v`
Expected: FAIL — migration `0016` doesn't exist yet.

- [ ] **Step 3: Add the models**

In `app/models.py`, add near `Vendor`:

```python
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
```

In the existing `Vendor` class: remove `contact_info: Mapped[str | None] = mapped_column(String(300))`
and add, in its place:

```python
    supplier: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    recommended: Mapped[bool | None] = mapped_column(Boolean)
    price_list_filename: Mapped[str | None] = mapped_column(String(120))
    price_list_url: Mapped[str | None] = mapped_column(String(500))
    price_list_updated_at: Mapped[date | None] = mapped_column(Date)

    contacts: Mapped[list["VendorContact"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
    payment_methods: Mapped[list["VendorPaymentMethod"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
```

Check `Vendor`'s own docstring/imports at the top of `app/models.py` (`Boolean`, `Date` should
already be imported given other models use them — verify rather than assume) before committing.

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0016_vendor_management.py`, following `0014_dose_logging.py`'s exact
style (`op.batch_alter_table`, explicit `sa.Column`/`sa.ForeignKeyConstraint`/
`sa.UniqueConstraint`):

```python
"""vendor management: contact/payment method types, vendor contacts/payment methods/favorites,
new Vendor columns, contact_info retirement

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'contact_method_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60, collation='NOCASE'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_contact_method_type_name'),
    )
    op.create_table(
        'payment_method_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60, collation='NOCASE'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_payment_method_type_name'),
    )

    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('supplier', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('contact_name', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('recommended', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('price_list_filename', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('price_list_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('price_list_updated_at', sa.Date(), nullable=True))

    # Backfill contact_info into notes before dropping it -- appended, not overwritten, so a
    # vendor with both a contact_info value and existing notes keeps both.
    bind.execute(text(
        "UPDATE vendors SET notes = "
        "CASE "
        "  WHEN contact_info IS NULL OR contact_info = '' THEN notes "
        "  WHEN notes IS NULL OR notes = '' THEN contact_info "
        "  ELSE notes || char(10) || char(10) || contact_info "
        "END "
        "WHERE contact_info IS NOT NULL AND contact_info != ''"
    ))
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.drop_column('contact_info')

    op.create_table(
        'vendor_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('method_type_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['method_type_id'], ['contact_method_types.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_vendor_contacts_vendor_id', 'vendor_contacts', ['vendor_id'])

    op.create_table(
        'vendor_payment_methods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('method_type_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['method_type_id'], ['payment_method_types.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_id', 'method_type_id', name='uq_vendor_payment_method'),
    )
    op.create_index('ix_vendor_payment_methods_vendor_id', 'vendor_payment_methods', ['vendor_id'])

    op.create_table(
        'vendor_favorites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'vendor_id', name='uq_vendor_favorite'),
    )
    op.create_index('ix_vendor_favorites_user_id', 'vendor_favorites', ['user_id'])
    op.create_index('ix_vendor_favorites_vendor_id', 'vendor_favorites', ['vendor_id'])

    for name in ("Email", "WhatsApp", "Telegram", "Phone"):
        bind.execute(text("INSERT INTO contact_method_types(name) VALUES (:name)"), {"name": name})
    for name in ("Credit Card", "Cash", "Crypto", "Alibaba"):
        bind.execute(text("INSERT INTO payment_method_types(name) VALUES (:name)"), {"name": name})


def downgrade() -> None:
    op.drop_index('ix_vendor_favorites_vendor_id', table_name='vendor_favorites')
    op.drop_index('ix_vendor_favorites_user_id', table_name='vendor_favorites')
    op.drop_table('vendor_favorites')
    op.drop_index('ix_vendor_payment_methods_vendor_id', table_name='vendor_payment_methods')
    op.drop_table('vendor_payment_methods')
    op.drop_index('ix_vendor_contacts_vendor_id', table_name='vendor_contacts')
    op.drop_table('vendor_contacts')

    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact_info', sa.String(length=300), nullable=True))
        batch_op.drop_column('price_list_updated_at')
        batch_op.drop_column('price_list_url')
        batch_op.drop_column('price_list_filename')
        batch_op.drop_column('recommended')
        batch_op.drop_column('contact_name')
        batch_op.drop_column('supplier')

    op.drop_table('payment_method_types')
    op.drop_table('contact_method_types')
```

Note: the downgrade's re-added `contact_info` column will be empty (the backfill-into-notes isn't
reversible) — this matches every other migration's downgrade in this codebase, which restore shape
but not necessarily historical values.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -k test_0016 -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: Some pre-existing tests almost certainly reference `Vendor.contact_info` or construct a
`Vendor(contact_info=...)` — these will now fail (the column is gone). Find every such reference
(`grep -rn "contact_info" tests/ app/`) and update it: if a test only sets `contact_info` in setup
data unrelated to what it's actually testing, remove that line; if a test specifically asserts
`contact_info`'s behavior, it needs judgment — read what it's actually testing and adapt (e.g. if
it's testing that vendor notes/contact display, redirect the assertion to the new `notes` field or
to a `VendorContact` row, whichever preserves the original test's intent). This is expected,
necessary cleanup for this task, not a regression to avoid — do it as part of Step 6, not a
follow-up.
Expected after cleanup: All pass.

- [ ] **Step 7: Commit**

```bash
git add app/models.py migrations/versions/0016_vendor_management.py tests/test_migrations.py
git commit -m "feat: add vendor management tables and retire Vendor.contact_info"
```

---

### Task 2: Type-table resolve helpers + price-list upload support

**Files:**
- Modify: `app/inventory/vendors.py` (or create `app/vendors/__init__.py` + a new module — the
  plan leaves this to the implementer's judgment: `resolve_vendor` already lives in
  `app/inventory/vendors.py`, so co-locating the two new resolve helpers there keeps one obvious
  place for "pick-or-create a named row," but a dedicated `app/vendors/` package may read cleaner
  given how much this feature adds — pick one and be consistent)
- Modify: `app/uploads.py`
- Test: `tests/test_vendors.py` (new file) or wherever the plan's own judgment says these pure
  helpers belong given the final module layout chosen above

**Interfaces:**
- Produces: `resolve_contact_method_type(session, name) -> ContactMethodType | None`,
  `resolve_payment_method_type(session, name) -> PaymentMethodType | None` (or one generic
  `resolve_named(session, model, name)` — implementer's call, but whichever is chosen, name it
  clearly and use it consistently); `save_price_list(upload) -> str`, `price_list_path(filename) ->
  Path`, `delete_price_list(filename) -> None`, `price_list_media_type(filename) -> str`. Task 3
  (Vendors router) and Task 5 (New Order flow) both consume these by name.

- [ ] **Step 1: Write the failing tests**

Read `app/inventory/vendors.py`'s existing `resolve_vendor` (already reviewed: strips/blank-checks
the name, does a case-sensitive-per-column-collation lookup since `Vendor.name` uses `NOCASE`
collation at the column level rather than an explicit `func.lower()` comparison — check this
detail directly in `app/models.py`'s `Vendor.name` column definition before writing the new
resolvers, so the new type tables' NOCASE columns get the same simple `==` comparison, not a
mismatched `func.lower()` one) and mirror its exact shape for the two new resolvers. Write tests
in a new `tests/test_vendors.py`:

```python
from app.db import SessionLocal
from app.models import ContactMethodType, PaymentMethodType, User
from app.vendors... import resolve_contact_method_type, resolve_payment_method_type  # match your chosen module path
from sqlalchemy import select


def test_resolve_contact_method_type_reuses_existing_case_insensitively(db):
    with SessionLocal() as s:
        a = resolve_contact_method_type(s, "Email")
        b = resolve_contact_method_type(s, "email")
        assert a.id == b.id


def test_resolve_contact_method_type_creates_new(db):
    with SessionLocal() as s:
        created = resolve_contact_method_type(s, "Signal")
        assert created.name == "Signal"
        again = resolve_contact_method_type(s, "signal")
        assert again.id == created.id


def test_resolve_contact_method_type_blank_is_none(db):
    with SessionLocal() as s:
        assert resolve_contact_method_type(s, "  ") is None


def test_resolve_payment_method_type_reuses_existing(db):
    with SessionLocal() as s:
        a = resolve_payment_method_type(s, "Crypto")
        b = resolve_payment_method_type(s, "CRYPTO")
        assert a.id == b.id
```

For the upload helpers, add to whatever test file already covers `app/uploads.py` (check for one;
if none exists, create `tests/test_uploads.py` matching the style of a nearby test file that
exercises file uploads, e.g. checking how `tests/test_inventory.py` tests COA uploads for the
request/fixture shape to reuse):

```python
def test_save_price_list_accepts_pdf():
    # mirror however the existing COA upload test constructs a fake UploadFile/multipart payload
    ...

def test_save_price_list_accepts_docx():
    ...

def test_save_price_list_rejects_exe():
    ...

def test_price_list_allowed_types_differ_from_coa_allowed_types():
    from app.uploads import ALLOWED_TYPES
    from app.uploads import PRICE_LIST_ALLOWED_TYPES  # name matches whatever you actually add
    assert PRICE_LIST_ALLOWED_TYPES != ALLOWED_TYPES
    assert ".doc" in PRICE_LIST_ALLOWED_TYPES or ".docx" in PRICE_LIST_ALLOWED_TYPES
```

Fill in the `...` upload-construction tests by reading how existing COA-upload tests build their
request payload (check `tests/test_inventory.py` for a COA-upload test) and adapting the same
shape for a price-list endpoint — note Task 2 only builds the pure `save_price_list`/etc. helper
functions themselves, not a route; test them directly (call the function with a constructed
`UploadFile`), not via HTTP, matching however `app/uploads.py`'s OWN existing behavior is unit
tested today (check for an existing `tests/test_uploads.py` first — if COA's `save_coa` already has
direct unit tests, mirror that file's exact test-construction pattern for `UploadFile` objects).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vendors.py -v` and the upload tests
Expected: FAIL — `ModuleNotFoundError`/`ImportError`/`AttributeError` (nothing exists yet).

- [ ] **Step 3: Write the resolve helpers**

In whichever module you chose, following `resolve_vendor`'s exact shape (read it first):

```python
def resolve_contact_method_type(session: Session, name: str) -> ContactMethodType | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(ContactMethodType).where(ContactMethodType.name == name))
    if existing is not None:
        return existing
    method_type = ContactMethodType(name=name)
    session.add(method_type)
    session.flush()
    return method_type


def resolve_payment_method_type(session: Session, name: str) -> PaymentMethodType | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(select(PaymentMethodType).where(PaymentMethodType.name == name))
    if existing is not None:
        return existing
    method_type = PaymentMethodType(name=name)
    session.add(method_type)
    session.flush()
    return method_type
```

(Adjust to a single generic `resolve_named` if that's the module-layout call you made in Step 3's
files section — keep behavior identical either way.)

- [ ] **Step 4: Extend `app/uploads.py`**

Read the existing `save_coa`/`coa_path`/`delete_coa`/`media_type`/`ALLOWED_TYPES`/`_sniff_ok`
functions in full (already summarized in the spec, but read the real file) and add a parallel set
for price lists, reusing `_sniff_ok`'s pattern for the two new extensions:

```python
PRICE_LIST_ALLOWED_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
```

Extend `_sniff_ok` (or write a parallel `_price_list_sniff_ok` that delegates to `_sniff_ok` for the
shared extensions and adds two new branches) for the two new extensions:
- `.docx` is a zip archive: `head.startswith(b"PK\x03\x04")`
- `.doc` (legacy binary format) is OLE2: `head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")`

Add `save_price_list`/`price_list_path`/`delete_price_list`/`price_list_media_type`, mirroring the
COA quartet exactly but using `PRICE_LIST_ALLOWED_TYPES`, the extended sniff check, and a
`config.PRICE_LIST_DIR` (check `app/config.py` for how `COA_DIR` is defined and add a sibling
constant the same way — read the file, don't guess its exact shape).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_vendors.py -v` and the upload tests
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add app/inventory/vendors.py app/uploads.py app/config.py tests/test_vendors.py
git commit -m "feat: add contact/payment method resolvers and price-list upload support"
```

(Adjust the file list in the commit to whatever module layout you actually used.)

---

### Task 3: Contact-link pure function module

**Files:**
- Create: `app/vendors/links.py` (or fold into wherever Task 2 put the resolve helpers, if you
  created a dedicated `app/vendors/` package there — keep contact-link logic and resolve helpers
  in the same package if one now exists, for discoverability)
- Test: `tests/test_vendors.py` (same file Task 2 created)

**Interfaces:**
- Produces: `contact_link(method_type_name: str, value: str) -> str | None` — Task 4's Vendor
  detail template calls this per contact entry.

- [ ] **Step 1: Write the failing tests**

```python
from app.vendors.links import contact_link  # match your chosen module path


def test_email_link():
    assert contact_link("Email", "vendor@example.com") == "mailto:vendor@example.com"


def test_phone_link():
    assert contact_link("Phone", "+1 (555) 123-4567") == "tel:+1 (555) 123-4567"


def test_whatsapp_link_strips_non_digits():
    assert contact_link("WhatsApp", "+1 (555) 123-4567") == "https://wa.me/15551234567"


def test_telegram_link_strips_leading_at():
    assert contact_link("Telegram", "@somehandle") == "https://t.me/somehandle"


def test_telegram_link_without_at():
    assert contact_link("Telegram", "somehandle") == "https://t.me/somehandle"


def test_custom_method_type_has_no_link():
    assert contact_link("Signal", "+15551234567") is None


def test_matching_is_case_insensitive_on_method_name():
    assert contact_link("email", "vendor@example.com") == "mailto:vendor@example.com"
    assert contact_link("EMAIL", "vendor@example.com") == "mailto:vendor@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vendors.py -k "link" -v`
Expected: FAIL — `ImportError`/function doesn't exist.

- [ ] **Step 3: Write `contact_link`**

```python
"""Builds a clickable link for a vendor contact entry, when its method type has a known scheme.
Pure function -- no database access."""

import re

_BUILDERS = {
    "email": lambda value: f"mailto:{value}",
    "phone": lambda value: f"tel:{value}",
    "whatsapp": lambda value: f"https://wa.me/{re.sub(r'\\D', '', value)}",
    "telegram": lambda value: f"https://t.me/{value.lstrip('@')}",
}


def contact_link(method_type_name: str, value: str) -> str | None:
    builder = _BUILDERS.get((method_type_name or "").strip().lower())
    return builder(value) if builder else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vendors.py -k "link" -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/vendors/links.py tests/test_vendors.py
git commit -m "feat: add contact-link builder for vendor contact entries"
```

(Adjust the file path to your chosen module layout.)

---

### Task 4: Vendors router + list/detail templates + favorite toggle

**Files:**
- Create: `app/routers/vendors.py`
- Create: `app/templates/vendors/list.html`
- Create: `app/templates/vendors/detail.html`
- Modify: `app/main.py` (register router)
- Modify: `app/templates/base.html` (nav link)
- Test: `tests/test_vendors_page.py` (new file, separate from Task 2/3's pure-function
  `tests/test_vendors.py` — this one exercises the actual HTTP routes)

**Interfaces:**
- Consumes: `resolve_contact_method_type`/`resolve_payment_method_type` (Task 2),
  `contact_link` (Task 3).
- Produces: `GET /vendors`, `GET /vendors/{id}`, `POST /vendors/{id}` (edit), `POST
  /vendors/{id}/favorite`, `POST /vendors/{id}/unfavorite` — Task 5's New Order flow reuses
  `resolve_vendor`/the new resolve helpers but does NOT depend on this router directly.

This task has no fully verbatim brief code, by design: read `app/routers/inventory.py`'s
`_visible_items`/`_visible_active_vials` (already reviewed in the spec — re-read the real function
bodies before writing this task's own Purchase History query, since the exact join/filter shape
matters) and `app/templates/inventory/list.html`'s existing edit-in-place form conventions before
writing this task's templates, so the new page matches this app's established look rather than
inventing a new one.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vendors_page.py`. Cover, at minimum (write complete, real test bodies — no
`...` placeholders):
- `GET /vendors` lists an existing vendor by name.
- `GET /vendors` sorted alphabetically by default; `?sort=recent` sorts by most recent order date
  (create two vendors, order from the older-named one more recently, assert it sorts first under
  `?sort=recent` but not under the alphabetical default).
- Favoriting a vendor (`POST /vendors/{id}/favorite`) makes it appear first in `GET /vendors`
  regardless of sort mode; a SECOND user's own favorite (or lack thereof) must not affect the
  first user's ordering (register a second user via this codebase's established
  `_register_other`/similar helper — check `tests/test_dashboard.py` for the exact pattern used
  there for creating a second test user).
- `GET /vendors/{id}` shows a vendor's contact entries as real links for built-in method types and
  plain text for a custom one (assert `href="mailto:..."` present for an Email contact, and the
  custom method's value rendered without an `href` wrapping it).
- `POST /vendors/{id}` (edit) can add a new contact entry with a brand-new custom method-type name,
  and that name is reusable (pick it from a dropdown, not retyped) on a SECOND vendor's edit — this
  proves the resolve-or-create/reuse behavior end-to-end through the real HTTP form, not just at
  the pure-function level Task 2 already tested.
- Purchase History on a vendor's detail page shows the signed-in user's own orders with that
  vendor, and (separately) does NOT show another user's orders with the same vendor when no
  `Share` exists between them; DOES show them, tagged with the owner's name, when an
  `INVENTORY`-category `Share` exists (mirror `tests/test_inventory.py`'s existing shared-item test
  pattern for constructing the second user + `Share` row).
- Recommend/don't-recommend toggle persists and round-trips via the edit form.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vendors_page.py -v`
Expected: FAIL — 404s (routes don't exist).

- [ ] **Step 3-4: Implement the router and templates**

Implement `app/routers/vendors.py` and the two templates. Structure the router's Purchase History
query and owner-tagging exactly like `_visible_items`/`_visible_active_vials` already do (read
those functions directly rather than re-deriving the join shape from scratch — this is
security-relevant, per Review Focus item 2, so get the scoping exactly right). Structure the
favorite/unfavorite routes as simple insert/delete against `VendorFavorite` scoped to
`current_user_id`. The edit route needs to accept: profile fields, a repeatable contact-entry
sub-form (method-type picker + value, "add another" pattern — check how the New Order form's own
repeatable-line-items JS/template pattern works in `app/templates/inventory/list.html` and
`app/static/js/inventory.js` for a precedent to follow rather than inventing a new repeatable-row
UI pattern), and payment-method checkboxes (existing types, checked/unchecked) plus an "add a new
payment type" text field.

- [ ] **Step 5: Register the router and nav link**

In `app/main.py`, add `vendors` to the router import list and `app.include_router(vendors.router)`
in the same style as every other router registration. In `app/templates/base.html`, add a
"Vendors" nav link (place it after "Inventory" — vendors are inventory-adjacent — matching the
`active_nav` pattern every other nav link already uses).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_vendors_page.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add app/routers/vendors.py app/templates/vendors/ app/main.py app/templates/base.html tests/test_vendors_page.py
git commit -m "feat: add standalone Vendors page (list, detail, favorites, purchase history)"
```

---

### Task 5: New Order flow — new-vendor branch, staleness prompt, per-line price recall

**Files:**
- Modify: `app/routers/inventory.py`
- Modify: `app/templates/inventory/list.html`
- Modify: `app/static/js/inventory.js`
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `resolve_contact_method_type`/`resolve_payment_method_type` (Task 2),
  `save_price_list`/`price_list_path` (Task 2).

This is the highest-judgment task in the plan: it changes an existing, already-well-tested flow
(`create_multi_item_order` in `app/routers/inventory.py`, and the New Order section of
`app/templates/inventory/list.html`) rather than adding something new from scratch. Read
`create_multi_item_order`, `_parse_order_header_fields`, `_parse_order_line_fields`, and the New
Order template section IN FULL before changing anything — this plan's earlier Dashboard feature
found real bugs from writing code against a remembered rather than freshly-read version of an
existing flow; do not repeat that here.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py` (read the file's existing New-Order tests first and match their
setup conventions exactly — likely `_create_item`/a full valid New Order POST payload pattern
already exists there to copy from):

```python
def test_new_order_new_vendor_branch_creates_vendor_with_full_profile(client, db):
    # POST to /inventory/orders (or whatever the real route path is -- check inventory.py) with
    # is_new_vendor=1 and a full vendor profile (name, supplier, contact_name, one contact entry,
    # one payment method) alongside a normal order line. Assert exactly one new Vendor row is
    # created with all the submitted profile fields, plus its VendorContact/VendorPaymentMethod
    # rows.
    ...


def test_new_order_existing_vendor_branch_populates_from_dropdown(client, db):
    # Create a vendor via the Vendors page or resolve_vendor helper first. POST a New Order
    # selecting that vendor by id (not by typing its name) and assert the order's vendor_id
    # matches -- no duplicate Vendor row created.
    ...


def test_new_order_price_recall_prefills_from_last_order_same_vendor_same_item(client, db):
    # Place a first order for "Retatrutide" from Vendor X at $84.00. Start a second New Order for
    # the same vendor, same item name -- assert the price field pre-fills to $84.00 (check
    # whatever the real prefill mechanism is: a GET endpoint the JS calls, or data embedded in the
    # page load -- implement whichever fits this app's existing JS/template conventions, and test
    # at whatever layer actually exercises it).
    ...


def test_new_order_price_recall_blank_when_never_ordered_from_this_vendor(client, db):
    ...


def test_new_order_price_recall_does_not_leak_across_vendors(client, db):
    # Order the same item name from Vendor X at $84 and Vendor Y at $50. Starting a New Order for
    # Vendor Y with that same item name must prefill $50, never $84.
    ...


def test_new_order_price_recall_respects_inventory_sharing(client, db):
    # A second user's order history for the same vendor+item must NOT leak into this user's price
    # recall unless that second user has shared INVENTORY with this user.
    ...


def test_staleness_prompt_replace_only_updates_the_one_vendor(client, db):
    # Vendor X and Vendor Y both have a price_list_filename/updated_at set. Trigger the "No, replace"
    # path for Vendor X during a New Order. Assert Vendor Y's price_list fields are completely
    # untouched.
    ...
```

Fill in every `...` with complete, real test bodies once you've read the actual route/template
code and decided the exact request shape (form fields, route paths) — these are sketches of INTENT,
not verbatim code, because the exact request shape depends on decisions you make while implementing
Steps 3+ below.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "new_vendor or price_recall or staleness_prompt" -v`
Expected: FAIL for the right reason (missing fields/behavior, not a typo).

- [ ] **Step 3: Implement the new-vendor/existing-vendor branch**

Add an `is_new_vendor` boolean field to the New Order form. When true, parse a full vendor profile
(reusing Task 2's resolve helpers for contact/payment method types) and create the `Vendor` +
`VendorContact`/`VendorPaymentMethod` rows in the SAME transaction as the order, only after all
validation passes (mirror how `create_multi_item_order` already defers all writes until `if not
errors:` — read that gate's exact placement before adding new writes near it, so a new-vendor
submission with a validation error elsewhere in the form doesn't create an orphaned `Vendor` row
per Review Focus item 5). When false, replace the current free-text vendor input with a `<select>`
of every existing vendor (alphabetical), and set `values["vendor_id"]`/`values["vendor"]` from the
selected id directly (no `resolve_vendor` call needed in this branch — the vendor already exists).

- [ ] **Step 4: Implement the price-list staleness prompt**

When the existing-vendor branch's selected vendor has a non-null `price_list_filename` or
`price_list_url`, show the "Price list from `<date>` still current?" Yes/No prompt (server-rendered
conditional in the template, or JS-driven once the vendor is selected from the dropdown — match
whichever this app's existing conditional-field patterns already use, e.g. how the Calculator's
pen-loading checkbox or the medium-aware Inventory form fields show/hide). "No" reveals an inline
file/URL replace field; submitting the order with a replacement value updates that ONE vendor's
`price_list_filename`/`price_list_url`/`price_list_updated_at` (today's date) before/alongside
creating the order.

- [ ] **Step 5: Implement per-line price recall**

For the existing-vendor branch, as each order line's item name is entered/selected, look up the
most recent prior `OrderItem.cost_cents` for `(Order.vendor_id == selected vendor, item name
case-insensitive match)`, scoped through the SAME visibility join `_visible_items` already uses
(the signed-in user's own orders, plus anyone who's shared `INVENTORY` with them — per Review Focus
item 2, this is not optional). Decide and implement whichever mechanism fits this app's existing
JS/template conventions for a "fill this field as you type/select" behavior (a small JSON blob
embedded in the page keyed by vendor+item-name pairs the JS reads on selection, similar to how the
Daily Dosing feature's `site-data` JSON blob already works, is one reasonable option — check that
precedent in `app/templates/dosing/today.html`/`app/static/js/dosing.js` before inventing a
different mechanism).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "new_vendor or price_recall or staleness_prompt" -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass. The existing New Order tests (typing a free-text vendor name) will likely need
updating to use the new is_new_vendor/vendor_id form shape — this is an intentional, necessary
behavior change (per this task's own scope), not a regression to route around; update those
existing tests' request payloads to match the new form shape rather than leaving them broken.

- [ ] **Step 8: Commit**

```bash
git add app/routers/inventory.py app/templates/inventory/list.html app/static/js/inventory.js tests/test_inventory.py
git commit -m "feat: New Order flow gains new-vendor branch, price-list staleness prompt, and per-line price recall"
```

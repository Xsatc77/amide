# Vendor Management Design (Roadmap Phase 5)

**Goal:** turn the bare `Vendor` table (name/website/contact_info/notes) into a full "Personal
Distributor Contacts" feature — a standalone Vendors page with structured, extensible contact and
payment info, a recommend/don't-recommend flag, a per-user Favorite, a reference price-list
attachment with a staleness check on reorder, and automatic per-line price recall from order
history — without duplicating any data that already exists on `Order`/`OrderItem`.

**Deferred, explicitly out of scope for this spec:** AI-assisted parsing of an uploaded price-list
document into structured data (a separate, future, opt-in integration); cost-per-mg/cost-per-dose/
monthly-spend-per-peptide analytics (a different, peptide-scoped slice of Phase 5's wishlist, not
vendor-scoped); a numeric star-rating (replaced by the simpler recommend/don't-recommend flag,
confirmed).

## Data model

Two new small "type" tables, following exactly the pattern `Vendor` and `Peptide` already establish
(global, shared across every user, case-insensitive-unique name, pick-or-create):

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
```

Seeded at migration time with the requested defaults: `ContactMethodType` — Email, WhatsApp,
Telegram, Phone. `PaymentMethodType` — Credit Card, Cash, Crypto, Alibaba. A user typing a new name
into either picker resolves-or-creates exactly the way `app.inventory.vendors.resolve_vendor`
already does for `Vendor` — reuse that same function shape (a new `resolve_contact_method_type`/
`resolve_payment_method_type` pair, or one generic `resolve_named(session, model, name)` helper if
that reads more cleanly; the plan decides).

```python
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


class VendorFavorite(Base):
    """Per-user favorite marker. Row exists = favorited; there is no boolean to toggle, just
    insert/delete, mirroring how Share's existence-is-the-grant pattern already works in this app."""
    __tablename__ = "vendor_favorites"
    __table_args__ = (UniqueConstraint("user_id", "vendor_id", name="uq_vendor_favorite"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
```

`Vendor` gains (and loses its old free-text `contact_info` — see Migration below):

```python
    supplier: Mapped[str | None] = mapped_column(String(200))       # the actual manufacturer behind a reseller
    contact_name: Mapped[str | None] = mapped_column(String(120))
    recommended: Mapped[bool | None] = mapped_column(Boolean)        # None = no opinion, True/False = recommend/don't
    price_list_filename: Mapped[str | None] = mapped_column(String(120))  # uploaded file, same storage as COAs
    price_list_url: Mapped[str | None] = mapped_column(String(500))      # OR a plain link -- either, not both required
    price_list_updated_at: Mapped[date | None] = mapped_column(Date)     # bumped on upload/replace or "still valid" confirm

    contacts: Mapped[list["VendorContact"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
    payment_methods: Mapped[list["VendorPaymentMethod"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
```

`Vendor.contact_info` (the old free-text field) is retired: its migration backfills any non-null
value onto `Vendor.notes` (appended, not overwritten) before dropping the column, so no existing
data is silently lost, then `VendorContact` rows become the one real source of contact info going
forward.

## Price-list attachment (reference document, separate from price recall)

Not parsed, not structured — a plain reference attachment plus a link, exactly as scoped in
brainstorming. Reuses the existing upload validation/storage pattern (`app/uploads.py`'s
`save_coa`/`coa_path`/`delete_coa`/`media_type`), extended with one addition: COAs never accept
`.doc`/`.docx`, but price lists must (per the original ask: "usually a .pdf or .doc or jpegs"). Add
a second allowed-type set and a parallel `save_price_list`/`price_list_path`/`delete_price_list`
trio in `app/uploads.py` (or generalize the existing functions to take an `allowed_types` parameter
— the plan decides which reads cleaner given the actual file, which wasn't fully reviewed here).
`.doc`/`.docx` sniffing: `.docx` is a zip (`PK\x03\x04` header, like `.xlsx`); legacy `.doc` is OLE2
(`\xd0\xcf\x11\xe0` header) — both distinguishable the same way the existing `_sniff_ok` checks
other types.

Either `price_list_filename` or `price_list_url` may be set (not both required; a vendor might have
a real PDF, or just "check their website," never neither-nor-both as a hard rule — the form simply
offers both fields and either can be filled).

## The "still valid?" staleness prompt

Triggered only in the New Order flow's "existing vendor" branch (see below), only when that vendor
has a non-null `price_list_filename` or `price_list_url`: "Price list from `<price_list_updated_at>`
still current? [Yes] [No]". **Yes** just proceeds — no write happens (the date shown was already
accurate). **No** opens an inline replace (the same file/URL fields as the vendor edit form,
scoped to just this vendor) right there in the order flow; saving it updates
`price_list_filename`/`price_list_url`/`price_list_updated_at` before the order form continues.
Skipping the prompt (no price list on file yet) means the New Order form proceeds straight through,
same as today.

## New Order flow change

The New Order form gains a first question: **"Is this a new vendor?"**

- **Yes** — the vendor section shows blank fields for a full profile: Company Name (required),
  Supplier, Contact Name, contact method entries (add as many as needed, each a method-type
  picker + value), payment-method checkboxes (existing types + an "add another" field), Notes.
  Submitting the order creates the `Vendor` row (and any new `ContactMethodType`/
  `PaymentMethodType`/`VendorContact`/`VendorPaymentMethod` rows) in the same transaction as the
  order itself — this is the one path where a brand-new vendor's full profile is entered as part of
  placing an order, rather than via the standalone Vendors page.
- **No** — a dropdown lists every `Vendor` in the system (global, not owner-scoped, matching how
  vendors already work), alphabetical. Picking one: (1) shows the staleness prompt above if a price
  list is on file; (2) enables per-line price recall (below) for every line item added to this
  order.

## Per-line price recall (no new storage)

When the selected vendor is an *existing* one (the "No" branch above), each order line's cost field
prefills from the most recent prior order with THIS vendor for an item of the SAME name
(case-insensitive, matching the item-name-matching convention already used for `Vendor`/`Peptide`
dedup elsewhere) — never a separate stored price list, exactly per the explicit "I don't want them
to put any extra data" requirement. Concretely: `select(OrderItem).join(Order).join(InventoryItem)
.where(Order.vendor_id == vendor.id, func.lower(InventoryItem.name) == func.lower(typed_name))
.order_by(Order.order_date.desc()).limit(1)` — take that line's own `cost` (not `total_cost`, which
includes allocated shipping/tax; the recalled figure should be the raw per-line price the vendor
actually charged, matching what the user is about to type into the same raw `cost` field) and
prefill the new line's cost input with it. No match (never ordered this item from this vendor
before) leaves the field blank, exactly as today. The prefilled value is always editable — this is
a convenience default, never an enforced value.

This lookup runs per-line, keyed off whatever name the user has typed/selected for that line (both
the "existing item" and "new item" line modes already carry a name at that point), so it naturally
updates as they add each new line to a multi-item order.

## Standalone Vendors page

New router `app/routers/vendors.py`:

- **`GET /vendors`** — list page. Every `Vendor` in the system (global list, matching the existing
  "shared like the peptide library" design). Sortable by two modes (a toggle or query param,
  matching the existing Inventory list's own sort-toggle convention if one exists — check
  `app/templates/inventory/list.html` for the pattern before inventing a new one): alphabetical by
  `name`, or by each vendor's own most recent `Order.order_date` (vendors with no orders yet sort
  last under date-sort, or use `NULLS LAST` if the DB layer supports it cleanly for SQLite). The
  signed-in user's own `VendorFavorite` rows pin their favorited vendors to the top of whichever
  sort is active (favorites-first, then the chosen sort within each group).
- **`GET /vendors/{id}`** — detail page: all profile fields (editable inline or via a form,
  matching this app's existing edit-in-place conventions), the contact list (each entry rendered as
  a clickable link when its method type is one of the four built-ins with a known link scheme,
  plain text otherwise), the payment-method badges, the recommend/don't-recommend toggle, the price
  list (file download link or URL, plus its age relative to today), and a **Purchase History**
  table.
- **`POST /vendors/{id}/favorite`** / **`POST /vendors/{id}/unfavorite`** — insert/delete the
  `VendorFavorite` row for the signed-in user. (Or a single toggle route — the plan decides,
  matching whatever this app's existing toggle-style routes look like, e.g. discard/undiscard
  patterns already in `inventory.py`.)

### Purchase History visibility

Confirmed: **not** the Dashboard's single-viewer-switch pattern. Matches the existing Inventory
list's blend-and-tag convention (`_visible_items`/`_visible_active_vials` in
`app/routers/inventory.py`, which return `(items, owner_names)` so the template can tag each row by
whose it is): a vendor's Purchase History shows the signed-in user's own orders with that vendor,
plus any orders belonging to a user who has shared their `ShareCategory.INVENTORY` category with
the signed-in user, each row tagged with the owner's name exactly like the Inventory list already
tags shared items. A user with no incoming Inventory shares sees only their own history — this is
the explicit "private unless shared" rule confirmed during brainstorming.

## Link-building for built-in contact methods

A small pure function, e.g. `app/vendors/links.py`'s `contact_link(method_type_name: str, value:
str) -> str | None`:

| Method type | Link built |
|---|---|
| Email | `mailto:{value}` |
| Phone | `tel:{value}` |
| WhatsApp | `https://wa.me/{value with non-digits stripped}` |
| Telegram | `https://t.me/{value with a leading @ stripped}` |
| anything else (custom, user-added) | `None` — rendered as plain text, no link |

## Review focus

1. A vendor with no `VendorFavorite` row for the CURRENT user must never show as favorited just
   because ANOTHER user favorited it — `VendorFavorite` is strictly per-user, and the list/detail
   queries must always filter by the signed-in user's own id, never a global "is this favorited by
   anyone" flag.
2. The per-line price recall must scope to `Order.vendor_id == <this vendor>` AND the matched item
   NAME — never leak a price from a *different* vendor for the same peptide, and never match a
   same-named item that belongs to a *different user's* order history the signed-in user has no
   Inventory-share access to (the recall lookup itself should be scoped the same way Purchase
   History is: the signed-in user's own orders, plus shared-with-them orders — never a global
   cross-user price lookup that would leak another user's negotiated pricing).
3. Retiring `Vendor.contact_info` must not silently drop data — the migration's backfill-into-notes
   step is not optional, and needs its own test asserting a non-null `contact_info` value survives
   (inside `notes`) after the migration runs.
4. The staleness prompt's "No" path must only ever update the ONE vendor being ordered from, never
   accidentally create a duplicate `Vendor` row or affect any other vendor's price list.
5. A price-list file upload must accept `.doc`/`.docx` (unlike the existing COA uploader, which
   deliberately doesn't) — a test should assert the new allowed-type set genuinely differs from
   `app.uploads.ALLOWED_TYPES` rather than accidentally sharing the same restricted set.

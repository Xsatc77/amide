# Daily Dosing Design (Roadmap Phase 3)

**Goal:** the core dosing loop — a Today view of what's due, logging a dose against an Active
Vial (drawing it down for real), injection-site rotation guidance, skip/missed/late tracking, and
peptide-pen support — without duplicating the scheduling logic `app/calendar/schedule.py` already
has.

**Deferred, explicitly out of scope for this spec:** protocol cycles (5-on/2-off), titration
templates, priming-loss modeling for pens, a separate reusable "pen device" entity distinct from
the Active Vial it's loaded from.

## Architecture

`app/calendar/schedule.py`'s `occurrences()`/`DueItem`/`is_due()` already compute exactly what's
due on which date from `Protocol`/`ProtocolItem` data — the Today view and the Calendar's
adherence overlay both read from it rather than re-deriving schedules. A new `DoseLog` model
records what actually happened for a given due item on a given date (logged / skipped / missed /
late), and `ActiveVial` gains real depletion tracking (`volume_remaining_ml`), replacing its
current `doses_total`, which today is "a static snapshot... it never depletes... since there is no
dose-logging feature yet" (its own docstring, in `app/models.py`).

## Data model

```python
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
    GLUTE_L = ("glute_l", "Left glute")  # IM only
    GLUTE_R = ("glute_r", "Right glute")  # IM only


class DoseStatus(LabeledEnum):
    ON_TIME = ("on_time", "On time")
    LATE = ("late", "Logged late")
    MISSED = ("missed", "Missed")
    SKIPPED = ("skipped", "Skipped")
```

`InjectionSite` pairs share a body part (abdomen/thigh/arm/glute); recommendation logic (below)
works in terms of "the other side of the same body part," never crossing body parts. Route
narrows which sites are offered: SubQ/IM both offer Abdomen/Thigh/Arm; only IM additionally offers
Glute. Oral/Nasal/Topical/Other routes never show a site picker at all.

```python
class DoseLog(Base):
    """One due-item-on-one-date outcome: logged, skipped, or (once the day passes with nothing
    logged) missed. Denormalizes peptide/dose/route/time_of_day at log time rather than trusting
    protocol_item_id to keep meaning -- editing a saved Protocol clears and rebuilds all its
    ProtocolItem rows (see app/protocols/forms.py's save_protocol), so a hard FK there would
    silently lose history on every edit. protocol_item_id is kept as a nullable, best-effort deep
    link only."""

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
    peptide_name: Mapped[str] = mapped_column(String(120))  # denormalized, survives peptide renames too
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit))
    route: Mapped[str] = mapped_column(String(20))  # Route's value, denormalized
    scheduled_date: Mapped[date] = mapped_column(Date)
    scheduled_time_of_day: Mapped[TimeOfDay] = mapped_column(_enum_column(TimeOfDay))
    status: Mapped[DoseStatus] = mapped_column(_enum_column(DoseStatus))
    logged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null while status=MISSED
    injection_site: Mapped[InjectionSite | None] = mapped_column(_enum_column(InjectionSite))
    volume_ml: Mapped[float | None] = mapped_column(Float)  # actual mL drawn; null for oral/topical/etc, or skipped/missed

    protocol: Mapped["Protocol"] = relationship()
    active_vial: Mapped["ActiveVial | None"] = relationship()
```

`ActiveVial` additions (in `app/models.py`, alongside its existing fields):

```python
    dispensing_method: Mapped[DispensingMethod] = mapped_column(_enum_column(DispensingMethod), default=DispensingMethod.SYRINGE)
    volume_remaining_ml: Mapped[float] = mapped_column(Float)  # initialized to water_ml at creation; decremented per logged dose
```

`doses_total` stays as today's at-creation estimate (unchanged, still shown for reference) —
`volume_remaining_ml` becomes the value that actually depletes and actually gates "is this vial
empty."

## Today view

New page, `GET /today` (or a `?view=today` mode alongside the existing Calendar — final routing
is a plan-time decision). Calls `occurrences(protocols, today, today)` for the signed-in user's
active protocols, same as Calendar already does, then cross-references each `DueItem` against
today's `DoseLog` rows to show its current state (not yet logged / on-time / skipped).

Each still-open due item gets two actions:

- **Log dose** — resolves the vial to draw from: the peptide's own open (non-discarded)
  `ActiveVial`s, oldest `discard_by` first (ties broken by `id`) — matching how the existing
  duplicate-vial-warning logic in `app/routers/inventory.py`'s `_open_active_vials` already picks
  "the one" open vial per item. Computes `volume_ml = dose_value / concentration_mg_ml` (the same
  math `app/calculator.py`'s `compute()` already does for the Calculator page) and shows it as
  either "Draw to *X* mL on a syringe" or "Dial the pen to *X* mL", based on the vial's
  `dispensing_method`. For SubQ/IM routes, also shows the injection-site picker (below). Submitting
  creates a `DoseLog` (`status=ON_TIME`, `logged_at=now`, denormalized fields copied from the
  `DueItem`/protocol/vial), decrements `volume_remaining_ml`, and — if that reaches `<= 0` — offers
  the same discard prompt the existing expiry-prompt flow already uses (`app/static/js/inventory.js`'s
  `expiry-prompt`/`reconstitute-again-prompt` pattern), just triggered by "empty" instead of
  "past discard-by."
- **Skip** — creates a `DoseLog` with `status=SKIPPED` immediately, no vial lookup, no site
  picker, no depletion.

A due item that's `AS_NEEDED` frequency (never scheduled, per `is_due()`'s own comment) doesn't
appear on the Today view as a due-today item at all — it's already excluded from `occurrences()`;
`as_needed()` is a separate lookup the Protocol/library page already uses for those, unaffected by
this spec.

## Missed / Late

A due item with no `DoseLog` for its `scheduled_date` once that date has fully passed (i.e. it's
now a later date) is **Missed** — computed lazily at read time (Protocol page, Calendar) by
diffing `occurrences()` against existing `DoseLog` rows for dates before today; no background job
or cron needed. A day's own Today view never shows a "Missed" state for TODAY's items — those stay
open/loggable all day, only flipping to Missed once the day is over.

A Missed item can still be logged after the fact (a "catch up" affordance on the Protocol page,
listing recent Missed items with the same Log-dose action) — doing so creates the `DoseLog` with
`scheduled_date` set to the day it was originally due, `logged_at` set to now, and
`status=LATE` (not `ON_TIME`, since it's outside its due day).

## Injection site rotation

Recommendation looks at the most recent `DoseLog` for this **peptide** (not this protocol —
rotation is peptide-scoped, so switching protocols doesn't reset it) that has a non-null
`injection_site`, and recommends the opposite side of that site's same body part (Left Abdomen →
Right Abdomen; never Abdomen → Thigh). No prior site for this peptide → no site is highlighted as
"last used" and no specific recommendation pulses; every eligible site shows as available.

Rendered as a body silhouette with one dot per eligible site (filtered by the current dose's
route): the last-used site is red, the recommended (mirrored) site pulses green, every other
eligible site is steady green. Sites not eligible for the current route (e.g. Glute when the route
is SubQ) aren't shown.

## Pen tracking

No new entity. Reconstitution (`app/routers/calculator.py`'s `reconstitute` route) gains one
question after the existing form fields: "Load into a peptide pen?" Answering yes sets the new
`ActiveVial`'s `dispensing_method=PEN` immediately; answering no leaves it `SYRINGE`, and a
"Convert to peptide pen" action becomes available on that vial's own entry (Inventory page's
Active Vials section) for later, whenever the transfer actually happens. Converting later is just
flipping `dispensing_method` on the existing row — the vial's `volume_remaining_ml` and history
don't change, since it's the same physical liquid.

No priming-loss deduction, and no pen-cartridge capacity cap (e.g. a hard 3 mL ceiling) — a
reconstituted volume that happens to exceed what physically fits in a cartridge is a real-world
constraint the user manages themselves; the app just tracks the liquid's remaining volume,
identically whether it's drawn via syringe or dialed via pen.

## Adherence display

- **Protocol page:** a history section listing that protocol's `DoseLog` rows (date, peptide,
  status, site), plus the "catch up on a Missed dose" affordance described above.
- **Calendar:** each occurrence gets a color dot reflecting its `DoseLog` status (or absence of
  one): green = on-time, yellow = late, red = missed or skipped, and a fourth (complementary,
  e.g. blue/gray — exact color is a frontend-implementation detail) for dates that haven't
  happened yet / have nothing logged because they're still upcoming.

## Review focus

1. Logging a dose against the wrong user's protocol/vial (ownership check on every write, matching
   this app's existing pattern everywhere else — `_own_item`-style checks).
2. A peptide with two open Active Vials: logging correctly draws from the older-`discard_by` one,
   never the newer one, and never lets you draw from a vial belonging to a *different* peptide.
3. `volume_remaining_ml` reaching exactly 0 (or going negative from a rounding edge) triggers the
   empty-vial prompt rather than silently allowing further doses to be logged against a drained
   vial.
4. A dose logged on its due day always gets `ON_TIME`, even if logged in the evening for a
   `time_of_day=AM` item — "missed at end of due day" means the whole day counts, not a
   time-of-day cutoff.
5. Editing a saved Protocol (which clears and rebuilds its `ProtocolItem` rows) never deletes or
   corrupts existing `DoseLog` history — `protocol_item_id` going stale/null on old rows is
   expected and fine; `peptide_name`/`dose_value`/`dose_unit`/`route` stay intact regardless.

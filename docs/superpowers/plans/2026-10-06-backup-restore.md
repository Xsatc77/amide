# Backup and Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From Settings, encrypted backups, exports and share files of chosen sections, and full restore or selective loading of them, for each person and (for the administrator) the whole installation.

**Architecture:** A small `app/backup/` package: a registry of sections (tables, filters, files, sharing rules), an export that reads raw SQLite rows into an AES-256-GCM-encrypted zip, a section loader (Add or Replace, new ids, links remapped or found again by natural key), and an all-or-nothing whole-installation restore with a safety backup. The Backup page and routes drive it; opened files wait in a private, expiring, account-bound temp file between steps.

**Tech Stack:** FastAPI, SQLAlchemy 2 (raw `text()` SQL on registry-named tables), SQLite, Jinja2, `cryptography` (AESGCM, scrypt), zipfile, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-backup-restore-design.md` (read it first; it records the owner's decisions).

All core code in this plan was written and tested in a throwaway copy of the repository first (1408 tests passing there, 92 of them new); the plan embeds those files and tests verbatim.

## Global Constraints

- Run tests with `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. The whole suite must pass after every task.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Work directly on `main`; do not push (the owner says "Push").
- **Vendors and price lists are never released in the repo** (the owner's legal rule). Backups contain them, so `*.amidebackup` is git-ignored; no test, doc or commit message may use real vendor, price-list or personal data (invented names only: Acme, Zephyr, Zorvex, Quillamine).
- Every backup, export or share is a **new dated file**; nothing overwrites an earlier file.
- Backup files are **always encrypted**; the passphrase is at least 8 characters and is never stored or logged. Nothing is kept in the browser (no localStorage/sessionStorage/IndexedDB).
- A person's own backup or export never contains password hashes, 2FA secrets, email, another person's rows or sessions; only a whole-installation backup (administrator) carries credentials. A share file only offers Vendors, Price lists, Library, Inventory, Workouts and Protocols and strips personal records; it loads with Add only.
- Shared sections (vendors, price lists, library) load only for the administrator, only with Add, merged by natural key. Whole-installation restore is administrator-only (404 to everyone else).
- New dependency: `cryptography` pinned in `requirements.txt` (already installed through the PDF libraries). No other new dependency.
- SQL is built only from names in the registry (`app/backup/sections.py`); every value is a bound parameter. Section keys and modes from a request are validated against the registry before use.
- Match the surrounding code: docstrings explain why; templates use existing classes (`builder-step`, `side-box`, `chips`, `chip-checkbox`, `tag`, `btn`, `wk-tabs`); dates display MM/DD/YYYY.

## Review Focus

Failure modes the spec implies that the happy path does not exercise; each has a test in the named task.

1. **A damaged, truncated, re-encrypted-with-the-wrong-key or tampered file** (any byte, header or body) must be refused before anything is parsed or changed: Task 1.
2. **A failure part-way through a load or restore** (an exception, a dangling foreign key, a full disk while writing the safety copy) must leave every row and file exactly as it was, and remove files it wrote: Tasks 3 and 4.
3. **A file from a newer or an older schema:** newer is refused; older loads and restores with defaults for columns it lacked: Tasks 3 and 4.
4. **A forged file** (rows claiming another person's `owner_id`, odd file names and extensions, entries with `..` paths, a zip that expands past the size limit): everything lands on the loading person's account, names are regenerated, nothing escapes the data directories: Tasks 1 and 3.
5. **One person loading another's token, an expired token, a reused token, or a non-administrator opening installation-only actions:** refused: Task 5.
6. **Unicode or space-padded passphrases, and the size limit on both creating and opening:** Tasks 1 and 5.
7. **The Replace/Add semantics people will actually hit:** duplicates (a second journal entry for the same day), merged vendors, links to inventory cleared when only inventory is replaced and restored when both are: Task 3.

## File Structure

| File | Responsibility |
|---|---|
| `app/backup/container.py` | Encrypt and decrypt bytes (AES-256-GCM, scrypt key) |
| `app/backup/archive.py` | The zip inside: manifest, hashed entries, safe reading |
| `app/backup/sections.py` | The registry: sections, tables, filters, files, sharing and merge rules |
| `app/backup/export.py` | Build an archive for a person or the whole installation |
| `app/backup/load.py` | Preview and load sections (Add / Replace), remapping and natural-key links |
| `app/backup/restore.py` | Whole-installation restore with a safety backup |
| `app/backup/pending.py` | Opened files waiting between steps (private temp file, ten minutes, account-bound) |
| `app/backup/service.py` | Make a sealed download; open an upload |
| `app/routers/backup.py` | Routes and the page; the older JSON/CSV endpoints stay |
| `app/templates/backup/` | `backup.html` (3 tabs), `preview.html`, `result.html`, `restored.html` |
| `app/static/js/backup.js` | Passphrase confirmation, toggle, typed RESTORE |

---

### Task 1: Container, archive, dependency and settings

**Files:**
- Create: `app/backup/__init__.py` (empty), `app/backup/container.py`, `app/backup/archive.py`, `tests/test_backup_container.py`
- Modify: `requirements.txt`, `app/config.py`, `.gitignore`

**Interfaces:**
- Produces: `container.BackupError(ValueError)`, `container.seal(data, passphrase, *, n=None) -> bytes`, `container.unseal(blob, passphrase) -> bytes`, `container.check_passphrase(passphrase)`, `container.SCRYPT_N`, `container.MIN_PASSPHRASE`; `archive.write_archive(manifest: dict, entries: dict[str, bytes]) -> bytes`, `archive.read_archive(data, *, max_bytes) -> Archive` (`.manifest`, `.names()`, `.has(name)`, `.read(name)`, `.json(name)`), `archive.FORMAT`; `config.MAX_BACKUP_BYTES`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_container.py`:

```python
import io
import zipfile

import pytest

from app.backup import container
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError, seal, unseal

FAST = 2 ** 10     # scrypt cost for tests; the real default is much higher


def test_a_sealed_file_round_trips_with_the_right_passphrase():
    blob = seal(b"hello backup", "correct horse", n=FAST)
    assert blob.startswith(b"AMIDEBK1") and b"hello backup" not in blob
    assert unseal(blob, "correct horse") == b"hello backup"


def test_each_seal_is_different_even_for_the_same_input():
    assert seal(b"x", "passphrase1", n=FAST) != seal(b"x", "passphrase1", n=FAST)


@pytest.mark.parametrize("passphrase", ["", "short", "wrong passphrase!"])
def test_a_wrong_passphrase_is_refused(passphrase):
    blob = seal(b"secret", "correct horse", n=FAST)
    with pytest.raises(BackupError, match="Wrong passphrase"):
        unseal(blob, passphrase)


def test_a_passphrase_under_eight_characters_cannot_make_a_backup():
    with pytest.raises(BackupError, match="at least 8"):
        seal(b"x", "short", n=FAST)


def test_any_change_to_the_file_is_detected_wherever_it_lands():
    blob = bytearray(seal(b"secret data here", "correct horse", n=FAST))
    for position in (9, 20, 30, len(blob) // 2, len(blob) - 1):    # header fields, ciphertext, tag
        changed = bytearray(blob)
        changed[position] ^= 0x01
        with pytest.raises(BackupError):
            unseal(bytes(changed), "correct horse")


def test_a_truncated_or_foreign_file_is_refused():
    blob = seal(b"secret", "correct horse", n=FAST)
    for bad in (blob[:20], blob[:-3], b"", b"PK\x03\x04 not a backup at all, long enough to pass the length check"):
        with pytest.raises(BackupError, match="not an Amide backup|Wrong passphrase|damaged"):
            unseal(bad, "correct horse")


def test_a_header_asking_for_an_absurd_scrypt_cost_is_refused_without_computing_it():
    blob = bytearray(seal(b"x", "correct horse", n=FAST))
    blob[8:12] = (2 ** 30).to_bytes(4, "big")
    with pytest.raises(BackupError, match="damaged"):
        unseal(bytes(blob), "correct horse")


def test_the_default_cost_is_the_real_one():
    assert container.SCRYPT_N >= 2 ** 15


# ---------------------------------------------------------------- the archive inside

def test_an_archive_round_trips_and_lists_hashes():
    zipped = write_archive({"kind": "backup"}, {"sections/a.json": b'{"x": 1}', "files/coa/f.pdf": b"%PDF"})
    archive = read_archive(zipped, max_bytes=10_000)
    assert archive.manifest["kind"] == "backup" and archive.manifest["format"] == 1
    assert archive.names() == ["files/coa/f.pdf", "sections/a.json"]
    assert archive.json("sections/a.json") == {"x": 1} and archive.read("files/coa/f.pdf") == b"%PDF"


def test_an_entry_changed_after_writing_fails_its_checksum():
    zipped = write_archive({}, {"sections/a.json": b'{"x": 1}'})
    source = zipfile.ZipFile(io.BytesIO(zipped))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for info in source.infolist():
            z.writestr(info.filename, b'{"x": 2}' if info.filename == "sections/a.json" else source.read(info.filename))
    with pytest.raises(BackupError, match="checksum"):
        read_archive(out.getvalue(), max_bytes=10_000)


def test_an_unlisted_extra_entry_is_refused():
    zipped = write_archive({}, {"sections/a.json": b"{}"})
    out = io.BytesIO(zipped)
    with zipfile.ZipFile(out, "a") as z:
        z.writestr("sections/smuggled.json", b"{}")
    with pytest.raises(BackupError, match="damaged"):
        read_archive(out.getvalue(), max_bytes=10_000)


@pytest.mark.parametrize("name", ["../evil", "/abs", "a/../b", "back\\slash", "", "manifest.json"])
def test_unsafe_entry_names_cannot_be_written(name):
    with pytest.raises(BackupError, match="Invalid entry name"):
        write_archive({}, {name: b"x"})


def test_an_oversized_archive_is_refused_before_it_is_read():
    zipped = write_archive({}, {"sections/big.json": b"0" * 50_000})
    with pytest.raises(BackupError, match="larger than"):
        read_archive(zipped, max_bytes=10_000)


def test_garbage_is_not_an_archive():
    with pytest.raises(BackupError, match="damaged"):
        read_archive(b"this is not a zip", max_bytes=10_000)


def test_any_passphrase_works_including_spaces_and_non_latin_characters():
    for passphrase in ("  spaces  around  ", "pass\u00e9\u00e8\u00ea word", "\u30d1\u30b9\u30ef\u30fc\u30c9\u30d1\u30b9\u30ef\u30fc\u30c9", "p" * 500):
        assert unseal(seal(b"data", passphrase, n=FAST), passphrase) == b"data"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backup_container.py -q -p no:warnings`
Expected: FAIL (collection error: `No module named 'app.backup'`).

- [ ] **Step 3: Declare the dependency, the size setting and the ignore rule**

Add the line `cryptography==50.0.2` to `requirements.txt` (alphabetical position is not required; keep the file's style). In `app/config.py`, directly after the `MAX_UPLOAD_BYTES` definition add:

```python

# A backup file (built in memory) larger than this is refused, both when creating and when opening one.
MAX_BACKUP_BYTES = int(os.environ.get("AMIDE_MAX_BACKUP_MB", "512")) * 1024 * 1024
```

Append `*.amidebackup` to `.gitignore` (backups hold vendor and personal data and must never be committed).

- [ ] **Step 4: Create the package, the container and the archive**

Create the empty file `app/backup/__init__.py`. Create `app/backup/container.py`:

```python
"""The encrypted wrapper around a backup archive.

A `.amidebackup` file is: magic bytes, the scrypt cost, a random salt and nonce, then the AES-256-GCM encryption of
the archive. The header is authenticated too, so any change to the file, a wrong passphrase, or a truncated download
all fail the same way, before anything inside is parsed. Pure bytes in, bytes out."""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"AMIDEBK1"
SALT_LEN = 16
NONCE_LEN = 12
SCRYPT_N = 2 ** 15              # about 100 ms and 32 MB per attempt
MIN_PASSPHRASE = 8
_N_MIN, _N_MAX = 2 ** 10, 2 ** 20   # accepted range when reading, so a crafted header cannot ask for gigabytes
_HEADER = struct.Struct(">8sI16s12s")


class BackupError(ValueError):
    """A backup cannot be created, read or loaded. The message is written for the person and safe to show."""


def _derive(passphrase: str, salt: bytes, n: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode("utf-8"))


def check_passphrase(passphrase: str) -> None:
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise BackupError(f"The passphrase must be at least {MIN_PASSPHRASE} characters.")


def seal(data: bytes, passphrase: str, *, n: int | None = None) -> bytes:
    """Encrypt `data` with `passphrase`. `n` is the scrypt cost (tests lower it; the default is SCRYPT_N)."""
    check_passphrase(passphrase)
    n = n or SCRYPT_N
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    header = _HEADER.pack(MAGIC, n, salt, nonce)
    return header + AESGCM(_derive(passphrase, salt, n)).encrypt(nonce, data, header)


def unseal(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a sealed file, or raise BackupError."""
    if len(blob) < _HEADER.size + 16 or blob[:8] != MAGIC:
        raise BackupError("This is not an Amide backup file.")
    magic, n, salt, nonce = _HEADER.unpack(blob[:_HEADER.size])
    if not _N_MIN <= n <= _N_MAX or n & (n - 1):
        raise BackupError("This backup file is damaged.")
    try:
        return AESGCM(_derive(passphrase or "", salt, n)).decrypt(nonce, blob[_HEADER.size:], blob[:_HEADER.size])
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the file is damaged.") from None
```

Create `app/backup/archive.py`:

```python
"""The zip archive inside a backup: a manifest, section JSON files and attached files, each checked by SHA-256.

`write_archive` turns {path: bytes} into one zip; `read_archive` opens one, verifies every entry against the manifest
and refuses anything oversized or oddly named. Pure bytes in, bytes out."""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

from app.backup.container import BackupError

FORMAT = 1
MANIFEST = "manifest.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(name: str) -> bool:
    return bool(name) and not name.startswith("/") and ".." not in name.split("/") and "\\" not in name


def write_archive(manifest: dict, entries: dict[str, bytes]) -> bytes:
    """One zip holding `manifest.json` (with a hash per entry) and every entry."""
    for name in entries:
        if not _safe(name) or name == MANIFEST:
            raise BackupError(f"Invalid entry name: {name}")
    full = {**manifest, "format": FORMAT, "entries": {name: _sha(data) for name, data in sorted(entries.items())}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(full, indent=1, ensure_ascii=False))
        for name, data in sorted(entries.items()):
            z.writestr(name, data)
    return out.getvalue()


@dataclass
class Archive:
    manifest: dict
    _entries: dict[str, bytes]

    def names(self) -> list[str]:
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def read(self, name: str) -> bytes:
        return self._entries[name]

    def json(self, name: str) -> dict:
        return json.loads(self._entries[name].decode("utf-8"))


def read_archive(data: bytes, *, max_bytes: int) -> Archive:
    """Open and verify an archive, or raise BackupError."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
        if sum(i.file_size for i in infos) > max_bytes:
            raise BackupError("This backup is larger than the allowed size.")
        if any(not _safe(i.filename) for i in infos):
            raise BackupError("This backup file is damaged.")
        manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        if manifest.get("format") != FORMAT:
            raise BackupError("This backup was made by a version of Amide this one cannot read.")
        entries = {}
        for name, digest in manifest.get("entries", {}).items():
            if not _safe(name) or name == MANIFEST:
                raise BackupError("This backup file is damaged.")
            blob = z.read(name)
            if _sha(blob) != digest:
                raise BackupError("This backup file is damaged (a checksum does not match).")
            entries[name] = blob
        if set(entries) != {i.filename for i in infos} - {MANIFEST}:
            raise BackupError("This backup file is damaged (unlisted or missing entries).")
        return Archive(manifest, entries)
    except BackupError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
        raise BackupError("This backup file is damaged.") from None
```

- [ ] **Step 5: Run the tests and the whole suite**

Expected: 22 passed in the file; full suite green.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app/config.py .gitignore app/backup/__init__.py app/backup/container.py app/backup/archive.py tests/test_backup_container.py
git commit -m "feat: encrypted backup container and verified archive format

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Section registry and export

**Files:**
- Create: `app/backup/sections.py`, `app/backup/export.py`, `tests/backup_helpers.py`, `tests/test_backup_export.py`

**Interfaces:**
- Consumes: Task 1 (`write_archive`, `read_archive`, `BackupError`), `config` directories, `Base.metadata`.
- Produces: `sections.SECTIONS: dict[str, Section]`, `Section(key, label, level, tables, shareable, library_files, help)`, `Tbl(name, where, file, share_drop, share_null, columns, reference)`, `LOAD_ORDER`, `PERSON_SECTIONS`, `SHARED_SECTIONS`, `SHAREABLE`, `PROFILE_COLUMNS`, `REFS`, `MERGE_KEYS`, `REFERENCE_TABLES`, `FILE_DIRS`, `NOT_BACKED_UP`, `table(name)`, `file_dir(key)`, `ordered(tbls)`, `person_columns(tbl)`, `uncovered_tables()`; `export.build_archive(session, *, kind, uid, creator, keys, installation=False) -> bytes` (kind `"backup" | "export" | "share"`), `export.table_rows(session, tbl, uid) -> list[dict]`, `export.section_payload(...)`, `export.KINDS`; test helpers `seed_world`, `person_counts`, `wipe_person`, `clean_files` in `tests/backup_helpers.py`.

- [ ] **Step 1: Write the test helpers and the failing tests**

Create `tests/backup_helpers.py` (builders with invented names; one person's data across every person section plus a vendor, a price list and a custom peptide):

```python
"""Builders for backup tests. Invented vendors (Acme, Zephyr) and peptides (Zorvex, Quillamine) only: real vendors and
price lists never appear in the repository."""

from datetime import date, datetime

from sqlalchemy import text

from app import config
from app.backup import sections as reg
from app.models import (
    ActiveVial, BodyMeasurement, ContactMethodType, DispensingMethod, DoseLog, DoseStatus, DoseUnit, FitnessTestExerciseName,
    FitnessTestResult, InventoryItem, JournalEntry, JournalEntrySideEffect, JournalQuickNote, JournalSideEffect, LabMarker,
    LabPanel, LabResult, Order, OrderItem, Peptide, PeptideSource, PriceList, PriceListItem, Protocol, ProtocolItem, Route, Sale,
    TimeOfDay, Vendor, VendorContact, VendorWallet, Warehouse, WarehouseSource, WaterLog, WorkoutExercise, WorkoutExerciseLog,
    WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource,
)

PDF = b"%PDF-1.4 test file"


def write(directory, name: str, data: bytes = PDF) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return name


def seed_world(db, uid: int, tag: str = "A") -> dict:
    """One person's data across every person section, plus a vendor, a price list and a custom peptide.
    Returns the main objects by name."""
    w: dict = {}
    method = db.query(ContactMethodType).filter_by(name="Email").first() or ContactMethodType(name="Email")
    vendor = Vendor(name=f"Acme Labs {tag}", website="https://acme.example", notes=f"note {tag}", created_by_id=uid,
                    price_list_filename=write(config.PRICE_LIST_DIR, f"vendor-{tag}.pdf"))
    vendor.contacts.append(VendorContact(method_type=method, value=f"sales@acme-{tag.lower()}.example"))
    vendor.wallets.append(VendorWallet(coin="BTC", address=f"bc1q{tag.lower()}wallet0000000000", network="Bitcoin",
                                       qr_filename=write(config.WALLET_QR_DIR, f"qr-{tag}.png", b"\x89PNG\r\n\x1a\n")))
    peptide = Peptide(name=f"Zorvex {tag}", source=PeptideSource.CUSTOM)
    db.add_all([vendor, peptide])
    db.flush()
    w["vendor"], w["peptide"] = vendor, peptide

    item = InventoryItem(owner_id=uid, name=f"Zorvex {tag} 10mg", vial_size_mg=10.0, vendor_id=vendor.id, count=5)
    db.add(item)
    db.flush()
    order = Order(order_date=date(2026, 3, 1), vendor_id=vendor.id, vendor=vendor.name)
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=5, received_quantity=5,
                     coa_filename=write(config.COA_DIR, f"coa-{tag}.pdf")))
    db.add(Sale(inventory_item_id=item.id, quantity=1, sale_date=date(2026, 3, 5), price_cents=2500))
    vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0, dose_value=250.0,
                      dose_unit=DoseUnit.MCG, doses_total=40, dispensing_method=DispensingMethod.SYRINGE,
                      volume_remaining_ml=1.5, date_mixed=date(2026, 3, 2), discard_by=date(2026, 4, 2))
    db.add(vial)
    db.flush()
    w["item"], w["order"], w["vial"] = item, order, vial

    protocol = Protocol(owner_id=uid, name=f"Stack {tag}", start_date=date(2026, 3, 3))
    protocol.items.append(ProtocolItem(peptide_id=peptide.id, inventory_item_id=item.id))
    db.add(protocol)
    db.flush()
    db.add(DoseLog(owner_id=uid, protocol_id=protocol.id, protocol_item_id=protocol.items[0].id, active_vial_id=vial.id,
                   peptide_id=peptide.id, peptide_name=peptide.name, dose_value=250.0, dose_unit=DoseUnit.MCG,
                   route=Route.SUBQ, scheduled_date=date(2026, 3, 4), scheduled_time_of_day=TimeOfDay.AM,
                   status=DoseStatus.ON_TIME))
    w["protocol"] = protocol

    plan = WorkoutPlan(owner_id=uid, name=f"Plan {tag}", source=WorkoutSource.MANUAL, started_on=date(2026, 3, 1),
                       source_pdf_filename=write(config.WORKOUT_PDF_DIR, f"plan-{tag}.pdf"))
    plan.days = [WorkoutPlanDay(position=0, label="Day A")]
    plan.days[0].exercises = [WorkoutExercise(position=0, name="Bench Press", sets_text="3", reps_text="8")]
    db.add(plan)
    db.flush()
    log = WorkoutLog(owner_id=uid, plan_day_id=plan.days[0].id, day_label="Day A", plan_name=plan.name,
                     log_date=date(2026, 3, 6))
    log.exercise_logs.append(WorkoutExerciseLog(exercise_id=plan.days[0].exercises[0].id, name="Bench Press", completed=True,
                                                sets=3, reps_value=8, weight_value=135.0, net_kcal=20.0))
    db.add(log)
    db.add(FitnessTestResult(owner_id=uid, exercise=FitnessTestExerciseName.MAX_PUSHUPS, value=30.0, tested_at=date(2026, 3, 7)))
    w["plan"] = plan

    db.add(BodyMeasurement(owner_id=uid, measured_at=date(2026, 3, 8), weight_lbs=180.0))
    db.add(WaterLog(owner_id=uid, logged_at=datetime(2026, 3, 8, 9, 0), ounces=16.0))
    entry = JournalEntry(owner_id=uid, entry_date=date(2026, 3, 9), mood=4, notes=f"journal {tag}")
    entry.side_effects.append(JournalEntrySideEffect(side_effect=JournalSideEffect.HEADACHE))
    entry.quick_notes.append(JournalQuickNote(noted_at=datetime(2026, 3, 9, 10, 0), text="quick"))
    db.add(entry)
    panel = LabPanel(owner_id=uid, drawn_at=date(2026, 3, 10), report_filename=write(config.LAB_REPORT_DIR, f"lab-{tag}.pdf"))
    panel.results.append(LabResult(marker=LabMarker.TOTAL_TESTOSTERONE, value=550.0))
    db.add(panel)
    pl = PriceList(vendor_name=vendor.name, vendor_id=vendor.id, warehouse=Warehouse.US, warehouse_source=WarehouseSource.FILENAME,
                   list_date=date(2026, 3, 11), source_filename=f"{vendor.name} - us - 2026-03-11.pdf")
    pl.items.append(PriceListItem(code="ZX", product_name=peptide.name, peptide_id=peptide.id, vial_amount=10, vial_unit="mg",
                                  pack_size=10, pack_price=100.0))
    db.add(pl)
    db.commit()
    return w


def person_counts(db, uid: int, keys=None) -> dict[str, int]:
    """{table: row count} of the person's rows for the person sections (and every row of the shared tables)."""
    from app.backup.export import table_rows
    out = {}
    for key in keys or reg.LOAD_ORDER:
        section = reg.SECTIONS[key]
        for tbl in section.tables:
            who = uid if section.level == reg.PERSON else None
            out[tbl.name] = len(table_rows(db, tbl, who))
    return out


def wipe_person(db, uid: int) -> None:
    """Delete one person's rows in every person section and every shared row the seed made (not accounts)."""
    for table in ("dose_logs", "protocols", "workout_logs", "workout_plans", "fitness_test_results", "body_measurements",
                  "water_logs", "journal_entries", "lab_panels", "active_vials"):
        db.execute(text(f"DELETE FROM {table} WHERE owner_id = :u"), {"u": uid})
    db.execute(text("DELETE FROM orders WHERE id IN (SELECT order_id FROM order_items)"))
    db.execute(text("DELETE FROM inventory_items WHERE owner_id = :u"), {"u": uid})
    db.execute(text("DELETE FROM price_lists"))
    db.execute(text("DELETE FROM vendors"))
    db.execute(text("DELETE FROM peptides WHERE source = 'custom'"))
    db.commit()


def clean_files() -> None:
    for directory in (config.COA_DIR, config.PRICE_LIST_DIR, config.LAB_REPORT_DIR, config.WORKOUT_PDF_DIR, config.WALLET_QR_DIR):
        if directory.is_dir():
            for f in directory.glob("*"):
                f.unlink()
```

Create `tests/test_backup_export.py`:

```python
from datetime import date

import pytest
from sqlalchemy import text

from app import config
from app.backup import sections as reg
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import BodyMeasurement, InventoryItem
from backup_helpers import clean_files, seed_world, wipe_person

ALL_PERSON = list(reg.PERSON_SECTIONS)


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def open_archive(data):
    return read_archive(data, max_bytes=50_000_000)


def backup(db, me, keys, kind="backup", **kw):
    return open_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys, **kw))


def test_the_registry_covers_every_table_in_the_schema():
    assert reg.uncovered_tables() == set()


def test_every_link_leaving_a_section_is_resolved_by_name_or_deliberately_left_empty():
    deliberately_empty = {("dose_logs", "active_vial_id")}          # a vial belongs to Inventory; empty without it
    for section in reg.SECTIONS.values():
        inside = {t.name for t in section.tables}
        for tbl in section.tables:
            for fk in reg.table(tbl.name).foreign_keys:
                target, col = fk.column.table.name, fk.parent.name
                assert target in inside or target == "users" or (tbl.name, col) in reg.REFS \
                    or (tbl.name, col) in deliberately_empty, (section.key, tbl.name, col)


def test_load_order_puts_shared_data_before_people_and_inventory_before_protocols():
    order = list(reg.LOAD_ORDER)
    assert order.index("vendors") < order.index("inventory") < order.index("protocols")
    assert order.index("library") < order.index("protocols") and "accounts" not in order


def test_a_backup_holds_only_the_chosen_sections_with_counts_and_files(client, db, me, world):
    archive = backup(db, me, ["inventory", "labs"])
    assert archive.manifest["kind"] == "backup" and archive.manifest["level"] == "person"
    assert set(archive.manifest["sections"]) == {"inventory", "labs"}
    assert archive.manifest["sections"]["inventory"]["rows"]["order_items"] == 1
    assert archive.has("sections/inventory.json") and not archive.has("sections/journal.json")
    names = archive.names()
    assert any(n.startswith("files/coa/") for n in names) and any(n.startswith("files/lab_reports/") for n in names)
    rows = archive.json("sections/inventory.json")["tables"]["inventory_items"]
    assert [r["name"] for r in rows] == ["Zorvex A 10mg"] and archive.manifest["revision"]


def test_the_profile_never_carries_credentials(client, db, me):
    archive = backup(db, me, ["profile"])
    row = archive.json("sections/profile.json")["tables"]["users"][0]
    assert set(row) <= set(reg.PROFILE_COLUMNS)
    blob = archive.read("sections/profile.json").decode()
    assert "password" not in blob and "totp" not in blob and "email" not in blob


def test_a_person_backup_never_contains_another_persons_rows(client, db, me, world):
    db.execute(text("INSERT INTO users (username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) "
                    "VALUES ('Other', 'other', 'x', 0, 0, 0, '2026-01-01')"))
    other = db.execute(text("SELECT id FROM users WHERE username_key = 'other'")).scalar()
    db.add_all([InventoryItem(owner_id=other, name="Quillamine Private", count=1),
                BodyMeasurement(owner_id=other, measured_at=date(2026, 1, 1), weight_lbs=99.0)])
    db.commit()
    try:
        archive = backup(db, me, ALL_PERSON)
        blob = b"".join(archive.read(n) for n in archive.names() if n.endswith(".json"))
        assert b"Quillamine Private" not in blob and b'"weight_lbs":99' not in blob
    finally:
        db.execute(text("DELETE FROM inventory_items WHERE owner_id = :o"), {"o": other})
        db.execute(text("DELETE FROM body_measurements WHERE owner_id = :o"), {"o": other})
        db.execute(text("DELETE FROM users WHERE username_key = 'other'"))
        db.commit()


def test_rows_that_point_outside_their_section_carry_natural_keys(client, db, me, world):
    protocols = backup(db, me, ["protocols"]).json("sections/protocols.json")["tables"]
    item = protocols["protocol_items"][0]
    assert item["_refs"]["peptide_id"] == ["Zorvex A"]
    assert item["_refs"]["inventory_item_id"] == ["Zorvex A 10mg", 10.0, "mg"]
    inventory = backup(db, me, ["inventory"]).json("sections/inventory.json")["tables"]
    assert inventory["inventory_items"][0]["_refs"]["vendor_id"] == ["Acme Labs A"]
    both = backup(db, me, ["inventory", "protocols"]).json("sections/protocols.json")["tables"]
    assert both["protocol_items"][0]["_refs"]["peptide_id"] == ["Zorvex A"]


def test_a_share_file_strips_personal_records_wallets_and_attached_personal_files(client, db, me, world):
    archive = backup(db, me, list(reg.SHAREABLE), kind="share")
    assert archive.manifest["kind"] == "share"
    tables = {}
    for name in archive.names():
        if name.startswith("sections/"):
            tables.update(archive.json(name)["tables"])
    for dropped in ("dose_logs", "workout_logs", "workout_exercise_logs", "fitness_test_results", "sales", "active_vials",
                    "vendor_wallets", "vendor_favorites", "price_alert_ignores"):
        assert dropped not in tables, dropped
    for kept in ("inventory_items", "orders", "order_items", "protocols", "protocol_items", "workout_plans",
                 "workout_exercises", "vendors", "vendor_contacts", "price_lists", "price_list_items", "peptides"):
        assert kept in tables, kept
    assert all(v["created_by_id"] is None for v in tables["vendors"])
    assert all(p["source_pdf_filename"] is None for p in tables["workout_plans"])
    blob = b"".join(archive.read(n) for n in archive.names() if n.endswith(".json"))
    assert b"bc1q" not in blob and b"vendor_wallets" not in blob
    assert not any(n.startswith("files/wallet_qr/") or n.startswith("files/workout_pdfs/") for n in archive.names())
    assert any(n.startswith("files/coa/") for n in archive.names())


@pytest.mark.parametrize("keys", [["journal"], ["labs"], ["measurements"], ["profile"], ["inventory", "journal"]])
def test_personal_sections_cannot_go_in_a_share_file(client, db, me, keys):
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        build_archive(db, kind="share", uid=me, creator="Tester", keys=keys)


@pytest.mark.parametrize("keys,message", [([], "at least one"), (["nonsense"], "Unknown section"), (["accounts"], "Unknown section")])
def test_bad_section_lists_are_refused(client, db, me, keys, message):
    with pytest.raises(BackupError, match=message):
        build_archive(db, kind="backup", uid=me, creator="Tester", keys=keys)


def test_an_unknown_kind_is_refused(client, db, me):
    with pytest.raises(BackupError, match="Unknown kind"):
        build_archive(db, kind="mystery", uid=me, creator="Tester", keys=["journal"])


def test_a_whole_installation_backup_has_every_person_every_shared_section_accounts_and_files(client, db, me, world):
    archive = open_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=[], installation=True))
    assert archive.manifest["level"] == "installation" and archive.manifest["persons"]
    mine = next(p for p in archive.manifest["persons"] if p["id"] == me)["username_key"]
    assert archive.has(f"persons/{mine}/inventory.json") and archive.has("sections/accounts.json")
    assert archive.has("sections/vendors.json") and archive.has("sections/library.json")
    accounts = archive.json("sections/accounts.json")["tables"]["users"]
    assert any(u["password_hash"] for u in accounts)                 # whole-installation backups do carry credentials
    assert not any("sessions" in n for n in archive.names())
    assert any(n.startswith("files/wallet_qr/") for n in archive.names())


def test_a_whole_installation_file_cannot_be_a_share_file(client, db, me):
    with pytest.raises(BackupError, match="cannot be a share"):
        build_archive(db, kind="share", uid=me, creator="Tester", keys=[], installation=True)


def test_library_cards_travel_with_the_library_section(client, db, me, world):
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CARDS_DIR / "001.jpg").write_bytes(b"\xff\xd8\xff card")
    config.CARDS_JSON.write_text("[]", encoding="utf-8")
    try:
        archive = backup(db, me, ["library"])
        assert archive.has("files/library/cards/001.jpg") and archive.has("files/library/cards.json")
    finally:
        (config.CARDS_DIR / "001.jpg").unlink()
        config.CARDS_JSON.unlink()


def test_a_missing_attached_file_is_noted_not_fatal(client, db, me, world):
    (config.LAB_REPORT_DIR / world["peptide"].name).unlink(missing_ok=True)
    for f in config.LAB_REPORT_DIR.glob("*"):
        f.unlink()
    archive = backup(db, me, ["labs"])
    assert archive.manifest["missing_files"] and archive.json("sections/labs.json")["tables"]["lab_panels"]
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`No module named 'app.backup.sections'`).

- [ ] **Step 3: Create the registry**

Create `app/backup/sections.py`:

```python
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
             "workout_pdfs": "WORKOUT_PDF_DIR", "wallet_qr": "WALLET_QR_DIR"}

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
              "journal", "labs")
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
```

- [ ] **Step 4: Create the export**

Create `app/backup/export.py`:

```python
"""Turns the database and attached files into an archive: a person's own sections, or the whole installation.

Rows are read as raw SQLite values, column by column, straight from the tables the registry names (names come only
from the registry, never from a request). A row that points at something outside its own section carries that
target's natural key under `_refs`, so loading it somewhere else can find the same vendor or peptide again."""

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.backup import sections as reg
from app.backup.archive import write_archive
from app.backup.container import BackupError

KINDS = ("backup", "export", "share")
SECTION_FORMAT = 1


def _columns(tbl: reg.Tbl) -> list[str]:
    names = [c.name for c in reg.table(tbl.name).columns]
    return [c for c in names if c in tbl.columns] if tbl.columns else names


def table_rows(session: Session, tbl: reg.Tbl, uid: int | None) -> list[dict]:
    """Raw rows of one table: a person's (filtered by the registry's SQL) or, with uid None, every row."""
    cols = ", ".join(f'"{c}"' for c in _columns(tbl))
    where = f" WHERE {tbl.where}" if tbl.where and uid is not None else ""
    return [dict(r) for r in session.execute(text(f'SELECT {cols} FROM "{tbl.name}"{where}'), {"uid": uid}
                                             if tbl.where and uid is not None else {}).mappings()]


def _attach_refs(session: Session, section: reg.Section, rows_by_table: dict[str, list[dict]]) -> None:
    """Add `_refs` to rows whose foreign key points outside the section's own tables (and not at a user)."""
    inside = {t.name for t in section.tables}
    cache: dict[tuple, list | None] = {}
    for tbl in section.tables:
        for fk in reg.table(tbl.name).foreign_keys:
            col, target = fk.parent.name, fk.column.table.name
            ref = reg.REFS.get((tbl.name, col))
            if target in inside or target == "users" or ref is None:
                continue
            keys = ref[1]
            for row in rows_by_table.get(tbl.name, ()):
                value = row.get(col)
                if value is None:
                    continue
                if (target, value) not in cache:
                    found = session.execute(
                        text(f'SELECT {", ".join(keys)} FROM "{target}" WHERE id = :id'), {"id": value}).first()
                    cache[(target, value)] = list(found) if found else None
                if cache[(target, value)] is not None:
                    row.setdefault("_refs", {})[col] = cache[(target, value)]


def section_payload(session: Session, section: reg.Section, uid: int | None, *, share: bool = False) -> dict:
    """{"tables": {name: rows}} for one section. In a share file, personal tables and columns are left out."""
    rows_by_table: dict[str, list[dict]] = {}
    for tbl in reg.ordered(section.tables):
        if share and tbl.share_drop:
            continue
        rows = table_rows(session, tbl, uid)
        if share and tbl.share_null:
            for row in rows:
                for col in tbl.share_null:
                    if col in row:
                        row[col] = None
        rows_by_table[tbl.name] = rows
    _attach_refs(session, section, rows_by_table)
    return {"format": SECTION_FORMAT, "section": section.key, "tables": rows_by_table}


def _files_for(section: reg.Section, payload: dict, *, share: bool) -> tuple[dict[str, bytes], list[str]]:
    """The attached files the payload's rows name, as {archive path: bytes}, and the names that were missing on disk."""
    files: dict[str, bytes] = {}
    missing: list[str] = []
    for tbl in section.tables:
        if not tbl.file or tbl.name not in payload["tables"]:
            continue
        col, dirkey = tbl.file
        if share and col in tbl.share_null:
            continue
        for row in payload["tables"][tbl.name]:
            name = row.get(col)
            if not name:
                continue
            path = reg.file_dir(dirkey) / name
            if path.is_file():
                files[f"files/{dirkey}/{name}"] = path.read_bytes()
            else:
                missing.append(f"{dirkey}/{name}")
    if section.library_files:
        if config.CARDS_JSON.is_file():
            files["files/library/cards.json"] = config.CARDS_JSON.read_bytes()
        if config.CARDS_DIR.is_dir():
            for path in sorted(config.CARDS_DIR.iterdir()):
                if path.is_file():
                    files[f"files/library/cards/{path.name}"] = path.read_bytes()
    return files, missing


def _revision(session: Session) -> str | None:
    return session.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _dump(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _counts(payload: dict) -> dict[str, int]:
    return {name: len(rows) for name, rows in payload["tables"].items()}


def build_archive(session: Session, *, kind: str, uid: int, creator: str, keys: list[str],
                  installation: bool = False) -> bytes:
    """The (unencrypted) zip bytes of a backup, export or share file.

    `kind` is "backup", "export" or "share". `keys` are the sections wanted. A share file takes only the shareable
    sections and strips personal records. With `installation` (administrators only) the file holds every section for
    every person, plus the shared ones, and `keys` is ignored."""
    if kind not in KINDS:
        raise BackupError("Unknown kind of backup.")
    share = kind == "share"
    entries: dict[str, bytes] = {}
    manifest_sections: dict[str, dict] = {}
    missing: list[str] = []

    def add(path: str, section: reg.Section, who: int | None, *, record: bool = True) -> None:
        payload = section_payload(session, section, who, share=share)
        entries[path] = _dump(payload)
        files, absent = _files_for(section, payload, share=share)
        entries.update(files)
        missing.extend(absent)
        if record:
            info = manifest_sections.setdefault(section.key, {"rows": {}, "files": 0})
            for name, n in _counts(payload).items():
                info["rows"][name] = info["rows"].get(name, 0) + n
            info["files"] += len(files)

    persons = []
    if installation:
        if share:
            raise BackupError("A whole-installation file cannot be a share file.")
        for uid_, key_, username in session.execute(text("SELECT id, username_key, username FROM users ORDER BY id")):
            persons.append({"id": uid_, "username_key": key_, "username": username})
            for key in reg.PERSON_SECTIONS:
                if key != "profile":
                    add(f"persons/{key_}/{key}.json", reg.SECTIONS[key], uid_)
        for key in reg.SHARED_SECTIONS + ("accounts",):
            add(f"sections/{key}.json", reg.SECTIONS[key], None)
    else:
        wanted = [k for k in dict.fromkeys(keys)]
        if not wanted:
            raise BackupError("Choose at least one section.")
        for key in wanted:
            section = reg.SECTIONS.get(key)
            if section is None or key == "accounts":
                raise BackupError("Unknown section.")
            if share and not section.shareable:
                raise BackupError(f"{section.label} cannot be put in a share file.")
            add(f"sections/{key}.json", section, uid if section.level == reg.PERSON else None)

    manifest = {
        "kind": kind, "level": "installation" if installation else "person",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "creator": creator,
        "revision": _revision(session), "sections": manifest_sections, "persons": persons, "missing_files": missing,
    }
    return write_archive(manifest, entries)
```

- [ ] **Step 5: Run the tests and the whole suite**

Expected: 21 passed in the file; full suite green. (`test_the_registry_covers_every_table_in_the_schema` is the guard: a future table must be added to a section or to `NOT_BACKED_UP`.)

- [ ] **Step 6: Commit**

```bash
git add app/backup/sections.py app/backup/export.py tests/backup_helpers.py tests/test_backup_export.py
git commit -m "feat: backup section registry and export (backup, export and share files)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Loading sections (Add and Replace)

**Files:**
- Create: `app/backup/load.py`, `tests/test_backup_load.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `load.ADD`, `load.REPLACE`, `load.Report(added, removed, skipped, unresolved, files)` (dicts keyed by section key, or `table.column` for `unresolved`), `load.describe(archive, *, username_key, is_admin, session=None, uid=None) -> list[dict]` (`key, label, level, rows, tables, allowed, reason, modes, current`; `current` is how many of the person's rows Replace would delete), `load.load(session, archive, *, uid, username_key, is_admin, plan: dict[str, str]) -> Report` (raises `BackupError`, commits on success), `load.check_revision(session, manifest)`.

Behaviour: Add inserts every row with a new primary key (links among loaded rows remapped; links outside found again by natural key, a missing peptide created as a custom peptide, anything unresolvable left empty and counted); duplicates that break a unique rule are skipped and counted. Replace first deletes the person's rows of the section (ids read before deleting), then adds. Shared sections are admin-only and Add-only, merged by name (existing vendor, peptide or price list kept; its dependent rows skipped). A share file loads with Add only. Everything runs in one transaction; any error rolls back and removes files written.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_load.py`:

```python
import json
import re

import pytest
from sqlalchemy import text

from app import config
from app.backup import load as loader
from app.backup import sections as reg
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from backup_helpers import clean_files, person_counts, seed_world, wipe_person

PERSON = list(reg.PERSON_SECTIONS)
UNIQUE_BY_DAY = {"journal_entries", "journal_entry_side_effects", "journal_quick_notes"}   # one entry per person per day


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


def export(db, me, keys, kind="export"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=keys), max_bytes=50_000_000)


def run(db, me, archive, plan, *, admin=True, username_key="tester"):
    return loader.load(db, archive, uid=me, username_key=username_key, is_admin=admin, plan=plan)


def add_all(keys):
    return {k: loader.REPLACE if k == "profile" else loader.ADD for k in keys}


def retag(archive, **changes):
    """The same archive with manifest fields changed (to test version handling)."""
    entries = {n: archive.read(n) for n in archive.names()}
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    manifest.update(changes)
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


def test_an_export_loads_into_an_empty_install_and_gives_the_same_data(client, db, me, world):
    keys = PERSON + ["vendors", "price_lists"]
    before = person_counts(db, me, keys)
    archive = export(db, me, keys)
    wipe_person(db, me)
    clean_files()
    assert person_counts(db, me, ["inventory"])["inventory_items"] == 0
    report = run(db, me, archive, add_all(keys))
    assert person_counts(db, me, keys) == before
    assert report.removed == {} and report.unresolved == {} and report.files == 5
    item = db.execute(text("SELECT id, vendor_id, name FROM inventory_items WHERE owner_id = :u"), {"u": me}).one()
    assert db.execute(text("SELECT name FROM vendors WHERE id = :v"), {"v": item.vendor_id}).scalar() == "Acme Labs A"
    link = db.execute(text("SELECT inventory_item_id, peptide_id FROM protocol_items")).one()
    assert link.inventory_item_id == item.id
    assert db.execute(text("SELECT name FROM peptides WHERE id = :p"), {"p": link.peptide_id}).scalar() == "Zorvex A"
    vial = db.execute(text("SELECT active_vial_id, protocol_item_id FROM dose_logs")).one()
    assert vial.active_vial_id and vial.protocol_item_id
    plist = db.execute(text("SELECT vendor_id FROM price_lists")).scalar()
    assert db.execute(text("SELECT name FROM vendors WHERE id = :v"), {"v": plist}).scalar() == "Acme Labs A"
    log = db.execute(text("SELECT plan_day_id FROM workout_logs")).scalar()
    assert db.execute(text("SELECT label FROM workout_plan_days WHERE id = :d"), {"d": log}).scalar() == "Day A"
    name = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    assert name and (config.COA_DIR / name).read_bytes().startswith(b"%PDF")


def test_every_owner_column_becomes_the_person_loading_whatever_the_file_says(client, db, me, world):
    archive = export(db, me, ["journal", "measurements"])
    entries = {n: archive.read(n) for n in archive.names()}
    for name in ("sections/journal.json", "sections/measurements.json"):
        payload = json.loads(entries[name])
        for rows in payload["tables"].values():
            for row in rows:
                if "owner_id" in row:
                    row["owner_id"] = 987654
        entries[name] = json.dumps(payload).encode()
    forged = read_archive(write_archive({k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}, entries),
                          max_bytes=50_000_000)
    wipe_person(db, me)
    run(db, me, forged, add_all(["journal", "measurements"]))
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = 987654")).scalar() == 0
    assert db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE owner_id = :u"), {"u": me}).scalar() == 1


def test_add_keeps_what_is_there_skips_duplicates_and_merges_shared_data_by_name(client, db, me, world):
    keys = ["inventory", "journal", "vendors"]
    before = person_counts(db, me, keys)
    report = run(db, me, export(db, me, keys), add_all(keys))
    after = person_counts(db, me, keys)
    assert after["inventory_items"] == before["inventory_items"] + 1 and after["order_items"] == before["order_items"] + 1
    assert after["journal_entries"] == before["journal_entries"]                  # one entry per day: the copy is skipped
    assert after["vendors"] == before["vendors"] and after["vendor_contacts"] == before["vendor_contacts"]
    assert report.skipped["journal"] >= 1 and report.skipped["vendors"] >= 1 and report.added["inventory"] > 0


def test_replace_makes_the_section_match_the_file_and_removes_what_it_replaced(client, db, me, world):
    archive = export(db, me, ["inventory"])
    db.execute(text("UPDATE inventory_items SET count = 99 WHERE owner_id = :u"), {"u": me})
    db.execute(text("INSERT INTO inventory_items (owner_id, name, count, created_at, updated_at) "
                    "VALUES (:u, 'Extra Item', 1, '2026-01-01', '2026-01-01')"), {"u": me})
    db.commit()
    old_file = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    report = run(db, me, archive, {"inventory": loader.REPLACE})
    rows = db.execute(text("SELECT name, count FROM inventory_items WHERE owner_id = :u"), {"u": me}).all()
    assert [(r.name, r.count) for r in rows] == [("Zorvex A 10mg", 5)]
    assert report.removed["inventory"] >= 4 and report.added["inventory"] >= 4
    assert not (config.COA_DIR / old_file).exists()                             # the replaced COA file was removed
    assert (config.COA_DIR / db.execute(text("SELECT coa_filename FROM order_items")).scalar()).exists()


def test_replacing_inventory_alone_clears_links_into_it_and_replacing_both_restores_them(client, db, me, world):
    archive = export(db, me, ["inventory", "protocols"])
    run(db, me, archive, {"inventory": loader.REPLACE})
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() is None
    run(db, me, archive, {"inventory": loader.REPLACE, "protocols": loader.REPLACE})
    item = db.execute(text("SELECT id FROM inventory_items WHERE owner_id = :u"), {"u": me}).scalar()
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() == item


def test_a_failure_part_way_changes_nothing_and_removes_the_files_it_wrote(client, db, me, world, monkeypatch):
    archive = export(db, me, ["inventory", "journal"])
    before = person_counts(db, me, ["inventory", "journal"])
    files_before = sorted(p.name for p in config.COA_DIR.glob("*"))
    real, calls = loader._insert_row, {"n": 0}

    def flaky(ctx, section, tbl, row):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("disk full")
        return real(ctx, section, tbl, row)

    monkeypatch.setattr(loader, "_insert_row", flaky)
    with pytest.raises(BackupError, match="nothing was changed"):
        run(db, me, archive, {"inventory": loader.REPLACE, "journal": loader.REPLACE})
    monkeypatch.undo()
    assert person_counts(db, me, ["inventory", "journal"]) == before
    assert sorted(p.name for p in config.COA_DIR.glob("*")) == files_before


def test_loading_one_section_leaves_the_rest_alone(client, db, me, world):
    archive = export(db, me, PERSON)
    before = person_counts(db, me, PERSON)
    run(db, me, archive, {"workouts": loader.REPLACE})
    assert person_counts(db, me, PERSON) == before


def test_shared_sections_need_an_administrator_and_can_only_be_added(client, db, me, world):
    archive = export(db, me, ["vendors", "inventory"])
    with pytest.raises(BackupError, match="administrator"):
        run(db, me, archive, {"vendors": loader.ADD}, admin=False)
    with pytest.raises(BackupError, match="can only be loaded as"):
        run(db, me, archive, {"vendors": loader.REPLACE})
    run(db, me, archive, {"inventory": loader.ADD}, admin=False)               # people load their own sections freely


def test_a_share_file_can_only_be_added_and_loads_into_an_empty_install(client, db, me, world):
    keys = list(reg.SHAREABLE)
    archive = export(db, me, keys, kind="share")
    with pytest.raises(BackupError, match="can only be loaded as"):
        run(db, me, archive, {"inventory": loader.REPLACE})
    wipe_person(db, me)
    clean_files()
    report = run(db, me, archive, {k: loader.ADD for k in ("vendors", "price_lists", "inventory", "protocols", "workouts")})
    counts = person_counts(db, me, ["inventory", "protocols", "workouts", "vendors"])
    assert counts["inventory_items"] == 1 and counts["protocol_items"] == 1 and counts["workout_plans"] == 1
    assert counts["vendors"] == 1 and counts["vendor_wallets"] == 0 and counts["workout_logs"] == 0 and counts["dose_logs"] == 0
    assert report.unresolved == {}


def test_a_file_from_a_newer_version_is_refused_and_an_older_one_loads_with_defaults(client, db, me, world):
    archive = export(db, me, ["workouts"])
    with pytest.raises(BackupError, match="newer version"):
        run(db, me, retag(archive, revision="9999"), {"workouts": loader.ADD})
    entries = {n: archive.read(n) for n in archive.names()}
    payload = json.loads(entries["sections/workouts.json"])
    for row in payload["tables"]["workout_exercise_logs"]:
        row.pop("net_kcal"), row.pop("compendium_code")                          # columns an older schema did not have
    entries["sections/workouts.json"] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "0001"}
    older = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    run(db, me, older, {"workouts": loader.REPLACE})
    assert db.execute(text("SELECT net_kcal FROM workout_exercise_logs")).scalar() is None


def test_links_that_cannot_be_found_become_empty_and_are_reported_and_missing_peptides_are_created(client, db, me, world):
    archive = export(db, me, ["protocols"])
    wipe_person(db, me)
    report = run(db, me, archive, {"protocols": loader.ADD})
    assert db.execute(text("SELECT inventory_item_id FROM protocol_items")).scalar() is None
    assert report.unresolved.get("protocol_items.inventory_item_id") == 1 and report.unresolved.get("dose_logs.active_vial_id") == 1
    peptide = db.execute(text("SELECT p.name, p.source FROM protocol_items i JOIN peptides p ON p.id = i.peptide_id")).one()
    assert (peptide.name, peptide.source) == ("Zorvex A", "custom")


def test_the_profile_is_applied_to_the_signed_in_person_only(client, db, me, world):
    db.execute(text("UPDATE users SET height_in = 70, water_goal_oz = 90 WHERE id = :u"), {"u": me})
    db.commit()
    archive = export(db, me, ["profile"])
    db.execute(text("UPDATE users SET height_in = 60, water_goal_oz = NULL WHERE id = :u"), {"u": me})
    db.commit()
    report = run(db, me, archive, {"profile": loader.REPLACE})
    row = db.execute(text("SELECT height_in, water_goal_oz, password_hash FROM users WHERE id = :u"), {"u": me}).one()
    assert (row.height_in, row.water_goal_oz) == (70, 90) and row.password_hash and report.added["profile"] == 1
    db.execute(text("UPDATE users SET height_in = NULL, water_goal_oz = NULL WHERE id = :u"), {"u": me})
    db.commit()


def test_bad_plans_are_refused_before_anything_changes(client, db, me, world):
    archive = export(db, me, ["journal"])
    for plan, message in (({}, "at least one"), ({"inventory": loader.ADD}, "not in this backup"),
                          ({"journal": "merge"}, "can only be loaded as"), ({"accounts": loader.ADD}, "not in this backup")):
        with pytest.raises(BackupError, match=message):
            run(db, me, archive, plan)


def test_describe_lists_sections_with_what_this_person_may_do(client, db, me, world):
    archive = export(db, me, ["journal", "vendors", "profile"])
    mine = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=False)}
    assert mine["journal"]["modes"] == ["add", "replace"] and mine["journal"]["rows"] == 3
    assert mine["vendors"]["allowed"] is False and mine["vendors"]["modes"] == []
    assert mine["profile"]["modes"] == ["replace"]
    admin = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=True)}
    assert admin["vendors"]["modes"] == ["add"]


def test_loading_from_a_whole_installation_file_takes_only_this_persons_partition(client, db, me, world):
    archive = read_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=[], installation=True),
                           max_bytes=50_000_000)
    listed = {d["key"] for d in loader.describe(archive, username_key="tester", is_admin=True)}
    assert {"inventory", "journal", "vendors", "library"} <= listed and "accounts" not in listed
    nobody = loader.describe(archive, username_key="nobody", is_admin=False)
    assert all(not d["allowed"] for d in nobody if d["level"] == reg.PERSON)
    wipe_person(db, me)
    run(db, me, archive, {"journal": loader.ADD, "inventory": loader.ADD, "vendors": loader.ADD})
    assert person_counts(db, me, ["journal"])["journal_entries"] == 1


def test_library_files_are_added_without_overwriting_existing_ones(client, db, me, world):
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CARDS_DIR / "001.jpg").write_bytes(b"original")
    config.CARDS_JSON.write_text("[]", encoding="utf-8")
    try:
        archive = export(db, me, ["library"])
        (config.CARDS_DIR / "001.jpg").write_bytes(b"local edit")
        (config.CARDS_DIR / "002.jpg").unlink(missing_ok=True)
        run(db, me, archive, {"library": loader.ADD})
        assert (config.CARDS_DIR / "001.jpg").read_bytes() == b"local edit"
    finally:
        (config.CARDS_DIR / "001.jpg").unlink(missing_ok=True)
        config.CARDS_JSON.unlink(missing_ok=True)


def test_attached_files_are_stored_under_new_names_with_a_safe_extension(client, db, me, world):
    archive = export(db, me, ["inventory"])
    entries = {n: archive.read(n) for n in archive.names()}
    old_name = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    payload = json.loads(entries["sections/inventory.json"])
    payload["tables"]["order_items"][0]["coa_filename"] = "weird name.b c"
    entries["sections/inventory.json"] = json.dumps(payload).encode()
    entries["files/coa/weird name.b c"] = entries.pop(f"files/coa/{old_name}")
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    run(db, me, read_archive(write_archive(manifest, entries), max_bytes=50_000_000), {"inventory": loader.REPLACE})
    stored = db.execute(text("SELECT coa_filename FROM order_items")).scalar()
    assert re.fullmatch(r"[0-9a-f]{32}", stored) and (config.COA_DIR / stored).is_file()


def test_describe_says_how_many_current_rows_replace_would_delete(client, db, me, world):
    archive = export(db, me, ["inventory", "journal", "profile", "vendors"])
    listed = {d["key"]: d for d in loader.describe(archive, username_key="tester", is_admin=True, session=db, uid=me)}
    assert listed["inventory"]["current"] == 5 and listed["journal"]["current"] == 3
    assert listed["profile"]["current"] is None and listed["vendors"]["current"] is None
    assert all(d["current"] is None for d in loader.describe(archive, username_key="tester", is_admin=True))
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`No module named 'app.backup.load'`).

- [ ] **Step 3: Create the loader**

Create `app/backup/load.py`:

```python
"""Loads chosen sections of a backup into the signed-in person's account (or, for an administrator, shared data).

Two modes per section. **Add** inserts every row of the section with a new primary key and keeps the links between
them; rows that would break a unique rule (a second journal entry for the same day, a vendor with a name already
taken) are skipped and counted. **Replace** first deletes the person's rows of that section, then adds. Shared
sections (vendors, price lists, library) are only ever added, by merging on a natural key: an existing vendor, peptide
or price list is kept and its dependent rows are skipped.

A row that points at something outside the loaded sections finds it again by natural key (a peptide or vendor by name,
an inventory item by name and vial size); an unresolvable link becomes empty and is counted in the report. Everything
runs in the caller's transaction: any error rolls it all back and removes the files it wrote."""

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.backup import sections as reg
from app.backup.archive import Archive
from app.backup.container import BackupError
from app.backup.export import _columns, _revision, table_rows

ADD, REPLACE = "add", "replace"
_CHUNK = 500


@dataclass
class Report:
    added: dict[str, int] = field(default_factory=dict)       # section -> rows inserted
    removed: dict[str, int] = field(default_factory=dict)     # section -> rows deleted before loading (Replace)
    skipped: dict[str, int] = field(default_factory=dict)     # section -> rows left out (duplicates, merged)
    unresolved: dict[str, int] = field(default_factory=dict)  # "table.column" or "file" -> links that became empty
    files: int = 0


def _source(archive: Archive, key: str, username_key: str) -> str | None:
    """Archive path of a section for this person, or None when the file does not have it."""
    section = reg.SECTIONS[key]
    path = (f"persons/{username_key}/{key}.json" if archive.manifest.get("level") == "installation"
            and section.level == reg.PERSON else f"sections/{key}.json")
    return path if archive.has(path) else None


def check_revision(session: Session, manifest: dict) -> None:
    """Refuse a backup made by a newer schema than this installation's."""
    current, theirs = _revision(session), manifest.get("revision")
    if theirs and current and theirs.isdigit() and current.isdigit() and int(theirs) > int(current):
        raise BackupError("This backup was made by a newer version of Amide than this one. Update Amide first.")


def describe(archive: Archive, *, username_key: str, is_admin: bool, session: Session | None = None,
             uid: int | None = None) -> list[dict]:
    """Every section in the file with what the signed-in person may do with it. With a session and uid, each person
    section also says how many of their current rows Replace would delete (`current`)."""
    kind = archive.manifest.get("kind")
    out = []
    for key in reg.LOAD_ORDER:
        path = _source(archive, key, username_key)
        section = reg.SECTIONS[key]
        info = archive.manifest.get("sections", {}).get(key)
        if path is None:
            if info and section.level == reg.PERSON and archive.manifest.get("level") == "installation":
                out.append({"key": key, "label": section.label, "level": section.level, "rows": 0, "allowed": False,
                            "reason": "Your account has no data of this kind in the file.", "modes": []})
            continue
        payload = archive.json(path)
        rows = sum(len(r) for r in payload["tables"].values())
        shared = section.level == reg.INSTALLATION
        allowed, reason = True, ""
        if shared and not is_admin:
            allowed, reason = False, "An administrator must load shared data."
        modes = [ADD] if (shared or kind == "share") else [ADD, REPLACE]
        if key == "profile":
            modes = [REPLACE]
        current = None
        if session is not None and uid is not None and not shared and key != "profile":
            current = sum(len(table_rows(session, tbl, uid)) for tbl in section.tables)
        out.append({"key": key, "label": section.label, "level": section.level, "rows": rows,
                    "tables": {n: len(r) for n, r in payload["tables"].items()}, "allowed": allowed, "reason": reason,
                    "modes": modes if allowed else [], "current": current})
    return out


@dataclass
class _Ctx:
    session: Session
    uid: int
    archive: Archive
    report: Report
    loaded: set[str]
    idmap: dict[str, dict[int, tuple[int | None, bool]]] = field(default_factory=dict)
    new_files: list[Path] = field(default_factory=list)
    old_files: list[Path] = field(default_factory=list)
    merge: bool = False


def _note(counter: dict[str, int], key: str, n: int = 1) -> None:
    counter[key] = counter.get(key, 0) + n


def _delete_rows(ctx: _Ctx, section: reg.Section) -> None:
    """Replace: delete this person's rows of the section (children first; ids are read before anything goes)."""
    ids: dict[str, list[int]] = {}
    for tbl in reg.ordered(section.tables):
        if tbl.where is None or "id" not in {c.name for c in reg.table(tbl.name).columns}:
            continue
        ids[tbl.name] = [r[0] for r in ctx.session.execute(text(f'SELECT id FROM "{tbl.name}" WHERE {tbl.where}'),
                                                           {"uid": ctx.uid})]
    for tbl in reversed(reg.ordered(section.tables)):
        found = ids.get(tbl.name, [])
        for start in range(0, len(found), _CHUNK):
            chunk = found[start:start + _CHUNK]
            marks = ", ".join(f":i{n}" for n in range(len(chunk)))
            params = {f"i{n}": v for n, v in enumerate(chunk)}
            if tbl.file:
                col, dirkey = tbl.file
                for (name,) in ctx.session.execute(text(f'SELECT "{col}" FROM "{tbl.name}" WHERE id IN ({marks})'), params):
                    if name:
                        ctx.old_files.append(reg.file_dir(dirkey) / name)
            ctx.session.execute(text(f'DELETE FROM "{tbl.name}" WHERE id IN ({marks})'), params)
        if found:
            _note(ctx.report.removed, section.key, len(found))


def _find(ctx: _Ctx, target: str, keys: tuple[str, ...], values: list) -> int | None:
    if any(v is None for v in values) and len(values) == 1:
        return None
    clause = " AND ".join(f'"{k}" IS :k{n}' if values[n] is None else f'"{k}" = :k{n}' for n, k in enumerate(keys))
    row = ctx.session.execute(text(f'SELECT id FROM "{target}" WHERE {clause}'),
                              {f"k{n}": v for n, v in enumerate(values)}).first()
    return row[0] if row else None


def _resolve_external(ctx: _Ctx, tbl: str, col: str, row: dict) -> int | None:
    ref = reg.REFS.get((tbl, col))
    keys = row.get("_refs", {}).get(col)
    if ref is None or keys is None:
        return None
    target, key_cols = ref
    found = _find(ctx, target, key_cols, keys)
    if found is None and target == "peptides" and keys and keys[0]:
        from app.routers.protocols import _find_or_create_peptide    # a protocol or price list names a peptide we lack
        found = _find_or_create_peptide(ctx.session, keys[0]).id
    return found


def _copy_file(ctx: _Ctx, dirkey: str, name: str) -> str | None:
    entry = f"files/{dirkey}/{name}"
    if not ctx.archive.has(entry):
        return None
    directory = reg.file_dir(dirkey)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower()
    new_name = uuid.uuid4().hex + (suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else "")
    path = directory / new_name
    path.write_bytes(ctx.archive.read(entry))
    ctx.new_files.append(path)
    ctx.report.files += 1
    return new_name


def _insert_row(ctx: _Ctx, section: reg.Section, tbl: reg.Tbl, row: dict) -> None:
    table = reg.table(tbl.name)
    has_id = "id" in table.columns
    values = {c: row[c] for c in _columns(tbl) if c in row and c != "id"}
    for fk in table.foreign_keys:
        col, target = fk.parent.name, fk.column.table.name
        value = values.get(col)
        if value is None:
            continue
        if target == "users":
            values[col] = ctx.uid
        elif target in ctx.loaded:
            mapped = ctx.idmap.get(target, {}).get(value)
            own_parent = target in {t.name for t in section.tables}
            if mapped is None:                      # the parent row is not in the file: leave the link empty
                values[col] = None
                _note(ctx.report.unresolved, f"{tbl.name}.{col}")
            elif own_parent and (mapped[0] is None or (not mapped[1] and target not in reg.REFERENCE_TABLES)):
                _note(ctx.report.skipped, section.key)    # its parent in this section was skipped, so this row is too
                if has_id and row.get("id") is not None:
                    ctx.idmap.setdefault(tbl.name, {})[row["id"]] = (None, False)
                return
            elif mapped[0] is None:                 # a row in another section was skipped: nothing to link to
                values[col] = None
                _note(ctx.report.unresolved, f"{tbl.name}.{col}")
            else:                                   # inserted here, or an existing row it was merged into
                values[col] = mapped[0]
        else:
            resolved = _resolve_external(ctx, tbl.name, col, row)
            if resolved is None:
                _note(ctx.report.unresolved, f"{tbl.name}.{col}")
            values[col] = resolved
    if tbl.file and values.get(tbl.file[0]):
        values[tbl.file[0]] = _copy_file(ctx, tbl.file[1], values[tbl.file[0]])
        if values[tbl.file[0]] is None:
            _note(ctx.report.unresolved, f"{tbl.file[1]} file")

    merge_keys = reg.MERGE_KEYS.get(tbl.name) if ctx.merge else None
    if merge_keys:
        existing = _find(ctx, tbl.name, merge_keys, [values.get(k) for k in merge_keys])
        if existing is not None:
            if has_id and row.get("id") is not None:
                ctx.idmap.setdefault(tbl.name, {})[row["id"]] = (existing, False)
            if not tbl.reference:
                _note(ctx.report.skipped, section.key)
            return
    cols = list(values)
    sql = f'INSERT OR IGNORE INTO "{tbl.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join(":" + c for c in cols)})'
    result = ctx.session.execute(text(sql), values)
    if result.rowcount == 0:
        if has_id and row.get("id") is not None:
            ctx.idmap.setdefault(tbl.name, {})[row["id"]] = (None, False)
        _note(ctx.report.skipped, section.key)
        return
    if has_id and row.get("id") is not None:
        new_id = ctx.session.execute(text("SELECT last_insert_rowid()")).scalar()
        ctx.idmap.setdefault(tbl.name, {})[row["id"]] = (new_id, True)
    _note(ctx.report.added, section.key)


def _apply_profile(ctx: _Ctx, payload: dict) -> None:
    rows = payload["tables"].get("users", [])
    if not rows:
        return
    allowed = {c.name for c in reg.table("users").columns} & set(reg.PROFILE_COLUMNS)
    values = {c: v for c, v in rows[0].items() if c in allowed}
    if values:
        sets = ", ".join(f'"{c}" = :{c}' for c in values)
        ctx.session.execute(text(f'UPDATE users SET {sets} WHERE id = :uid'), {**values, "uid": ctx.uid})
        _note(ctx.report.added, "profile")


def load(session: Session, archive: Archive, *, uid: int, username_key: str, is_admin: bool,
         plan: dict[str, str]) -> Report:
    """Load the sections in `plan` ({section key: "add" | "replace"}). Raises BackupError and changes nothing on any
    problem; on success the transaction is committed and replaced files are removed."""
    if not plan:
        raise BackupError("Choose at least one section to load.")
    check_revision(session, archive.manifest)
    available = {d["key"]: d for d in describe(archive, username_key=username_key, is_admin=is_admin)}
    for key, mode in plan.items():
        item = available.get(key)
        if item is None:
            raise BackupError("That section is not in this backup.")
        if not item["allowed"]:
            raise BackupError(f'{item["label"]}: {item["reason"]}')
        if mode not in item["modes"]:
            raise BackupError(f'{item["label"]} can only be loaded as: {", ".join(item["modes"])}.')
    ctx = _Ctx(session, uid, archive, Report(), {t.name for k in plan for t in reg.SECTIONS[k].tables})
    try:
        for key in reg.LOAD_ORDER:
            if key not in plan:
                continue
            section = reg.SECTIONS[key]
            payload = archive.json(_source(archive, key, username_key))
            ctx.merge = section.level == reg.INSTALLATION
            if key == "profile":
                _apply_profile(ctx, payload)
                continue
            if plan[key] == REPLACE:
                _delete_rows(ctx, section)
            for tbl in reg.ordered(section.tables):
                for row in payload["tables"].get(tbl.name, ()):
                    _insert_row(ctx, section, tbl, row)
            if section.library_files:
                _load_library_files(ctx)
        session.commit()
    except BaseException as exc:
        session.rollback()
        for path in ctx.new_files:
            path.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Loading failed and nothing was changed ({type(exc).__name__}).") from exc
    for path in ctx.old_files:
        path.unlink(missing_ok=True)
    return ctx.report


def _load_library_files(ctx: _Ctx) -> None:
    """Card images and cards.json: files are added without overwriting any that already exist."""
    from app import config
    for name in ctx.archive.names():
        if not name.startswith("files/library/"):
            continue
        rel = name[len("files/library/"):]
        target = config.CARDS_JSON if rel == "cards.json" else config.CARDS_DIR / rel.split("/", 1)[1]
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ctx.archive.read(name))
        ctx.new_files.append(target)
        ctx.report.files += 1
```

- [ ] **Step 4: Run the tests and the whole suite**

Expected: 18 passed in the file; full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/backup/load.py tests/test_backup_load.py
git commit -m "feat: load backup sections with Add or Replace, remapped links and natural-key merging

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Restore everything

**Files:**
- Create: `app/backup/restore.py`, `tests/test_backup_restore.py`

**Interfaces:**
- Consumes: Tasks 1-3 (`build_archive`, `seal`, `check_revision`, registry).
- Produces: `restore.restore_installation(session, archive, *, uid, creator, safety_passphrase) -> RestoreReport(rows, files, safety_copy)`, `restore.write_safety_backup(session, *, uid, creator, passphrase) -> Path`, `restore.backups_dir()`, `restore.SAFETY_KEEP = 3`.

Order of work: refuse anything but a whole-installation backup of an acceptable revision; write the safety backup (failure to write it refuses the restore); stage the files; one transaction deletes every row and inserts the backup's rows with original ids, then `PRAGMA foreign_key_check`; commit; only then swap the file directories. The tests run against a private scratch database and private directories so restoring never touches the shared test database.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_restore.py`:

```python
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.backup import restore
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError, unseal
from app.backup.export import build_archive
from app.db import Base, SessionLocal, make_engine
from app.models import User
from backup_helpers import seed_world

PASS = "safety passphrase"
FILE_DIRS = ("COA_DIR", "PRICE_LIST_DIR", "LAB_REPORT_DIR", "WORKOUT_PDF_DIR", "WALLET_QR_DIR")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A private database and private file directories, so restoring never touches the shared test database."""
    for name in FILE_DIRS:
        monkeypatch.setattr(config, name, tmp_path / "uploads" / name.lower())
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "library" / "cards")
    monkeypatch.setattr(config, "CARDS_JSON", tmp_path / "library" / "cards.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    engine = make_engine(f"sqlite:///{(tmp_path / 'scratch.db').as_posix()}")
    Base.metadata.create_all(engine)
    with SessionLocal() as shared:
        head = shared.execute(text("SELECT version_num FROM alembic_version")).scalar()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES (?)", (head,))
    with Session(engine) as s:
        admin = User(username="Admin", username_key="admin", password_hash="hash-admin", is_admin=True)
        other = User(username="Other", username_key="other", password_hash="hash-other", totp_secret="SECRET")
        s.add_all([admin, other])
        s.commit()
        seed_world(s, admin.id, "A")
        seed_world(s, other.id, "B")
        s.execute(text("INSERT INTO sessions (id, user_id, twofa_pending, created_at, last_seen) "
                       "VALUES ('tok', :u, 0, '2026-01-01', '2026-01-01')"), {"u": admin.id})
        s.commit()
        yield s, admin.id, tmp_path
    engine.dispose()


def snapshot(s):
    """Every row of every table (sorted), and the attached files, as plain data to compare."""
    tables = {}
    for (name,) in s.execute(text("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN "
                                  "('sessions', 'alembic_version', 'sqlite_sequence') ORDER BY name")):
        rows = [dict(r) for r in s.execute(text(f'SELECT * FROM "{name}"')).mappings()]
        tables[name] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    files = {}
    for key in FILE_DIRS:
        directory = getattr(config, key)
        files[key] = {p.name: p.read_bytes() for p in directory.glob("*")} if directory.is_dir() else {}
    return tables, files


def whole(s, uid):
    return read_archive(build_archive(s, kind="backup", uid=uid, creator="Admin", keys=[], installation=True),
                        max_bytes=50_000_000)


def test_restoring_everything_rebuilds_every_table_with_its_ids_and_its_files(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    expected = snapshot(s)
    s.execute(text("DELETE FROM inventory_items WHERE owner_id = :u"), {"u": admin})
    s.execute(text("DELETE FROM journal_entries"))
    s.execute(text("UPDATE users SET password_hash = 'tampered'"))
    s.execute(text("INSERT INTO vendors (name, created_at) VALUES ('Junk Vendor', '2026-01-01')"))
    s.commit()
    (config.COA_DIR / "junk.pdf").write_bytes(b"junk")
    for f in config.WALLET_QR_DIR.glob("*"):
        f.unlink()
    report = restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == expected                                    # same rows, same ids, same files, nothing extra
    assert not (config.COA_DIR / "junk.pdf").exists() and report.files == 10
    assert report.rows["users"] == 2 and report.rows["inventory_items"] == 2


def test_restoring_signs_everyone_out(scratch):
    s, admin, _ = scratch
    assert s.execute(text("SELECT COUNT(*) FROM sessions")).scalar() == 1
    s.commit()
    restore.restore_installation(s, whole(s, admin), uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM sessions")).scalar() == 0


def test_a_safety_backup_of_what_was_replaced_is_written_and_readable_and_only_three_are_kept(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    for _ in range(4):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    copies = sorted((tmp / "backups").glob("amide-safety-*.amidebackup"))
    assert len(copies) <= 3 and copies
    inner = read_archive(unseal(copies[-1].read_bytes(), PASS), max_bytes=50_000_000)
    assert inner.manifest["level"] == "installation" and inner.has("sections/accounts.json")
    with pytest.raises(BackupError, match="Wrong passphrase"):
        unseal(copies[-1].read_bytes(), "another passphrase")


def test_an_inconsistent_backup_changes_nothing(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("inventory.json"))
    payload = json.loads(entries[key])
    payload["tables"]["order_items"][0]["inventory_item_id"] = 424242        # a parent that is not in the file
    entries[key] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    broken = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    before = snapshot(s)
    with pytest.raises(BackupError, match="inconsistent"):
        restore.restore_installation(s, broken, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before
    assert not list((tmp / "backups").glob(".restore-*"))                      # staging removed


def test_only_a_whole_installation_backup_can_restore_everything(scratch):
    s, admin, _ = scratch
    person = read_archive(build_archive(s, kind="backup", uid=admin, creator="Admin", keys=["journal"]), max_bytes=50_000_000)
    with pytest.raises(BackupError, match="whole-installation"):
        restore.restore_installation(s, person, uid=admin, creator="Admin", safety_passphrase=PASS)


def test_a_newer_backup_and_a_short_safety_passphrase_are_refused_before_anything_happens(scratch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "9999"}
    newer = read_archive(write_archive(manifest, entries), max_bytes=50_000_000)
    before = snapshot(s)
    with pytest.raises(BackupError, match="newer version"):
        restore.restore_installation(s, newer, uid=admin, creator="Admin", safety_passphrase=PASS)
    with pytest.raises(BackupError, match="at least 8"):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase="short")
    assert snapshot(s) == before and not (tmp / "backups").exists()


def test_an_older_backup_restores_with_defaults_for_columns_it_did_not_have(scratch):
    s, admin, _ = scratch
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("workouts.json"))
    payload = json.loads(entries[key])
    for row in payload["tables"]["workout_exercise_logs"]:
        row.pop("net_kcal")
    entries[key] = json.dumps(payload).encode()
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")} | {"revision": "0001"}
    restore.restore_installation(s, read_archive(write_archive(manifest, entries), max_bytes=50_000_000),
                                 uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM workout_exercise_logs WHERE net_kcal IS NULL")).scalar() >= 1


def test_if_the_safety_backup_cannot_be_written_nothing_is_changed(scratch, monkeypatch):
    s, admin, tmp = scratch
    archive = whole(s, admin)
    before = snapshot(s)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(restore, "write_safety_backup", broken)
    with pytest.raises(BackupError, match="safety backup could not be written"):
        restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert snapshot(s) == before
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`No module named 'app.backup.restore'`).

- [ ] **Step 3: Create the restore**

Create `app/backup/restore.py`:

```python
"""Restore everything: replace the whole installation with the contents of a whole-installation backup.

The order matters. A safety backup of the current installation is written first. The attached files are staged in a
temporary directory next to the real ones. Then one database transaction deletes every row and inserts the backup's
rows with their original primary keys; any error, or any foreign key left dangling, rolls it back and nothing has
changed. Only after the commit are the file directories swapped in (the old ones are moved aside, then removed).
Sessions are not part of a backup, so everyone, including the person restoring, is signed out afterwards."""

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import sort_tables

from app import config
from app.backup import sections as reg
from app.backup.archive import Archive
from app.backup.container import BackupError, check_passphrase, seal
from app.backup.export import build_archive
from app.backup.load import check_revision

SAFETY_KEEP = 3


@dataclass
class RestoreReport:
    rows: dict[str, int] = field(default_factory=dict)
    files: int = 0
    safety_copy: str = ""


def backups_dir() -> Path:
    return config.DATA_DIR / "backups"


def write_safety_backup(session: Session, *, uid: int, creator: str, passphrase: str) -> Path:
    """An encrypted whole-installation backup of what is about to be replaced; the newest three are kept."""
    check_passphrase(passphrase)
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    data = build_archive(session, kind="backup", uid=uid, creator=creator, keys=[], installation=True)
    path = folder / f"amide-safety-{datetime.now(timezone.utc):%Y-%m-%d-%H%M%S}.amidebackup"
    path.write_bytes(seal(data, passphrase))
    for old in sorted(folder.glob("amide-safety-*.amidebackup"))[:-SAFETY_KEEP]:
        old.unlink(missing_ok=True)
    return path


def _all_tables() -> list:
    """Every restorable table, parents before children."""
    names = {t.name for s in reg.SECTIONS.values() for t in s.tables}
    return sort_tables([reg.table(n) for n in names])


def _rows_by_table(archive: Archive) -> dict[str, list[dict]]:
    """Every row in the file by table: the shared sections once, each person's sections together."""
    out: dict[str, list[dict]] = {}
    for name in archive.names():
        if not name.endswith(".json") or not (name.startswith("sections/") or name.startswith("persons/")):
            continue
        payload = json.loads(archive.read(name).decode("utf-8"))
        for table, rows in payload.get("tables", {}).items():
            out.setdefault(table, []).extend(rows)
    return out


def _stage_files(archive: Archive) -> tuple[Path, dict[str, int]]:
    """Write every attached file under a staging directory, returning it and the count per directory key."""
    stage = backups_dir() / f".restore-{uuid.uuid4().hex}"
    counts: dict[str, int] = {}
    for name in archive.names():
        if not name.startswith("files/"):
            continue
        rel = name[len("files/"):]
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(name))
        counts[rel.split("/", 1)[0]] = counts.get(rel.split("/", 1)[0], 0) + 1
    stage.mkdir(parents=True, exist_ok=True)
    return stage, counts


def _swap(stage: Path) -> int:
    """Move the staged directories in place of the live ones; the old ones are removed once the new are in."""
    moved = 0
    pairs = [(stage / key, reg.file_dir(key)) for key in reg.FILE_DIRS]
    pairs += [(stage / "library" / "cards", config.CARDS_DIR)]
    for new, live in pairs:
        if not new.is_dir():
            new.mkdir(parents=True, exist_ok=True)
        aside = live.with_name(live.name + ".restore-old")
        if live.exists():
            shutil.rmtree(aside, ignore_errors=True)
            live.rename(aside)
        live.parent.mkdir(parents=True, exist_ok=True)
        new.rename(live)
        shutil.rmtree(aside, ignore_errors=True)
        moved += 1
    cards = stage / "library" / "cards.json"
    if cards.is_file():
        config.CARDS_JSON.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cards, config.CARDS_JSON)
    return moved


def restore_installation(session: Session, archive: Archive, *, uid: int, creator: str,
                         safety_passphrase: str) -> RestoreReport:
    """Replace everything with the backup. Raises BackupError and leaves the installation as it was on any failure."""
    if archive.manifest.get("level") != "installation" or archive.manifest.get("kind") != "backup":
        raise BackupError("Restore everything needs a whole-installation backup made by an administrator.")
    check_revision(session, archive.manifest)
    report = RestoreReport()
    try:
        report.safety_copy = write_safety_backup(session, uid=uid, creator=creator, passphrase=safety_passphrase).name
    except OSError as exc:
        raise BackupError("The safety backup could not be written, so nothing was changed.") from exc
    stage = None
    try:
        stage, counts = _stage_files(archive)
        report.files = sum(counts.values())
        rows = _rows_by_table(archive)
        tables = _all_tables()
        for table in reversed(tables):
            session.execute(text(f'DELETE FROM "{table.name}"'))
        for table in tables:
            names = {c.name for c in table.columns}
            for row in rows.get(table.name, ()):
                values = {k: v for k, v in row.items() if k in names}
                if not values:
                    continue
                cols = list(values)
                session.execute(text(f'INSERT OR IGNORE INTO "{table.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                                     f'VALUES ({", ".join(":" + c for c in cols)})'), values)
            report.rows[table.name] = len(rows.get(table.name, ()))
        broken = session.execute(text("PRAGMA foreign_key_check")).all()
        if broken:
            raise BackupError("This backup is inconsistent (rows point at data that is not in it); nothing was changed.")
        session.commit()
    except BaseException as exc:
        session.rollback()
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, IntegrityError):
            raise BackupError("This backup is inconsistent (rows point at data that is not in it); nothing was changed.") from exc
        raise BackupError(f"Restore failed and nothing was changed ({type(exc).__name__}).") from exc
    try:
        _swap(stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return report
```

- [ ] **Step 4: Run the tests and the whole suite**

Expected: 8 passed in the file; full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/backup/restore.py tests/test_backup_restore.py
git commit -m "feat: restore everything from a whole-installation backup, all or nothing, with a safety copy

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Backup page and routes

**Files:**
- Create: `app/backup/pending.py`, `app/backup/service.py`, `app/templates/backup/preview.html`, `app/templates/backup/result.html`, `app/templates/backup/restored.html`, `app/static/js/backup.js`, `tests/test_backup_page.py`
- Replace: `app/routers/backup.py`, `app/templates/backup/backup.html`
- Modify: `app/templates/settings/settings.html`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: routes `GET /backup?tab=backup|export|restore`, `POST /backup/create` (fields `kind`, `scope`, repeated `section`, `passphrase`, `confirm`; returns the file as a download), `POST /backup/open` (file + `passphrase`; returns the preview with a `token`), `POST /backup/load` (`token`, repeated `load`, `mode-<key>`), `POST /backup/restore` (administrators only; `token`, `confirm`, `safety_passphrase`, `safety_confirm`); `pending.put/get/drop/purge`, `pending.TTL_SECONDS`; `service.make_download`, `service.open_upload`, `service.download_name`. The older `/backup/export.json`, `/backup/export/inventory.csv` and `/backup/import` stay (shown under "Older formats").

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_page.py` (note: the older `tests/test_backup.py` creates a user named `BackupOther`, so these tests use `vaultother`):

```python
import html
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import config
from app.backup import pending
from app.backup import restore as backup_restore
from app.backup.archive import read_archive
from app.backup.container import unseal
from app.main import app
from backup_helpers import clean_files, person_counts, seed_world, wipe_person

PASS = "correct horse battery"


@pytest.fixture
def world(client, db, me):
    wipe_person(db, me)
    w = seed_world(db, me)
    yield w
    wipe_person(db, me)
    clean_files()


@pytest.fixture
def other_client(db):
    """A second, non-administrator person in their own browser; removed afterwards."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "vaultother", "password": "Other1!", "confirm": "Other1!"})
    yield other
    db.execute(text("DELETE FROM users WHERE username_key = 'vaultother'"))
    db.commit()


def text_of(response):
    return html.unescape(response.text)


def create(client, **fields):
    data = {"kind": "backup", "scope": "person", "passphrase": PASS, "confirm": PASS, **fields}
    return client.post("/backup/create", data=data)


def archive_from(response):
    return read_archive(unseal(response.content, PASS), max_bytes=50_000_000)


def open_file(client, blob, passphrase=PASS):
    return client.post("/backup/open", files={"file": ("x.amidebackup", blob, "application/octet-stream")},
                       data={"passphrase": passphrase})


def token_of(response):
    return re.search(r'name="token" value="([^"]+)"', response.text).group(1)


def test_the_page_has_three_tabs_and_the_older_formats(client):
    for tab in ("backup", "export", "restore"):
        page = text_of(client.get("/backup", params={"tab": tab}))
        assert "Back up" in page and "Export / Share" in page and "Restore / Import" in page
        assert "/backup/export.json" in page and "/backup/export/inventory.csv" in page
    assert 'action="/backup/create"' in client.get("/backup?tab=backup").text
    assert 'action="/backup/open"' in client.get("/backup?tab=restore").text
    assert client.get("/backup?tab=nonsense").status_code == 200


def test_an_administrator_sees_the_whole_installation_option_and_shared_sections(client, other_client):
    admin = text_of(client.get("/backup?tab=backup"))
    assert "Whole installation" in admin and 'value="vendors"' in admin and 'value="library"' in admin
    person = text_of(other_client.get("/backup?tab=backup"))
    assert "Whole installation" not in person and 'value="library"' not in person and 'value="vendors"' not in person
    assert 'value="inventory"' in person and 'value="journal"' in person
    share = text_of(other_client.get("/backup?tab=export"))
    assert "Share non-medical data" in share and 'value="vendors"' not in share and 'value="inventory"' in share


def test_a_backup_downloads_as_a_new_dated_encrypted_file_of_the_chosen_sections(client, db, me, world):
    r = create(client, section=["inventory", "journal"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert re.fullmatch(r'attachment; filename="amide-backup-\d{4}-\d{2}-\d{2}-\d{6}\.amidebackup"',
                        r.headers["content-disposition"])
    assert r.headers["cache-control"] == "no-store" and b"Zorvex" not in r.content
    archive = archive_from(r)
    assert set(archive.manifest["sections"]) == {"inventory", "journal"} and archive.manifest["creator"] == "Tester"


def test_a_share_download_holds_only_shareable_sections(client, db, me, world):
    r = create(client, kind="share", scope="person", section=["inventory", "protocols", "workouts"])
    archive = archive_from(r)
    assert archive.manifest["kind"] == "share" and "dose_logs" not in archive.json("sections/protocols.json")["tables"]
    bad = create(client, kind="share", section=["journal"])
    assert bad.status_code == 422 and "cannot be put in a share file" in text_of(bad)


@pytest.mark.parametrize("fields,message", [
    ({"confirm": "different pass"}, "do not match"),
    ({"passphrase": "short", "confirm": "short"}, "at least 8"),
    ({"section": []}, "at least one"),
    ({"section": ["nonsense"]}, "Unknown section"),
    ({"kind": "mystery", "section": ["journal"]}, "Unknown kind"),
])
def test_bad_requests_show_a_message_and_download_nothing(client, fields, message):
    r = create(client, **{"section": ["journal"], **fields})
    assert r.status_code == 422 and message in text_of(r) and r.headers["content-type"].startswith("text/html")


def test_only_an_administrator_can_back_up_the_whole_installation_or_shared_sections(client, other_client):
    r = create(other_client, scope="installation", section=[])
    assert r.status_code == 422 and "Only an administrator" in text_of(r)
    r = create(other_client, section=["vendors"])
    assert r.status_code == 422 and "Only an administrator" in text_of(r)
    assert create(other_client, section=["journal"]).status_code == 200
    whole = create(client, scope="installation", section=[])
    assert archive_from(whole).manifest["level"] == "installation"


def test_opening_a_file_shows_what_is_inside_and_changes_nothing(client, db, me, world):
    blob = create(client, section=["inventory", "journal", "profile"]).content
    before = person_counts(db, me)
    page = open_file(client, blob)
    assert page.status_code == 200
    text = text_of(page)
    assert "What is in this file" in text and "Inventory" in text and "Journal" in text and "Made by" in text
    assert "Restore everything" not in text and 'name="token"' in text
    assert "Replace deletes your 5 current rows" in text and "Replace deletes your 3 current rows" in text
    assert person_counts(db, me) == before


def test_a_wrong_passphrase_a_damaged_file_and_a_foreign_file_are_refused(client, db, me, world):
    blob = create(client, section=["journal"]).content
    assert "Wrong passphrase" in text_of(open_file(client, blob, "not the passphrase"))
    assert open_file(client, blob, "not the passphrase").status_code == 422
    assert "damaged" in text_of(open_file(client, blob[:-5]))
    assert "not an Amide backup" in text_of(open_file(client, b"just some text that is not a backup file at all"))


def test_a_file_over_the_size_limit_is_refused(client, db, me, world, monkeypatch):
    blob = create(client, section=["journal"]).content
    monkeypatch.setattr(config, "MAX_BACKUP_BYTES", 100)
    assert "larger than the allowed size" in text_of(open_file(client, blob))


def test_loading_sections_through_the_page_reports_what_happened(client, db, me, world):
    blob = create(client, section=["inventory", "journal"]).content
    db.execute(text("UPDATE inventory_items SET count = 99 WHERE owner_id = :u"), {"u": me})
    db.commit()
    token = token_of(open_file(client, blob))
    r = client.post("/backup/load", data={"token": token, "load": ["inventory", "journal"],
                                          "mode-inventory": "replace", "mode-journal": "add"})
    page = text_of(r)
    assert r.status_code == 200 and "Loaded" in page and "Inventory" in page and "Journal" in page
    assert db.execute(text("SELECT count FROM inventory_items WHERE owner_id = :u"), {"u": me}).scalar() == 5
    again = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert again.status_code == 422 and "expired" in text_of(again)             # a step can be used once


def test_a_bad_choice_keeps_the_step_open_with_a_message(client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "merge"})
    assert r.status_code == 422 and "can only be loaded as" in text_of(r) and token in r.text
    none = client.post("/backup/load", data={"token": token})
    assert none.status_code == 422 and "at least one section" in text_of(none)


def test_a_step_belongs_to_the_account_that_opened_the_file(client, other_client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = other_client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert r.status_code == 422 and "expired" in text_of(r)
    assert client.post("/backup/load", data={"token": "../../etc/passwd", "load": ["journal"]}).status_code == 422


def test_a_step_expires(client, db, me, world, monkeypatch):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    monkeypatch.setattr(pending, "TTL_SECONDS", -1)
    r = client.post("/backup/load", data={"token": token, "load": ["journal"], "mode-journal": "add"})
    assert r.status_code == 422 and "expired" in text_of(r)


def test_a_person_can_only_load_their_own_sections_of_a_whole_installation_file(client, other_client, db, me, world):
    whole = create(client, scope="installation", section=[]).content
    page = text_of(open_file(other_client, whole))
    assert "Restore everything" not in page
    token = token_of(open_file(other_client, whole))
    r = other_client.post("/backup/load", data={"token": token, "load": ["vendors"], "mode-vendors": "add"})
    assert r.status_code == 422 and "administrator" in text_of(r).lower()


def test_restore_everything_is_for_administrators_and_needs_the_typed_word(client, other_client, db, me, world):
    whole = create(client, scope="installation", section=[]).content
    page = open_file(client, whole)
    assert "Restore everything" in text_of(page) and "RESTORE" in page.text
    token = token_of(page)
    assert other_client.post("/backup/restore", data={"token": token}).status_code == 404
    base = {"token": token, "safety_passphrase": PASS, "safety_confirm": PASS}
    assert "Type RESTORE" in text_of(client.post("/backup/restore", data={**base, "confirm": "restore it"}))
    assert "do not match" in text_of(client.post("/backup/restore", data={**base, "confirm": "RESTORE", "safety_confirm": "x" * 9}))
    short = client.post("/backup/restore", data={**base, "confirm": "RESTORE", "safety_passphrase": "short", "safety_confirm": "short"})
    assert short.status_code == 422 and "at least 8" in text_of(short)


def test_restore_everything_runs_the_restore_and_signs_the_person_out(client, db, me, world, monkeypatch):
    blob = create(client, scope="installation", section=[]).content
    own = TestClient(app, follow_redirects=False)       # its own browser: the sign-out below must not end the shared one
    own.post("/notice", data={"understand": "1"})
    assert own.post("/login", data={"username": "Tester", "password": "Test1!"}).status_code in (200, 303)
    token = token_of(open_file(own, blob))
    seen = {}

    def fake(session, archive, *, uid, creator, safety_passphrase):
        seen.update(uid=uid, creator=creator, passphrase=safety_passphrase)
        return backup_restore.RestoreReport(rows={"users": 1, "vendors": 2}, files=3, safety_copy="amide-safety-test.amidebackup")

    monkeypatch.setattr(backup_restore, "restore_installation", fake)
    r = own.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS,
                                           "safety_confirm": PASS})
    assert r.status_code == 200 and "Everything was restored" in text_of(r) and "amide-safety-test" in r.text
    assert seen == {"uid": me, "creator": "Tester", "passphrase": PASS}
    assert 'amide_session=""' in r.headers["set-cookie"] or "amide_session=;" in r.headers["set-cookie"]
    again = own.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS,
                                               "safety_confirm": PASS})
    assert "expired" in text_of(again) or again.status_code in (302, 303, 401)   # the sign-out already took effect


def test_a_person_level_file_cannot_restore_everything(client, db, me, world):
    token = token_of(open_file(client, create(client, section=["journal"]).content))
    r = client.post("/backup/restore", data={"token": token, "confirm": "RESTORE", "safety_passphrase": PASS, "safety_confirm": PASS})
    assert r.status_code == 422 and "whole-installation" in text_of(r)


def test_the_settings_page_links_to_the_new_page(client):
    page = text_of(client.get("/settings"))
    assert 'href="/backup"' in page and "Back up, export, share and restore" in page


def test_a_backup_over_the_size_limit_is_refused_with_a_message(client, db, me, world, monkeypatch):
    monkeypatch.setattr(config, "MAX_BACKUP_BYTES", 100)
    r = create(client, section=["inventory"])
    assert r.status_code == 422 and "larger than the allowed size" in text_of(r)
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (404 on `/backup/create`, missing templates).

- [ ] **Step 3: Create the pending store and the service**

Create `app/backup/pending.py`:

```python
"""Opened backups waiting for the person's next step.

After a file is decrypted and checked, the preview page needs the contents again to load sections. Rather than ask for
the file and passphrase twice, the verified archive is kept in a private temporary file for ten minutes under a random
token tied to the account that opened it. Nothing is kept in the browser. Expired and used files are deleted."""

import json
import re
import secrets
import time

from app import config
from app.backup.container import BackupError

TTL_SECONDS = 600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")


def _folder():
    folder = config.DATA_DIR / "backups" / ".pending"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def purge() -> None:
    """Delete every pending file older than the time to live."""
    cutoff = time.time() - TTL_SECONDS
    for path in _folder().glob("*"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def put(uid: int, zipped: bytes) -> str:
    purge()
    token = secrets.token_urlsafe(24)
    (_folder() / f"{token}.zip").write_bytes(zipped)
    (_folder() / f"{token}.json").write_text(json.dumps({"uid": uid}), encoding="utf-8")
    return token


def get(uid: int, token: str) -> bytes:
    """The archive bytes for this token if it is the caller's and still fresh, else BackupError."""
    purge()
    if not _TOKEN.fullmatch(token or ""):
        raise BackupError("That step has expired. Open the backup file again.")
    meta, data = _folder() / f"{token}.json", _folder() / f"{token}.zip"
    try:
        owner = json.loads(meta.read_text(encoding="utf-8"))["uid"]
        if owner != uid:
            raise BackupError("That step has expired. Open the backup file again.")
        return data.read_bytes()
    except (OSError, ValueError, KeyError):
        raise BackupError("That step has expired. Open the backup file again.") from None


def drop(token: str) -> None:
    if _TOKEN.fullmatch(token or ""):
        (_folder() / f"{token}.zip").unlink(missing_ok=True)
        (_folder() / f"{token}.json").unlink(missing_ok=True)
```

Create `app/backup/service.py`:

```python
"""What the Backup page does, with the checks the page relies on: making a sealed download and opening an upload."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import config
from app.backup import sections as reg
from app.backup.archive import Archive, read_archive
from app.backup.container import BackupError, check_passphrase, seal, unseal
from app.backup.export import build_archive
from app.models import User


def download_name(kind: str) -> str:
    """A new dated name every time, so one file never overwrites another."""
    return f"amide-{kind}-{datetime.now(timezone.utc):%Y-%m-%d-%H%M%S}.amidebackup"


def make_download(session: Session, user: User, *, kind: str, keys: list[str], installation: bool,
                  passphrase: str, confirm: str) -> tuple[str, bytes]:
    """(file name, sealed bytes) for a backup, export or share file. Raises BackupError with a message to show."""
    if passphrase != confirm:
        raise BackupError("The two passphrases do not match.")
    check_passphrase(passphrase)
    if installation and not user.is_admin:
        raise BackupError("Only an administrator can back up the whole installation.")
    if not installation:
        shared = [k for k in keys if k in reg.SHARED_SECTIONS]
        if shared and not user.is_admin:
            raise BackupError("Only an administrator can include shared data (vendors, price lists, library).")
    data = build_archive(session, kind=kind, uid=user.id, creator=user.username, keys=keys, installation=installation)
    if len(data) > config.MAX_BACKUP_BYTES:
        raise BackupError("This backup is larger than the allowed size. Choose fewer sections.")
    return download_name(kind), seal(data, passphrase)


def open_upload(blob: bytes, passphrase: str) -> tuple[bytes, Archive]:
    """Decrypt and verify an uploaded file: (the verified zip bytes, the opened archive)."""
    zipped = unseal(blob, passphrase)
    return zipped, read_archive(zipped, max_bytes=config.MAX_BACKUP_BYTES)
```

- [ ] **Step 4: Replace the router**

Replace the whole of `app/routers/backup.py` with the following (it keeps the older JSON/CSV endpoints and adds the new routes):

```python
"""Backup and restore. The page offers encrypted backups, exports and share files of chosen sections, and loading or
restoring them (see app/backup/ and docs/superpowers/specs/2026-10-06-backup-restore-design.md). The older plain JSON
export/import of inventory and protocols and the inventory CSV remain under "Older formats": that import is additive
only and never updates or deletes anything.
"""

import csv
import io
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import config
from app.auth import sessions as login_sessions
from app.auth.deps import current_user_id
from app.backup import load as backup_load
from app.backup import pending
from app.backup import restore as backup_restore
from app.backup import sections as backup_sections
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.service import make_download, open_upload
from app.db import get_session
from app.goals import GOALS_BY_SLUG
from app.models import (
    Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Protocol, ProtocolGoal, ProtocolItem,
    ProtocolItemCycleOff, PurchasingUnit, Route, Sale, StorageLocation, TimeOfDay, TitrationStep, User,
)
from app.routers.protocols import _find_or_create_peptide
from app.templating import templates

router = APIRouter()


# ---------------------------------------------------------------- export

def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _inventory_row(i: InventoryItem) -> dict:
    return {
        "name": i.name, "category": i.category.value, "count": i.count, "vial_size_mg": i.vial_size_mg,
        "vial_size_unit": i.vial_size_unit.value, "purchasing_unit": i.purchasing_unit.value,
        "medium": i.medium.value if i.medium else None,
        "volume_ml": i.volume_ml, "units_per_package": i.units_per_package,
        "storage": i.storage.value if i.storage else None,
        "cost": i.cost, "vendor": i.vendor, "notes": i.notes,
        "reconstituted_count": i.reconstituted_count, "sold_count": i.sold_count,
        "orders": [_order_row(li) for li in i.order_items],
        "sales": [_sale_row(s) for s in i.sales],
    }


def _order_row(li) -> dict:
    return {
        "quantity": li.quantity, "received_quantity": li.received_quantity,
        "order_date": _iso(li.order.order_date), "shipped_date": _iso(li.order.shipped_date),
        "arrival_date": _iso(li.order.arrival_date), "tracking_site": li.order.tracking_site,
        "tracking_number": li.order.tracking_number, "vendor": li.order.vendor,
        "lot_number": li.lot_number, "cost": li.cost,
        "tax": (li.allocated_tax_cents / 100) if li.allocated_tax_cents else None,
        "shipping": (li.allocated_shipping_cents / 100) if li.allocated_shipping_cents else None,
        "expiration_date": _iso(li.expiration_date),
        "coa_vial_size_mg": li.coa_vial_size_mg, "coa_purity_pct": li.coa_purity_pct,
    }


def _sale_row(s: Sale) -> dict:
    return {"quantity": s.quantity, "sale_date": _iso(s.sale_date), "price": s.price}


def _protocol_row(p: Protocol) -> dict:
    return {
        "name": p.name, "start_date": _iso(p.start_date), "end_date": _iso(p.end_date), "notes": p.notes,
        "titration_enabled": p.titration_enabled, "goals": p.goal_slugs,
        "items": [
            {
                "peptide": it.peptide.name, "dose": it.dose, "dose_unit": it.dose_unit.value,
                "frequency": it.frequency.value, "every_n_days": it.every_n_days, "weekdays": it.weekdays,
                "time_of_day": it.time_of_day.value, "route": it.route.value, "notes": it.notes,
                "steps": [{"start_week": s.start_week, "end_week": s.end_week, "dose": s.dose} for s in it.steps],
                "cycle_offs": [{"start_week": c.start_week, "end_week": c.end_week} for c in it.cycle_offs],
            }
            for it in p.items
        ],
    }


TABS = ("backup", "export", "restore")
RESTORE_WORD = "RESTORE"


def _context(session: Session, uid: int, tab: str, **extra) -> dict:
    me = session.get(User, uid)
    admin = bool(me.is_admin)
    sec = backup_sections.SECTIONS
    return {
        "tab": tab if tab in TABS else "backup", "is_admin": admin,
        "person_sections": [sec[k] for k in backup_sections.PERSON_SECTIONS],
        "shared_sections": [sec[k] for k in backup_sections.SHARED_SECTIONS] if admin else [],
        "shareable_sections": [sec[k] for k in backup_sections.SHAREABLE if admin or sec[k].level == backup_sections.PERSON],
        "max_mb": config.MAX_BACKUP_BYTES // (1024 * 1024), **extra,
    }


def _page(request: Request, session: Session, uid: int, tab: str, status_code: int = 200, **extra):
    return templates.TemplateResponse(request, "backup/backup.html", _context(session, uid, tab, **extra),
                                      status_code=status_code)


@router.get("/backup")
def backup_page(request: Request, tab: str = "backup", session: Session = Depends(get_session),
                uid: int = Depends(current_user_id)):
    return _page(request, session, uid, tab)


@router.post("/backup/create")
async def create_backup(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Build an encrypted backup, export or share file and send it as a download."""
    form = await request.form()
    kind = str(form.get("kind") or "backup")
    me = session.get(User, uid)
    try:
        name, blob = make_download(
            session, me, kind=kind, keys=[str(k) for k in form.getlist("section")],
            installation=str(form.get("scope") or "person") == "installation",
            passphrase=str(form.get("passphrase") or ""), confirm=str(form.get("confirm") or ""))
    except BackupError as exc:
        return _page(request, session, uid, "backup" if kind == "backup" else "export", 422, error=str(exc), error_kind=kind)
    return Response(blob, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _preview(request: Request, session: Session, uid: int, token: str, archive, status_code: int = 200, **extra):
    me = session.get(User, uid)
    manifest = archive.manifest
    return templates.TemplateResponse(request, "backup/preview.html", {
        "token": token, "manifest": manifest, "is_admin": bool(me.is_admin),
        "sections": backup_load.describe(archive, username_key=me.username_key, is_admin=bool(me.is_admin),
                                         session=session, uid=uid),
        "can_restore": bool(me.is_admin) and manifest.get("level") == "installation" and manifest.get("kind") == "backup",
        "restore_word": RESTORE_WORD, **extra}, status_code=status_code)


@router.post("/backup/open")
async def open_backup(request: Request, file: UploadFile, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    """Decrypt and verify an uploaded backup and show what is in it. Nothing is changed yet."""
    form = await request.form()
    blob = await file.read(config.MAX_BACKUP_BYTES + 1)
    try:
        if len(blob) > config.MAX_BACKUP_BYTES:
            raise BackupError("This file is larger than the allowed size.")
        zipped, archive = open_upload(blob, str(form.get("passphrase") or ""))
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    token = pending.put(uid, zipped)
    return _preview(request, session, uid, token, archive)


def _opened(uid: int, token: str):
    return read_archive(pending.get(uid, token), max_bytes=config.MAX_BACKUP_BYTES)


@router.post("/backup/load")
async def load_backup(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Load the ticked sections of an opened backup, each as Add or Replace."""
    form = await request.form()
    token = str(form.get("token") or "")
    me = session.get(User, uid)
    try:
        archive = _opened(uid, token)
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    plan = {str(k): str(form.get(f"mode-{k}") or "") for k in form.getlist("load")}
    try:
        report = backup_load.load(session, archive, uid=uid, username_key=me.username_key, is_admin=bool(me.is_admin),
                                  plan=plan)
    except BackupError as exc:
        return _preview(request, session, uid, token, archive, 422, error=str(exc))
    pending.drop(token)
    return templates.TemplateResponse(request, "backup/result.html", {
        "report": report, "labels": {k: backup_sections.SECTIONS[k].label for k in backup_sections.SECTIONS}})


@router.post("/backup/restore")
async def restore_everything(request: Request, session: Session = Depends(get_session),
                             uid: int = Depends(current_user_id)):
    """Replace the whole installation with an opened whole-installation backup (administrators only)."""
    me = session.get(User, uid)
    if not me.is_admin:
        raise HTTPException(404)
    form = await request.form()
    token = str(form.get("token") or "")
    try:
        archive = _opened(uid, token)
    except BackupError as exc:
        return _page(request, session, uid, "restore", 422, error=str(exc))
    try:
        if str(form.get("confirm") or "").strip() != RESTORE_WORD:
            raise BackupError(f"Type {RESTORE_WORD} to confirm.")
        if str(form.get("safety_passphrase") or "") != str(form.get("safety_confirm") or ""):
            raise BackupError("The two safety-copy passphrases do not match.")
        report = backup_restore.restore_installation(
            session, archive, uid=uid, creator=me.username, safety_passphrase=str(form.get("safety_passphrase") or ""))
    except BackupError as exc:
        return _preview(request, session, uid, token, archive, 422, error=str(exc))
    pending.drop(token)
    response = templates.TemplateResponse(request, "backup/restored.html", {"report": report})
    response.delete_cookie(login_sessions.COOKIE)
    return response


@router.get("/backup/export.json")
def export_json(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.order_items).selectinload(OrderItem.order),
                selectinload(InventoryItem.sales)).order_by(InventoryItem.name)
    ).all()
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid)
        .options(selectinload(Protocol.goals), selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
                selectinload(Protocol.items).selectinload(ProtocolItem.steps),
                selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs))
        .order_by(Protocol.name)
    ).all()
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "inventory": [_inventory_row(i) for i in inventory],
        "protocols": [_protocol_row(p) for p in protocols],
    }
    body = json.dumps(payload, indent=2)
    filename = f"amide-backup-{date.today().isoformat()}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


CSV_COLUMNS = [
    ("Name", "name"), ("Category", "category"), ("Count", "count"), ("Amount", "vial_size_mg"),
    ("Unit", "vial_size_unit"), ("Medium", "medium"), ("Volume (mL)", "volume_ml"),
    ("Units per package", "units_per_package"), ("Storage", "storage"), ("Cost", "cost"),
    ("Vendor", "vendor"), ("Notes", "notes"),
    ("Reconstituted", "reconstituted_count"), ("Sold", "sold_count"),
]
# Tax/Shipping here are each line's own allocated share of the order-level tax/shipping (see
# OrderItem.allocated_tax_cents/allocated_shipping_cents), not the order's full amount -- a
# multi-item order's total tax/shipping is split across its lines so re-importing doesn't
# double-count it once per line.
ORDER_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Received", "received_quantity"), ("Order date", "order_date"),
    ("Shipped date", "shipped_date"), ("Arrival date", "arrival_date"), ("Tracking site", "tracking_site"),
    ("Tracking number", "tracking_number"), ("Vendor", "vendor"), ("Lot/Batch #", "lot_number"),
    ("Cost", "cost"), ("Tax", "tax"), ("Shipping", "shipping"), ("Expiration", "expiration_date"),
]
SALE_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Sale date", "sale_date"), ("Price", "price"),
]


@router.get("/backup/export/inventory.csv")
def export_inventory_csv(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.order_items).selectinload(OrderItem.order),
                selectinload(InventoryItem.sales)).order_by(InventoryItem.name)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for i in inventory:
        row = _inventory_row(i)
        writer.writerow(["" if row[key] is None else row[key] for _, key in CSV_COLUMNS])
    writer.writerow([])
    writer.writerow([header for header, _ in ORDER_CSV_COLUMNS])
    for i in inventory:
        for li in i.order_items:
            row = {**_order_row(li), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in ORDER_CSV_COLUMNS])
    writer.writerow([])
    writer.writerow([header for header, _ in SALE_CSV_COLUMNS])
    for i in inventory:
        for sale in i.sales:
            row = {**_sale_row(sale), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in SALE_CSV_COLUMNS])
    filename = f"amide-inventory-{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- import (additive only)

def _import_inventory_row(session: Session, uid: int, row: dict) -> None:
    medium = Medium(row["medium"]) if row.get("medium") else None
    item = InventoryItem(
        owner_id=uid, name=row["name"], category=Category(row.get("category") or "Medicine"),
        count=row.get("count", 1), vial_size_mg=row.get("vial_size_mg"),
        vial_size_unit=DoseUnit(row.get("vial_size_unit") or "mg"),
        purchasing_unit=PurchasingUnit(row.get("purchasing_unit") or "individual"), medium=medium,
        volume_ml=row.get("volume_ml"), units_per_package=row.get("units_per_package"),
        storage=StorageLocation(row["storage"]) if row.get("storage") else None,
        cost_cents=round(row["cost"] * 100) if row.get("cost") is not None else None,
        vendor=row.get("vendor"), notes=row.get("notes"),
        reconstituted_count=row.get("reconstituted_count") or 0, sold_count=row.get("sold_count") or 0,
    )
    for o in row.get("orders", []):  # absent entirely in a pre-multi-item-orders backup file -- treat as none
        order = Order(
            order_date=date.fromisoformat(o["order_date"]),
            shipped_date=date.fromisoformat(o["shipped_date"]) if o.get("shipped_date") else None,
            arrival_date=date.fromisoformat(o["arrival_date"]) if o.get("arrival_date") else None,
            tracking_site=o.get("tracking_site"), tracking_number=o.get("tracking_number"),
            vendor=o.get("vendor"),
            tax_cents=round(o["tax"] * 100) if o.get("tax") is not None else None,
            shipping_cents=round(o["shipping"] * 100) if o.get("shipping") is not None else None,
        )
        # A file with no received_quantity key predates multi-item orders -- if it had arrived, it
        # already counted as fully available under the old model, so backfill received_quantity to
        # quantity (matching migration 0013's own backfill rule) rather than leaving it NULL.
        received_quantity = o.get("received_quantity")
        if received_quantity is None and o.get("arrival_date"):
            received_quantity = o["quantity"]
        li = OrderItem(
            quantity=o["quantity"], received_quantity=received_quantity,
            lot_number=o.get("lot_number"),
            cost_cents=round(o["cost"] * 100) if o.get("cost") is not None else None,
            expiration_date=date.fromisoformat(o["expiration_date"]) if o.get("expiration_date") else None,
            coa_vial_size_mg=o.get("coa_vial_size_mg"), coa_purity_pct=o.get("coa_purity_pct"),
        )
        item.order_items.append(li)
        order.items.append(li)
        session.add(order)
    for sale in row.get("sales", []):  # absent entirely in a pre-Sold-flow backup file -- treat as none
        item.sales.append(Sale(
            quantity=sale["quantity"], sale_date=date.fromisoformat(sale["sale_date"]),
            price_cents=round(sale["price"] * 100),
        ))
    session.add(item)


def _import_protocol_row(session: Session, uid: int, row: dict) -> None:
    p = Protocol(
        owner_id=uid, name=row["name"], start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]) if row.get("end_date") else None,
        notes=row.get("notes"), titration_enabled=bool(row.get("titration_enabled")),
    )
    p.goals = [ProtocolGoal(goal=g) for g in row.get("goals", []) if g in GOALS_BY_SLUG]
    for position, item in enumerate(row.get("items", [])):
        peptide = _find_or_create_peptide(session, item["peptide"])
        p.items.append(ProtocolItem(
            peptide_id=peptide.id, position=position, dose=item.get("dose"),
            dose_unit=DoseUnit(item.get("dose_unit") or "mg"), frequency=Frequency(item.get("frequency") or "daily"),
            every_n_days=item.get("every_n_days"), weekdays=item.get("weekdays"),
            time_of_day=TimeOfDay(item.get("time_of_day") or "any"), route=Route(item.get("route") or "subq"),
            notes=item.get("notes"),
            steps=[TitrationStep(start_week=s["start_week"], end_week=s.get("end_week"), dose=s["dose"])
                  for s in item.get("steps", [])],
            cycle_offs=[ProtocolItemCycleOff(start_week=c["start_week"], end_week=c["end_week"])
                       for c in item.get("cycle_offs", [])],
        ))
    session.add(p)


@router.post("/backup/import")
async def import_backup(request: Request, file: UploadFile, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    try:
        payload = json.loads(await file.read())
        assert isinstance(payload, dict)
    except (json.JSONDecodeError, UnicodeDecodeError, AssertionError):
        return _page(request, session, uid, "restore", 422,
                     legacy_error="That file doesn't look like an Amide backup (not valid JSON).")

    for row in payload.get("inventory", []):
        _import_inventory_row(session, uid, row)
    for row in payload.get("protocols", []):
        _import_protocol_row(session, uid, row)
    session.commit()
    return RedirectResponse("/backup?imported=1", status_code=303)
```

- [ ] **Step 5: Create the templates and script**

Replace `app/templates/backup/backup.html`:

```jinja
{% extends "base.html" %}
{% block title %}Backup & restore{% endblock %}

{#- A row of section checkboxes. `checked` ticks them all by default. -#}
{% macro checklist(items, checked=true) %}
<div class="chips backup-sections">
  {% for s in items %}
  <label class="chip-checkbox" title="{{ s.help }}"><input type="checkbox" name="section" value="{{ s.key }}"{% if checked %} checked{% endif %}> {{ s.label }}</label>
  {% endfor %}
</div>
{% endmacro %}

{% macro passphrase_fields() %}
<div class="grid">
  <label class="field"><span>Passphrase</span>
    <input type="password" name="passphrase" minlength="8" autocomplete="new-password" required data-passphrase>
  </label>
  <label class="field"><span>Passphrase again</span>
    <input type="password" name="confirm" minlength="8" autocomplete="new-password" required data-passphrase-confirm>
  </label>
</div>
<p class="muted small">At least 8 characters. The file is encrypted with it and <strong>there is no way to recover a lost passphrase</strong>, so keep it somewhere safe.</p>
{% endmacro %}

{% block content %}
<div class="page-head">
  <div>
    <h1>Backup &amp; restore</h1>
    <p class="muted">Encrypted files you keep. Every backup, export or share is a new dated file and never overwrites an earlier one.</p>
  </div>
</div>

<nav class="wk-tabs" role="tablist" aria-label="Backup sections">
  <a href="/backup?tab=backup" class="wk-tab {{ 'active' if tab == 'backup' }}" role="tab" aria-selected="{{ 'true' if tab == 'backup' else 'false' }}">Back up</a>
  <a href="/backup?tab=export" class="wk-tab {{ 'active' if tab == 'export' }}" role="tab" aria-selected="{{ 'true' if tab == 'export' else 'false' }}">Export / Share</a>
  <a href="/backup?tab=restore" class="wk-tab {{ 'active' if tab == 'restore' }}" role="tab" aria-selected="{{ 'true' if tab == 'restore' else 'false' }}">Restore / Import</a>
</nav>

{% if error %}<div class="alert" role="alert">{{ error }}</div>{% endif %}

{% if tab == 'backup' %}
<form method="post" action="/backup/create" class="builder-step backup-form" data-backup-form>
  <h2 class="section-title">Back up</h2>
  <p class="muted small">A copy of your data for safekeeping. It contains health and personal records: keep the file private.</p>
  <input type="hidden" name="kind" value="backup">
  {% if is_admin %}
  <div class="chips" role="radiogroup" aria-label="What to back up">
    <label class="chip-radio"><input type="radio" name="scope" value="person" checked data-scope> My data</label>
    <label class="chip-radio"><input type="radio" name="scope" value="installation" data-scope> Whole installation (every account, vendors, price lists, library and all files)</label>
  </div>
  {% else %}<input type="hidden" name="scope" value="person">{% endif %}
  <div data-sections>
    <h3 class="small muted">My data</h3>
    {{ checklist(person_sections) }}
    {% if shared_sections %}
    <h3 class="small muted">Shared data (administrator)</h3>
    {{ checklist(shared_sections) }}
    {% endif %}
  </div>
  {{ passphrase_fields() }}
  <p class="muted small">Files up to {{ max_mb }} MB. A whole-installation backup also holds password hashes and 2FA secrets.</p>
  <button type="submit" class="btn btn-primary">Download backup</button>
</form>

{% elif tab == 'export' %}
<form method="post" action="/backup/create" class="builder-step backup-form" data-backup-form>
  <h2 class="section-title">Export to move to a new computer</h2>
  <p class="muted small">Pick the sections to carry to another install of Amide (for example a new computer). Load the file there from Restore / Import.</p>
  <input type="hidden" name="kind" value="export">
  <input type="hidden" name="scope" value="person">
  {{ checklist(person_sections, false) }}
  {% if shared_sections %}{{ checklist(shared_sections, false) }}{% endif %}
  {{ passphrase_fields() }}
  <button type="submit" class="btn btn-primary">Download export</button>
</form>

<form method="post" action="/backup/create" class="builder-step backup-form" style="margin-top: 16px;" data-backup-form>
  <h2 class="section-title">Share non-medical data</h2>
  <p class="muted small">A file you can give to someone else. It leaves out personal records: dose logs, workout logs and fitness tests, sales and vials in use, wallet addresses, accounts, profile, measurements, journal and labs. It still keeps free-text notes and costs, so read your notes first. Vendor and price-list files hold supplier information; sharing them is your decision. Whoever loads it needs the passphrase, and shared vendors, price lists and library entries can only be loaded by an administrator.</p>
  <input type="hidden" name="kind" value="share">
  <input type="hidden" name="scope" value="person">
  {{ checklist(shareable_sections, false) }}
  {{ passphrase_fields() }}
  <button type="submit" class="btn btn-primary">Download share file</button>
</form>

{% else %}
<div class="builder-step">
  <h2 class="section-title">Open a backup, export or share file</h2>
  <p class="muted small">Choose the file and enter its passphrase. You will see what is inside before anything is changed.</p>
  <form method="post" action="/backup/open" enctype="multipart/form-data" class="backup-form">
    <div class="grid">
      <label class="field"><span>File (.amidebackup)</span><input type="file" name="file" accept=".amidebackup" required></label>
      <label class="field"><span>Passphrase</span><input type="password" name="passphrase" autocomplete="off" required></label>
    </div>
    <button type="submit" class="btn btn-primary">Open</button>
  </form>
</div>
{% endif %}

<details class="builder-step" style="margin-top: 16px;">
  <summary><strong>Older formats</strong></summary>
  <p class="muted small">The earlier plain-text export of your inventory and protocols. These files are not encrypted.</p>
  <div class="auth-actions" style="justify-content: flex-start;">
    <a class="btn" href="/backup/export.json">Download inventory and protocols (.json)</a>
    <a class="btn" href="/backup/export/inventory.csv">Download inventory (.csv)</a>
  </div>
  <p class="muted small">Upload one of those <code>.json</code> files. This <strong>adds</strong> its inventory items and protocols to your account; it never changes or deletes anything you already have.</p>
  {% if legacy_error %}<div class="alert" role="alert">{{ legacy_error }}</div>{% endif %}
  <form method="post" action="/backup/import" enctype="multipart/form-data" class="auth-actions" style="justify-content: flex-start;">
    <input type="file" name="file" accept="application/json" required>
    <button type="submit" class="btn">Import</button>
  </form>
</details>
{% endblock %}

{% block scripts %}
<script src="{{ static_url('js/backup.js') }}" defer></script>
{% endblock %}
```

Create `app/templates/backup/preview.html`:

```jinja
{% extends "base.html" %}
{% block title %}Backup contents{% endblock %}

{% block content %}
<div class="page-head">
  <div>
    <h1>What is in this file</h1>
    <p class="muted">Nothing has been changed yet.</p>
  </div>
</div>

{% if error %}<div class="alert" role="alert">{{ error }}</div>{% endif %}

<section class="side-box">
  <dl class="kv kv-tight">
    <dt>Kind</dt><dd>{{ {'backup': 'Backup', 'export': 'Export', 'share': 'Share file'}.get(manifest.kind, manifest.kind) }}{% if manifest.level == 'installation' %} (whole installation){% endif %}</dd>
    <dt>Made by</dt><dd>{{ manifest.creator }}</dd>
    <dt>Created</dt><dd>{{ manifest.created_at[:10][5:7] }}/{{ manifest.created_at[8:10] }}/{{ manifest.created_at[:4] }} {{ manifest.created_at[11:16] }} UTC</dd>
    <dt>Database version</dt><dd>{{ manifest.revision or 'unknown' }}</dd>
  </dl>
  {% if manifest.kind == 'share' %}<p class="muted small">A share file: personal records were left out when it was made, and it can only be added, never used to replace anything.</p>{% endif %}
  {% if manifest.missing_files %}<p class="muted small">{{ manifest.missing_files | length }} attached file(s) were missing when this was made and are not in it.</p>{% endif %}
</section>

<form method="post" action="/backup/load" class="builder-step backup-form" style="margin-top: 16px;">
  <h2 class="section-title">Load sections</h2>
  <p class="muted small">Tick what to load. <strong>Add</strong> keeps what you have and adds the file's rows (rows that would duplicate something, such as a second journal entry for the same day, are skipped). <strong>Replace</strong> deletes your current rows in that section first and makes it match the file.</p>
  <input type="hidden" name="token" value="{{ token }}">
  <div class="table-wrap">
    <table class="inv-table">
      <thead><tr><th>Load</th><th>Section</th><th>Rows</th><th>How</th></tr></thead>
      <tbody>
        {% for s in sections %}
        <tr>
          <td data-label="Load"><input type="checkbox" name="load" value="{{ s.key }}"{% if not s.allowed %} disabled{% endif %} aria-label="Load {{ s.label }}"></td>
          <td data-label="Section"><strong>{{ s.label }}</strong>{% if s.level == 'installation' %} <span class="tag tag-plain">shared</span>{% endif %}
            {% if s.tables is defined %}<div class="muted small">{% for name, n in s.tables.items() if n %}{{ name | replace('_', ' ') }} {{ n }}{{ ', ' if not loop.last }}{% endfor %}</div>{% endif %}</td>
          <td data-label="Rows">{{ s.rows }}</td>
          <td data-label="How">
            {% if not s.allowed %}<span class="muted small">{{ s.reason }}</span>
            {% elif s.key == 'profile' %}<span class="muted small">Applied to your profile</span><input type="hidden" name="mode-{{ s.key }}" value="replace">
            {% elif s.modes | length == 1 %}<span class="muted small">Add only{% if s.level == 'installation' %} (existing vendors, peptides and lists are kept){% endif %}</span><input type="hidden" name="mode-{{ s.key }}" value="add">
            {% else %}
            <label class="chip-radio"><input type="radio" name="mode-{{ s.key }}" value="add" checked> Add</label>
            <label class="chip-radio"><input type="radio" name="mode-{{ s.key }}" value="replace"> Replace</label>
            {% if s.current is not none %}<div class="muted small">Replace deletes your {{ s.current }} current row{{ '' if s.current == 1 else 's' }} in this section first. Take a backup first if you may want them.</div>{% endif %}
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <button type="submit" class="btn btn-primary" style="margin-top: 12px;">Load selected sections</button>
</form>

{% if can_restore %}
<form method="post" action="/backup/restore" class="builder-step backup-form" style="margin-top: 16px;" data-restore-form>
  <h2 class="section-title">Restore everything</h2>
  <div class="alert" role="alert">This <strong>deletes all current data</strong> (every account, inventory, logs, vendors, price lists, the library and every uploaded file) and rebuilds it from this file. Everyone is signed out afterwards. Before anything is deleted, a safety backup of what is there now is saved on this computer in the <code>data/backups</code> folder, protected by the passphrase below.</div>
  <input type="hidden" name="token" value="{{ token }}">
  <div class="grid">
    <label class="field"><span>Passphrase for the safety copy</span><input type="password" name="safety_passphrase" minlength="8" autocomplete="new-password" required></label>
    <label class="field"><span>Passphrase again</span><input type="password" name="safety_confirm" minlength="8" autocomplete="new-password" required></label>
    <label class="field"><span>Type {{ restore_word }} to confirm</span><input name="confirm" autocomplete="off" required data-restore-word="{{ restore_word }}"></label>
  </div>
  <button type="submit" class="btn btn-danger" data-restore-submit disabled>Restore everything</button>
</form>
{% endif %}

<p style="margin-top: 16px;"><a href="/backup?tab=restore">Open a different file</a></p>
{% endblock %}

{% block scripts %}
<script src="{{ static_url('js/backup.js') }}" defer></script>
{% endblock %}
```

Create `app/templates/backup/result.html`:

```jinja
{% extends "base.html" %}
{% block title %}Backup loaded{% endblock %}

{% block content %}
<div class="page-head"><div><h1>Loaded</h1><p class="muted">The selected sections were loaded in one step; if anything had failed, nothing would have changed.</p></div></div>

<section class="side-box">
  <table class="lib-table">
    <thead><tr><th>Section</th><th>Added</th><th>Removed first</th><th>Skipped</th></tr></thead>
    <tbody>
      {% for key in ((report.added.keys() | list) + (report.removed.keys() | list) + (report.skipped.keys() | list)) | unique %}
      <tr><td>{{ labels.get(key, key) }}</td><td>{{ report.added.get(key, 0) }}</td><td>{{ report.removed.get(key, 0) }}</td><td>{{ report.skipped.get(key, 0) }}</td></tr>
      {% else %}
      <tr><td colspan="4" class="muted">Nothing to load.</td></tr>
      {% endfor %}
    </tbody>
  </table>
  <p class="muted small">{{ report.files }} attached file(s) copied.</p>
  {% if report.skipped %}<p class="muted small">Skipped rows were duplicates of something already there (or belonged to a skipped parent).</p>{% endif %}
</section>

{% if report.unresolved %}
<section class="side-box" style="margin-top: 16px;">
  <h2 class="section-title">Links that could not be matched</h2>
  <p class="muted small">These rows were loaded, but the item they pointed at is not in this installation, so the link is empty.</p>
  <ul class="plain-list">{% for name, n in report.unresolved.items() %}<li>{{ name | replace('_', ' ') | replace('.', ': ') }}: {{ n }}</li>{% endfor %}</ul>
</section>
{% endif %}

<p style="margin-top: 16px;"><a class="btn" href="/backup?tab=restore">Back to Backup &amp; restore</a></p>
{% endblock %}
```

Create `app/templates/backup/restored.html`:

```jinja
{% extends "base.html" %}
{% block title %}Restored{% endblock %}

{% block content %}
<div class="page-head"><div><h1>Everything was restored</h1><p class="muted">Everyone has been signed out. Sign in again with an account from the backup.</p></div></div>

<section class="side-box">
  <p>{{ report.rows.values() | sum }} rows and {{ report.files }} files were restored.</p>
  <p class="muted small">A safety backup of what was replaced is saved as <code>data/backups/{{ report.safety_copy }}</code>. Keep it until you are sure the restore is what you wanted.</p>
  <a class="btn btn-primary" href="/">Sign in</a>
</section>
{% endblock %}
```

Create `app/static/js/backup.js`:

```js
// Backup & restore page: passphrase confirmation, the whole-installation toggle and the typed RESTORE confirmation.
// Nothing is stored in the browser; the server checks everything again.
(() => {
  document.querySelectorAll("[data-backup-form]").forEach((form) => {
    const first = form.querySelector("[data-passphrase]");
    const second = form.querySelector("[data-passphrase-confirm]");
    if (first && second) {
      const check = () => second.setCustomValidity(first.value === second.value ? "" : "The two passphrases do not match.");
      first.addEventListener("input", check);
      second.addEventListener("input", check);
    }
    const sections = form.querySelector("[data-sections]");
    form.querySelectorAll("[data-scope]").forEach((radio) => {
      radio.addEventListener("change", () => {
        if (sections) sections.hidden = form.querySelector("[data-scope]:checked").value === "installation";
      });
    });
  });

  const restore = document.querySelector("[data-restore-form]");
  if (restore) {
    const word = restore.querySelector("[data-restore-word]");
    const button = restore.querySelector("[data-restore-submit]");
    const sync = () => { button.disabled = word.value.trim() !== word.dataset.restoreWord; };
    word.addEventListener("input", sync);
    sync();
  }
})();
```

In `app/templates/settings/settings.html`, replace the Settings card text `<p class="small muted">Download or restore your inventory and protocols.</p>` with `<p class="small muted">Back up, export, share and restore your data, in encrypted files.</p>`.

- [ ] **Step 6: Run the page tests, the older backup tests and the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backup_page.py tests/test_backup.py tests/test_settings.py -q -p no:warnings` then the full suite.
Expected: 23 passed in the new file; all older tests pass; full suite green.

- [ ] **Step 7: Browser check**

On a scratch database (`AMIDE_DATA_DIR` pointing at a temp folder; never the owner's `data/`): register an account, add a little data, open Settings then Backup & restore. Verify the three tabs, creating a backup (file downloads with a dated name), opening it with the passphrase (preview lists sections), loading a section with Add and with Replace (result page), the share tab wording, the typed RESTORE button staying disabled until the word matches, and the whole page at 375 px and in dark theme. Delete the scratch data afterwards.

- [ ] **Step 8: Commit**

```bash
git add app/backup/pending.py app/backup/service.py app/routers/backup.py app/templates/backup app/static/js/backup.js app/templates/settings/settings.html tests/test_backup_page.py
git commit -m "feat: Backup page with back up, export/share, open, load sections and restore everything

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Roadmap, verification and review

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Roadmap**

Add a built-item note near the other Settings/Backup notes in `docs/ROADMAP.md` (no vendor or price-list data): the encrypted `.amidebackup` format, the three kinds (Back up, Export, Share), the sections and what a share file strips, Add/Replace loading, whole-installation restore with the safety copy, administrator-only shared data, the `AMIDE_MAX_BACKUP_MB` setting, and known limits (the file is built in memory; the passphrase cannot be recovered; scheduled backups and cloud destinations are not built; restore needs the same or a newer Amide than the backup).

- [ ] **Step 2: Full verification**

Run the whole suite; `git status` shows no `*.amidebackup` or data files; run the staged-diff vendor-name scan for the final commit.

- [ ] **Step 3: Independent review**

Dispatch one reviewer (most capable model) over the commits for this plan with the spec, this plan and the Review Focus list; fix every Critical and Important finding test-first in one pass; record Minor findings in the final message.

- [ ] **Step 4: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: roadmap notes for encrypted backup, export, share and restore

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

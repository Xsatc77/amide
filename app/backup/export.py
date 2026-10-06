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
    """Add `_refs` to rows whose foreign key points outside the section's own tables (and not at a user), or at a row of
    the section's own table that is not in the file (a food log pointing at a built-in starter food)."""
    inside = {t.name for t in section.tables}
    cache: dict[tuple, list | None] = {}
    for tbl in section.tables:
        for fk in reg.table(tbl.name).foreign_keys:
            col, target = fk.parent.name, fk.column.table.name
            ref = reg.REFS.get((tbl.name, col))
            if target == "users" or ref is None:
                continue
            present = {r.get("id") for r in rows_by_table.get(target, ())} if target in inside else None
            if target in inside and present is None:
                continue
            keys = ref[1]
            for row in rows_by_table.get(tbl.name, ()):
                value = row.get(col)
                if value is None or (present is not None and value in present):
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

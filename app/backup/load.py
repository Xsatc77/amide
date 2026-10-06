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
from sqlalchemy.exc import IntegrityError
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
    params = {f"k{n}": v for n, v in enumerate(values)}
    if target == "inventory_items":            # a person's inventory is theirs alone: never link to someone else's item
        clause, params["uid"] = f"{clause} AND owner_id = :uid", ctx.uid
    if target == "foods":                      # a person's own food or a built-in starter food: never someone else's
        clause, params["uid"] = f"{clause} AND (owner_id IS NULL OR owner_id = :uid)", ctx.uid
    row = ctx.session.execute(text(f'SELECT id FROM "{target}" WHERE {clause}'), params).first()
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
    if dirkey == "body_photos":      # a photo is re-encoded clean (no metadata, sane size) under a name this app can serve
        from app import body_photos
        try:
            new_name = body_photos.store(body_photos.process_upload(ctx.archive.read(entry)))
        except body_photos.PhotoError:
            return None
        ctx.new_files.append(directory / new_name)
        ctx.report.files += 1
        return new_name
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
    if tbl.where is not None:                  # a person's rows always belong to the person loading, whatever the file says
        for col in reg.person_columns(tbl):
            values[col] = ctx.uid
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
            if mapped is None:                      # the parent row is not in the file: find it by name, else leave it empty
                values[col] = _resolve_external(ctx, tbl.name, col, row)
                if values[col] is None:
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
            if tbl.file_required:
                return
    elif tbl.file and tbl.file_required:
        _note(ctx.report.unresolved, f"{tbl.file[1]} file")
        return

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
    sql = f'INSERT INTO "{tbl.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join(":" + c for c in cols)})'
    try:
        with ctx.session.begin_nested():
            ctx.session.execute(text(sql), values)
    except IntegrityError as exc:
        if "UNIQUE constraint failed" not in str(exc.orig):    # only a duplicate is skippable; anything else is a bad file
            raise BackupError(f"A row of {tbl.name.replace('_', ' ')} is missing required data, so nothing was changed.") from exc
        existing = None
        if tbl.reference and (key := reg.MERGE_KEYS.get(tbl.name)):       # a lookup row the account already has: its children use it
            existing = _find(ctx, tbl.name, key, [values.get(k) for k in key])
        if has_id and row.get("id") is not None:
            ctx.idmap.setdefault(tbl.name, {})[row["id"]] = (existing, False)
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

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

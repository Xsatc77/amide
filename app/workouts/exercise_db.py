"""The exercise database: the owner's workbook of 230 gym exercises (MET, default style, seconds per rep, rest),
its aliases, the style profiles and the Compendium speed tables, loaded once from exercise_data.json.

Regenerate that file from the workbook with tools/build_exercise_data.py."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).with_name("exercise_data.json")


@dataclass(frozen=True)
class Exercise:
    name: str
    equipment: str | None
    area: str | None
    pattern: str | None
    code: str | None
    met: float | None
    evidence: str | None
    style: str | None
    sec_per_rep: float | None
    rest_min: float | None
    model: str                # "rep" or "duration"
    note: str | None
    speed_table: str | None   # a key of speed_tables(): the Compendium rows that give this exercise its MET

    @property
    def is_duration(self) -> bool:
        return self.model == "duration"


@dataclass(frozen=True)
class SpeedBand:
    code: str
    min: float
    met: float
    label: str


@dataclass(frozen=True)
class SpeedTable:
    key: str
    basis: str                # "speed_mph" or "grade_pct"
    unit_label: str
    rows: tuple[SpeedBand, ...]


@dataclass(frozen=True)
class StyleProfile:
    name: str
    met: float
    sec_per_rep: float | None
    rest_min: float | None


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def all_exercises() -> tuple[Exercise, ...]:
    return tuple(Exercise(**row) for row in _raw()["exercises"])


@lru_cache(maxsize=1)
def _by_name() -> dict[str, Exercise]:
    return {e.name.casefold(): e for e in all_exercises()}


def get(name: str | None) -> Exercise | None:
    """The exercise with this canonical name (case-insensitive), or None."""
    return _by_name().get((name or "").strip().casefold())


@lru_cache(maxsize=1)
def aliases() -> dict[str, str]:
    """casefolded alias -> canonical exercise name."""
    return dict(_raw()["aliases"])


def canonical_for_alias(text: str | None) -> str | None:
    return aliases().get((text or "").strip().casefold())


@lru_cache(maxsize=1)
def styles() -> dict[str, StyleProfile]:
    return {name: StyleProfile(name, p["met"], p["sec_per_rep"], p["rest_min"]) for name, p in _raw()["styles"].items()}


def choosable_styles() -> list[str]:
    """The styles a person can pick on a rep-based row (not the rest-recovery assumption or the "General" label)."""
    return [name for name, s in styles().items() if s.sec_per_rep is not None and name != "General"]


@lru_cache(maxsize=1)
def speed_tables() -> dict[str, SpeedTable]:
    out = {}
    for key, table in _raw()["speed_tables"].items():
        rows = tuple(SpeedBand(r["code"], r["min"], r["met"], r["label"])
                     for r in sorted(table["rows"], key=lambda r: r["min"]))
        out[key] = SpeedTable(key, table["basis"], table["unit_label"], rows)
    return out


def search(text: str, limit: int = 12) -> list[Exercise]:
    """Exercises whose name or an alias contains every word of `text`, shortest names first."""
    words = (text or "").casefold().split()
    if not words:
        return []
    alias_hits = {canonical for alias, canonical in aliases().items() if all(w in alias for w in words)}
    hits = [e for e in all_exercises() if all(w in e.name.casefold() for w in words) or e.name in alias_hits]
    return sorted(hits, key=lambda e: (len(e.name), e.name))[:limit]

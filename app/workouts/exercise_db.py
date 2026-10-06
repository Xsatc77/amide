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
    basis: str                # "speed_mph", "grade_pct", "watts" or "effort"
    unit_label: str
    rows: tuple[SpeedBand, ...]
    default: SpeedBand | None = None   # the row used when nothing is entered (bikes, rowing, elliptical, ski erg)


@dataclass(frozen=True)
class Category:
    """A Compendium row a person can pick for a rep-based exercise (the resistance and calisthenics rows)."""
    code: str
    met: float
    label: str


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
def categories() -> tuple[Category, ...]:
    return tuple(Category(c["code"], c["met"], c["label"]) for c in _raw()["categories"])


def category(code: str | None) -> Category | None:
    return next((c for c in categories() if c.code == code), None)


@lru_cache(maxsize=1)
def compendium() -> dict[str, dict]:
    """Every Compendium row the data uses: code -> {met, description}."""
    return {row["code"]: row for row in _raw()["compendium"]}


@lru_cache(maxsize=1)
def speed_tables() -> dict[str, SpeedTable]:
    out = {}
    for key, table in _raw()["speed_tables"].items():
        rows = tuple(SpeedBand(r["code"], r["min"], r["met"], r["label"])
                     for r in sorted(table["rows"], key=lambda r: r["min"]))
        default = None
        if table.get("default"):
            row = compendium()[table["default"]]
            default = SpeedBand(row["code"], 0, row["met"], row["description"])
        out[key] = SpeedTable(key, table["basis"], table["unit_label"], rows, default)
    return out


def search(text: str, limit: int = 12) -> list[Exercise]:
    """Exercises whose name or an alias contains every word of `text`, shortest names first."""
    words = (text or "").casefold().split()
    if not words:
        return []
    alias_hits = {canonical for alias, canonical in aliases().items() if all(w in alias for w in words)}
    hits = [e for e in all_exercises() if all(w in e.name.casefold() for w in words) or e.name in alias_hits]
    return sorted(hits, key=lambda e: (len(e.name), e.name))[:limit]

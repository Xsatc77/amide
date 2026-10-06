# app/library/price_lists/names.py
"""Names for price-list rows: carry a group's name down its rows, learn what a short code means, repair."""

import difflib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.library.matching import name_key, qualifiers
from app.library.price_lists.rows import ParsedRow, code_prefix

_TYPO_RATIO = 0.8


def propagate_names(rows: list[ParsedRow]) -> None:
    """Vendors merge the name cell across a group, so only one row of a group carries it, at the top or in the
    middle. A run is a stretch of consecutive rows with the same code prefix (or consecutive rows with no code).
    Rows above a run's first name take that name and rows below its last name take it. A row between two
    different names is only filled when the list puts names on the first row of a group (then it belongs to the
    name above); otherwise it is left unnamed and flagged "ambiguous-name", never guessed."""
    i = 0
    while i < len(rows):
        prefix = code_prefix(rows[i].code)
        j = i + 1
        while j < len(rows) and code_prefix(rows[j].code) == prefix:
            j += 1
        _fill_run(rows[i:j], coded=prefix is not None)
        i = j


def _fill_run(run: list[ParsedRow], coded: bool) -> None:
    if not any(row.name for row in run):
        return
    top_aligned = bool(run[0].name)
    for k, row in enumerate(run):
        if row.name:
            continue
        above = next((run[m].name for m in range(k - 1, -1, -1) if run[m].name), None)
        below = next((run[m].name for m in range(k + 1, len(run)) if run[m].name), None) if coded else None
        if above is None and below is not None:
            row.name = below
        elif below is None:
            row.name = above
        elif top_aligned or name_key(above) == name_key(below):
            row.name = above
        else:
            row.flags.append("ambiguous-name")


@dataclass(frozen=True)
class PrefixName:
    key: str      # name_key of the agreed name
    display: str  # its most common spelling


def learn_prefixes(observations: Iterable[tuple[str, str, str]]) -> dict[str, PrefixName]:
    """(vendor_key, code_prefix, product_name) -> {prefix: name}. A prefix is learned only when at least two
    vendors give the same name and no rival name is as common."""
    vendors: dict[str, dict[str, set[str]]] = {}
    spellings: dict[tuple[str, str], Counter] = {}
    for vendor, prefix, name in observations:
        key = name_key(name)
        if not prefix or not key:
            continue
        vendors.setdefault(prefix, {}).setdefault(key, set()).add(vendor)
        spellings.setdefault((prefix, key), Counter())[name.strip()] += 1
    table: dict[str, PrefixName] = {}
    for prefix, by_key in vendors.items():
        ranked = sorted(by_key.items(), key=lambda item: len(item[1]), reverse=True)
        best_key, best_vendors = ranked[0]
        if len(best_vendors) < 2:
            continue
        if len(ranked) > 1 and len(ranked[1][1]) >= len(best_vendors):
            continue
        counts = spellings[(prefix, best_key)]
        display = max(counts, key=lambda s: (counts[s], s[:1].isupper(), s))
        table[prefix] = PrefixName(best_key, display)
    return table


def repair_names(rows: list[ParsedRow], table: dict[str, PrefixName], is_known: Callable[[str], bool]) -> None:
    """Fill a blank name from the learned code, or fix a misspelling the library does not recognise. A different
    product that merely shares the prefix (a cartridge) is left alone."""
    for row in rows:
        learned = table.get(code_prefix(row.code) or "")
        if learned is None:
            continue
        if not row.name:
            row.name = learned.display
        elif (name_key(row.name) != learned.key and qualifiers(row.name) == qualifiers(learned.display)
              and not is_known(row.name) and difflib.SequenceMatcher(None, name_key(row.name), learned.key).ratio() >= _TYPO_RATIO):
            row.name = learned.display
        else:
            continue
        row.flags.append("name-from-code")

# app/library/price_lists/names.py
"""Names for price-list rows: carry a group's name down its rows, learn what a short code means, repair."""

import difflib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.library.matching import name_key
from app.library.price_lists.rows import ParsedRow, code_prefix

_TYPO_RATIO = 0.8


def propagate_names(rows: list[ParsedRow]) -> None:
    """Vendors merge the name cell across a group, so only one row of the group has it. A run is a stretch of
    consecutive rows with the same code prefix; rows without a name take the first name found in their run."""
    i = 0
    while i < len(rows):
        prefix = code_prefix(rows[i].code)
        j = i + 1
        while prefix is not None and j < len(rows) and code_prefix(rows[j].code) == prefix:
            j += 1
        run = rows[i:j]
        name = next((row.name for row in run if row.name), None)
        if name:
            for row in run:
                row.name = row.name or name
        i = j


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
        elif (name_key(row.name) != learned.key and not is_known(row.name)
              and difflib.SequenceMatcher(None, name_key(row.name), learned.key).ratio() >= _TYPO_RATIO):
            row.name = learned.display
        else:
            continue
        row.flags.append("name-from-code")

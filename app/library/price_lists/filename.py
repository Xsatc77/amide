# app/library/price_lists/filename.py
"""Vendor, warehouse and date from a price-list filename: "<Vendor> - [<Warehouse> ]Price List - <date>"."""

import re
from dataclasses import dataclass
from datetime import date

_EXTENSIONS = re.compile(r"(\.[A-Za-z0-9]{2,4})+\s*$")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:-\d+)?")  # a trailing -NN is a page suffix
_CHINA = re.compile(r"\b(china|chinese)\b", re.IGNORECASE)
_US = re.compile(r"\b(usa|us|u\.s\.|united states)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FileInfo:
    vendor: str
    warehouse: str | None  # "us" | "china" | None when the filename names none
    list_date: date


def parse_filename(name: str) -> FileInfo:
    stem = _EXTENSIONS.sub("", name)  # not stripped first: a leading " - " must leave an empty vendor
    parts = [part.strip() for part in stem.split(" - ")]
    if len(parts) < 2:
        raise ValueError(f"no date in {name!r}")
    match = _DATE.fullmatch(parts[-1])
    if match is None:
        raise ValueError(f"no date in {name!r}")
    vendor = parts[0]
    if not vendor:
        raise ValueError(f"no vendor in {name!r}")
    list_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    middle = " ".join(parts[1:-1])
    warehouse = "china" if _CHINA.search(middle) else "us" if _US.search(middle) else None
    return FileInfo(vendor, warehouse, list_date)

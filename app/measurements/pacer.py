"""Reading the Pacer app's data export: WeightLogs and HeartLogs (blood pressure and pulse) CSVs, loose or inside the exported zip.
The step files are never read. Each row becomes a reading; readings go in through the same 12-hour rule as the scale import, so a weigh-in and its
blood pressure taken minutes apart become one entry, and importing the same files again adds nothing."""

import csv
import io
import zipfile
from datetime import datetime

from app.measurements.renpho import Parsed, Reading
from app.units import LB_PER_KG

FILE_MARK = "---FILE---"          # the page joins the text of several chosen CSV files with this line
_MAX_BYTES = 2_000_000


class ImportError_(ValueError):
    """Nothing in what was chosen is a Pacer weight or blood-pressure log; the message says why."""


def _stamp(text: str) -> datetime:
    return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")


def _num(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def _rows(text: str):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def parse_weight(text: str) -> Parsed:
    out = Parsed()
    for n, row in enumerate(_rows(text), start=2):
        weight = _num(row.get("Weight"))
        try:
            when = _stamp(row.get("Time") or "")
        except ValueError:
            weight = None
        if weight is None or weight <= 0:
            out.errors.append(f"Line {n}: no readable date or weight.")
            continue
        if (row.get("Unit") or "").strip().lower().startswith("kg"):
            weight = round(weight * LB_PER_KG, 1)
        out.readings.append(Reading(when, {"weight_lbs": weight}))
    return out


def parse_heart(text: str) -> Parsed:
    out = Parsed()
    for n, row in enumerate(_rows(text), start=2):
        sys_, dia, pulse = _num(row.get("Systolic")), _num(row.get("Diastolic")), _num(row.get("HeartRate"))
        try:
            when = _stamp(row.get("Time") or "")
        except ValueError:
            sys_ = None
        if not sys_ or not dia:
            out.errors.append(f"Line {n}: no readable date or blood pressure.")
            continue
        values = {"systolic": int(sys_), "diastolic": int(dia)}
        if pulse:                                   # Pacer writes 0 when no pulse was taken
            values["heart_rate_bpm"] = int(pulse)
        out.readings.append(Reading(when, values))
    return out


def parse_texts(texts: list[str]) -> Parsed:
    """Every weight and blood-pressure reading found in the given CSV texts (each sniffed by its header), oldest first."""
    result = Parsed()
    found = False
    for text in (t.strip() for t in texts):
        head = text.lstrip("﻿").lstrip().split("\n", 1)[0].lower()
        if len(text.encode("utf-8", "ignore")) > _MAX_BYTES:
            raise ImportError_("That file is too large to be a Pacer export.")
        if head.startswith("time,weight"):
            part = parse_weight(text)
        elif head.startswith("time,systolic"):
            part = parse_heart(text)
        else:
            continue
        found = True
        result.readings += part.readings
        result.errors += part.errors
    if not found:
        raise ImportError_("That does not look like a Pacer export. Choose the exported zip, or its WeightLogs and HeartLogs files.")
    result.readings.sort(key=lambda r: r.stamp)
    return result


def texts_from_zip(data: bytes) -> list[str]:
    """The WeightLogs and HeartLogs files of the exported zip; anything else (the large step files) is left unread."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("That zip file could not be opened.")
    out = []
    for info in z.infolist():
        name = info.filename.rsplit("/", 1)[-1].lower()
        if name.startswith(("weightlogs", "heartlogs")) and name.endswith(".csv") and info.file_size <= _MAX_BYTES:
            out.append(z.read(info).decode("utf-8-sig", errors="replace"))
    return out


def import_readings(session, owner_id: int, parsed: Parsed):
    """Add each reading through the 12-hour rule, oldest first. The caller commits."""
    from sqlalchemy import select

    from app.measurements import same_time
    from app.measurements.renpho import ImportResult
    from app.models import BodyMeasurement
    rows = list(session.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == owner_id)))
    imported = skipped = merged = 0
    for reading in parsed.readings:
        original = same_time.find_original(session, owner_id, reading.stamp, rows)
        if original is not None:
            if same_time.fill_blanks(original, reading.values):
                merged += 1
            else:
                skipped += 1
            continue
        row = BodyMeasurement(owner_id=owner_id, measured_at=reading.stamp.date(), recorded_at=reading.stamp, **reading.values)
        session.add(row)
        rows.append(row)
        imported += 1
    session.flush()
    return ImportResult(imported, skipped, parsed.errors, merged)

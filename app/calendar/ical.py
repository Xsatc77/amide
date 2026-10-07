"""The private iCal feed: each due dose as a calendar event, so Apple, Google and Outlook calendars can show (and ring for) it."""

from datetime import datetime, timedelta

from app.models import TimeOfDay

# Clock time of each slot in the calendar app (floating, so it reads as the viewer's own local time). "Any time" is an all-day event.
SLOT_TIMES = {
    TimeOfDay.FASTING: (6, 30), TimeOfDay.WAKING: (7, 0), TimeOfDay.AM: (8, 0), TimeOfDay.PRE_WORKOUT: (16, 30),
    TimeOfDay.POST_WORKOUT: (18, 30), TimeOfDay.PM: (20, 0), TimeOfDay.BEFORE_BED: (21, 30), TimeOfDay.BEDTIME: (22, 0),
}
EVENT_MINUTES = 15


def escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Lines are at most 75 octets; a longer one continues on the next line after one space."""
    raw, out, current, size = line, [], "", 0
    for ch in raw:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def build_calendar(occurrences, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Amide//Doses//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Amide doses"]
    for occ in occurrences:
        for it in occ.items:
            dose = f"{it.dose:g} {it.unit}" if it.dose is not None else "dose not set"
            slot = it.time_of_day
            summary = f"{it.peptide} {dose}" + (f" ({slot.label})" if slot is not TimeOfDay.ANY else "")
            details = f"{occ.protocol_name}" + (f" - titration step {it.step}" if it.step else "") + (f" - {it.route}" if it.route else "")
            lines += ["BEGIN:VEVENT", f"UID:amide-{it.protocol_item_id}-{occ.date:%Y%m%d}@amide", f"DTSTAMP:{now:%Y%m%dT%H%M%SZ}"]
            if slot in SLOT_TIMES:
                start = datetime(occ.date.year, occ.date.month, occ.date.day, *SLOT_TIMES[slot])
                lines += [f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(start + timedelta(minutes=EVENT_MINUTES))}"]
            else:
                lines += [f"DTSTART;VALUE=DATE:{occ.date:%Y%m%d}", f"DTEND;VALUE=DATE:{occ.date + timedelta(days=1):%Y%m%d}"]
            lines += [f"SUMMARY:{escape(summary)}", f"DESCRIPTION:{escape(details)}"]
            if slot in SLOT_TIMES:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(summary)}", "TRIGGER:PT0M", "END:VALARM"]
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)

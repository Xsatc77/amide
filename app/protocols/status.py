"""Protocol status and schedule helpers. Pure functions: no database access."""

import enum
from datetime import date


class Status(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SCHEDULED = "scheduled"
    ENDED = "ended"

    @property
    def label(self) -> str:
        return self.value.capitalize()


def protocol_status(p, today: date) -> Status:
    """Priority: Ended > Paused > Scheduled > Active. A protocol is still Active on its end date."""
    if p.ended_on is not None or (p.end_date is not None and p.end_date < today):
        return Status.ENDED
    if p.paused:
        return Status.PAUSED
    if p.start_date > today:
        return Status.SCHEDULED
    return Status.ACTIVE


def day_number(start: date, today: date) -> int | None:
    """1 on the start day; None before it starts."""
    return None if today < start else (today - start).days + 1


def current_week(start: date, today: date) -> int | None:
    """Week 1 is the start day plus the six days after it; None before it starts."""
    return None if today < start else (today - start).days // 7 + 1


def _covering(ranges, week: int | None):
    """The first of `ranges` (each with start_week/end_week, end_week possibly None meaning
    onward) whose range includes `week`, or None."""
    if week is None:
        return None
    for r in ranges:
        if r.start_week <= week and (r.end_week is None or week <= r.end_week):
            return r
    return None


def current_step(steps, week: int | None):
    """The titration step covering `week` (an open end_week means "onward"), or None."""
    return _covering(steps, week)


def is_cycled_off(cycle_offs, week: int | None) -> bool:
    """True when `week` falls inside any of this item's cycle-off ranges."""
    return _covering(cycle_offs, week) is not None


def merge_stacks(goal_slugs: list[str], stacks: dict[str, list[int]]) -> list[int]:
    """Peptide ids suggested for the chosen goals: goal order, then stack order, no duplicates."""
    merged: list[int] = []
    for slug in goal_slugs:
        for peptide_id in stacks.get(slug, []):
            if peptide_id not in merged:
                merged.append(peptide_id)
    return merged

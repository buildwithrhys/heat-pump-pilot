"""Timezone-aware time helpers.

UK settlement runs on half-hour slots in local time. Days with clock changes
have 46 or 50 slots. Everything here works in UTC internally and converts at
the edges.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")

SLOT = timedelta(minutes=30)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC)


def local_day_bounds(day: date, tz: ZoneInfo = LONDON) -> tuple[datetime, datetime]:
    """Return the UTC start and end of a local calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def half_hour_slots(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Half-open 30 minute slots covering [start, end) in UTC."""
    start, end = to_utc(start), to_utc(end)
    slots = []
    t = floor_to_slot(start)
    while t < end:
        slots.append((t, t + SLOT))
        t += SLOT
    return slots


def slots_in_local_day(day: date, tz: ZoneInfo = LONDON) -> list[tuple[datetime, datetime]]:
    """All half-hour slots in a local day: 48 normally, 46 or 50 on clock-change days."""
    s, e = local_day_bounds(day, tz)
    return half_hour_slots(s, e)


def floor_to_slot(dt: datetime) -> datetime:
    dt = to_utc(dt)
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def ceil_to_slot(dt: datetime) -> datetime:
    f = floor_to_slot(dt)
    return f if f == to_utc(dt) else f + SLOT


def overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0.0, (hi - lo).total_seconds())

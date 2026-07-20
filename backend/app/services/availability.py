from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def compute_slots(
    *,
    day: date,
    duration_minutes: int,
    timezone_name: str,
    busy_periods: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Paris")
    opening = datetime.combine(day, time(8), tzinfo=zone)
    closing = datetime.combine(day, time(21), tzinfo=zone)
    duration = timedelta(minutes=duration_minutes)
    increment = timedelta(minutes=30)
    now = datetime.now(zone)
    slots: list[tuple[datetime, datetime]] = []
    cursor = opening
    while cursor + duration <= closing:
        slot_end = cursor + duration
        overlaps = any(cursor < busy_end and slot_end > busy_start for busy_start, busy_end in busy_periods)
        if cursor > now and not overlaps:
            slots.append((cursor, slot_end))
        cursor += increment
    return slots

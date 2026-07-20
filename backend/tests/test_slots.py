from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.app.services.availability import compute_slots


def test_slots_obey_working_hours_and_busy_periods():
    zone = ZoneInfo("Europe/Paris")
    day = date(2099, 5, 12)
    busy = [(datetime(2099, 5, 12, 10, 0, tzinfo=zone), datetime(2099, 5, 12, 11, 0, tzinfo=zone))]
    slots = compute_slots(
        day=day,
        duration_minutes=60,
        timezone_name="Europe/Paris",
        busy_periods=busy,
    )
    assert slots[0][0].hour == 8
    assert all(start.hour >= 8 and end.hour <= 21 for start, end in slots)
    assert not any(start.hour == 10 and start.minute == 0 for start, _ in slots)
    assert not any(start.hour == 9 and start.minute == 30 for start, _ in slots)


def test_duration_reduces_slot_count():
    day = date(2099, 5, 12)
    short = compute_slots(day=day, duration_minutes=30, timezone_name="Europe/Paris", busy_periods=[])
    long = compute_slots(day=day, duration_minutes=120, timezone_name="Europe/Paris", busy_periods=[])
    assert len(short) > len(long)

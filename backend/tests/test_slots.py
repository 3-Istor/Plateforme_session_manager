from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from backend.app.services.availability import (
    BusyPeriods,
    compute_detailed_slots,
    compute_slots,
    working_window,
)


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


def test_detailed_slots_keep_member_and_collective_conflicts_separate():
    zone = ZoneInfo("Europe/Paris")
    day = date(2099, 5, 12)
    detailed = compute_detailed_slots(
        day=day,
        duration_minutes=60,
        timezone_name="Europe/Paris",
        busy_periods=BusyPeriods(
            by_participant={
                "member@example.com": [
                    (
                        datetime(2099, 5, 12, 10, tzinfo=zone),
                        datetime(2099, 5, 12, 11, tzinfo=zone),
                    )
                ]
            },
            collective=[
                (
                    datetime(2099, 5, 12, 14, tzinfo=zone),
                    datetime(2099, 5, 12, 15, tzinfo=zone),
                )
            ],
        ),
    )
    at_ten = next(slot for slot in detailed if slot.start_at.hour == 10)
    at_fourteen = next(slot for slot in detailed if slot.start_at.hour == 14)
    assert at_ten.busy_participant_emails == ["member@example.com"]
    assert at_ten.collective_calendar_busy is False
    assert at_fourteen.busy_participant_emails == []
    assert at_fourteen.collective_calendar_busy is True


def test_working_window_uses_the_requested_local_timezone():
    opening, closing = working_window(date(2099, 5, 12), "Europe/Paris")
    assert opening.hour == 8
    assert closing.hour == 21
    assert opening.astimezone(timezone.utc).hour == 6
    assert closing.astimezone(timezone.utc).hour == 19

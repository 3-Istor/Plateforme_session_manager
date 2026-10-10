from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from backend.app import main
from backend.app.schemas import AvailabilityQuery, CalendarAvailability
from backend.app.services.availability import BusyPeriods, calendar_busy_blocks, working_window
from backend.tests.test_session_features import backend_context, add_busy_request, user, MEMBER, MANAGER, SECOND_MEMBER

PARIS = ZoneInfo("Europe/Paris")


def dt(hour, minute=0, day=12):
    return datetime(2099, 5, day, hour, minute, tzinfo=PARIS)


def test_busy_blocks_split_overlaps_and_deduplicate_sessions():
    busy = BusyPeriods(by_participant={
        MEMBER: [(dt(9), dt(11)), (dt(9), dt(11))],
        SECOND_MEMBER: [(dt(10), dt(12))],
    }, collective=[(dt(10, 30), dt(11, 30))])
    blocks = calendar_busy_blocks(busy, dt(8), dt(21))
    assert [(b.start_at, b.end_at, b.busy_participant_emails, b.collective_calendar_busy) for b in blocks] == [
        (dt(9), dt(10), [MEMBER], False),
        (dt(10), dt(10, 30), [MEMBER, SECOND_MEMBER], False),
        (dt(10, 30), dt(11), [MEMBER, SECOND_MEMBER], True),
        (dt(11), dt(11, 30), [SECOND_MEMBER], True),
        (dt(11, 30), dt(12), [SECOND_MEMBER], False),
    ]


def test_busy_blocks_clip_all_day_events_and_merge_adjacent_intervals():
    busy = BusyPeriods(by_participant={MEMBER: [
        (dt(0), dt(12)), (dt(12), dt(0, day=13)),
        (dt(9), dt(10)), (dt(1, day=13), dt(2, day=13)),
    ]})
    blocks = calendar_busy_blocks(busy, dt(8), dt(21))
    assert len(blocks) == 1
    assert (blocks[0].start_at, blocks[0].end_at) == (dt(8), dt(21))


def test_busy_blocks_preserve_real_minutes_without_rounding():
    busy = BusyPeriods(by_participant={MEMBER: [(dt(9, 7), dt(9, 22)), (dt(9, 40), dt(9, 55))]})
    blocks = calendar_busy_blocks(busy, dt(8), dt(21))
    assert [(b.start_at, b.end_at) for b in blocks] == [(dt(9, 7), dt(9, 22)), (dt(9, 40), dt(9, 55))]


@pytest.mark.parametrize("minutes", [15, 50, 60, 480])
def test_week_slots_match_existing_free_and_forced_searches(backend_context, minutes):
    db, settings = backend_context
    add_busy_request(db, 10, 11, MEMBER)
    query = AvailabilityQuery(day="2099-05-12", duration_minutes=minutes, participant_emails=[MEMBER])
    week = main.calendar_availability(query, db, user(MEMBER), settings)
    assert len(week.days) == 7
    assert week.days[0].day.weekday() == 0
    assert week.days[-1].day.weekday() == 6
    assert week.timezone == "Europe/Paris"
    day = next(d for d in week.days if d.day == query.day)
    assert day.slots == main.forced_availability(query, db, user(MEMBER), settings)
    normal = main.availability(query, db, user(MEMBER), settings)
    assert [(s.start_at, s.end_at) for s in day.slots if not s.busy_participant_emails and not s.collective_calendar_busy] == [(s.start_at, s.end_at) for s in normal]
    assert len(day.busy) == 1
    assert (day.busy[0].start_at, day.busy[0].end_at) == (dt(10), dt(11))
    serialized = CalendarAvailability.model_validate(week).model_dump_json()
    assert "Session existante" not in serialized
    assert "Ordre du jour" not in serialized
    assert "requester" not in serialized
    assert SECOND_MEMBER not in serialized


def test_week_reads_combined_calendars_once_and_reports_collective_busy(backend_context, monkeypatch):
    db, settings = backend_context
    calls = []
    def read(db, settings, emails, start, end):
        calls.append((emails, start, end))
        return BusyPeriods(collective=[(dt(14), dt(15))])
    monkeypatch.setattr(main, "combined_busy_periods", read)
    query = AvailabilityQuery(day="2099-05-12", duration_minutes=60, participant_emails=[MEMBER])
    week = main.calendar_availability(query, db, user(MEMBER), settings)
    assert len(calls) == 1
    assert calls[0][0] == [MEMBER]
    assert calls[0][1].hour == 8 and calls[0][2].hour == 21
    assert calls[0][2].date() - calls[0][1].date() == timedelta(days=6)
    day = next(d for d in week.days if d.day == query.day)
    assert day.busy[0].collective_calendar_busy
    assert day.busy[0].busy_participant_emails == []
    assert next(s for s in day.slots if s.start_at.hour == 14).collective_calendar_busy


def test_week_blocks_when_google_cannot_read_availability(backend_context, monkeypatch):
    db, settings = backend_context
    def unavailable(*args):
        raise HTTPException(502, "Agenda inaccessible")
    monkeypatch.setattr(main, "combined_busy_periods", unavailable)
    query = AvailabilityQuery(day="2099-05-12", duration_minutes=60, participant_emails=[MEMBER])
    with pytest.raises(HTTPException) as error:
        main.calendar_availability(query, db, user(MEMBER), settings)
    assert error.value.status_code == 502


@pytest.mark.parametrize("failure", [None, "missing", "denied"])
@pytest.mark.parametrize("view", ["new", "review", "edit"])
def test_delegates_calendar_blocks_new_week_view_and_reservation(backend_context, monkeypatch, failure, view):
    from fastapi import BackgroundTasks
    from backend.app.models import CalendarConnection
    from backend.app.schemas import SessionCreate
    from backend.app.services import user_calendar
    from backend.app.services.user_calendar import FREEBUSY_SCOPE
    from backend.tests.test_session_features import request_payload

    db, settings = backend_context
    original = main.create_request(SessionCreate(**{**request_payload(8, 9), "participant_emails": [MEMBER]}),
                                   BackgroundTasks(), db, user(MEMBER), settings) if view != "new" else None
    settings.auth_mode = "google"
    settings.google_availability_calendar_ids = ""
    for email in [MANAGER, MEMBER]:
        db.add(CalendarConnection(email=email, encrypted_refresh_token="test-only", scopes=FREEBUSY_SCOPE))
    db.commit()
    calls = []

    class GoogleFreeBusy:
        def __init__(self, email):
            self.email = email

        def freebusy(self):
            return self

        def query(self, *, body):
            ids = [item["id"] for item in body["items"]]
            calls.append((self.email, ids))
            self.calendars = {calendar_id: {"busy": []} for calendar_id in ids}
            if "deleguessigl@gmail.com" in ids:
                self.calendars["deleguessigl@gmail.com"]["busy"] = [
                    {"start": "2099-05-12T10:00:00Z", "end": "2099-05-12T11:00:00Z"}
                ]
                if failure == "missing":
                    del self.calendars["deleguessigl@gmail.com"]
                elif failure == "denied":
                    self.calendars["deleguessigl@gmail.com"] = {"errors": [{"reason": "forbidden"}]}
            return self

        def execute(self):
            return {"calendars": self.calendars}

    monkeypatch.setattr(user_calendar, "_credentials", lambda db, config, email, scope: email)
    monkeypatch.setattr(user_calendar, "build", lambda *args, credentials, **kwargs: GoogleFreeBusy(credentials))
    query = AvailabilityQuery(day="2099-05-12", duration_minutes=60, participant_emails=[MEMBER])
    def read_week():
        if view == "review":
            return main.request_calendar(original.id, query.day, db, user(MANAGER), settings)
        if view == "edit":
            return main.modification_calendar(original.id, query, db, user(MEMBER), settings)
        return main.calendar_availability(query, db, user(MEMBER), settings)
    if failure:
        with pytest.raises(HTTPException) as error:
            read_week()
        assert error.value.status_code == 502
        return

    week = read_week()
    assert calls == [(MEMBER, ["primary"]), (MANAGER, [settings.google_target_calendar_id, "deleguessigl@gmail.com"])]
    day = next(d for d in week.days if d.day == query.day)
    assert len(day.busy) == 1
    assert (day.busy[0].start_at, day.busy[0].end_at) == (dt(12), dt(13))
    assert day.busy[0].collective_calendar_busy
    assert day.busy[0].busy_participant_emails == []
    assert next(s for s in day.slots if s.start_at == dt(12)).collective_calendar_busy
    free = [s for s in day.slots if not s.busy_participant_emails and not s.collective_calendar_busy]
    assert not any(s.start_at < dt(13) and s.end_at > dt(12) for s in free)
    payload = {**request_payload(12, 13), "participant_emails": [MEMBER]}
    with pytest.raises(HTTPException) as occupied:
        main.create_request(SessionCreate(**payload), BackgroundTasks(), db, user(MEMBER), settings)
    assert occupied.value.status_code == 409
    forced = main.create_request(SessionCreate(**{**payload, "force": True}), BackgroundTasks(), db, user(MEMBER), settings)
    assert forced.is_forced
    assert forced.collective_calendar_busy
    assert forced.status.value == "pending"


def test_week_rejects_non_team_participants(backend_context):
    db, settings = backend_context
    query = AvailabilityQuery(day="2099-05-12", duration_minutes=60, participant_emails=["outsider@example.com"])
    with pytest.raises(HTTPException) as error:
        main.calendar_availability(query, db, user(MEMBER), settings)
    assert error.value.status_code == 422


@pytest.mark.parametrize("target", [date(2027, 3, 28), date(2027, 10, 31)])
def test_week_uses_paris_time_across_daylight_saving_changes(backend_context, monkeypatch, target):
    db, settings = backend_context
    opening, closing = working_window(target, "Europe/Paris")
    monkeypatch.setattr(main, "combined_busy_periods", lambda *args: BusyPeriods(collective=[(opening, closing)]))
    week = main.calendar_availability(AvailabilityQuery(day=target, duration_minutes=15, participant_emails=[MEMBER]), db, user(MEMBER), settings)
    day = next(d for d in week.days if d.day == target)
    assert day.busy[0].start_at.hour == 8
    assert day.busy[0].end_at.hour == 21
    assert all(s.collective_calendar_busy for s in day.slots)

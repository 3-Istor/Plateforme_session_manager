from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BusyPeriod = tuple[datetime, datetime]


@dataclass
class BusyPeriods:
    """Busy periods kept separate so forced slots can explain each conflict."""

    by_participant: dict[str, list[BusyPeriod]] = field(default_factory=dict)
    collective: list[BusyPeriod] = field(default_factory=list)

    @property
    def all(self) -> list[BusyPeriod]:
        return [
            *(period for periods in self.by_participant.values() for period in periods),
            *self.collective,
        ]

    def merge(self, other: "BusyPeriods") -> "BusyPeriods":
        for email, periods in other.by_participant.items():
            self.by_participant.setdefault(email, []).extend(periods)
        self.collective.extend(other.collective)
        return self

    def __len__(self) -> int:
        return len(self.all)


@dataclass(frozen=True)
class DetailedSlot:
    start_at: datetime
    end_at: datetime
    busy_participant_emails: list[str]
    collective_calendar_busy: bool


def timezone_for_name(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Paris")


def working_window(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = timezone_for_name(timezone_name)
    return (
        datetime.combine(day, time(8), tzinfo=zone),
        datetime.combine(day, time(21), tzinfo=zone),
    )


def periods_overlap(
    start_at: datetime,
    end_at: datetime,
    busy_period: BusyPeriod,
) -> bool:
    busy_start, busy_end = busy_period
    return start_at < busy_end and end_at > busy_start


def compute_detailed_slots(
    *,
    day: date,
    duration_minutes: int,
    timezone_name: str,
    busy_periods: BusyPeriods,
) -> list[DetailedSlot]:
    opening, closing = working_window(day, timezone_name)
    duration = timedelta(minutes=duration_minutes)
    increment = timedelta(minutes=30)
    now = datetime.now(opening.tzinfo)
    slots: list[DetailedSlot] = []
    cursor = opening
    while cursor + duration <= closing:
        slot_end = cursor + duration
        if cursor > now:
            busy_emails = sorted(
                email
                for email, periods in busy_periods.by_participant.items()
                if any(periods_overlap(cursor, slot_end, period) for period in periods)
            )
            slots.append(
                DetailedSlot(
                    start_at=cursor,
                    end_at=slot_end,
                    busy_participant_emails=busy_emails,
                    collective_calendar_busy=any(
                        periods_overlap(cursor, slot_end, period)
                        for period in busy_periods.collective
                    ),
                )
            )
        cursor += increment
    return slots


def compute_slots(
    *,
    day: date,
    duration_minutes: int,
    timezone_name: str,
    busy_periods: list[BusyPeriod],
) -> list[tuple[datetime, datetime]]:
    opening, closing = working_window(day, timezone_name)
    duration = timedelta(minutes=duration_minutes)
    increment = timedelta(minutes=30)
    now = datetime.now(opening.tzinfo)
    slots: list[tuple[datetime, datetime]] = []
    cursor = opening
    while cursor + duration <= closing:
        slot_end = cursor + duration
        overlaps = any(periods_overlap(cursor, slot_end, period) for period in busy_periods)
        if cursor > now and not overlaps:
            slots.append((cursor, slot_end))
        cursor += increment
    return slots

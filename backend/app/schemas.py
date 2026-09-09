from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .models import RequestStatus

SCHEDULE_TIMEZONE = "Europe/Paris"


class User(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    avatar_url: str | None = None
    is_manager: bool = False
    first_name: str = ""
    last_name: str = ""
    manager_status: str = "member"


class ProfileUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    request_manager: bool = False
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class RoleDecision(BaseModel):
    approve: bool


class GoogleCredentialIn(BaseModel):
    credential: str = Field(min_length=100, max_length=10000)


class Member(BaseModel):
    email: EmailStr
    name: str
    initials: str
    color: str


class AvailabilityQuery(BaseModel):
    day: date
    duration_minutes: int = Field(ge=30, le=480, multiple_of=30)
    participant_emails: list[EmailStr] = Field(min_length=1, max_length=20)
    timezone: str = "Europe/Paris"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != SCHEDULE_TIMEZONE:
            raise ValueError("La planification utilise le fuseau Europe/Paris")
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Fuseau horaire inconnu") from exc
        return normalized


class Slot(BaseModel):
    start_at: datetime
    end_at: datetime


class ForcedSlot(Slot):
    busy_participant_emails: list[str]
    collective_calendar_busy: bool


class SessionCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    session_type: str = Field(min_length=2, max_length=60)
    agenda: str = Field(min_length=10, max_length=4000)
    start_at: datetime
    end_at: datetime
    participant_emails: list[EmailStr] = Field(min_length=1, max_length=20)
    force: bool = False
    timezone: str = "Europe/Paris"

    @field_validator("title", "session_type", "agenda", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != SCHEDULE_TIMEZONE:
            raise ValueError("La planification utilise le fuseau Europe/Paris")
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Fuseau horaire inconnu") from exc
        return normalized

    @model_validator(mode="after")
    def validate_period(self):
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("Le fuseau horaire de début est obligatoire")
        if self.end_at.tzinfo is None or self.end_at.utcoffset() is None:
            raise ValueError("Le fuseau horaire de fin est obligatoire")
        if self.end_at <= self.start_at:
            raise ValueError("La fin doit être après le début")
        duration = (self.end_at - self.start_at).total_seconds() / 60
        if duration < 30 or duration > 480 or duration % 30:
            raise ValueError("La durée doit être un multiple de 30 minutes, entre 30 minutes et 8 heures")
        zone = ZoneInfo(self.timezone)
        local_start = self.start_at.astimezone(zone)
        local_end = self.end_at.astimezone(zone)
        if local_start.date() != local_end.date():
            raise ValueError("Le début et la fin doivent être le même jour")
        if any(
            value.minute not in {0, 30} or value.second != 0 or value.microsecond != 0
            for value in (local_start, local_end)
        ):
            raise ValueError("Le créneau doit commencer et finir sur une demi-heure")
        if local_start.time().replace(tzinfo=None) < time(8) or local_end.time().replace(tzinfo=None) > time(21):
            raise ValueError("Le créneau doit être compris entre 08:00 et 21:00")
        return self


class ParticipantOut(BaseModel):
    email: str
    model_config = ConfigDict(from_attributes=True)


class SessionOut(BaseModel):
    id: int
    requester_email: str
    requester_name: str
    title: str
    session_type: str
    agenda: str
    start_at: datetime
    end_at: datetime
    status: RequestStatus
    manager_note: str | None
    created_at: datetime
    participants: list[ParticipantOut]
    is_forced: bool
    busy_participant_emails: list[str]
    collective_calendar_busy: bool
    model_config = ConfigDict(from_attributes=True)


class DecisionIn(BaseModel):
    status: RequestStatus
    manager_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def decision_only(self):
        if self.status not in {RequestStatus.approved, RequestStatus.declined}:
            raise ValueError("La décision doit être approved ou declined")
        return self

    @field_validator("manager_note", mode="before")
    @classmethod
    def trim_note(cls, value):
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class NotificationOut(BaseModel):
    id: int
    title: str
    message: str
    request_id: int | None
    read_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CalendarStatus(BaseModel):
    connected: bool
    can_create_events: bool
    connected_emails: list[str]


class AuthorizationUrl(BaseModel):
    authorization_url: str


class LatenessUpdate(BaseModel):
    points: int = Field(ge=0, le=1_000_000)


class LatenessEntry(BaseModel):
    email: EmailStr
    name: str
    points: int
    updated_at: datetime | None = None

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from .models import RequestStatus


class User(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    avatar_url: str | None = None
    is_manager: bool = False


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


class Slot(BaseModel):
    start_at: datetime
    end_at: datetime


class SessionCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    session_type: str = Field(min_length=2, max_length=60)
    agenda: str = Field(min_length=10, max_length=4000)
    start_at: datetime
    end_at: datetime
    participant_emails: list[EmailStr] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_at <= self.start_at:
            raise ValueError("La fin doit être après le début")
        duration = (self.end_at - self.start_at).total_seconds() / 60
        if duration < 30 or duration > 480:
            raise ValueError("La durée doit être comprise entre 30 minutes et 8 heures")
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
    model_config = ConfigDict(from_attributes=True)


class DecisionIn(BaseModel):
    status: RequestStatus
    manager_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def decision_only(self):
        if self.status not in {RequestStatus.approved, RequestStatus.declined}:
            raise ValueError("La décision doit être approved ou declined")
        return self


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

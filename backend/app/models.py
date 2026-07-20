import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store UTC safely, including on SQLite which drops timezone metadata."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A timezone-aware datetime is required")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class RequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    declined = "declined"


class SessionRequest(Base):
    __tablename__ = "session_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_email: Mapped[str] = mapped_column(String(320), index=True)
    requester_name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160))
    session_type: Mapped[str] = mapped_column(String(60))
    agenda: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.pending, index=True)
    manager_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("session_requests.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))

    request: Mapped[SessionRequest] = relationship(back_populates="participants")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_email: Mapped[str] = mapped_column(String(320), index=True)
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CalendarConnection(Base):
    """Encrypted, revocable Google Calendar authorization for one member."""

    __tablename__ = "calendar_connections"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ManagedCalendar(Base):
    """Secondary calendar created and owned by the app for approved sessions."""

    __tablename__ = "managed_calendars"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

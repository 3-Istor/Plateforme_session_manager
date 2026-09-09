import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
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


class UserSession(Base):
    """Server-side login session. Only a SHA-256 hash of the browser token is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(50), default="")
    last_name: Mapped[str] = mapped_column(String(50), default="")
    manager_status: Mapped[str] = mapped_column(String(20), default="member")
    reviewed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


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
    force_record: Mapped["ForcedSession | None"] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )

    @property
    def is_forced(self) -> bool:
        return self.force_record is not None

    @property
    def busy_participant_emails(self) -> list[str]:
        if not self.force_record:
            return []
        return sorted(participant.email for participant in self.force_record.busy_participants)

    @property
    def collective_calendar_busy(self) -> bool:
        return bool(self.force_record and self.force_record.collective_calendar_busy)


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("session_requests.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))

    request: Mapped[SessionRequest] = relationship(back_populates="participants")


class ForcedSession(Base):
    """Marker stored separately so existing databases need no destructive migration."""

    __tablename__ = "forced_sessions"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("session_requests.id", ondelete="CASCADE"), primary_key=True
    )
    collective_calendar_busy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    request: Mapped[SessionRequest] = relationship(back_populates="force_record")
    busy_participants: Mapped[list["ForcedBusyParticipant"]] = relationship(
        back_populates="forced_session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ForcedBusyParticipant(Base):
    __tablename__ = "forced_busy_participants"
    __table_args__ = (
        UniqueConstraint("request_id", "email", name="uq_forced_busy_participant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("forced_sessions.request_id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))

    forced_session: Mapped[ForcedSession] = relationship(back_populates="busy_participants")


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
    """Manager/target pair whose write access was verified during OAuth."""

    __tablename__ = "managed_calendars"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LatenessRecord(Base):
    __tablename__ = "lateness_records"
    __table_args__ = (CheckConstraint("points >= 0", name="ck_lateness_points_non_negative"),)

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

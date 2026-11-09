from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored value is UTC, so treat them as such."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Role(StrEnum):
    PROFESSOR = "professor"
    STUDENT = "student"


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    EXCUSED = "excused"


# Statuses that mean the student was in class.
ATTENDED_STATUSES = (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CourseSection(Base):
    __tablename__ = "course_sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    professor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    professor: Mapped[User] = relationship()


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("section_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Sessions outside [enrolled_at, ended_at) don't count for this student. Null enrolled_at means "from the start".
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    opened_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=SessionStatus.OPEN)
    code_hash: Mapped[str] = mapped_column(Text)
    code_version: Mapped[int] = mapped_column(Integer, default=1)
    location_latitude: Mapped[float | None] = mapped_column(Float)
    location_longitude: Mapped[float | None] = mapped_column(Float)
    allowed_radius_meters: Mapped[int | None] = mapped_column(Integer)
    late_after_minutes: Mapped[int | None] = mapped_column(Integer)


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("attendance_session_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attendance_session_id: Mapped[str] = mapped_column(ForeignKey("attendance_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=AttendanceStatus.PRESENT)
    confirmation_method: Mapped[str] = mapped_column(String(20))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    qr_nonce: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    device_id: Mapped[str | None] = mapped_column(String(128))


class CodeAttempt(Base):
    __tablename__ = "attendance_code_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attendance_session_id: Mapped[str] = mapped_column(ForeignKey("attendance_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    result: Mapped[str] = mapped_column(String(30))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    device_id: Mapped[str | None] = mapped_column(String(128))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AbsenceRequest(Base):
    __tablename__ = "absence_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    attendance_session_id: Mapped[str | None] = mapped_column(ForeignKey("attendance_sessions.id"))
    reason: Mapped[str] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    attachment_path: Mapped[str | None] = mapped_column(String(64))
    attachment_name: Mapped[str | None] = mapped_column(String(255))
    attachment_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Message(Base):
    """One post in a newsletter thread. A thread's first message has parent_id None and thread_id == id."""

    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    # Sent to whole groups. audience_label keeps the names as they were at send time.
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    audience_label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    sender: Mapped[User] = relationship()


class MessageRecipient(Base):
    """Who received a message. Group sends are expanded here, so later joiners don't see earlier posts."""

    __tablename__ = "message_recipients"
    __table_args__ = (UniqueConstraint("message_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoteVisibility(StrEnum):
    """When students see the totals of a vote."""

    LIVE = "live"  # once they have voted
    AFTER_CLOSE = "after_close"
    PROFESSOR_ONLY = "professor_only"


class VoteStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class VoteKind(StrEnum):
    DATE = "date"  # options are dates and times
    TEXT = "text"  # options are short texts, such as project topics or yes/no


class Vote(Base):
    """A professor asks one or more classes to pick a date, or one of several written options."""

    __tablename__ = "votes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    professor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10), default=VoteKind.DATE)
    allow_multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    results_visibility: Mapped[str] = mapped_column(String(20), default=VoteVisibility.AFTER_CLOSE)
    status: Mapped[str] = mapped_column(String(20), default=VoteStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The option the professor set when closing the vote.
    chosen_option_id: Mapped[str | None] = mapped_column(String(36))


class VoteOption(Base):
    __tablename__ = "vote_options"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    vote_id: Mapped[str] = mapped_column(ForeignKey("votes.id"), index=True)
    # Order shown to students: by date for date votes, as written for text votes.
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Set on date votes.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on text votes.
    label: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(200))


class VoteGroup(Base):
    """A class whose students may vote."""

    __tablename__ = "vote_groups"
    __table_args__ = (UniqueConstraint("vote_id", "section_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    vote_id: Mapped[str] = mapped_column(ForeignKey("votes.id"), index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)


class VoteChoice(Base):
    """One option a student picked. Kept per student, also for anonymous votes, so they can change it."""

    __tablename__ = "vote_choices"
    __table_args__ = (UniqueConstraint("option_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    vote_id: Mapped[str] = mapped_column(ForeignKey("votes.id"), index=True)
    option_id: Mapped[str] = mapped_column(ForeignKey("vote_options.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Newsletter(Base):
    """A mailing list a professor runs, separate from any class."""

    __tablename__ = "newsletters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    owner: Mapped[User] = relationship()


class NewsletterSubscription(Base):
    __tablename__ = "newsletter_subscriptions"
    __table_args__ = (UniqueConstraint("newsletter_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    newsletter_id: Mapped[str] = mapped_column(ForeignKey("newsletters.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SectionSubscription(Base):
    """Receive a group's posts without being enrolled, so attendance and statistics are unaffected."""

    __tablename__ = "section_subscriptions"
    __table_args__ = (UniqueConstraint("section_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section_id: Mapped[str] = mapped_column(ForeignKey("course_sections.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

"""Votes on a date: a professor offers dates to one or more classes, students pick, the professor sets the date."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import (
    AuditLog,
    CourseSection,
    Enrollment,
    Notification,
    User,
    Vote,
    VoteChoice,
    VoteGroup,
    VoteOption,
    VoteStatus,
    VoteVisibility,
    as_utc,
)
from .schemas import StudentSummary, UtcDatetime
from .security import current_user, require_role

router = APIRouter(prefix="/api/v1/votes", tags=["votes"])


class OptionCreate(BaseModel):
    starts_at: datetime
    note: str | None = Field(default=None, max_length=200)


class VoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    section_ids: list[str] = Field(min_length=1, max_length=20)
    options: list[OptionCreate] = Field(min_length=2, max_length=20)
    allow_multiple: bool = False
    anonymous: bool = False
    results_visibility: VoteVisibility = VoteVisibility.AFTER_CLOSE


class ChoiceUpdate(BaseModel):
    option_ids: list[str] = Field(min_length=1, max_length=20)


class VoteClose(BaseModel):
    # Leave empty to take the date with the most votes (the earliest one on a tie).
    option_id: str | None = None


class GroupSummary(BaseModel):
    id: str
    name: str


class OptionResult(BaseModel):
    id: str
    starts_at: UtcDatetime
    note: str | None
    # None when the viewer may not see the totals yet.
    votes: int | None = None
    # Who picked this date: professors only, and only on named votes.
    voters: list[StudentSummary] | None = None


class VoteResponse(BaseModel):
    id: str
    title: str
    description: str | None
    allow_multiple: bool
    anonymous: bool
    results_visibility: str
    status: str
    created_at: UtcDatetime
    closed_at: UtcDatetime | None
    chosen_option_id: str | None
    groups: list[GroupSummary]
    options: list[OptionResult]
    results_visible: bool
    eligible: int | None = None
    voted: int | None = None
    # Professors only.
    not_voted: list[StudentSummary] | None = None
    # Students only: the options they picked.
    my_choice: list[str] | None = None


def summary(student: User) -> StudentSummary:
    return StudentSummary(id=student.id, email=student.email, first_name=student.first_name, last_name=student.last_name)


def eligible_students(db: Session, vote: Vote) -> list[User]:
    """Students actively enrolled in any of the vote's classes, each once."""
    section_ids = select(VoteGroup.section_id).where(VoteGroup.vote_id == vote.id)
    return db.scalars(
        select(User)
        .join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.section_id.in_(section_ids), Enrollment.status == "active")
        .distinct()
        .order_by(User.last_name, User.first_name)
    ).all()


def vote_options(db: Session, vote: Vote) -> list[VoteOption]:
    return db.scalars(select(VoteOption).where(VoteOption.vote_id == vote.id).order_by(VoteOption.starts_at)).all()


def counted_choices(db: Session, vote: Vote, eligible_ids: set[str]) -> list[tuple[str, str]]:
    """(option_id, student_id) pairs of students who can still vote; a student taken out of the class no longer counts."""
    rows = db.execute(select(VoteChoice.option_id, VoteChoice.student_id).where(VoteChoice.vote_id == vote.id)).all()
    return [(option_id, student_id) for option_id, student_id in rows if student_id in eligible_ids]


def results_visible_to_student(vote: Vote, has_voted: bool) -> bool:
    if vote.results_visibility == VoteVisibility.PROFESSOR_ONLY:
        return False
    if vote.status == VoteStatus.CLOSED:
        return True
    return vote.results_visibility == VoteVisibility.LIVE and has_voted


def vote_response(db: Session, vote: Vote, viewer: User) -> VoteResponse:
    eligible = eligible_students(db, vote)
    eligible_ids = {student.id for student in eligible}
    choices = counted_choices(db, vote, eligible_ids)
    voter_ids = {student_id for _, student_id in choices}
    options = vote_options(db, vote)
    groups = db.execute(
        select(CourseSection.id, CourseSection.name)
        .join(VoteGroup, VoteGroup.section_id == CourseSection.id)
        .where(VoteGroup.vote_id == vote.id)
        .order_by(CourseSection.name)
    ).all()
    response = VoteResponse(
        id=vote.id,
        title=vote.title,
        description=vote.description,
        allow_multiple=vote.allow_multiple,
        anonymous=vote.anonymous,
        results_visibility=vote.results_visibility,
        status=vote.status,
        created_at=vote.created_at,
        closed_at=vote.closed_at,
        chosen_option_id=vote.chosen_option_id,
        groups=[GroupSummary(id=section_id, name=name) for section_id, name in groups],
        options=[OptionResult(id=option.id, starts_at=option.starts_at, note=option.note) for option in options],
        results_visible=viewer.role == "professor",
    )
    if viewer.role == "student":
        response.my_choice = [option_id for option_id, student_id in choices if student_id == viewer.id]
        response.results_visible = results_visible_to_student(vote, viewer.id in voter_ids)
    if not response.results_visible:
        return response
    by_id = {student.id: student for student in eligible}
    for option in response.options:
        picked = [student_id for option_id, student_id in choices if option_id == option.id]
        option.votes = len(picked)
        if viewer.role == "professor" and not vote.anonymous:
            option.voters = sorted(
                (summary(by_id[student_id]) for student_id in picked), key=lambda person: (person.last_name, person.first_name)
            )
    response.eligible = len(eligible)
    response.voted = len(voter_ids)
    if viewer.role == "professor":
        response.not_voted = [summary(student) for student in eligible if student.id not in voter_ids]
    return response


def owned_vote(db: Session, vote_id: str, professor: User) -> Vote:
    vote = db.get(Vote, vote_id)
    if vote is None or vote.professor_id != professor.id:
        raise HTTPException(status_code=404, detail="Vote not found")
    return vote


def eligible_vote(db: Session, vote_id: str, student: User) -> Vote:
    vote = db.get(Vote, vote_id)
    if vote is None or student.id not in {person.id for person in eligible_students(db, vote)}:
        raise HTTPException(status_code=404, detail="Vote not found")
    return vote


def notify(db: Session, students: list[User], title: str, message: str) -> None:
    for student in students:
        db.add(Notification(user_id=student.id, title=title[:200], message=message))


def local_text(moment: datetime) -> str:
    """A date for notification text, in the department's time zone."""
    try:
        zone = ZoneInfo(get_settings().timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return as_utc(moment).strftime("%A %d %B %Y, %H:%M UTC")
    return as_utc(moment).astimezone(zone).strftime("%A %d %B %Y, %H:%M")


@router.get("", response_model=list[VoteResponse])
def list_votes(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[VoteResponse]:
    """Professors see the votes they started; students see the votes of the classes they are in."""
    if user.role == "professor":
        query = select(Vote).where(Vote.professor_id == user.id)
    else:
        sections = select(Enrollment.section_id).where(Enrollment.student_id == user.id, Enrollment.status == "active")
        query = select(Vote).where(Vote.id.in_(select(VoteGroup.vote_id).where(VoteGroup.section_id.in_(sections))))
    votes = db.scalars(query.order_by(Vote.created_at.desc())).all()
    return [vote_response(db, vote, user) for vote in votes]


@router.post("", response_model=VoteResponse, status_code=201)
def create_vote(
    payload: VoteCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> VoteResponse:
    section_ids = set(payload.section_ids)
    sections = db.scalars(select(CourseSection).where(CourseSection.id.in_(section_ids))).all()
    if len(sections) != len(section_ids) or any(section.professor_id != professor.id for section in sections):
        raise HTTPException(status_code=404, detail="Section not found")
    dates = [as_utc(option.starts_at) for option in payload.options]
    if any(moment <= datetime.now(timezone.utc) for moment in dates):
        raise HTTPException(status_code=422, detail="Every date must be in the future")
    if len(set(dates)) != len(dates):
        raise HTTPException(status_code=422, detail="The same date is offered twice")
    vote = Vote(
        professor_id=professor.id,
        title=payload.title.strip(),
        description=(payload.description or "").strip() or None,
        allow_multiple=payload.allow_multiple,
        anonymous=payload.anonymous,
        results_visibility=payload.results_visibility,
    )
    db.add(vote)
    db.flush()
    for option, moment in zip(payload.options, dates):
        db.add(VoteOption(vote_id=vote.id, starts_at=moment, note=(option.note or "").strip() or None))
    for section_id in section_ids:
        db.add(VoteGroup(vote_id=vote.id, section_id=section_id))
    db.flush()
    notify(
        db,
        eligible_students(db, vote),
        f"New vote: {vote.title}",
        f"{professor.first_name} {professor.last_name} asks you to pick a date. Open Votes in Roll Call to answer.",
    )
    db.add(AuditLog(actor_id=professor.id, action="vote_created", entity_type="vote", entity_id=vote.id))
    db.commit()
    return vote_response(db, vote, professor)


@router.get("/{vote_id}", response_model=VoteResponse)
def get_vote(vote_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> VoteResponse:
    vote = owned_vote(db, vote_id, user) if user.role == "professor" else eligible_vote(db, vote_id, user)
    return vote_response(db, vote, user)


@router.put("/{vote_id}/choice", response_model=VoteResponse)
def set_choice(
    vote_id: str,
    payload: ChoiceUpdate,
    student: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
) -> VoteResponse:
    """Pick dates. A new choice replaces the previous one until the vote closes."""
    vote = eligible_vote(db, vote_id, student)
    if vote.status != VoteStatus.OPEN:
        raise HTTPException(status_code=409, detail="This vote is closed")
    picked = set(payload.option_ids)
    if not picked <= {option.id for option in vote_options(db, vote)}:
        raise HTTPException(status_code=422, detail="Pick dates from this vote")
    if not vote.allow_multiple and len(picked) != 1:
        raise HTTPException(status_code=422, detail="Pick one date")
    db.execute(delete(VoteChoice).where(VoteChoice.vote_id == vote.id, VoteChoice.student_id == student.id))
    for option_id in picked:
        db.add(VoteChoice(vote_id=vote.id, option_id=option_id, student_id=student.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Your choice changed at the same time. Try again.") from exc
    return vote_response(db, vote, student)


@router.post("/{vote_id}/close", response_model=VoteResponse)
def close_vote(
    vote_id: str,
    payload: VoteClose,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> VoteResponse:
    """End the vote and set the date. Every student in the classes is told the date."""
    vote = owned_vote(db, vote_id, professor)
    if vote.status != VoteStatus.OPEN:
        raise HTTPException(status_code=409, detail="This vote is already closed")
    options = vote_options(db, vote)
    if payload.option_id is not None:
        chosen = next((option for option in options if option.id == payload.option_id), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="Pick a date from this vote")
    else:
        eligible = eligible_students(db, vote)
        counts = {option.id: 0 for option in options}
        for option_id, _ in counted_choices(db, vote, {student.id for student in eligible}):
            counts[option_id] += 1
        # options are sorted by date, so max() keeps the earliest of equal counts
        chosen = max(options, key=lambda option: counts[option.id])
    vote.status = VoteStatus.CLOSED
    vote.closed_at = datetime.now(timezone.utc)
    vote.chosen_option_id = chosen.id
    when = local_text(chosen.starts_at)
    notify(
        db,
        eligible_students(db, vote),
        f"Date set: {vote.title}",
        f"The date for {vote.title} is {when}" + (f" ({chosen.note})." if chosen.note else "."),
    )
    db.add(AuditLog(actor_id=professor.id, action="vote_closed", entity_type="vote", entity_id=vote.id))
    db.commit()
    return vote_response(db, vote, professor)


@router.delete("/{vote_id}", status_code=204)
def delete_vote(
    vote_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> None:
    vote = owned_vote(db, vote_id, professor)
    if vote.status == VoteStatus.OPEN:
        notify(db, eligible_students(db, vote), f"Vote cancelled: {vote.title}", "Your professor cancelled this vote.")
    db.execute(delete(VoteChoice).where(VoteChoice.vote_id == vote.id))
    db.execute(delete(VoteOption).where(VoteOption.vote_id == vote.id))
    db.execute(delete(VoteGroup).where(VoteGroup.vote_id == vote.id))
    db.delete(vote)
    db.add(AuditLog(actor_id=professor.id, action="vote_deleted", entity_type="vote", entity_id=vote_id))
    db.commit()

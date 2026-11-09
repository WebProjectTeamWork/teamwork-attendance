"""Newsletter: professors and students message each other, and professors post to whole groups.

A group is a course section. Replies to a group post go back to its author only, never to the whole group.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .db import get_db
from .models import (
    AuditLog,
    CourseSection,
    Enrollment,
    Message,
    MessageRecipient,
    Newsletter,
    NewsletterSubscription,
    Notification,
    Role,
    SectionSubscription,
    User,
)
from .schemas import UtcDatetime
from .security import current_user, require_role

router = APIRouter(prefix="/api/v1/messages", tags=["newsletter"])
subscriptions_router = APIRouter(prefix="/api/v1", tags=["newsletter"])

Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]

PREVIEW_LENGTH = 140
MAX_THREADS_LISTED = 200


class MessageCreate(BaseModel):
    """Send to one person (recipient_id), or to whole groups and newsletters."""

    recipient_id: str | None = None
    section_ids: list[str] = Field(default_factory=list, max_length=50)
    newsletter_ids: list[str] = Field(default_factory=list, max_length=50)
    subject: Subject
    body: Body

    @model_validator(mode="after")
    def one_audience(self) -> "MessageCreate":
        if bool(self.recipient_id) == bool(self.section_ids or self.newsletter_ids):
            raise ValueError("Choose one person, or at least one group or newsletter")
        return self


class ReplyCreate(BaseModel):
    body: Body


class SentResponse(BaseModel):
    id: str
    thread_id: str
    recipient_count: int


class ContactResponse(BaseModel):
    id: str
    name: str
    role: str
    classes: list[str]


class GroupResponse(BaseModel):
    id: str
    name: str
    student_ids: list[str]


class ContactsResponse(BaseModel):
    people: list[ContactResponse]
    # Empty for students: only professors post to groups and newsletters.
    groups: list[GroupResponse]
    newsletters: list[GroupResponse] = []


class ThreadSummary(BaseModel):
    id: str
    subject: str
    is_group: bool
    # The other side: a person's name, or the groups a post went to.
    counterpart: str
    preview: str
    last_sender: str
    last_at: UtcDatetime
    unread: int
    message_count: int


class MessageResponse(BaseModel):
    id: str
    sender_id: str
    sender_name: str
    sender_role: str
    mine: bool
    is_group: bool
    to: str
    body: str
    created_at: UtcDatetime
    # Only on the sender's own group posts.
    read_count: int | None = None
    recipient_count: int | None = None


class ThreadDetail(BaseModel):
    id: str
    subject: str
    is_group: bool
    audience: str
    can_reply: bool
    messages: list[MessageResponse]


class UnreadCount(BaseModel):
    unread: int


class NewsletterCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class NewsletterResponse(BaseModel):
    id: str
    name: str
    description: str
    subscriber_count: int


class DirectoryGroup(BaseModel):
    id: str
    name: str
    owner_name: str
    # Enrolled students always receive their group's posts, so there is nothing to subscribe to.
    member: bool
    subscribed: bool


class DirectoryNewsletter(BaseModel):
    id: str
    name: str
    description: str
    owner_name: str
    subscribed: bool


class DirectoryResponse(BaseModel):
    groups: list[DirectoryGroup]
    newsletters: list[DirectoryNewsletter]


def full_name(user: User) -> str:
    return f"{user.first_name} {user.last_name}"


def subscription_links(user: User) -> list:
    """Queries for the people linked to this user by a group or newsletter subscription."""
    if user.role == Role.PROFESSOR:
        return [
            select(User, CourseSection.name)
            .join(SectionSubscription, SectionSubscription.student_id == User.id)
            .join(CourseSection, CourseSection.id == SectionSubscription.section_id)
            .where(CourseSection.professor_id == user.id, User.is_active.is_(True)),
            select(User, Newsletter.name)
            .join(NewsletterSubscription, NewsletterSubscription.student_id == User.id)
            .join(Newsletter, Newsletter.id == NewsletterSubscription.newsletter_id)
            .where(Newsletter.owner_id == user.id, User.is_active.is_(True)),
        ]
    return [
        select(User, CourseSection.name)
        .join(CourseSection, CourseSection.professor_id == User.id)
        .join(SectionSubscription, SectionSubscription.section_id == CourseSection.id)
        .where(SectionSubscription.student_id == user.id, User.is_active.is_(True)),
        select(User, Newsletter.name)
        .join(Newsletter, Newsletter.owner_id == User.id)
        .join(NewsletterSubscription, NewsletterSubscription.newsletter_id == Newsletter.id)
        .where(NewsletterSubscription.student_id == user.id, User.is_active.is_(True)),
    ]


def contacts_of(db: Session, user: User, include_subscriptions: bool = False) -> dict[str, tuple[User, list[str]]]:
    """Everyone this user may message right now, with the classes they share.

    Subscribers can continue a conversation, but only people who share a class can start one.
    """
    if user.role == Role.PROFESSOR:
        query = (
            select(User, CourseSection.name)
            .join(Enrollment, Enrollment.student_id == User.id)
            .join(CourseSection, CourseSection.id == Enrollment.section_id)
            .where(CourseSection.professor_id == user.id)
        )
    else:
        query = (
            select(User, CourseSection.name)
            .join(CourseSection, CourseSection.professor_id == User.id)
            .join(Enrollment, Enrollment.section_id == CourseSection.id)
            .where(Enrollment.student_id == user.id)
        )
    rows = list(
        db.execute(
            query.where(Enrollment.status == "active", User.is_active.is_(True)).order_by(User.last_name, User.first_name, CourseSection.name)
        ).all()
    )
    if include_subscriptions:
        for link in subscription_links(user):
            rows.extend(db.execute(link).all())
    contacts: dict[str, tuple[User, list[str]]] = {}
    for person, class_name in rows:
        names = contacts.setdefault(person.id, (person, []))[1]
        if class_name not in names:
            names.append(class_name)
    return contacts


def group_members(db: Session, section_ids: list[str]) -> dict[str, list[str]]:
    """Who receives each section's posts: active students and subscribers, as {section_id: [student_id, ...]}."""
    members: dict[str, set[str]] = {section_id: set() for section_id in section_ids}
    rows = db.execute(
        select(Enrollment.section_id, Enrollment.student_id)
        .join(User, User.id == Enrollment.student_id)
        .where(Enrollment.section_id.in_(section_ids), Enrollment.status == "active", User.is_active.is_(True))
    ).all()
    subscribers = db.execute(
        select(SectionSubscription.section_id, SectionSubscription.student_id)
        .join(User, User.id == SectionSubscription.student_id)
        .where(SectionSubscription.section_id.in_(section_ids), User.is_active.is_(True))
    ).all()
    for section_id, student_id in [*rows, *subscribers]:
        members[section_id].add(student_id)
    return {section_id: sorted(students) for section_id, students in members.items()}


def newsletter_members(db: Session, newsletter_ids: list[str]) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {newsletter_id: [] for newsletter_id in newsletter_ids}
    rows = db.execute(
        select(NewsletterSubscription.newsletter_id, NewsletterSubscription.student_id)
        .join(User, User.id == NewsletterSubscription.student_id)
        .where(NewsletterSubscription.newsletter_id.in_(newsletter_ids), User.is_active.is_(True))
    ).all()
    for newsletter_id, student_id in rows:
        members[newsletter_id].append(student_id)
    return members


def enforce_send_limit(db: Session, user: User) -> None:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.message_window_minutes)
    sent = db.scalar(select(func.count(Message.id)).where(Message.sender_id == user.id, Message.created_at >= cutoff))
    if sent >= settings.message_limit_per_user:
        raise HTTPException(status_code=429, detail=f"You have sent too many messages. Wait {settings.message_window_minutes} minutes and try again.")


def deliver(
    db: Session,
    sender: User,
    subject: str,
    body: str,
    label: str,
    is_group: bool,
    recipient_ids: list[str],
    parent: Message | None = None,
) -> Message:
    """Add the message, one recipient row each, and a bell notification each. The caller commits."""
    message_id = str(uuid4())
    message = Message(
        id=message_id,
        thread_id=parent.thread_id if parent else message_id,
        parent_id=parent.id if parent else None,
        sender_id=sender.id,
        subject=subject,
        body=body,
        is_group=is_group,
        audience_label=label,
    )
    db.add(message)
    db.flush()
    title = f"New message from {full_name(sender)}"[:200]
    for recipient_id in recipient_ids:
        db.add(MessageRecipient(message_id=message_id, user_id=recipient_id))
        db.add(Notification(user_id=recipient_id, title=title, message=subject))
    return message


def owned_newsletters(db: Session, professor: User) -> list[Newsletter]:
    return list(db.scalars(select(Newsletter).where(Newsletter.owner_id == professor.id).order_by(Newsletter.name)).all())


def visible_condition(user: User):
    received = select(MessageRecipient.message_id).where(MessageRecipient.user_id == user.id)
    return or_(Message.sender_id == user.id, Message.id.in_(received))


def thread_summaries(db: Session, user: User, sent_box: bool) -> list[ThreadSummary]:
    messages = db.scalars(
        select(Message).options(selectinload(Message.sender)).where(visible_condition(user)).order_by(Message.created_at)
    ).all()
    unread_ids = set(
        db.scalars(select(MessageRecipient.message_id).where(MessageRecipient.user_id == user.id, MessageRecipient.read_at.is_(None))).all()
    )
    threads: dict[str, list[Message]] = {}
    for message in messages:
        threads.setdefault(message.thread_id, []).append(message)

    summaries = []
    for thread_id, items in threads.items():
        root, last = items[0], items[-1]
        started_by_me = root.sender_id == user.id
        belongs = started_by_me if sent_box else any(item.sender_id != user.id for item in items)
        if not belongs:
            continue
        summaries.append(
            ThreadSummary(
                id=thread_id,
                subject=root.subject,
                is_group=root.is_group,
                counterpart=root.audience_label if started_by_me else full_name(root.sender),
                preview=" ".join(last.body.split())[:PREVIEW_LENGTH],
                last_sender="You" if last.sender_id == user.id else full_name(last.sender),
                last_at=last.created_at,
                unread=sum(1 for item in items if item.id in unread_ids),
                message_count=len(items),
            )
        )
    summaries.sort(key=lambda summary: summary.last_at, reverse=True)
    return summaries[:MAX_THREADS_LISTED]


def visible_thread(db: Session, user: User, thread_id: str) -> list[Message]:
    messages = db.scalars(
        select(Message)
        .options(selectinload(Message.sender))
        .where(Message.thread_id == thread_id, visible_condition(user))
        .order_by(Message.created_at)
    ).all()
    if not messages:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages


@router.get("/contacts", response_model=ContactsResponse)
def contacts(user: User = Depends(current_user), db: Session = Depends(get_db)) -> ContactsResponse:
    people = [
        ContactResponse(id=person.id, name=full_name(person), role=person.role, classes=classes)
        for person, classes in contacts_of(db, user).values()
    ]
    groups: list[GroupResponse] = []
    newsletters: list[GroupResponse] = []
    if user.role == Role.PROFESSOR:
        sections = db.scalars(select(CourseSection).where(CourseSection.professor_id == user.id).order_by(CourseSection.name)).all()
        members = group_members(db, [section.id for section in sections])
        groups = [GroupResponse(id=section.id, name=section.name, student_ids=members[section.id]) for section in sections]
        owned = owned_newsletters(db, user)
        subscribers = newsletter_members(db, [item.id for item in owned])
        newsletters = [GroupResponse(id=item.id, name=item.name, student_ids=subscribers[item.id]) for item in owned]
    return ContactsResponse(people=people, groups=groups, newsletters=newsletters)


@router.get("/unread-count", response_model=UnreadCount)
def unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UnreadCount:
    count = db.scalar(select(func.count(MessageRecipient.id)).where(MessageRecipient.user_id == user.id, MessageRecipient.read_at.is_(None)))
    return UnreadCount(unread=count)


@router.get("/inbox", response_model=list[ThreadSummary])
def inbox(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ThreadSummary]:
    """Conversations in which someone else has written to this user."""
    return thread_summaries(db, user, sent_box=False)


@router.get("/sent", response_model=list[ThreadSummary])
def sent(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ThreadSummary]:
    """Conversations this user started."""
    return thread_summaries(db, user, sent_box=True)


@router.post("", response_model=SentResponse, status_code=201)
def send_message(payload: MessageCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> SentResponse:
    enforce_send_limit(db, user)
    if payload.section_ids or payload.newsletter_ids:
        if user.role != Role.PROFESSOR:
            raise HTTPException(status_code=403, detail="Only professors can post to a group or newsletter")
        section_ids = list(dict.fromkeys(payload.section_ids))
        newsletter_ids = list(dict.fromkeys(payload.newsletter_ids))
        sections = {
            section.id: section
            for section in db.scalars(
                select(CourseSection).where(CourseSection.id.in_(section_ids), CourseSection.professor_id == user.id)
            ).all()
        }
        if len(sections) != len(section_ids):
            raise HTTPException(status_code=404, detail="Section not found")
        newsletters = {item.id: item for item in owned_newsletters(db, user) if item.id in newsletter_ids}
        if len(newsletters) != len(newsletter_ids):
            raise HTTPException(status_code=404, detail="Newsletter not found")
        audiences = [*group_members(db, section_ids).values(), *newsletter_members(db, newsletter_ids).values()]
        recipient_ids = list(dict.fromkeys(student_id for audience in audiences for student_id in audience))
        if not recipient_ids:
            raise HTTPException(status_code=422, detail="Nobody would receive this yet. Choose a group with students or a newsletter with subscribers.")
        label = ", ".join([*(sections[section_id].name for section_id in section_ids), *(newsletters[newsletter_id].name for newsletter_id in newsletter_ids)])
        message = deliver(db, user, payload.subject, payload.body, label, True, recipient_ids)
        db.add(AuditLog(actor_id=user.id, action="group_message_sent", entity_type="message", entity_id=message.id))
    else:
        recipient_id = payload.recipient_id or ""
        contact = contacts_of(db, user).get(recipient_id)
        if contact is None:
            raise HTTPException(status_code=403, detail="You can only message people in your classes")
        recipient_ids = [recipient_id]
        message = deliver(db, user, payload.subject, payload.body, full_name(contact[0]), False, recipient_ids)
    db.commit()
    return SentResponse(id=message.id, thread_id=message.thread_id, recipient_count=len(recipient_ids))


@router.post("/{message_id}/reply", response_model=SentResponse, status_code=201)
def reply(message_id: str, payload: ReplyCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> SentResponse:
    """Answer someone else's message privately, or follow up your own message to the same people."""
    parent = db.scalar(select(Message).where(Message.id == message_id, visible_condition(user)))
    if parent is None:
        raise HTTPException(status_code=404, detail="Message not found")
    enforce_send_limit(db, user)
    allowed = contacts_of(db, user, include_subscriptions=True)
    if parent.sender_id == user.id:
        targets = db.scalars(select(MessageRecipient.user_id).where(MessageRecipient.message_id == parent.id)).all()
        label, is_group = parent.audience_label, parent.is_group
    else:
        targets = [parent.sender_id]
        label, is_group = full_name(parent.sender), False
    recipient_ids = [target for target in targets if target in allowed]
    if not recipient_ids:
        raise HTTPException(status_code=403, detail="This person is no longer in your classes")
    message = deliver(db, user, parent.subject, payload.body, label, is_group, recipient_ids, parent)
    db.commit()
    return SentResponse(id=message.id, thread_id=message.thread_id, recipient_count=len(recipient_ids))


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
def thread(thread_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ThreadDetail:
    messages = visible_thread(db, user, thread_id)
    mine_ids = [message.id for message in messages if message.sender_id == user.id]
    recipient_rows = (
        db.execute(select(MessageRecipient.message_id, MessageRecipient.user_id, MessageRecipient.read_at).where(MessageRecipient.message_id.in_(mine_ids))).all()
        if mine_ids
        else []
    )
    allowed = contacts_of(db, user, include_subscriptions=True)
    participants = {message.sender_id for message in messages if message.sender_id != user.id} | {row.user_id for row in recipient_rows}

    responses = []
    for message in messages:
        mine = message.sender_id == user.id
        read_count = recipient_count = None
        if mine and message.is_group:
            rows = [row for row in recipient_rows if row.message_id == message.id]
            recipient_count = len(rows)
            read_count = sum(1 for row in rows if row.read_at is not None)
        responses.append(
            MessageResponse(
                id=message.id,
                sender_id=message.sender_id,
                sender_name=full_name(message.sender),
                sender_role=message.sender.role,
                mine=mine,
                is_group=message.is_group,
                to="You" if not mine and not message.is_group else message.audience_label,
                body=message.body,
                created_at=message.created_at,
                read_count=read_count,
                recipient_count=recipient_count,
            )
        )
    root = messages[0]
    return ThreadDetail(
        id=thread_id,
        subject=root.subject,
        is_group=root.is_group,
        audience=root.audience_label,
        can_reply=any(person in allowed for person in participants),
        messages=responses,
    )


@router.post("/threads/{thread_id}/read", status_code=204)
def mark_thread_read(thread_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    messages = visible_thread(db, user, thread_id)
    rows = db.scalars(
        select(MessageRecipient).where(
            MessageRecipient.user_id == user.id,
            MessageRecipient.read_at.is_(None),
            MessageRecipient.message_id.in_([message.id for message in messages]),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.read_at = now
    db.commit()


@subscriptions_router.get("/newsletters", response_model=list[NewsletterResponse])
def list_newsletters(professor: User = Depends(require_role("professor")), db: Session = Depends(get_db)) -> list[NewsletterResponse]:
    owned = owned_newsletters(db, professor)
    subscribers = newsletter_members(db, [item.id for item in owned])
    return [
        NewsletterResponse(id=item.id, name=item.name, description=item.description, subscriber_count=len(subscribers[item.id]))
        for item in owned
    ]


@subscriptions_router.post("/newsletters", response_model=NewsletterResponse, status_code=201)
def create_newsletter(
    payload: NewsletterCreate,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> NewsletterResponse:
    newsletter = Newsletter(owner_id=professor.id, name=payload.name, description=payload.description)
    db.add(newsletter)
    db.commit()
    return NewsletterResponse(id=newsletter.id, name=newsletter.name, description=newsletter.description, subscriber_count=0)


@subscriptions_router.delete("/newsletters/{newsletter_id}", status_code=204)
def delete_newsletter(
    newsletter_id: str,
    professor: User = Depends(require_role("professor")),
    db: Session = Depends(get_db),
) -> None:
    """Past posts stay in everyone's mailbox; only the list and its subscriptions go."""
    newsletter = db.get(Newsletter, newsletter_id)
    if newsletter is None or newsletter.owner_id != professor.id:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    for subscription in db.scalars(select(NewsletterSubscription).where(NewsletterSubscription.newsletter_id == newsletter_id)).all():
        db.delete(subscription)
    db.delete(newsletter)
    db.commit()


@subscriptions_router.get("/subscriptions", response_model=DirectoryResponse)
def subscriptions(student: User = Depends(require_role("student")), db: Session = Depends(get_db)) -> DirectoryResponse:
    """Every group and newsletter a student can follow. Rosters are never included."""
    member_of = set(db.scalars(select(Enrollment.section_id).where(Enrollment.student_id == student.id, Enrollment.status == "active")).all())
    followed_groups = set(db.scalars(select(SectionSubscription.section_id).where(SectionSubscription.student_id == student.id)).all())
    followed_newsletters = set(db.scalars(select(NewsletterSubscription.newsletter_id).where(NewsletterSubscription.student_id == student.id)).all())
    sections = db.scalars(
        select(CourseSection)
        .options(selectinload(CourseSection.professor))
        .join(User, User.id == CourseSection.professor_id)
        .where(User.is_active.is_(True))
        .order_by(CourseSection.name)
    ).all()
    newsletters = db.scalars(
        select(Newsletter)
        .options(selectinload(Newsletter.owner))
        .join(User, User.id == Newsletter.owner_id)
        .where(User.is_active.is_(True))
        .order_by(Newsletter.name)
    ).all()
    return DirectoryResponse(
        groups=[
            DirectoryGroup(
                id=section.id,
                name=section.name,
                owner_name=full_name(section.professor),
                member=section.id in member_of,
                subscribed=section.id in followed_groups,
            )
            for section in sections
        ],
        newsletters=[
            DirectoryNewsletter(
                id=item.id,
                name=item.name,
                description=item.description,
                owner_name=full_name(item.owner),
                subscribed=item.id in followed_newsletters,
            )
            for item in newsletters
        ],
    )


def save_subscription(db: Session, subscription: SectionSubscription | NewsletterSubscription) -> None:
    """Subscribing twice, even at the same moment, is not an error."""
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


@subscriptions_router.put("/subscriptions/groups/{section_id}", status_code=204)
def subscribe_to_group(section_id: str, student: User = Depends(require_role("student")), db: Session = Depends(get_db)) -> None:
    if db.get(CourseSection, section_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    enrolled = db.scalar(select(Enrollment.id).where(Enrollment.section_id == section_id, Enrollment.student_id == student.id, Enrollment.status == "active"))
    if enrolled is not None:
        raise HTTPException(status_code=409, detail="You are in this group, so you already receive its posts")
    save_subscription(db, SectionSubscription(section_id=section_id, student_id=student.id))


@subscriptions_router.delete("/subscriptions/groups/{section_id}", status_code=204)
def unsubscribe_from_group(section_id: str, student: User = Depends(require_role("student")), db: Session = Depends(get_db)) -> None:
    subscription = db.scalar(select(SectionSubscription).where(SectionSubscription.section_id == section_id, SectionSubscription.student_id == student.id))
    if subscription is not None:
        db.delete(subscription)
        db.commit()


@subscriptions_router.put("/subscriptions/newsletters/{newsletter_id}", status_code=204)
def subscribe_to_newsletter(newsletter_id: str, student: User = Depends(require_role("student")), db: Session = Depends(get_db)) -> None:
    if db.get(Newsletter, newsletter_id) is None:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    save_subscription(db, NewsletterSubscription(newsletter_id=newsletter_id, student_id=student.id))


@subscriptions_router.delete("/subscriptions/newsletters/{newsletter_id}", status_code=204)
def unsubscribe_from_newsletter(newsletter_id: str, student: User = Depends(require_role("student")), db: Session = Depends(get_db)) -> None:
    subscription = db.scalar(
        select(NewsletterSubscription).where(NewsletterSubscription.newsletter_id == newsletter_id, NewsletterSubscription.student_id == student.id)
    )
    if subscription is not None:
        db.delete(subscription)
        db.commit()

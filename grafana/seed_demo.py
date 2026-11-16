"""Fill a development database with a semester of made-up attendance, to try the Grafana dashboard.

    docker compose exec api python -m grafana.seed_demo   (or locally: python -m grafana.seed_demo)

Refuses to run with APP_ENV=production. Every demo account has an @demo.example email and the password
"demo-password-123"; running it again does nothing.
"""

import random
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import (
    AbsenceRequest,
    AttendanceRecord,
    AttendanceSession,
    CourseSection,
    Enrollment,
    User,
)
from app.security import digest_code, hash_password, make_code

PASSWORD = "demo-password-123"
FIRST = ["Ana", "Mihai", "Ioana", "Andrei", "Elena", "Radu", "Maria", "Vlad", "Irina", "Bogdan", "Diana", "Paul",
         "Sara", "Tudor", "Alexia", "Matei", "Carla", "Stefan", "Daria", "Luca", "Bianca", "Petru", "Oana", "Sorin"]
LAST = ["Popescu", "Ionescu", "Dumitru", "Stan", "Georgescu", "Matei", "Constantin", "Marin", "Tudor", "Florea",
        "Barbu", "Nistor", "Dinu", "Lazar", "Ene", "Preda"]
# Class name, weekday (0 = Monday), hour.
CLASSES = [
    ("prof.popa", "Popa", "Web Applications - Group 1", 0, 10),
    ("prof.popa", "Popa", "Web Applications - Group 2", 2, 14),
    ("prof.radu", "Radu", "Databases - Group 1", 1, 8),
    ("prof.radu", "Radu", "Databases - Group 2", 3, 12),
]


def main() -> None:
    if get_settings().app_env == "production":
        raise SystemExit("Refusing to add demo data to a production database.")
    Base.metadata.create_all(bind=engine)
    rng = random.Random(42)
    db = SessionLocal()
    if db.scalar(select(User).where(User.email.like("%@demo.example"))):
        print("Demo data already present.")
        return
    password_hash = hash_password(PASSWORD)

    def user(email: str, first: str, last: str, role: str) -> User:
        account = User(email=email, password_hash=password_hash, first_name=first, last_name=last, role=role)
        db.add(account)
        return account

    professors: dict[str, User] = {}
    students = [user(f"student{i + 1}@demo.example", FIRST[i % len(FIRST)], LAST[i % len(LAST)], "student")
                for i in range(48)]
    # Each student has a habit: most come, a few skip often, some are often late.
    habit = {s.email: rng.choice([0.95, 0.9, 0.9, 0.85, 0.8, 0.75, 0.6, 0.45]) for s in students}
    lateness = {s.email: rng.choice([0.02, 0.05, 0.1, 0.25]) for s in students}
    db.flush()

    today = date.today()
    semester_start = today - timedelta(weeks=14)
    for index, (handle, last, name, weekday, hour) in enumerate(CLASSES):
        if handle not in professors:
            professors[handle] = user(f"{handle}@demo.example", "Prof.", last, "professor")
            db.flush()
        professor = professors[handle]
        section = CourseSection(name=name, professor_id=professor.id)
        db.add(section)
        db.flush()
        # 24 students per class; the two groups of a course share no students.
        members = students[(index % 2) * 24:(index % 2) * 24 + 24]
        joined_late = members[-2:]  # joined the class in week 4
        start = datetime.combine(semester_start, time(), timezone.utc)
        enrollments = {}
        for student in members:
            enrolled_at = start + timedelta(weeks=4) if student in joined_late else start - timedelta(days=7)
            enrollments[student.id] = Enrollment(section_id=section.id, student_id=student.id, enrolled_at=enrolled_at)
            db.add(enrollments[student.id])

        day = semester_start + timedelta(days=(weekday - semester_start.weekday()) % 7)
        week = 0
        while day < today:
            week += 1
            starts_at = datetime.combine(day, time(hour - 2), timezone.utc)  # stored in UTC
            session = AttendanceSession(
                section_id=section.id, opened_by=professor.id, session_date=day, starts_at=starts_at,
                expires_at=starts_at + timedelta(minutes=110), status="closed",
                code_hash=digest_code(make_code()), late_after_minutes=15,
            )
            if week == 7 and index == 1:
                session.status = "cancelled"  # a holiday; must not count
            db.add(session)
            db.flush()
            if session.status != "cancelled":
                # Attendance drifts down mid-semester and recovers before exams.
                mood = 1.0 - 0.12 * max(0, min(week - 5, 11 - week)) / 3
                for student in members:
                    if student in joined_late and week <= 4:
                        continue
                    roll = rng.random()
                    if roll < habit[student.email] * mood:
                        late = rng.random() < lateness[student.email]
                        minutes = rng.randint(16, 40) if late else rng.randint(0, 12)
                        db.add(AttendanceRecord(
                            attendance_session_id=session.id, student_id=student.id,
                            status="late" if late else "present",
                            confirmation_method=rng.choices(["code", "qr", "manual", "scan"], [45, 40, 10, 5])[0],
                            confirmed_at=starts_at + timedelta(minutes=minutes),
                        ))
                    elif roll > 0.97:
                        db.add(AttendanceRecord(
                            attendance_session_id=session.id, student_id=student.id, status="excused",
                            confirmation_method="absence_request", confirmed_at=starts_at + timedelta(days=1),
                        ))
                        db.add(AbsenceRequest(student_id=student.id, section_id=section.id,
                                              attendance_session_id=session.id, reason="Medical certificate",
                                              status="approved", created_at=starts_at + timedelta(hours=5)))
                    elif roll > 0.94 and today - day < timedelta(days=21):
                        db.add(AbsenceRequest(student_id=student.id, section_id=section.id,
                                              attendance_session_id=session.id, reason="I was sick",
                                              status="pending", created_at=starts_at + timedelta(hours=8)))
            day += timedelta(weeks=1)
    db.commit()
    print(f"Added {len(students)} students and {len(professors)} professors in {len(CLASSES)} classes. "
          f"Log in as prof.popa@demo.example / {PASSWORD}")


if __name__ == "__main__":
    main()

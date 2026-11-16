"""Read-only SQL views for attendance reports in Grafana (PostgreSQL only).

The views repeat the rules of the statistics endpoints in `main.py`, so Grafana and the app show the same numbers:

- cancelled sessions never count, and a session still taking attendance (open, before its end time) is left out
  until it ends;
- a student is expected at a session if they were enrolled when it started, or if they have a record for it
  (students visiting from another group);
- the attendance rate is (present + late) / (expected - excused).

Grafana connects as a separate login role that may read only the `reporting` schema. The views run with the
owner's rights, so that role never sees the underlying tables (password hashes, codes, IP addresses).
"""

import re

from sqlalchemy import text

from .config import get_settings
from .db import engine

SCHEMA = "reporting"

# Order matters: each view may use the ones before it.
VIEWS: dict[str, str] = {
    "sections": """
        SELECT cs.id AS section_id,
               cs.name AS section_name,
               p.id AS professor_id,
               p.first_name || ' ' || p.last_name AS professor_name,
               p.email AS professor_email
        FROM course_sections cs
        JOIN users p ON p.id = cs.professor_id
    """,
    "finished_sessions": """
        SELECT s.id AS session_id,
               s.section_id,
               sec.section_name,
               sec.professor_id,
               sec.professor_name,
               s.session_date,
               s.starts_at,
               s.expires_at,
               s.late_after_minutes
        FROM attendance_sessions s
        JOIN reporting.sections sec ON sec.section_id = s.section_id
        WHERE s.status <> 'cancelled'
          AND NOT (s.status = 'open' AND s.expires_at > now())
    """,
    # One row per student expected at a finished session.
    "student_session_status": """
        WITH expected AS (
            SELECT fs.session_id, e.student_id
            FROM reporting.finished_sessions fs
            JOIN enrollments e ON e.section_id = fs.section_id
            WHERE (e.enrolled_at IS NULL OR e.enrolled_at <= fs.starts_at)
              AND (e.ended_at IS NULL OR e.ended_at > fs.starts_at)
            UNION
            SELECT r.attendance_session_id, r.student_id
            FROM attendance_records r
            JOIN reporting.finished_sessions fs ON fs.session_id = r.attendance_session_id
        )
        SELECT fs.session_id,
               fs.section_id,
               fs.section_name,
               fs.professor_id,
               fs.professor_name,
               fs.session_date,
               fs.starts_at,
               u.id AS student_id,
               u.first_name || ' ' || u.last_name AS student_name,
               u.email AS student_email,
               COALESCE(r.status, 'absent') AS status,
               r.confirmation_method,
               r.confirmed_at,
               CASE WHEN r.confirmed_at IS NOT NULL
                    THEN round(CAST(EXTRACT(EPOCH FROM (r.confirmed_at - fs.starts_at)) / 60 AS numeric), 1)
               END AS minutes_after_start,
               COALESCE(r.status IN ('present', 'late'), false) AS attended,
               -- Students who were in the class when the session started (not visitors from other groups).
               EXISTS (
                   SELECT 1 FROM enrollments e
                   WHERE e.section_id = fs.section_id AND e.student_id = u.id
                     AND (e.enrolled_at IS NULL OR e.enrolled_at <= fs.starts_at)
                     AND (e.ended_at IS NULL OR e.ended_at > fs.starts_at)
               ) AS enrolled
        FROM expected x
        JOIN reporting.finished_sessions fs ON fs.session_id = x.session_id
        JOIN users u ON u.id = x.student_id
        LEFT JOIN attendance_records r
               ON r.attendance_session_id = x.session_id AND r.student_id = x.student_id
    """,
    # One row per finished session.
    "session_attendance": """
        SELECT session_id,
               section_id,
               section_name,
               professor_id,
               professor_name,
               session_date,
               starts_at,
               count(*) AS expected,
               count(*) FILTER (WHERE status = 'present') AS present,
               count(*) FILTER (WHERE status = 'late') AS late,
               count(*) FILTER (WHERE status = 'excused') AS excused,
               count(*) FILTER (WHERE status = 'absent') AS absent,
               count(*) FILTER (WHERE attended) AS attended,
               round(100.0 * count(*) FILTER (WHERE attended)
                     / NULLIF(count(*) - count(*) FILTER (WHERE status = 'excused'), 0), 2) AS attendance_percentage
        FROM reporting.student_session_status
        GROUP BY session_id, section_id, section_name, professor_id, professor_name, session_date, starts_at
    """,
    "absence_requests": """
        SELECT a.id AS request_id,
               a.section_id,
               sec.section_name,
               sec.professor_id,
               a.student_id,
               u.first_name || ' ' || u.last_name AS student_name,
               a.attendance_session_id,
               s.session_date,
               a.status,
               a.created_at,
               a.attachment_path IS NOT NULL OR a.attachment_url IS NOT NULL AS has_attachment
        FROM absence_requests a
        JOIN reporting.sections sec ON sec.section_id = a.section_id
        JOIN users u ON u.id = a.student_id
        LEFT JOIN attendance_sessions s ON s.id = a.attendance_session_id
    """,
}

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def ensure_reporting_views() -> None:
    """(Re)create the reporting views, and the Grafana login role when REPORTING_DB_PASSWORD is set."""
    if engine.dialect.name != "postgresql":
        return
    settings = get_settings()
    role = settings.reporting_db_user
    if not _ROLE_NAME.match(role):
        raise RuntimeError("REPORTING_DB_USER must be a lowercase SQL identifier")
    with engine.begin() as connection:
        # Only one app instance at a time rebuilds the views.
        connection.execute(text("SELECT pg_advisory_xact_lock(724501)"))
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        # Dropping first lets a view change its columns; grants are given again below.
        for name in reversed(VIEWS):
            connection.execute(text(f"DROP VIEW IF EXISTS {SCHEMA}.{name} CASCADE"))
        for name, query in VIEWS.items():
            connection.execute(text(f"CREATE VIEW {SCHEMA}.{name} AS {query}"))

        password = settings.reporting_db_password
        role_exists = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
        if password:
            # format(%L) quotes the password as a literal; ALTER/CREATE ROLE take no bind parameters.
            action = "ALTER" if role_exists else "CREATE"
            statement = connection.execute(
                text(f"SELECT format('{action} ROLE {role} LOGIN PASSWORD %L', CAST(:p AS text))"), {"p": password}
            ).scalar_one()
            connection.execute(text(statement))
            role_exists = True
        if role_exists:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            connection.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {role}'))
            connection.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}"))
            connection.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}"))
            # Grafana runs arbitrary SQL as this role: keep it out of the application tables.
            connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}"))

# Attendance App Database

This design supports professors opening class attendance and students confirming
attendance only with a valid, unexpired code or QR code.

## Core entities

- `users`: shared login and identity data; roles are `admin`, `professor`, or `student`.
- `student_profiles` and `professor_profiles`: role-specific information.
- `departments`, `courses`, and `course_sections`: academic structure.
- `enrollments`: active students registered in a section.
- `class_meetings`: recurring schedule and room information.
- `attendance_sessions`: one professor-created attendance event for a class.
- `attendance_records`: successful attendance confirmations.
- `attendance_code_attempts`: accepted and rejected code/QR submissions.
- `audit_logs`: immutable record of privileged changes.
- `absence_requests`: student-submitted requests for an excused absence.
- `notifications`: cancellation and system notifications for users.

## Attendance confirmation

1. A professor opens an `attendance_session`.
2. The system generates a temporary code and QR representation.
3. A student enters the code or scans the QR code.
4. The server checks that the session is open, the code is valid, the code is
   unexpired, and the student is actively enrolled.
5. Only after all checks pass, the system creates an `attendance_record`.

Plain codes must never be stored. Store a hash in `attendance_sessions.code_hash`
and compare it with a hash of the submitted value. QR payloads should be
short-lived and signed.

## Important constraints

```sql
UNIQUE (section_id, student_id)              -- one enrollment
UNIQUE (attendance_session_id, student_id)   -- one confirmation per session
```

Attendance creation and code-attempt logging should run in one database
transaction. Manual changes by professors or administrators should be recorded
in an audit log.

## Security requirements

### Authentication and authorization

- Hash passwords with Argon2id or a modern password-hashing service; never store
  plaintext passwords.
- Require verified institutional email or an administrator-approved account.
- Enforce server-side role-based access control on every endpoint. A professor
  may manage only their own sections; a student may submit attendance only for
  their own account.
- Use short-lived access tokens, refresh-token rotation, secure, HTTP-only,
  `SameSite` cookies, and HTTPS in production.
- Check authorization again when reading attendance reports, not only when
  loading the user interface.

### Code and QR protection

- Generate codes with a cryptographically secure random generator. Human codes
  should be sufficiently long and rate-limited; QR payloads should contain a
  signed session ID, expiry, version, and one-time nonce.
- Store no plaintext code. Use a keyed hash (HMAC) or a high-entropy random
  token hash with a server-side secret. Rotate the code when it is regenerated.
- Keep sessions short-lived, close them explicitly, and reject codes for closed,
  cancelled, or expired sessions.
- Rate-limit attempts per account, session, device, and IP. Return generic
  failure messages so attackers cannot enumerate valid sessions or students.
- Use the unique attendance constraint inside a transaction to prevent replay
  and duplicate confirmations.
- Never trust `student_id`, role, section ID, or timestamps supplied by the
  client; derive them from the authenticated account and server time.

### Privacy and abuse prevention

- Collect location only if the feature is enabled and clearly disclosed.
  Minimize precision and retention, and restrict access to authorized staff.
- Treat client coordinates as untrusted input. Compare them with the session
  location on the server using a radius calculation; do not expose the
  professor's exact coordinates to students.
- Encrypt data in transit and at rest. Do not log passwords, raw codes, QR
  tokens, or sensitive documents.
- Treat device and IP data as security metadata with a retention policy.
- Add CSRF protection for cookie-authenticated requests and validate all input.
- Use parameterized queries, output encoding, secure file-upload checks, and
  malware scanning for absence attachments.
- Keep `audit_logs` append-only and record actor, action, entity, timestamp,
  and before/after values for manual attendance changes.

### Operational safeguards

- Keep secrets in a secret manager or environment configuration, never in the
  repository.
- Use database least-privilege accounts, encrypted backups, migrations, and
  tested restore procedures.
- Monitor spikes in failed attempts, repeated device use, and unusual manual
  overrides. Alert without exposing student data in logs.

## Suggested initial tables

`users`, `student_profiles`, `professor_profiles`, `departments`, `courses`,
`course_sections`, `enrollments`, `class_meetings`, `attendance_sessions`,
`attendance_records`, `attendance_code_attempts`, and `audit_logs`.
`absence_requests` and `notifications` support excused absences and
professor-cancellation alerts. `messages` and `message_recipients` hold the
newsletter; group posts are expanded into one recipient row per student. `votes`,
`vote_options`, `vote_groups`, and `vote_choices` hold the votes (on dates or written
options); a choice row is one option a student picked.

The PlantUML files in this directory can be rendered with any PlantUML-compatible
editor or extension.

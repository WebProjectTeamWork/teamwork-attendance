# Teamwork Attendance (Roll Call) – Audit Report

Scope: the whole repository (`app/`, `tests/`, `docs/`, Docker/Compose config). Audit date: 2026-11-10. Branch `main`, clean working tree at start.

Written for: the project team, to decide what to fix before any real deployment.

## 1. Summary

| Area | Result |
|---|---|
| Automated tests | **282 passed, 0 failed** (153 s) |
| Statement coverage (backend) | **97 %** (1,799 statements, 53 missed) |
| Static security scan (Bandit) | **0 findings** in 3,088 LOC |
| Dependency vulnerabilities (pip-audit) | 1 advisory, dev-only (`pytest 8.4.2`, fixed in 9.0.3) |
| Lint (Ruff) | 204 findings, mostly low-value (see §6) |
| JavaScript syntax (`node --check`) | 4/4 files pass; **no JS tests exist** |
| Live probes against the running app | 10 checks; 5 confirmed weaknesses |

**Overall:** the core security design is sound (see §3). The main risks are **deployment configuration** (committed secrets and professor access code in `docker-compose.yml`), **missing HTTP security headers**, and **a few authentication/rate-limit behaviours** that can lock users out. No critical code-execution or injection issue was found.

## 2. Findings by severity

| Severity | Count | IDs |
|---|---|---|
| High | 1 | S-01 |
| Medium | 5 | S-02, S-03, S-04, S-05, D-01 |
| Low | 17 | S-06 – S-12, C-01, C-02, Q-01 – Q-07, Ops-01 |
| Info | 2 | S-13, Doc-01 |

## 3. What is working well

- **Passwords:** hashed with `pwdlib` (Argon2 recommended profile) — `app/security.py:16`.
- **Attendance codes** are stored only as HMAC-SHA256 digests and compared in constant time (`security.py:44-49`). The code alphabet avoids look-alike characters.
- **JWT:** algorithm pinned to HS256 on decode (`security.py:82`); user role and `is_active` are re-read from the database on each request (`security.py:86-88`), so role or deactivation changes take effect immediately.
- **Authorization:** every professor endpoint checks section/session ownership (`owned_section`, `owned_session`, inline checks). Students can only read their own absence requests and attachments. Route audit: 56 operations — 25 professor-only, 11 student-only, 15 signed-in with the role checked inside the handler, 5 public.
- **Browser session:** token in an `HttpOnly`, `SameSite=Strict` cookie (`main.py:308-315`), not in `localStorage`.
- **Professor sign-up** requires a shared access code (`main.py:252-256`). Verified live: missing or wrong code → 403.
- **Uploads:** size limit, file-signature check (magic bytes, not the browser's MIME type), random storage names (no path traversal), `nosniff` and `no-store` on download (`main.py:751-807`).
- **XSS:** the frontend builds DOM with `textContent` through an `el()` helper. The only `innerHTML` uses assign static icon markup or a server-generated QR SVG from our own URL (`app.js:50`, `app.js:1473`). No API value reaches `innerHTML`.
- **SQL:** all queries go through the SQLAlchemy ORM. The only raw SQL is DDL built from constants (`db.py:27-75`).
- **Production guard:** `check_production_settings()` refuses to start on development secrets when `APP_ENV=production` (`main.py:96-106`) — but see S-01: the Compose file never sets it.
- **Bandit:** zero findings at all severities.

## 4. Findings

### S-01 — Committed secrets and professor access code in Compose (High, conditional)

`docker-compose.yml` contains `JWT_SECRET` (line 20), `CODE_SECRET` (line 21), `PROFESSOR_SIGNUP_CODE: professor-dev-only` (line 24), a database password (line 7), and publishes port 8000 on all interfaces (line 29). `APP_ENV` is not set, so `check_production_settings()` (`main.py:96`, called at `main.py:111`) does nothing.

**Impact:** anyone who can read this repository and reach a deployment started from this file can register as a professor (`main.py:252-256`) and can forge JWTs and attendance QR tokens.

**Fix:** load secrets from an `.env` file or a secret store that is not committed; set `APP_ENV=production` in any non-local profile; bind to `127.0.0.1` for local use; rotate the values if this compose file was ever used with real data.

### S-02 — No HTTP security headers; third-party scripts without integrity (Medium)

Live check: `/health` and `/` return **no** `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, or `Strict-Transport-Security`. `index.html:13` loads `qrcode-generator` from cdnjs without an `integrity` attribute; fonts load from Google (`index.html:11`).

**Impact:** pages can be framed (clickjacking against professor actions such as closing a session); no CSP backstop if a script is compromised.

**Fix:** add middleware setting these headers (CSP with `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, HSTS when served over HTTPS); add SRI hashes to the CDN script, or self-host it.

### S-03 — Logout does not invalidate the token (Medium)

Live check: a Bearer token copied before `POST /api/v1/auth/logout` still returned **200** on `/api/v1/users/me` afterwards. Logout only deletes the cookie (`main.py:320-321`). The token stays valid for `ACCESS_TOKEN_MINUTES` (default 30).

**Fix:** short token lifetime plus a server-side denylist or a per-user "tokens valid after" timestamp checked in `current_user`.

### S-04 — Account-wide login lockout enables targeted denial of service (Medium)

`authenticate()` (`main.py:271-291`) counts failures **per email only**. Live check: 10 wrong passwords for an account → the **correct** password then returns **429** for 15 minutes. Anyone who knows a student's or professor's email address can lock them out of sign-in at will.

**Fix:** key the throttle on email **and** source IP (or add a backoff/CAPTCHA step), so a single attacker cannot lock out an account from every network.

### S-05 — Attendance IP limit can block a whole class behind shared Wi-Fi (Medium)

`attempt_limit_reached()` (`main.py:146-167`) allows 30 failures per IP per 10 minutes, and this check runs **before** the code is validated (`main.py:516`, code check at `main.py:530`). Students on one campus NAT share an IP. Six students each mistyping five codes can lock every classmate out for 10 minutes — including students who type the **correct** code. The per-device limit is trivially bypassed because `X-Device-Id` is client-supplied (documented in `docs/presenter-guide.md` §12).

**Fix:** do not let the IP limit gate correct codes; use a higher IP limit or a time-based backoff; consider lowering the cost of a mistyped code (e.g., show "wrong code" without counting toward the IP limit once the student is known).

### D-01 — Roster import merges students with the same name (Medium, data integrity)

`placeholder_email()` (`roster.py:84-89`) derives each student's account email from their name. Two different students with the same first and last name become **one account**. `import_roster` then skips the second enrollment because one already exists (`roster.py:144-149`). The second student cannot be enrolled or marked present. Attendance statistics for that class are then wrong.

**Fix:** use the department's student ID (if the CSV has one) as the key, or detect duplicate names and stop with a clear error for manual resolution.

### S-06 — Account enumeration (Low)

- Duplicate email → `409 Email already registered` (`main.py:257`; verified live).
- Enrollment by email returns `404 No student account with that email` (`main.py:359-360`), so any professor can test whether an address has a student account.
- Login skips the Argon2 check when the user does not exist (`main.py:286`), which is a timing side channel. This is from code reading; timing was not measured.

**Fix:** return generic responses where possible; always run a dummy hash verify for unknown emails.

### S-07 — No maximum password length (Low)

`UserCreate.password` has `min_length=12` and no maximum (`schemas.py:13`). Live check: a 4,096-character password was accepted (201). Argon2 cost grows with input size, so very long passwords are a cheap CPU-exhaustion vector.

**Fix:** `max_length` of 128–256.

### S-08 — Cookie `Secure` flag and CSRF defence rely on configuration (Low)

- `Secure` is set only when `request.url.scheme == "https"` (`main.py:313`). Behind a TLS-terminating reverse proxy without forwarded headers, the cookie is sent without `Secure`.
- There is no CSRF token. Cookie-authenticated POST/PUT/PATCH/DELETE rely entirely on `SameSite=Strict` (`main.py:314`). That is acceptable in current browsers, but any change to `Lax` would open CSRF.

**Fix:** trust forwarded headers only from the proxy and set `Secure` unconditionally in production; document that `SameSite=Strict` is load-bearing.

### S-09 — External "attachment URL" still accepted and rendered (Low)

`AbsenceRequestCreate.attachment_url` accepts any `http(s)` URL (`schemas.py:188`) and professors see it as a clickable link (`app.js:1735-1736`). This is a phishing and tracking path that bypasses the upload checks.

**Fix:** remove the field now that uploads exist (the upload endpoint is the supported path).

### S-10 — Uploaded PDFs served inline from the app's origin (Low)

`download_absence_attachment` sends PDFs with `Content-Disposition: inline` (`main.py:801-807`). Mitigations are in place (magic-byte check, `nosniff`, `no-store`), but a PDF viewer runs in the app's origin.

**Fix:** serve PDFs as `attachment`, or sandbox the viewer.

### S-11 — Docker image runs as root with no health check (Low)

`Dockerfile` has no `USER` directive and no `HEALTHCHECK`.

**Fix:** create an unprivileged user; add `HEALTHCHECK` on `/health`.

### S-12 — API documentation public in production (Low)

Live check: `/docs` and `/openapi.json` return **200** without authentication. This discloses every route and parameter.

**Fix:** disable `docs_url`/`openapi_url` when `APP_ENV=production`, or require auth.

### S-13 — Dev-only dependency advisory (Info)

`pip-audit` reports PYSEC-2026-1845 for `pytest 8.4.2` (UNIX `/tmp` directory pattern; fixed in 9.0.3). It is a test dependency only; runtime packages are clean.

### C-01 — Session date uses UTC; other code uses local time (Low)

`open_attendance` stores `session_date = datetime.now(timezone.utc).date()` (`main.py:426`). Notifications use `Europe/Bucharest` (`votes.py` `local_text`). `preview_scan` uses the server's local `date.today()` (`attendance_scan.py:113`). A session opened between midnight and 02:00–03:00 local time is recorded with the previous day's date.

**Fix:** use the configured time zone consistently.

### C-02 — Concurrent duplicate registration returns 500 (Low)

`register()` (`main.py:256-268`) has no `IntegrityError` handler around `commit()`, unlike `enroll_student`. Two simultaneous sign-ups with the same email produce a server error instead of 409.

### Q-01 — Monolithic `main.py` (Low, maintainability)

`app/main.py` is 1,232 lines with 48 functions: sessions, attendance, absences, statistics, notifications, and static mounting in one module. Feature routers exist for scans, votes, and newsletters, but the core attendance logic is still here.

**Fix:** split into `attendance.py`, `absence.py`, `statistics.py`, and `sections.py`.

### Q-02 — Complexity hotspots (Low)

Ruff C901 (threshold 10): `confirm_attendance` 13 (`main.py:500`), `match_names` 12 (`recognition.py:212`), `create_vote` 11 (`votes.py:229`).

### Q-03 — Repeated ownership checks (Low)

`owned_session()` exists (`main.py:220`) but `refresh_qr_token` (`main.py:459-461`), `rotate_code` (`main.py:478-480`), and `cancel_attendance` (`main.py:618-620`) repeat the same inline check. A future change to one copy may miss the others.

### Q-04 — Lint debt (Low)

Ruff (app + tests): 204 findings. Real ones:
- Unused imports: `main.py:3` (`date`), `main.py:11` (`status`), `tests/test_attendance.py:3`, `tests/test_ui_endpoints.py:3`.
- `status` name shadowing in `student_statistics` (`main.py:885`).
- `zip()` without `strict=` (`votes.py:247, 257`; `recognition.py:118`).

Not real: 147 × B008 (the FastAPI `Depends()` default idiom), and S105 hits on config defaults and test constants.

### Q-05 — No database migrations (Low)

Schema is created with `create_all()` plus a hand-written additive helper (`db.py:27-75`). Carried over from `PROBLEMS.md` item 14.

### Q-06 — Unbounded queries and N+1 patterns (Low, performance)

- `list_sections` runs three queries per section (`main.py:955-1004`).
- `thread_summaries` loads every visible message, then slices to 200 threads (`newsletter.py:307-310`).

Acceptable for one department; will degrade at scale.

### Q-07 — No frontend tests (Low)

About 3,275 lines of JavaScript (`app/static/*.js`) have only a syntax check. The UI is exercised through endpoint tests (`tests/test_ui_endpoints.py`), not in a browser.

### Ops-01 — Unpinned dependency installation in the image (Low)

`Dockerfile:8` runs `pip install .`, which ignores `uv.lock`. Builds of the same commit can resolve to different versions (carried over from `PROBLEMS.md` item 16).

### Doc-01 — `PROBLEMS.md` is stale (Info)

It describes an earlier state of the project and refers to a `frontend/` directory that does not exist here. Several items are already fixed (see §5). It should be updated or removed so the team does not chase closed items.

## 5. Status of the items in `PROBLEMS.md`

| # | Item | Status now |
|---|---|---|
| 1 | Anyone can create a professor account | **Fixed** — access code required (`main.py:252-256`). Residual: S-01. |
| 2 | Compose predictable secrets / demo accounts | **Open** — S-01. Demo seeding is gone (no `SEED_DEMO_DATA` in code). |
| 3 | No rate limiting | **Largely fixed** for login and attendance. Residual: S-04, S-05. |
| 4 | Open sessions counted as absences | **Fixed** (`main.py:915`, `main.py:862-867`). |
| 5 | Enrollment history | **Fixed** (`models.py:68-69`, `main.py:820`). |
| 6 | Token in `sessionStorage` | **Fixed** — HttpOnly cookie. |
| 7 | Client-reported GPS | **Open by design**; documented as a deterrent. |
| 8 | Approved absences don't affect stats | **Fixed** when the request names a session (`main.py:706-723`). |
| 9 | Attachments without upload boundary | **Mostly fixed** — upload endpoint. Residual: S-09, S-10. |
| 10 | Notifications can't be marked read | **Fixed** (`main.py:1216-1222`; bulk only). |
| 11 | `late` never assigned | **Fixed** (`main.py:559-561`), when the professor sets `late_after_minutes`. |
| 12 | Deprecated startup event | **Fixed** — lifespan (`main.py:109-114`). |
| 13 | Vite `__dirname` | **Not applicable** — no `frontend/` here. |
| 14 | No migrations | **Open** — Q-05. |
| 15 | Small test suite | **Fixed** — 282 tests, 97 % coverage. JS still untested (Q-07). |
| 16 | Broad dependency ranges | **Open** — Ops-01. |
| 17 | Stale docs | **Partly** — `presenter-guide.md` §12 is current; `PROBLEMS.md` is not (Doc-01). |

## 6. Test and tool results

**Pytest** (`pytest -p no:cacheprovider --cov=app`): 282 passed, 1 warning (Starlette deprecation for `httpx` in the test client).

| Module | Statements | Missed | Coverage |
|---|---|---|---|
| `newsletter.py` | 276 | 0 | 100 % |
| `security.py`, `schemas.py`, `models.py`, `config.py` | 213 | 0 | 100 % |
| `main.py` | 505 | 10 | 98 % |
| `votes.py` | 203 | 5 | 98 % |
| `attendance_scan.py` | 84 | 2 | 98 % |
| `db.py` | 27 | 2 | 93 % |
| `recognition.py` | 235 | 18 | 92 % |
| `roster.py` | 110 | 16 | 85 % |
| **Total** | **1,799** | **53** | **97 %** |

**Bandit** 1.x on `app/`: 0 issues (all severities and confidences).

**pip-audit** `--local`: 1 advisory, `pytest` (dev dependency), S-13.

**Ruff** (`app` + `tests`): 204 findings: 147 B008 (FastAPI idiom), 38 UP017, 8 I001, 5 RUF100, 4 F401, 1 PIE810, 1 F811. Complexity > 10: 3 functions.

**`node --check`** on `app.js`, `newsletter.js`, `scan.js`, `votes.js`: all pass.

**Live probes** (in-process `TestClient`, temporary database):

| Check | Result | Verdict |
|---|---|---|
| Professor sign-up without / with wrong code | 403 / 403 | Pass |
| Security headers on `/health` and `/` | none | Fail (S-02) |
| `/docs`, `/openapi.json` unauthenticated | 200 / 200 | Exposure (S-12) |
| Login cookie flags over HTTP | HttpOnly ✔, SameSite=Strict ✔, Secure ✘ (expected on http) | Pass with note (S-08) |
| Token after logout | 200 | Fail (S-03) |
| Correct password after 10 failures | 429 | Fail (S-04) |
| Duplicate email registration | 409 | Enumeration (S-06) |
| 4,096-character password | 201 | Note (S-07) |
| 6th wrong code by one student (per-student limit 5) | 429 | Pass |
| Correct code from a second student on the same IP, below IP limit | 200 | Pass (limit not reached) |
| Session statistics, cancelled and open sessions | covered by existing tests | Pass |

## 7. Recommended order

1. **S-01** — remove committed secrets, set `APP_ENV=production`, bind to localhost. Rotate values if ever deployed.
2. **S-04, S-05** — fix the lockout keying so correct codes and correct passwords are not blocked by others.
3. **S-02, S-03, S-12** — security headers, token revocation on logout, hide API docs in production.
4. **D-01** — roster homonym merge (data correctness).
5. **C-01, C-02, S-06, S-07, S-09** — smaller correctness and hardening items.
6. **Q-01 – Q-07, Ops-01** — refactoring, migrations, tests for the frontend, pinned dependencies.

## 8. Method and limits

- Read every backend module in full (`app/*.py`) and the frontend's security-relevant paths (API client, auth, confirmation flow, uploads, DOM sinks).
- Ran the existing suite and Bandit, Ruff, pip-audit, `node --check`.
- Enumerated routes via AST over all route decorators (includes routers mounted with `include_router`).
- Ran 10 live probes in-process. No network scanning, no load testing, no Docker build, no browser testing.
- Not verified: timing side channel (S-06) by measurement; behaviour behind a real reverse proxy; the Docker image itself.

Raw evidence: `audit/evidence/` (pytest log, coverage JSON, Bandit JSON, pip-audit JSON, ruff output, probe script).

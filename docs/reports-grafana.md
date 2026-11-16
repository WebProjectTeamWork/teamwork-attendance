# Attendance reports (Grafana)

A Grafana dashboard with attendance per class, session and student, read straight from the database.

## Run it locally

```bash
docker compose up --build
# optional: a semester of made-up data (refuses to run with APP_ENV=production)
docker compose run --rm -v ./grafana:/app/grafana api python -m grafana.seed_demo
```

Open <http://localhost:3000>, log in as `admin` / `grafana-dev-only`. The **Attendance overview** is the home
page.

## The three reports

| Report | For | What is on it |
|---|---|---|
| **Attendance overview** (home page) | all classes of the selected professors | headline numbers, trend with the target line, classes compared, students below target, every session, how and when students check in |
| **Class report** | one class | attendance register (one row per student, one cell per session), attendance per session, each week's split, students, absence requests |
| **Student report** | one student | running attendance against the target, each class, week-by-week history, missed sessions with their absence request |

Click a class name or bar to open its class report, or a student name for the student report. The settings and
time range come along. The buttons under the settings switch between the three reports.

## Settings

Every report has its settings at the top. Changing one updates every panel.

| Setting | Choices | Effect |
|---|---|---|
| Professor, Class | one or more | which classes are included |
| Target attendance | 50–90 %, or type a number | students under it are **Below target**, within 5 points **Close to target** |
| Late arrivals | Count as attended / Count as missed | whether a late check-in counts toward the rate |
| Excused absences | Leave out / Count as missed | leave out matches the app's statistics pages |
| Flag after | 1–8 sessions | students with fewer sessions show as **Too early to tell** and are never flagged |
| Trend by | Session / Week / Month | how the trend on the overview is grouped |
| Trend lines | All together / One per class / One per professor | split the trend into one line each |
| Time range (top right) | any | only sessions in this range count; **Change** compares with the same length just before |

With the default settings the numbers equal the app's statistics pages: cancelled sessions and sessions still
taking attendance are left out, a student is expected only if enrolled when the session started (or recorded
there as a visitor), and excused absences do not lower the rate.

To keep your own version of a report, change it and use **Save as**; the original stays as it is.

Colors mean the same everywhere: present blue, late amber, excused aqua, absent red. They were checked for
color-blind readers on both the light and dark Grafana themes, and every colored chart also has a legend or a
table with the same numbers.

## How it works

- At startup the app (re)creates SQL views in the `reporting` schema ([app/reporting.py](../app/reporting.py)):
  `sections`, `finished_sessions`, `student_session_status`, `session_attendance`, `absence_requests`.
- When `REPORTING_DB_PASSWORD` is set, the app also creates the login role `REPORTING_DB_USER`
  (default `grafana_reader`) that may read **only** those views. It cannot read the application tables
  (password hashes, codes, IP addresses, attachments).
- Grafana's data source and dashboard are provisioned from [grafana/](../grafana), so nothing is set up by hand.
- The reports are generated: edit `grafana/build_dashboard.py` and run `python grafana/build_dashboard.py`.
  Grafana picks up the new files in `grafana/dashboards/` within 30 seconds.

Anyone who logs into Grafana sees every class. Give Grafana accounts to staff only (Viewer role for professors).

## Production (Render + Neon + Grafana Cloud free)

1. In Render, set `REPORTING_DB_PASSWORD` on the web service to a long random value and redeploy. The app
   creates the `grafana_reader` role in Neon.
2. Create a free stack at <https://grafana.com> and add a **PostgreSQL** data source with UID `attendance-db`:
   host = your Neon host with `:5432`, database = `neondb`, user `grafana_reader`, the password from step 1,
   TLS/SSL mode `require`.
3. **Dashboards → New → Import**, and upload `overview.json`, `class.json` and `student.json` from
   `grafana/dashboards/`.

Or run the `grafana/grafana-oss` image anywhere with the files in `grafana/provisioning` and the
`REPORTING_DB_*` variables from `docker-compose.yml`.

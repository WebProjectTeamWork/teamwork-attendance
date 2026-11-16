"""Builds the attendance report dashboards in grafana/dashboards/.

Edit the panels here, then run:  python grafana/build_dashboard.py   (Grafana reloads them within 30 seconds)

Three linked reports share the same settings (variables), so a click carries them along:
  overview.json  every class of the selected professors
  class.json     one class: attendance register, sessions, students
  student.json   one student: progress, history per class, absences

Colors (validated with the dataviz palette checker on Grafana's light and dark panel surfaces, all pairs):
present blue, late amber, excused aqua, absent red. Single-series charts use the blue only.
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "grafana-postgresql-datasource", "uid": "attendance-db"}

PRESENT, LATE, EXCUSED, ABSENT = "#2a78d6", "#c98500", "#1baf7a", "#d03b3b"
SERIES = PRESENT  # one-series charts
REFERENCE = "#898781"  # the target line
GOOD, WARNING, CRITICAL, MUTED = "#0ca30c", "#fab219", "#d03b3b", "#898781"

SSS = "reporting.student_session_status"

# --- Report settings, used by every query ---------------------------------------------------------------------
ATTENDED = "(status = 'present' OR (status = 'late' AND '$late' = 'yes'))"
LEFT_OUT = "(status = 'excused' AND '$excused' = 'skip')"
TARGET = "CAST(NULLIF('$target', '') AS numeric)"
MIN_SESSIONS = "CAST(NULLIF('$min_sessions', '') AS integer)"


def rate(alias=None):
    sql = (f"round(100.0 * count(*) FILTER (WHERE {ATTENDED})"
           f" / NULLIF(count(*) - count(*) FILTER (WHERE {LEFT_OUT}), 0), 1)")
    return f'{sql} AS "{alias}"' if alias else sql


def standing(rate_sql=None, sessions_sql="count(*)"):
    """A word for where a student stands. Colors come from the value mapping, never alone."""
    r = rate_sql or rate()
    return (f"CASE WHEN {sessions_sql} < {MIN_SESSIONS} THEN 'Too early to tell'"
            f" WHEN {r} < {TARGET} THEN 'Below target'"
            f" WHEN {r} < {TARGET} + 5 THEN 'Close to target'"
            f" ELSE 'On track' END")


def share(status, alias):
    return f'round(100.0 * count(*) FILTER (WHERE status = \'{status}\') / count(*), 1) AS "{alias}"'


STATUS_CODE = "CASE status WHEN 'present' THEN 1 WHEN 'late' THEN 2 WHEN 'excused' THEN 3 ELSE 4 END"

# --- Panel building blocks ------------------------------------------------------------------------------------


def target(sql, fmt="table", ref="A"):
    return {"refId": ref, "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": sql.strip()}


def panel(kind, title, w, h, sql, fmt="table", description="", extra_targets=(), defaults=None, overrides=None,
          options=None, **extra):
    targets = [target(sql, fmt)] if sql else []
    targets += [target(s, f, chr(66 + i)) for i, (s, f) in enumerate(extra_targets)]
    return {
        "type": kind, "title": title, "description": description, "datasource": DS,
        "gridPos": {"w": w, "h": h}, "targets": targets,
        "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []},
        "options": options or {}, **extra,
    }


def fixed(color):
    return {"mode": "fixed", "fixedColor": color}


def by_name(name, *properties):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": k, "value": v} for k, v in properties]}


def stat(title, sql, unit="none", description="", decimals=0, w=4, size=34):
    return panel("stat", title, w, 4, sql, description=description,
                 defaults={"unit": unit, "decimals": decimals, "color": fixed("text"), "noValue": "–"},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False},
                          "colorMode": "none", "graphMode": "none", "textMode": "value", "justifyMode": "auto",
                          "text": {"valueSize": size},
                          "wideLayout": True, "showPercentChange": False})


def status_mapping():
    """present/late/excused/absent codes to their label and color."""
    names = [("1", "Present", PRESENT), ("2", "Late", LATE), ("3", "Excused", EXCUSED), ("4", "Absent", ABSENT)]
    return [{"type": "value", "options": {code: {"text": text, "color": color, "index": i}
                                          for i, (code, text, color) in enumerate(names)}}]


def standing_mapping():
    names = [("Below target", CRITICAL), ("Close to target", WARNING), ("On track", GOOD), ("Too early to tell", MUTED)]
    return [{"type": "value", "options": {text: {"color": color, "index": i} for i, (text, color) in enumerate(names)}}]


def standing_column(name="Standing"):
    return by_name(name, ("mappings", standing_mapping()), ("color", {"mode": "thresholds"}),
                   ("custom.cellOptions", {"type": "color-background", "mode": "basic"}), ("custom.width", 150))


def percent_column(name):
    return by_name(name, ("unit", "percent"), ("decimals", 1), ("custom.width", 120))


def hidden(name):
    return by_name(name, ("custom.hidden", True))


def link(name, title, url):
    return by_name(name, ("links", [{"title": title, "url": url}]))


SETTINGS_QUERY = "${late:queryparam}&${excused:queryparam}&${target:queryparam}&${min_sessions:queryparam}&${__url_time_range}"
STUDENT_URL = "/d/attendance-student/student-report?var-student=${__data.fields.student_id}&" + SETTINGS_QUERY
CLASS_URL = "/d/attendance-class/class-report?var-section=${__data.fields.section_id}&" + SETTINGS_QUERY


def table(title, w, h, sql, description="", overrides=(), sort=None):
    return panel("table", title, w, h, sql, description=description,
                 defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True},
                           "noValue": "–"},
                 overrides=list(overrides),
                 options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                          "sortBy": sort or []})


def target_line():
    """The target as a dashed reference series across the whole time range."""
    return (f"""
SELECT $__timeFrom()::timestamptz AS time, {TARGET} AS "Target"
UNION ALL SELECT $__timeTo()::timestamptz, {TARGET}""", "time_series")


def trend_panel(title, w, h, sql, description, split_colors=True):
    return panel(
        "timeseries", title, w, h, sql, fmt="time_series", description=description, extra_targets=[target_line()],
        defaults={"unit": "percent", "min": 0, "max": 100, "decimals": 1,
                  "color": {"mode": "palette-classic-by-name"} if split_colors else fixed(SERIES),
                  "custom": {"drawStyle": "line", "lineWidth": 2, "pointSize": 8, "showPoints": "always",
                             "spanNulls": True, "fillOpacity": 0, "axisSoftMin": 0, "gradientMode": "none",
                             "axisLabel": "", "lineInterpolation": "linear"}},
        overrides=[
            by_name("Attendance rate", ("color", fixed(SERIES))),
            by_name("Attendance so far", ("color", fixed(SERIES))),
            by_name("Target", ("color", fixed(REFERENCE)), ("custom.lineStyle", {"fill": "dash", "dash": [6, 4]}),
                    ("custom.lineWidth", 1), ("custom.showPoints", "never")),
        ],
        options={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}},
    )


def bars(title, w, h, sql, x, description="", unit="percent", horizontal=True, overrides=(), decimals=1):
    return panel(
        "barchart", title, w, h, sql, description=description,
        defaults={"unit": unit, "decimals": decimals, "min": 0, **({"max": 100} if unit == "percent" else {}),
                  "color": fixed(SERIES), "custom": {"fillOpacity": 85, "lineWidth": 0, "gradientMode": "none",
                                                     "axisSoftMin": 0}},
        # Values are printed on the bars, so the value axis is hidden; the category axis stays.
        overrides=[{"matcher": {"id": "byType", "options": "number"},
                    "properties": [{"id": "custom.axisPlacement", "value": "hidden"}]}, *overrides],
        options={"xField": x, "orientation": "horizontal" if horizontal else "vertical", "showValue": "always",
                 "groupWidth": 0.55, "barWidth": 0.7, "barRadius": 0.15, "stacking": "none",
                 "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "single"}, "xTickLabelMaxLength": 32, "fullHighlight": False},
    )


def breakdown(title, w, h, sql, x, description=""):
    """Present/late/excused/absent as one 100% stacked bar per row."""
    return panel(
        "barchart", title, w, h, sql, description=description,
        defaults={"unit": "percent", "decimals": 1, "min": 0, "max": 100,
                  "custom": {"fillOpacity": 90, "lineWidth": 1, "gradientMode": "none"}},
        overrides=[{"matcher": {"id": "byType", "options": "number"},
                    "properties": [{"id": "custom.axisPlacement", "value": "hidden"}]},
                   *[by_name(n, ("color", fixed(c))) for n, c in
                     (("Present", PRESENT), ("Late", LATE), ("Excused", EXCUSED), ("Absent", ABSENT))]],
        options={"xField": x, "orientation": "horizontal", "showValue": "never", "stacking": "normal",
                 "groupWidth": 0.55, "barWidth": 0.8, "barRadius": 0,
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "multi"}, "xTickLabelMaxLength": 32},
    )


def register(title, w, h, sql, description):
    """Grid of attendance states: one row per student (or class), one cell per session."""
    return panel(
        "status-history", title, w, h, sql, fmt="time_series", description=description,
        defaults={"mappings": status_mapping(), "color": {"mode": "thresholds"},
                  "thresholds": {"mode": "absolute", "steps": [{"color": MUTED, "value": None}]},
                  "custom": {"fillOpacity": 90, "lineWidth": 2}},
        options={"showValue": "never", "rowHeight": 0.85, "colWidth": 0.92,
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "single"}},
    )


def text_panel(title, w, h, content):
    return {"type": "text", "title": title, "gridPos": {"w": w, "h": h},
            "options": {"mode": "markdown", "content": content.strip()}}


# --- Variables (the report settings) ----------------------------------------------------------------------


def query_var(name, label, sql, multi=True, description=""):
    return {"name": name, "label": label, "description": description, "type": "query", "datasource": DS,
            "query": sql, "definition": sql, "refresh": 1, "sort": 0, "multi": multi, "includeAll": multi,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]} if multi else {}}


def choice_var(name, label, choices, default, description="", custom=False):
    """choices: [(shown text, value)]."""
    return {"name": name, "label": label, "description": description, "type": "custom",
            "query": ", ".join(f"{t} : {v}" for t, v in choices), "multi": False, "includeAll": False,
            "allowCustomValue": custom,
            "current": {"selected": True, "text": next(t for t, v in choices if v == default), "value": default},
            "options": [{"selected": v == default, "text": t, "value": v} for t, v in choices]}


PROFESSOR = query_var("professor", "Professor",
                      "SELECT professor_name AS __text, professor_id AS __value FROM reporting.sections "
                      "GROUP BY 1, 2 ORDER BY 1")
CLASSES = query_var("section", "Class",
                    "SELECT section_name AS __text, section_id AS __value FROM reporting.sections "
                    "WHERE professor_id IN ($professor) ORDER BY 1")
ONE_CLASS = {**query_var("section", "Class",
                         "SELECT section_name || ' (' || professor_name || ')' AS __text, section_id AS __value "
                         "FROM reporting.sections WHERE professor_id IN ($professor) ORDER BY 1", multi=False)}
TARGET_VAR = choice_var("target", "Target attendance", [(f"{v}%", str(v)) for v in (50, 60, 70, 75, 80, 85, 90)], "75",
                        "Students under this rate are flagged. You can also type your own number.", custom=True)
LATE_VAR = choice_var("late", "Late arrivals", [("Count as attended", "yes"), ("Count as missed", "no")], "yes",
                      "Whether a late check-in counts toward the attendance rate.")
EXCUSED_VAR = choice_var("excused", "Excused absences", [("Leave out", "skip"), ("Count as missed", "count")], "skip",
                         "Leave out: an excused absence does not lower the rate (the app's rule).")
MIN_VAR = choice_var("min_sessions", "Flag after", [(f"{v} session{'s' if v > 1 else ''}", str(v)) for v in (1, 2, 3, 4, 5, 6, 8)],
                     "3", "A student is flagged only after this many sessions, so one missed first class is not an alarm.")
BUCKET_VAR = choice_var("bucket", "Trend by", [("Session", "session"), ("Week", "week"), ("Month", "month")], "week")
SPLIT_VAR = choice_var("split", "Trend lines", [("All together", "all"), ("One per class", "class"),
                                                ("One per professor", "professor")], "all")
RULES = [TARGET_VAR, LATE_VAR, EXCUSED_VAR, MIN_VAR]


def dashboard(uid, title, description, variables, rows):
    """rows: [(row title or None, collapsed, [panels])]. Panels flow left to right, 24 columns wide."""
    panels, y, next_id = [], 0, 1

    def place(items, y0):
        nonlocal next_id
        x = 0
        row_h = 0
        y = y0
        for p in items:
            w, h = p["gridPos"]["w"], p["gridPos"]["h"]
            if x + w > 24:
                x, y, row_h = 0, y + row_h, 0
            p["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
            p["id"] = next_id
            next_id += 1
            x += w
            row_h = max(row_h, h)
        return y + row_h

    for title_, collapsed, items in rows:
        if title_ is not None:
            row = {"type": "row", "title": title_, "collapsed": collapsed, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                   "id": next_id, "panels": []}
            next_id += 1
            panels.append(row)
            y += 1
            if collapsed:
                place(items, y)
                row["panels"] = items
                continue
        y = place(items, y)
        panels.extend(items)

    return {
        "uid": uid, "title": title, "description": description, "tags": ["attendance"],
        "timezone": "browser", "editable": True, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
        "refresh": "", "liveNow": False, "fiscalYearStartMonth": 0, "weekStart": "monday",
        "time": {"from": "now-6M", "to": "now"},
        "timepicker": {"refresh_intervals": ["5m", "15m", "1h"]},
        "templating": {"list": variables},
        "links": [{"title": "Attendance reports", "type": "dashboards", "tags": ["attendance"], "asDropdown": False,
                   "includeVars": False, "keepTime": True, "targetBlank": False, "icon": "external link"}],
        "annotations": {"list": []},
        "panels": panels,
    }


# --- Overview -------------------------------------------------------------------------------------------------

F = f"section_id IN ($section) AND $__timeFilter(starts_at)"
PREVIOUS = ("section_id IN ($section) AND starts_at < $__timeFrom()::timestamptz AND starts_at >= "
            "$__timeFrom()::timestamptz - ($__timeTo()::timestamptz - $__timeFrom()::timestamptz)")


def change_vs_previous(where_now, where_before):
    return f"""
WITH now_ AS (SELECT {rate()} AS r FROM {SSS} WHERE {where_now}),
     before AS (SELECT {rate()} AS r FROM {SSS} WHERE {where_before})
SELECT CASE WHEN before.r IS NULL OR now_.r IS NULL THEN 'No earlier data'
            WHEN now_.r = before.r THEN 'No change'
            ELSE to_char(now_.r - before.r, 'SG990.0') || ' points' END AS "Change"
FROM now_, before"""


def students_table_sql(where, having=""):
    return f"""
SELECT student_id, section_id,
       student_name AS "Student", student_email AS "Email", section_name AS "Class",
       count(*) AS "Sessions",
       count(*) FILTER (WHERE status = 'absent') AS "Absent",
       count(*) FILTER (WHERE status = 'late') AS "Late",
       count(*) FILTER (WHERE status = 'excused') AS "Excused",
       {rate("Attendance")},
       {standing()} AS "Standing",
       max(starts_at) FILTER (WHERE status IN ('present', 'late')) AS "Last seen"
FROM {SSS}
WHERE {where}
GROUP BY student_id, student_name, student_email, section_id, section_name
{having}
ORDER BY "Attendance", "Absent" DESC, "Student\""""


STUDENT_TABLE_OVERRIDES = [
    hidden("student_id"), hidden("section_id"),
    link("Student", "Open the student report", STUDENT_URL),
    link("Class", "Open the class report", CLASS_URL),
    percent_column("Attendance"), standing_column(),
    by_name("Last seen", ("unit", "dateTimeFromNow"), ("custom.width", 130)),
    by_name("Email", ("custom.width", 220)), by_name("Class", ("custom.width", 220)),
    *[by_name(n, ("custom.width", 105), ("custom.filterable", False)) for n in ("Sessions", "Absent", "Late", "Excused")],
]

BELOW_TARGET = f"HAVING count(*) >= {MIN_SESSIONS} AND {rate()} < {TARGET}"

ABOUT = """
**What counts.** A session counts once it has ended; cancelled sessions never count. A student is expected at a
session if they were in the class when it started, or if they checked in there as a visitor from another group.

**Attendance rate** = sessions attended ÷ sessions expected. The settings at the top change the rule:

- *Late arrivals*: count a late check-in as attended (default) or as missed.
- *Excused absences*: leave them out of the rate (default, like the app) or count them as missed.
- *Target attendance*: students below it are marked **Below target**, within 5 points **Close to target**.
- *Flag after*: students with fewer sessions are shown as **Too early to tell**.

**Getting around.** Click a class name to open its class report, or a student name for the student report.
Your settings and time range come along. The links at the top right switch between the three reports.

**Your own version.** Change any panel, then use *Save as* to keep a personal copy. The original stays as is.
"""

overview = dashboard(
    "attendance-report", "Attendance overview",
    "Attendance across classes: trend, classes compared, students below target.",
    [PROFESSOR, CLASSES, *RULES, BUCKET_VAR, SPLIT_VAR],
    [
        (None, False, [
            stat("Attendance rate", f"SELECT {rate()} FROM {SSS} WHERE {F}", "percent", decimals=1,
                 description="Sessions attended out of sessions expected, with the rules picked at the top."),
            stat("Change", change_vs_previous(F, PREVIOUS), size=22,
                 description="Attendance rate compared with the same length of time just before the selected range."),
            stat("Sessions held", f"SELECT count(DISTINCT session_id) FROM {SSS} WHERE {F}",
                 description="Finished sessions; cancelled ones are not counted."),
            stat("Students", f"SELECT count(DISTINCT student_id) FROM {SSS} WHERE {F}"),
            stat("Below target", f"""
SELECT count(*) FROM (SELECT 1 FROM {SSS} WHERE {F} GROUP BY student_id, section_id {BELOW_TARGET}) s""",
                 description="Students below the target in at least one class (listed further down)."),
            stat("Requests waiting", f"""
SELECT count(*) FROM reporting.absence_requests WHERE section_id IN ($section) AND status = 'pending'""",
                 description="Requests not yet approved or rejected, whatever their date."),
        ]),
        ("Trend", False, [
            trend_panel("Attendance over time", 16, 9, f"""
SELECT CASE WHEN '$bucket' = 'session' THEN starts_at ELSE date_trunc('$bucket', starts_at) END AS time,
       CASE '$split' WHEN 'class' THEN section_name WHEN 'professor' THEN professor_name
            ELSE 'Attendance rate' END AS metric,
       {rate()} AS value
FROM {SSS} WHERE {F}
GROUP BY 1, 2 ORDER BY 1""",
                        "Use 'Trend by' and 'Trend lines' at the top to group by week or month, or split by class. "
                        "The dashed line is the target."),
            bars("By day of the week", 8, 9, f"""
SELECT trim(to_char(session_date, 'Day')) AS "Day", {rate("Attendance rate")}
FROM {SSS} WHERE {F}
GROUP BY 1, extract(isodow FROM session_date) ORDER BY extract(isodow FROM session_date)""",
                 "Day", "Attendance rate on each weekday that had sessions."),
        ]),
        ("Classes", False, [
            bars("Attendance by class", 12, 8, f"""
SELECT section_id, section_name AS "Class", {rate("Attendance rate")}
FROM {SSS} WHERE {F}
GROUP BY section_id, section_name ORDER BY 3 DESC""",
                 "Class", "Click a bar to open that class's report.",
                 overrides=[link("Attendance rate", "Open the class report", CLASS_URL)]),
            breakdown("What happened at each class", 12, 8, f"""
SELECT section_name AS "Class",
       {share("present", "Present")},
       {share("late", "Late")},
       {share("excused", "Excused")},
       {share("absent", "Absent")}
FROM {SSS} WHERE {F}
GROUP BY section_name ORDER BY section_name""",
                      "Class", "Share of expected check-ins that were on time, late, excused or missed."),
        ]),
        ("Students", False, [
            table("Students below target", 24, 9, students_table_sql(F, BELOW_TARGET),
                  "Students under the target attendance in a class. Click a name for their full history.",
                  [*STUDENT_TABLE_OVERRIDES, hidden("Standing")]),
            table("All students", 24, 12, students_table_sql(F),
                  "Every student per class. Use the column filters to narrow the list.",
                  STUDENT_TABLE_OVERRIDES),
        ]),
        ("Sessions", True, [
            table("Every session", 24, 12, f"""
SELECT section_id, starts_at AS "Session", section_name AS "Class",
       count(*) AS "Expected",
       count(*) FILTER (WHERE status = 'present') AS "Present",
       count(*) FILTER (WHERE status = 'late') AS "Late",
       count(*) FILTER (WHERE status = 'excused') AS "Excused",
       count(*) FILTER (WHERE status = 'absent') AS "Absent",
       {rate("Attendance")}
FROM {SSS} WHERE {F}
GROUP BY session_id, section_id, section_name, starts_at
ORDER BY starts_at DESC""", overrides=[
                hidden("section_id"), link("Class", "Open the class report", CLASS_URL),
                by_name("Session", ("unit", "dateTimeAsLocal")), percent_column("Attendance")]),
        ]),
        ("Check-ins", True, [
            bars("How students checked in", 12, 8, f"""
SELECT CASE confirmation_method WHEN 'code' THEN 'Typed the code' WHEN 'qr' THEN 'Scanned the QR code'
            WHEN 'manual' THEN 'Marked by the professor' WHEN 'scan' THEN 'Attendance sheet photo'
            WHEN 'absence_request' THEN 'Approved absence request' ELSE coalesce(confirmation_method, 'Other') END
       AS "Method",
       count(*) AS "Check-ins"
FROM {SSS} WHERE {F} AND confirmation_method IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC""", "Method", unit="none", decimals=0),
            bars("When students checked in", 12, 8, f"""
WITH b AS (
  SELECT CASE WHEN minutes_after_start < 5 THEN 1 WHEN minutes_after_start < 10 THEN 2
              WHEN minutes_after_start < 15 THEN 3 WHEN minutes_after_start < 20 THEN 4
              WHEN minutes_after_start < 30 THEN 5 ELSE 6 END AS k
  FROM {SSS} WHERE {F} AND status IN ('present', 'late') AND minutes_after_start IS NOT NULL)
SELECT (ARRAY['0–5 min', '5–10 min', '10–15 min', '15–20 min', '20–30 min', '30+ min'])[k] AS "Minutes after start",
       count(*) AS "Check-ins"
FROM b GROUP BY k ORDER BY k""", "Minutes after start", unit="none", decimals=0, horizontal=False,
                 description="Minutes between the start of the session and the check-in."),
        ]),
        ("About these numbers", True, [text_panel("How the report works", 24, 11, ABOUT)]),
    ],
)

# --- Class report ---------------------------------------------------------------------------------------------

FC = "section_id = '$section' AND $__timeFilter(starts_at)"
PREVIOUS_C = PREVIOUS.replace("section_id IN ($section)", "section_id = '$section'")

class_report = dashboard(
    "attendance-class", "Class report",
    "One class: attendance register, each session, and every student.",
    [PROFESSOR, ONE_CLASS, *RULES],
    [
        (None, False, [
            stat("Attendance rate", f"SELECT {rate()} FROM {SSS} WHERE {FC}", "percent", decimals=1),
            stat("Change", change_vs_previous(FC, PREVIOUS_C), size=22,
                 description="Compared with the same length of time just before the selected range."),
            stat("Sessions held", f"SELECT count(DISTINCT session_id) FROM {SSS} WHERE {FC}"),
            stat("Students", f"SELECT count(DISTINCT student_id) FROM {SSS} WHERE {FC}"),
            stat("Below target", f"""
SELECT count(*) FROM (SELECT 1 FROM {SSS} WHERE {FC} GROUP BY student_id {BELOW_TARGET}) s"""),
            stat("Typical check-in", f"""
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes_after_start)
FROM {SSS} WHERE {FC} AND status IN ('present', 'late')""", unit="m", decimals=0,
                 description="Median minutes after the start of the session."),
        ]),
        ("Register", False, [
            register("Attendance register", 24, 16, f"""
SELECT starts_at AS time, student_name AS metric, {STATUS_CODE} AS value
FROM {SSS} WHERE {FC}
ORDER BY 1, 2""", "One row per student, one cell per session. Hover a cell for the date and state. "
                  "Empty cells: the student was not in the class yet."),
        ]),
        ("Sessions", False, [
            trend_panel("Attendance per session", 14, 9, f"""
SELECT starts_at AS time, {rate("Attendance rate")}
FROM {SSS} WHERE {FC}
GROUP BY session_id, starts_at ORDER BY 1""", "The dashed line is the target.", split_colors=False),
            breakdown("What happened each week", 10, 9, f"""
SELECT to_char(date_trunc('week', session_date), 'DD Mon') AS "Week",
       {share("present", "Present")},
       {share("late", "Late")},
       {share("excused", "Excused")},
       {share("absent", "Absent")}
FROM {SSS} WHERE {FC}
GROUP BY date_trunc('week', session_date) ORDER BY date_trunc('week', session_date) DESC""", "Week"),
        ]),
        ("Students", False, [
            table("Students", 24, 12, students_table_sql(FC), "Click a name for the student's full history.",
                  [*STUDENT_TABLE_OVERRIDES, hidden("Class")], sort=[{"displayName": "Attendance", "desc": False}]),
        ]),
        ("Absence requests", True, [
            table("Absence requests", 24, 9, """
SELECT student_id, student_name AS "Student", session_date AS "For the session of",
       initcap(status) AS "Decision", created_at AS "Sent", has_attachment AS "Attachment"
FROM reporting.absence_requests
WHERE section_id = '$section' AND $__timeFilter(created_at)
ORDER BY status <> 'pending', created_at DESC""", "Waiting requests first.", [
                hidden("student_id"), link("Student", "Open the student report", STUDENT_URL),
                by_name("Sent", ("unit", "dateTimeAsLocal")),
                by_name("Decision", ("mappings", [{"type": "value", "options": {
                    "Pending": {"text": "Waiting", "color": WARNING, "index": 0},
                    "Approved": {"color": GOOD, "index": 1}, "Rejected": {"color": MUTED, "index": 2}}}]),
                        ("custom.cellOptions", {"type": "color-background", "mode": "basic"}), ("custom.width", 120)),
                by_name("Attachment", ("mappings", [{"type": "value", "options": {
                    "true": {"text": "Yes", "index": 0}, "false": {"text": "No", "index": 1}}}])),
            ]),
        ]),
    ],
)

# --- Student report -------------------------------------------------------------------------------------------

STUDENT = query_var("student", "Student",
                    f"SELECT student_name || ' (' || student_email || ')' AS __text, student_id AS __value "
                    f"FROM {SSS} GROUP BY 1, 2 ORDER BY 1", multi=False)
STUDENT_CLASSES = query_var("section", "Class",
                            f"SELECT section_name AS __text, section_id AS __value FROM {SSS} "
                            f"WHERE student_id = '$student' GROUP BY 1, 2 ORDER BY 1")
FS = "student_id = '$student' AND section_id IN ($section) AND $__timeFilter(starts_at)"


def count_stat(title, status, description=""):
    return stat(title, f"SELECT count(*) FILTER (WHERE status = '{status}') FROM {SSS} WHERE {FS}",
                description=description)


student_report = dashboard(
    "attendance-student", "Student report",
    "One student: progress against the target, history per class, absences.",
    [STUDENT, STUDENT_CLASSES, *RULES],
    [
        (None, False, [
            stat("Attendance rate", f"SELECT {rate()} FROM {SSS} WHERE {FS}", "percent", decimals=1),
            stat("Sessions", f"SELECT count(*) FROM {SSS} WHERE {FS}",
                 description="Sessions this student was expected at."),
            count_stat("Present", "present"),
            count_stat("Late", "late"),
            count_stat("Excused", "excused", "Absences with an approved request."),
            count_stat("Absent", "absent"),
        ]),
        ("Progress", False, [
            trend_panel("Attendance so far", 24, 8, f"""
SELECT starts_at AS time,
       round(100.0 * sum(attended) OVER w / NULLIF(sum(1 - left_out) OVER w, 0), 1) AS "Attendance so far"
FROM (SELECT starts_at, CASE WHEN {ATTENDED} THEN 1 ELSE 0 END AS attended,
             CASE WHEN {LEFT_OUT} THEN 1 ELSE 0 END AS left_out
      FROM {SSS} WHERE {FS}) s
WINDOW w AS (ORDER BY starts_at ROWS UNBOUNDED PRECEDING)
ORDER BY 1""", "The running attendance rate after each session. The dashed line is the target.",
                        split_colors=False),
            table("By class", 24, 5, students_table_sql(FS), overrides=[
                *STUDENT_TABLE_OVERRIDES, hidden("Student"), hidden("Email")]),
        ]),
        ("History", False, [
            register("Week by week, per class", 24, 7, f"""
SELECT date_trunc('week', starts_at) AS time, section_name AS metric, max({STATUS_CODE}) AS value
FROM {SSS} WHERE {FS}
GROUP BY 1, 2 ORDER BY 1, 2""", "One row per class, one cell per week. A week with two sessions shows the worse one."),
            table("Missed and excused sessions", 24, 9, f"""
SELECT s.starts_at AS "Session", s.section_name AS "Class", initcap(s.status) AS "State",
       coalesce(initcap(r.status), 'No request') AS "Absence request"
FROM {SSS} s
LEFT JOIN LATERAL (SELECT status FROM reporting.absence_requests a
                   WHERE a.student_id = s.student_id AND a.attendance_session_id = s.session_id
                   ORDER BY a.created_at DESC LIMIT 1) r ON true
WHERE s.student_id = '$student' AND s.section_id IN ($section) AND $__timeFilter(s.starts_at)
  AND s.status IN ('absent', 'excused')
ORDER BY s.starts_at DESC""", "Absences, newest first, with the state of any absence request.", [
                by_name("Session", ("unit", "dateTimeAsLocal")),
                by_name("State", ("mappings", [{"type": "value", "options": {
                    "Absent": {"color": ABSENT, "index": 0}, "Excused": {"color": EXCUSED, "index": 1}}}]),
                        ("custom.cellOptions", {"type": "color-background", "mode": "basic"}), ("custom.width", 110)),
                by_name("Absence request", ("mappings", [{"type": "value", "options": {
                    "Pending": {"text": "Waiting", "index": 0}}}])),
            ]),
        ]),
    ],
)

OUT.mkdir(exist_ok=True)
for name, board in (("overview", overview), ("class", class_report), ("student", student_report)):
    (OUT / f"{name}.json").write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT / name}.json")

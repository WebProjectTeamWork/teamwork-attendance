import os, sys, tempfile, json
from pathlib import Path
REPO = r"d:\uni\third\web\teamwork-attendance"
sys.path.insert(0, REPO)
d = Path(tempfile.mkdtemp(prefix="probe-"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{d.as_posix()}/p.db",
    "JWT_SECRET": "probe-jwt-secret-0123456789abcdef0123456789",
    "CODE_SECRET": "probe-code-secret-0123456789abcdef012345678",
    "PROFESSOR_SIGNUP_CODE": "probe-code",
    "UPLOAD_DIR": f"{d.as_posix()}/up",
})
from fastapi.testclient import TestClient
from app.main import app
PW = "correct-horse-battery"
out = {}
with TestClient(app) as c:
    # (a) security headers + (c) public docs
    r = c.get("/health")
    out["headers_health"] = {k: v for k, v in r.headers.items() if k.lower() in ("content-security-policy","x-frame-options","x-content-type-options","strict-transport-security","referrer-policy","permissions-policy")}
    out["docs_status_unauth"] = c.get("/docs").status_code
    out["openapi_status_unauth"] = c.get("/openapi.json").status_code
    r = c.get("/")
    out["static_index_headers"] = {k: v for k, v in r.headers.items() if k.lower() in ("content-security-policy","x-frame-options","x-content-type-options")}

    # accounts
    c.post("/api/v1/auth/register", json={"email":"prof@x.org","password":PW,"first_name":"P","last_name":"R","role":"professor","professor_code":"probe-code"})
    c.post("/api/v1/auth/register", json={"email":"stud@x.org","password":PW,"first_name":"S","last_name":"T","role":"student"})
    c.post("/api/v1/auth/register", json={"email":"victim@x.org","password":PW,"first_name":"V","last_name":"I","role":"student"})
    # (d) professor signup without code is refused
    out["prof_signup_without_code"] = c.post("/api/v1/auth/register", json={"email":"evil@x.org","password":PW,"first_name":"E","last_name":"V","role":"professor"}).status_code
    out["prof_signup_wrong_code"] = c.post("/api/v1/auth/register", json={"email":"evil2@x.org","password":PW,"first_name":"E","last_name":"V","role":"professor","professor_code":"nope"}).status_code

    # (e) cookie flags on browser login over http
    r = c.post("/api/v1/auth/login", data={"username":"stud@x.org","password":PW})
    sc = r.headers.get("set-cookie","")
    out["login_cookie_flags"] = {"httponly": "httponly" in sc.lower(), "samesite": "samesite=strict" in sc.lower(), "secure": "secure" in sc.lower().split("; ")}
    token = c.cookies.get("attendance_session")

    # (f) logout does not revoke a copied token
    c.post("/api/v1/auth/logout")
    c.cookies.clear()
    out["token_after_logout_status"] = c.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}).status_code

    # (g) account lockout: 10 wrong passwords block the right password
    for _ in range(10):
        c.post("/api/v1/auth/token", data={"username":"victim@x.org","password":"wrong-password-xx"})
    out["victim_correct_pw_after_10_fail"] = c.post("/api/v1/auth/token", data={"username":"victim@x.org","password":PW}).status_code

    # (h) email-enumeration: 404 vs success on enrollment is a professor-only view; register 409 leaks existence
    out["register_duplicate_email"] = c.post("/api/v1/auth/register", json={"email":"stud@x.org","password":PW,"first_name":"a","last_name":"b","role":"student"}).status_code
    # (i) password length upper bound: is there any max?
    out["password_4096_chars_register"] = c.post("/api/v1/auth/register", json={"email":"long@x.org","password":"a"*4096,"first_name":"a","last_name":"b","role":"student"}).status_code

    c.post("/api/v1/auth/register", json={"email":"vic2@x.org","password":PW,"first_name":"V","last_name":"Two","role":"student"})
    # (j) shared-IP availability: a wrong-code storm from one IP blocks a correct student on same IP
    c.cookies.clear()
    tok_prof = c.post("/api/v1/auth/token", data={"username":"prof@x.org","password":PW}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok_prof}"}
    sec = c.post("/api/v1/sections", json={"name":"Probe 1241EA"}, headers=H).json()["id"]
    ids = {}
    for em in ("stud@x.org","vic2@x.org"):
        ids[em] = c.post(f"/api/v1/sections/{sec}/enrollments", json={"email":em}, headers=H).status_code
    sess = c.post("/api/v1/attendance-sessions", json={"section_id": sec, "duration_minutes": 15}, headers=H).json()
    code = sess["code"]
    def login(em):
        return c.post("/api/v1/auth/token", data={"username":em,"password":PW}).json()["access_token"]
    t_stud = login("stud@x.org"); t_vic = login("vic2@x.org")
    # victim (same IP) submits 6 wrong codes -> per-student limit 5
    codes_victim = []
    for _ in range(6):
        codes_victim.append(c.post(f"/api/v1/attendance-sessions/{sess['id']}/confirm", json={"code":"WRONGCODE22"}, headers={"Authorization": f"Bearer {t_vic}", "X-Device-Id":"dev-v"}).status_code)
    out["victim_wrong_codes"] = codes_victim
    # now the stud (different student, same IP, correct code) is checked
    out["correct_student_correct_code_status"] = c.post(f"/api/v1/attendance-sessions/{sess['id']}/confirm", json={"code":code}, headers={"Authorization": f"Bearer {t_stud}", "X-Device-Id":"dev-s"}).status_code
    out["rate_limit_checked_before_code"] = "see status above"
    # (k) confirm with code lowercase + dashes works
    out["enroll_status"] = ids
print(json.dumps(out, indent=2, default=str))

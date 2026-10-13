import datetime as dt, hashlib, hmac, secrets, re
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import db, ai, fhir, seed

app = FastAPI(title="Health Copilot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
db.init(); seed.run()
bearer = HTTPBearer(auto_error=False)

def _password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310000).hex()

def require_session(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Please sign in to continue")
    digest = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    for session in db.all_("auth_sessions"):
        if hmac.compare_digest(session["token_hash"], digest):
            if dt.datetime.fromisoformat(session["expires_at"]) <= dt.datetime.now(dt.timezone.utc):
                db.delete("auth_sessions", session["id"], session["owner"])
                break
            return session["owner"]
    raise HTTPException(401, "Session is invalid or expired")

def require_owner(uid, session_uid):
    if uid != session_uid:
        raise HTTPException(403, "You cannot access another patient's data")

def _issue_session(uid):
    token = secrets.token_urlsafe(32)
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)
    db.put("auth_sessions", {"owner": uid, "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "expires_at": expires.isoformat()}, owner=uid)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat()}

def public_user(user_doc):
    return {key: value for key, value in user_doc.items() if key != "auth"}

def get_user_doc(uid):
    result = db.get("users", uid)
    if not result:
        raise HTTPException(404, "User not found")
    return result

# ---------- Users / ABHA (mock) ----------
class UserIn(BaseModel):
    name: str; age: int; gender: str = "unknown"; blood_group: str = "O+"; language: str = "English"
    lat: Optional[float] = None; lng: Optional[float] = None

class RegisterIn(BaseModel):
    name: str
    email: str
    password: str
    age: int = 0
    gender: str = "unknown"
    blood_group: str = "O+"
    language: str = "English"

class LoginIn(BaseModel):
    email: str
    password: str

@app.post("/api/auth/register")
def register(auth: RegisterIn):
    email = auth.email.strip().lower()
    if (not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254
            or len(auth.password) < 8 or len(auth.password) > 1024
            or not auth.name.strip() or len(auth.name.strip()) > 100 or auth.age < 0 or auth.age > 120
            or auth.language not in {"English", "Tamil", "Hindi"}):
        raise HTTPException(422, "Enter a valid email, name, age, and password with at least 8 characters")
    if auth.blood_group not in {"O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"}:
        raise HTTPException(422, "Select a valid blood group")
    if any(u.get("auth", {}).get("email") == email for u in db.all_("users")):
        raise HTTPException(409, "An account with this email already exists")
    salt = secrets.token_bytes(16)
    u = _create_user(UserIn(name=auth.name.strip(), age=auth.age, gender=auth.gender, blood_group=auth.blood_group, language=auth.language))
    u["auth"] = {"email": email, "salt": salt.hex(), "password_hash": _password_hash(auth.password, salt)}
    db.put("users", u)
    return {"user": public_user(u), **_issue_session(u["id"])}

@app.post("/api/auth/login")
def login(auth: LoginIn):
    email = auth.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(auth.password) > 1024:
        raise HTTPException(401, "Email or password is incorrect")
    u = next((item for item in db.all_("users") if item.get("auth", {}).get("email") == email), None)
    if not u:
        raise HTTPException(401, "Email or password is incorrect")
    credentials = u["auth"]
    salt = bytes.fromhex(credentials["salt"])
    if not hmac.compare_digest(_password_hash(auth.password, salt), credentials["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect")
    return {"user": public_user(u), **_issue_session(u["id"])}

@app.post("/api/auth/logout")
def logout(session_uid: str = Depends(require_session), credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    digest = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    session = next((s for s in db.all_("auth_sessions", session_uid)
                    if hmac.compare_digest(s["token_hash"], digest)), None)
    if session:
        db.delete("auth_sessions", session["id"], session_uid)
    return {"ok": True}

def _create_user(u: UserIn):
    import random
    abha = "-".join(["91"] + [f"{random.randint(0,9999):04d}" for _ in range(3)])
    doc = {"resourceType": "Patient", "name": [{"text": u.name}], "gender": u.gender,
           "identifier": [{"system": "https://healthid.abdm.gov.in", "value": abha, "type": {"text": "ABHA (mock)"}}],
           "extension": {"age": u.age, "blood_group": u.blood_group, "language": u.language,
                         "location": {"lat": u.lat, "lng": u.lng}, "donor_opt_in": False}}
    return db.put("users", doc)

@app.get("/api/users/{uid}")
def user(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    return public_user(get_user_doc(uid))

# ---------- Documents: upload -> vision OCR -> extraction -> FHIR -> summary ----------
@app.post("/api/upload")
async def upload(user_id: str = Form(...), language: str = Form("English"), file: UploadFile = File(...),
                 session_uid: str = Depends(require_session)):
    require_owner(user_id, session_uid)
    supported_types = {"application/pdf", "image/jpeg", "image/png"}
    media_type = (file.content_type or "").lower()
    if media_type not in supported_types:
        raise HTTPException(415, "Upload a PDF, JPEG, or PNG medical document")
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(422, "The uploaded file is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "The uploaded file exceeds the 10 MB limit")
    if (media_type == "application/pdf" and not content.startswith(b"%PDF-")
            or media_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff")
            or media_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n")):
        raise HTTPException(415, "The uploaded file does not match its declared file type")
    get_user_doc(user_id)
    ex = ai.extract(content, media_type)
    for lab in ex.get("lab_results", []):
        lab["warning"] = lab.get("flag") in ("low", "high", "critical")
    date = ex.get("date") or dt.date.today().isoformat()
    rec = {"resourceType": "DocumentReference", "status": "current", "type": {"text": ex.get("doc_type")},
           "subject": {"reference": f"Patient/{user_id}"}, "date": date, "extracted": ex,
           "fhir_bundle": fhir.to_bundle(user_id, ex), "summary": {language: ai.explain(ex, language)}}
    db.put("records", rec, owner=user_id)
    return rec

@app.get("/api/records/{uid}")
def records(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    return db.all_("records", uid)

@app.post("/api/records/{rid}/translate")
def translate(rid: str, language: str, session_uid: str = Depends(require_session)):
    r = db.get("records", rid)
    if not r or r.get("subject", {}).get("reference") != f"Patient/{session_uid}":
        raise HTTPException(404, "Record not found")
    r["summary"][language] = ai.explain(r["extracted"], language)
    return db.put("records", r)

@app.get("/api/timeline/{uid}")
def timeline(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    ev = []
    for r in db.all_("records", uid):
        e = r["extracted"]
        ev.append({"date": r["date"], "type": r["type"]["text"], "doctor": e.get("doctor"),
                   "diagnoses": e.get("diagnoses", []), "abnormal": [l for l in e.get("lab_results", []) if l.get("warning")],
                   "medicines": [m["name"] for m in e.get("medicines", [])], "record_id": r["id"]})
    return sorted(ev, key=lambda x: x["date"])

@app.get("/api/trends/{uid}/{test}")
def trend(uid: str, test: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    pts = [{"date": r["date"], "value": l["value"], "unit": l.get("unit")}
           for r in db.all_("records", uid) for l in r["extracted"].get("lab_results", [])
           if test.lower() in l["test"].lower() and l.get("value") is not None]
    return sorted(pts, key=lambda x: x["date"])

@app.get("/api/fhir/{uid}")
def export_fhir(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    entries = [{"resource": public_user(get_user_doc(uid))}]
    for r in db.all_("records", uid): entries += r["fhir_bundle"]["entry"]
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}

# ---------- Schedules & diet ----------
@app.get("/api/schedule/{uid}")
def schedule(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    return db.all_("schedules", uid)

class ScheduleIn(BaseModel):
    kind: str
    title: str
    time: str
    details: str = ""

def validate_schedule_time(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise HTTPException(422, "Time must use 24-hour HH:MM format")

def validate_schedule(item):
    if item.kind not in {"medicine", "food", "hydration", "activity"}:
        raise HTTPException(422, "Schedule type must be medicine, food, hydration, or activity")
    if not item.title.strip() or len(item.title) > 120 or len(item.details) > 300:
        raise HTTPException(422, "Enter a reminder name up to 120 characters; notes may be up to 300 characters")
    validate_schedule_time(item.time)

@app.post("/api/schedule/{uid}")
def add_schedule(uid: str, item: ScheduleIn, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    validate_schedule(item)
    return db.put("schedules", {"resourceType": "Reminder", "kind": item.kind, "medicine": item.title,
        "title": item.title, "times": [item.time], "details": item.details, "taken_log": [],
        "start": dt.date.today().isoformat(), "end": "2099-12-31"}, owner=uid)

@app.put("/api/schedule/{sid}")
def update_schedule(sid: str, item: ScheduleIn, session_uid: str = Depends(require_session)):
    existing = next((s for s in db.all_("schedules", session_uid) if s["id"] == sid), None)
    if not existing:
        raise HTTPException(404, "Schedule not found")
    validate_schedule(item)
    existing.update({"kind": item.kind, "title": item.title, "medicine": item.title,
        "times": [item.time], "details": item.details})
    return db.put("schedules", existing, owner=session_uid)

@app.delete("/api/schedule/{sid}")
def delete_schedule(sid: str, session_uid: str = Depends(require_session)):
    if not db.delete("schedules", sid, session_uid):
        raise HTTPException(404, "Schedule not found")
    return {"ok": True}

@app.post("/api/schedule/{sid}/taken")
def taken(sid: str, time: str, session_uid: str = Depends(require_session)):
    s = next((item for item in db.all_("schedules", session_uid) if item["id"] == sid), None)
    if not s: raise HTTPException(404, "Schedule not found")
    s["taken_log"].append({"time": time, "at": dt.datetime.now().isoformat()})
    return db.put("schedules", s)

@app.get("/api/diet/{uid}")
def diet(uid: str, language: str = "English", session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    recs = db.all_("records", uid)
    conds = {d for r in recs for d in r["extracted"].get("diagnoses", [])}
    conds |= {f'{l["test"]} {l["flag"]}' for r in recs for l in r["extracted"].get("lab_results", []) if l.get("warning")}
    return {"conditions": list(conds), "plan": ai.diet_plan(list(conds), language)}

# ---------- Doctors & emergency home care ----------
@app.get("/api/doctors")
def doctors(specialty: str = "", city: str = ""):
    return [d for d in db.all_("doctors")
            if (not specialty or specialty.lower() in d["specialty"].lower()) and (not city or city.lower() in d["city"].lower())]

@app.post("/api/emergency")
def emergency(session_uid: str = Depends(require_session)):
    raise HTTPException(503, "Live emergency dispatch is not configured. Call your local emergency number now.")

# ---------- Blood donation network ----------
BLOOD_GROUPS = {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"}

@app.post("/api/blood/register")
def register_donor(uid: str, available: bool = True, blood_group: str = "O+", city: str = "",
                   session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    u = db.get("users", uid)
    if not u: raise HTTPException(404, "User not found")
    if blood_group not in BLOOD_GROUPS:
        raise HTTPException(422, "Select a valid blood group")
    if available and not city.strip():
        raise HTTPException(422, "Enter your city or service area before opting in")
    u["extension"]["donor_opt_in"] = available
    u["extension"]["blood_group"] = blood_group
    u["extension"]["donor_city"] = city.strip()[:100] if available else ""
    return public_user(db.put("users", u))

@app.post("/api/blood/request")
def blood_request(session_uid: str = Depends(require_session)):
    raise HTTPException(503, "Verified blood-bank request matching is not configured. Contact a licensed blood bank or treating hospital directly.")

@app.get("/api/blood/alerts/{uid}")
def alerts(uid: str, session_uid: str = Depends(require_session)):
    require_owner(uid, session_uid)
    return db.all_("alerts", uid)

# ---------- Chatbot ----------
class Chat(BaseModel):
    user_id: str; message: str; language: str = "English"; history: list = []

@app.post("/api/chat")
def chat(c: Chat, session_uid: str = Depends(require_session)):
    require_owner(c.user_id, session_uid)
    ctx = [{"date": r["date"], **r["extracted"]} for r in db.all_("records", c.user_id)]
    try:
        reply = ai.chat(c.history, c.message, ctx, c.language)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"reply": reply.strip(), "action": None}

app.mount("/", StaticFiles(directory=__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "frontend"), html=True), name="ui")

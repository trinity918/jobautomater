"""FastAPI app: dashboard, REST API, live WebSockets, schedulers. Start with run.bat."""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, discovery, events, inbox, info, llm, profile_fill, ranker, resume, resume_render
from .apply import engine
from .apply.browser import manager
from .config import (CONFIG_DIR, RESUME_DIR, SHOTS_DIR, WEB_DIR, load_profile, load_settings, save_profile,
                     save_settings)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request floods the console
_bg: list[asyncio.Task] = []


async def _discovery_loop() -> None:
    s = load_settings()
    if s.get("discover_on_start", True):
        last = db.kv_get("discovery_last") or {}
        if time.time() - last.get("at", 0) > 3600:
            await asyncio.sleep(3)
            try:
                await discovery.discover()
            except Exception as e:
                events.logline(f"Discovery failed: {e}", "error")
    while True:
        hours = float(load_settings().get("discovery_interval_hours", 4))
        await asyncio.sleep(max(0.25, hours) * 3600)
        try:
            await discovery.discover()
        except Exception as e:
            events.logline(f"Discovery failed: {e}", "error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    info.ensure_template()
    _autofill_profile(resume.current())   # pick up anything new the resume reader can extract
    await engine.start()
    _bg.append(asyncio.create_task(_discovery_loop()))
    _bg.append(asyncio.create_task(inbox.loop()))
    s = load_settings()
    url = f"http://{s['host']}:{s['port']}"
    events.logline(f"JobAutomater is running at {url}")
    if s.get("open_browser", True):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    yield
    for t in _bg:
        t.cancel()
    await engine.stop()
    await manager.shutdown()


app = FastAPI(title="JobAutomater", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/shots", StaticFiles(directory=SHOTS_DIR), name="shots")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ state
@app.get("/api/state")
async def state():
    s = load_settings()
    res = resume.current()
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
    jobs_total = db.one("SELECT COUNT(*) n FROM jobs WHERE status IN ('new','queued')")["n"]
    return {
        "settings": s,
        "llm": {"enabled": llm.enabled(), "model": llm.llm_config()["model"], "base_url": llm.llm_config()["base_url"],
                "tokens_today": llm.tokens_used_today()},
        "inbox": {"configured": inbox.configured(), "last": db.kv_get("inbox_last")},
        "discovery": db.kv_get("discovery_last"),
        "queue": engine.queue_size(),
        "parked": engine.parked_ids(),
        "submitted_today": engine.submitted_today(),
        "app_counts": counts,
        "jobs_open": jobs_total,
        "resume": {"file": res.get("file"), "profile": res.get("profile"), "template": res.get("template"),
                   "pdf": Path(res["pdf"]).name if res.get("pdf") else None},
        "profile_ready": bool((load_profile().get("personal") or {}).get("email")),
        "sessions": manager.list(),
        "signin": {"open": manager.login_open(), "last": db.kv_get("signin_last")},
        "profile_missing": [m["label"] for m in profile_fill.form()["missing"]],
    }


@app.post("/api/browser/signin")
async def browser_signin(body: dict | None = None):
    """Open a plain browser window on the app's profile to sign in to Google / LinkedIn / Workday once."""
    url = (body or {}).get("url") or "https://accounts.google.com/"
    r = await manager.open_login_window(url)
    if r.get("ok"):
        db.kv_set("signin_last", time.time())
    return r


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
async def jobs(q: str = "", sort: str = "best", exp: str = "", employer: str = "", where: str = "",
               source: str = "", status: str = "new", min_score: float = 0, limit: int = 50, offset: int = 0):
    clauses, params = [], []
    if status:
        clauses.append("status=?"); params.append(status)
    if q:
        clauses.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if source:
        clauses.append("source=?"); params.append(source)
    if min_score:
        clauses.append("score>=?"); params.append(min_score)
    if where == "remote":
        clauses.append("remote=1")
    elif where == "india":
        clauses.append("remote=0")
    if exp == "fresher":
        clauses.append("json_extract(breakdown,'$.level_label') IN ('entry','unknown')")
    elif exp == "experienced":
        clauses.append("json_extract(breakdown,'$.level_label') IN ('mid','senior','unknown')")
    if employer in ("startup", "mnc"):
        clauses.append("json_extract(breakdown,'$.employer_type')=?"); params.append(employer)
    wh = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "COALESCE(posted_at, first_seen) DESC" if sort == "latest" else "COALESCE(score,0) DESC"
    total = db.one(f"SELECT COUNT(*) n FROM jobs {wh}", params)["n"]
    rows = db.query(f"SELECT id,source,ats,company,title,location,remote,url,apply_url,salary,posted_at,first_seen,score,"
                    f"llm_score,reason,breakdown,status,extra FROM jobs {wh} ORDER BY {order} LIMIT ? OFFSET ?",
                    params + [limit, offset])
    for r in rows:
        r["breakdown"] = json.loads(r["breakdown"]) if r["breakdown"] else {}
        r["extra"] = json.loads(r["extra"]) if r["extra"] else {}
        r["auto"] = r["ats"] in engine.SUPPORTED
    sources = [r["source"] for r in db.query("SELECT DISTINCT source FROM jobs ORDER BY source")]
    return {"total": total, "jobs": rows, "sources": sources}


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str):
    j = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j:
        raise HTTPException(404)
    j["breakdown"] = json.loads(j["breakdown"]) if j["breakdown"] else {}
    return j


@app.post("/api/jobs/{job_id}/apply")
async def job_apply(job_id: str, body: dict | None = None):
    mode = (body or {}).get("mode") or load_settings().get("apply_mode", "review")
    if not db.one("SELECT id FROM jobs WHERE id=?", (job_id,)):
        raise HTTPException(404)
    return {"application_id": engine.enqueue(job_id, mode)}


@app.post("/api/jobs/{job_id}/dismiss")
async def job_dismiss(job_id: str):
    db.execute("UPDATE jobs SET status='dismissed' WHERE id=?", (job_id,))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/restore")
async def job_restore(job_id: str):
    db.execute("UPDATE jobs SET status='new' WHERE id=?", (job_id,))
    return {"ok": True}


@app.post("/api/jobs/apply-top")
async def apply_top(body: dict):
    n = int(body.get("n", 10))
    mode = body.get("mode") or load_settings().get("apply_mode", "review")
    min_score = float(body.get("min_score", load_settings().get("min_score_to_apply", 70)))
    rows = db.query("SELECT id FROM jobs WHERE status='new' AND score>=? ORDER BY score DESC LIMIT ?", (min_score, n))
    ids = [engine.enqueue(r["id"], mode) for r in rows]
    return {"queued": len(ids)}


# ------------------------------------------------------------------ Today: Start -> Apply -> done for the day
def _day_start() -> float:
    return time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))


@app.get("/api/today")
async def today(days: int = 1, min_score: float = 0):
    """Fresh roles (posted, or first seen, in the last `days` days) that you have not applied to."""
    since = time.time() - max(1, days) * 86400
    rows = db.query(
        "SELECT id,source,ats,company,title,location,remote,url,salary,posted_at,first_seen,score,reason,breakdown "
        "FROM jobs WHERE status='new' AND COALESCE(score,0)>=? AND (posted_at>=? OR (posted_at IS NULL AND first_seen>=?)) "
        "AND id NOT IN (SELECT job_id FROM applications) ORDER BY score DESC LIMIT 300", (min_score, since, since))
    for r in rows:
        r["breakdown"] = json.loads(r["breakdown"]) if r["breakdown"] else {}
        r["auto"] = r["ats"] in engine.SUPPORTED
    s = load_settings()
    batch = db.kv_get("today_batch") or {}
    if batch.get("day") != time.strftime("%Y-%m-%d"):
        batch = {}
    progress = {}
    if batch.get("apps"):
        ids = batch["apps"]
        for r in db.query(f"SELECT status, COUNT(*) n FROM applications WHERE id IN ({','.join('?' * len(ids))}) GROUP BY status", ids):
            progress[r["status"]] = r["n"]
    return {"jobs": rows, "min_score_default": s.get("min_score_to_apply", 70),
            "cap": int(s.get("daily_apply_cap", 25)), "sent_today": engine.submitted_today(),
            "queued_today": len(batch.get("apps", [])), "progress": progress, "started_at": batch.get("started_at"),
            "discovery": db.kv_get("discovery_last"), "queue": engine.queue_size()}


@app.post("/api/today/start")
async def today_start(body: dict | None = None):
    """Search every source now (skipped if a search finished in the last 20 minutes, unless force=true)."""
    last = db.kv_get("discovery_last") or {}
    batch = db.kv_get("today_batch") or {}
    if batch.get("day") != time.strftime("%Y-%m-%d"):
        db.kv_set("today_batch", {"day": time.strftime("%Y-%m-%d"), "started_at": time.time(), "apps": []})
    if (body or {}).get("force") or time.time() - last.get("at", 0) > 20 * 60:
        asyncio.create_task(discovery.discover())
        return {"searching": True}
    return {"searching": False, "fresh_from": last.get("at")}


@app.post("/api/today/apply")
async def today_apply(body: dict):
    """Queue the chosen jobs, inside what is left of today's cap."""
    ids = list(dict.fromkeys(body.get("ids") or []))
    mode = body.get("mode") or "auto"
    s = load_settings()
    batch = db.kv_get("today_batch") or {}
    if batch.get("day") != time.strftime("%Y-%m-%d"):
        batch = {"day": time.strftime("%Y-%m-%d"), "started_at": time.time(), "apps": []}
    room = max(0, int(s.get("daily_apply_cap", 25)) - engine.submitted_today() - engine.queue_size())
    take, left_out = ids[:room], ids[room:]
    app_ids = [engine.enqueue(jid, mode) for jid in take]
    batch["apps"] = list(dict.fromkeys(batch.get("apps", []) + app_ids))
    db.kv_set("today_batch", batch)
    events.logline(f"Today: queued {len(app_ids)} applications ({mode} mode)"
                   + (f"; {len(left_out)} left for tomorrow by the daily cap" if left_out else "") + ".")
    return {"queued": len(app_ids), "over_cap": len(left_out), "mode": mode}


@app.post("/api/discover")
async def run_discovery():
    asyncio.create_task(discovery.discover())
    return {"started": True}


@app.post("/api/rank")
async def rerank():
    n = await asyncio.to_thread(ranker.rank_all)
    asyncio.create_task(ranker.llm_rerank())
    return {"ranked": n}


# ------------------------------------------------------------------ applications
@app.get("/api/applications")
async def applications(status: str = "", q: str = ""):
    clauses, params = [], []
    if status:
        clauses.append("a.status=?"); params.append(status)
    if q:
        clauses.append("(j.title LIKE ? OR j.company LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    wh = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.query(
        f"SELECT a.id,a.status,a.mode,a.started_at,a.updated_at,a.error,a.session_id,j.id job_id,j.title,j.company,"
        f"j.location,j.ats,j.score,j.url,"
        f"(SELECT message FROM events e WHERE e.application_id=a.id ORDER BY e.id DESC LIMIT 1) latest "
        f"FROM applications a JOIN jobs j ON j.id=a.job_id {wh} ORDER BY a.updated_at DESC LIMIT 500", params)
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
    week = time.time() - 7 * 86400
    sent_week = db.one("SELECT COUNT(DISTINCT application_id) n FROM events WHERE step='submitted' AND at>?", (week,))["n"]
    sent_prev = db.one("SELECT COUNT(DISTINCT application_id) n FROM events WHERE step='submitted' AND at>? AND at<=?",
                       (week - 7 * 86400, week))["n"]
    companies = db.one("SELECT COUNT(DISTINCT j.company) n FROM applications a JOIN jobs j ON j.id=a.job_id "
                       "WHERE a.status IN ('submitted','confirmed','replied','interview','rejected')")["n"]
    new_week = db.one("SELECT COUNT(*) n FROM jobs WHERE first_seen>? AND status='new' AND score>=50", (week,))["n"]
    total_sent = db.one("SELECT COUNT(DISTINCT application_id) n FROM events WHERE step='submitted'")["n"]
    return {"applications": rows, "counts": counts,
            "stats": {"sent_week": sent_week, "sent_prev_week": sent_prev, "companies": companies,
                      "new_roles_week": new_week, "hours_saved": round(total_sent * 35 / 60, 1)}}


@app.get("/api/applications/{app_id}")
async def application_detail(app_id: int):
    a = db.one("SELECT * FROM applications WHERE id=?", (app_id,))
    if not a:
        raise HTTPException(404)
    job = db.one("SELECT * FROM jobs WHERE id=?", (a["job_id"],))
    evs = db.query("SELECT * FROM events WHERE application_id=? ORDER BY id", (app_id,))
    mails = db.query("SELECT id,at,from_name,from_addr,subject,snippet,body,category FROM emails WHERE application_id=? "
                     "ORDER BY at DESC", (app_id,))
    a["answers"] = json.loads(a["answers"]) if a.get("answers") else {}
    questions = [e["message"].replace("Needs your answer: ", "") for e in evs if e["step"] == "question"]
    return {"application": a, "job": job, "events": evs, "emails": mails, "questions": questions,
            "live": app_id in engine.parked_ids()}


@app.post("/api/applications/{app_id}/submit")
async def app_submit(app_id: int):
    return {"result": await engine.approve_submit(app_id)}


@app.post("/api/applications/{app_id}/retry")
async def app_retry(app_id: int, body: dict | None = None):
    if body and body.get("mode"):
        db.execute("UPDATE applications SET mode=? WHERE id=?", (body["mode"], app_id))
    await engine.retry(app_id)
    return {"ok": True}


@app.post("/api/applications/{app_id}/cancel")
async def app_cancel(app_id: int):
    await engine.cancel(app_id)
    return {"ok": True}


@app.post("/api/applications/{app_id}/answers")
async def app_answers(app_id: int, body: dict):
    """Save the user's answers to open questions, then retry the application."""
    for label, ans in (body.get("answers") or {}).items():
        if str(ans).strip():
            engine.save_user_answer(label, str(ans).strip())
    if body.get("retry", True):
        await engine.retry(app_id)
    return {"ok": True}


@app.post("/api/applications/{app_id}/status")
async def app_set_status(app_id: int, body: dict):
    engine.set_status(app_id, body["status"])
    engine.add_event(app_id, body["status"], f"You marked this as {body['status']}")
    return {"ok": True}


@app.get("/api/pipeline")
async def pipeline():
    rows = db.query("SELECT a.id,a.status,a.updated_at,j.title,j.company FROM applications a JOIN jobs j ON j.id=a.job_id "
                    "WHERE a.status NOT IN ('skipped','dry_run') ORDER BY a.updated_at DESC")
    cols = collections.OrderedDict((k, []) for k in
                                   ["needs_you", "review", "applying", "queued", "submitted", "confirmed", "replied",
                                    "interview", "rejected", "failed"])
    for r in rows:
        cols.setdefault(r["status"], []).append(r)
    return cols


# ------------------------------------------------------------------ tracker / inbox
@app.get("/api/inbox")
async def inbox_list(category: str = "", q: str = ""):
    clauses, params = [], []
    if category:
        clauses.append("e.category=?"); params.append(category)
    if q:
        clauses.append("(e.subject LIKE ? OR e.from_name LIKE ? OR e.body LIKE ?)"); params += [f"%{q}%"] * 3
    wh = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.query(f"SELECT e.id,e.at,e.from_name,e.from_addr,e.subject,e.snippet,e.category,e.is_read,e.application_id,"
                    f"j.title,j.company FROM emails e LEFT JOIN applications a ON a.id=e.application_id "
                    f"LEFT JOIN jobs j ON j.id=a.job_id {wh} ORDER BY e.at DESC LIMIT 300", params)
    counts = {r["category"]: r["n"] for r in db.query("SELECT category, COUNT(*) n FROM emails GROUP BY category")}
    return {"emails": rows, "counts": counts, "configured": inbox.configured()}


@app.get("/api/inbox/{email_id:path}")
async def inbox_one(email_id: str):
    e = db.one("SELECT * FROM emails WHERE id=?", (email_id,))
    if not e:
        raise HTTPException(404)
    inbox.mark_read(email_id)
    if e.get("application_id"):
        e["timeline"] = db.query("SELECT step,at,message FROM events WHERE application_id=? AND step IN "
                                 "('submitted','confirmed','replied','rejected') ORDER BY at", (e["application_id"],))
        a = db.one("SELECT j.url,j.title,j.company,j.ats FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                   (e["application_id"],))
        e["job"] = a
    return e


@app.post("/api/inbox-refresh")
async def inbox_refresh():
    return await inbox.poll()


# ------------------------------------------------------------------ live sessions
@app.get("/api/sessions")
async def sessions():
    return manager.list()


@app.post("/api/sessions/{sid}/takeover")
async def takeover(sid: str, body: dict):
    s = manager.sessions.get(sid)
    if not s:
        raise HTTPException(404)
    s.take_over(bool(body.get("on", True)))
    return s.info()


@app.post("/api/sessions/{sid}/close")
async def close_session(sid: str):
    s = manager.sessions.get(sid)
    if s and s.application_id and s.application_id in engine.parked_ids():
        await engine.cancel(s.application_id)
    else:
        await manager.close_session(sid)
    return {"ok": True}


@app.websocket("/ws/live/{sid}")
async def ws_live(ws: WebSocket, sid: str):
    await ws.accept()
    s = manager.sessions.get(sid)
    if not s:
        await ws.send_json({"type": "closed"})
        await ws.close()
        return
    q = s.subscribe()

    async def pump():
        while True:
            msg = await q.get()
            await ws.send_json(msg)
            if msg.get("type") == "closed":
                break

    sender = asyncio.create_task(pump())
    try:
        while True:
            ev = await ws.receive_json()
            if s.taken_over or ev.get("type") in ("navigate",):
                await s.input(ev)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        s.unsubscribe(q)


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    q = events.subscribe()
    try:
        for m in events.recent_logs()[-60:]:
            await ws.send_json(m)
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(q)


# ------------------------------------------------------------------ resume studio
@app.get("/api/resume")
async def get_resume():
    r = resume.current()
    return {"file": r.get("file"), "struct": r.get("struct"), "profile": r.get("profile"),
            "template": r.get("template", "classic"), "pdf": Path(r["pdf"]).name if r.get("pdf") else None,
            "templates": resume_render.TEMPLATES}


@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt"):
        raise HTTPException(400, "Upload a PDF, DOCX or TXT file.")
    safe = "".join(ch for ch in Path(file.filename).stem if ch.isalnum() or ch in "-_ ")[:60] or "resume"
    dest = RESUME_DIR / f"{safe}{suffix}"
    dest.write_bytes(await file.read())
    try:
        data = await resume.ingest(dest)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _autofill_profile(data)
    asyncio.create_task(asyncio.to_thread(ranker.rank_all))
    return {"file": data["file"], "profile": data["profile"], "struct": data["struct"]}


def _autofill_profile(data: dict, overwrite: bool = False) -> list[str]:
    """Copy everything the resume gives (contacts, city, education, current role, a pitch) into profile.yaml."""
    if not data or not data.get("struct"):
        return []
    try:
        return profile_fill.fill_profile(data["struct"], data.get("text", ""),
                                         (data.get("profile") or {}).get("skills"), overwrite)
    except Exception as e:
        events.logline(f"Could not copy details from the resume: {e}", "warning")
        return []


@app.get("/api/profile/form")
async def profile_form():
    return profile_fill.form()


@app.put("/api/profile/form")
async def save_profile_form(body: dict):
    profile_fill.save_form(body.get("values") or {})
    return profile_fill.form()


@app.get("/api/info")
async def get_info():
    text = info.INFO_PATH.read_text(encoding="utf-8") if info.INFO_PATH.exists() else ""
    used = info.overrides()
    extra = {k: v for k, v in info.read().items() if v and k not in info.ALIASES}
    return {"text": text, "path": str(info.INFO_PATH), "used": used, "extra": extra}


@app.put("/api/info")
async def put_info(body: dict):
    info.INFO_PATH.write_text(body.get("text", ""), encoding="utf-8")
    return await get_info()


@app.post("/api/profile/from-resume")
async def profile_from_resume(body: dict | None = None):
    changed = _autofill_profile(resume.current(), bool((body or {}).get("overwrite")))
    return {"changed": changed, **profile_fill.form()}


@app.get("/api/resume/render", response_class=HTMLResponse)
async def render_resume(template: str = "", editable: int = 1, draft: int = 0):
    r = resume.current()
    struct = (db.kv_get("resume_draft") if draft else None) or r.get("struct") or {}
    return HTMLResponse(resume_render.render(struct, template or r.get("template", "classic"), bool(editable)))


@app.post("/api/resume/edit")
async def edit_resume(body: dict):
    r = resume.current()
    struct = resume_render.set_path(r.get("struct") or {}, body["path"], body.get("value", ""))
    resume.update_struct(struct)
    return {"ok": True}


@app.post("/api/resume/item")
async def resume_item(body: dict):
    """Add/remove a bullet: {section, item, action: add_bullet|remove_bullet, bullet}."""
    r = resume.current()
    st = r.get("struct") or {}
    it = st["sections"][int(body["section"])]["items"][int(body["item"])]
    if body["action"] == "add_bullet":
        it.setdefault("bullets", []).append(body.get("text", "New point"))
    elif body["action"] == "remove_bullet":
        it["bullets"].pop(int(body["bullet"]))
    resume.update_struct(st)
    return {"ok": True}


@app.post("/api/resume/upgrade")
async def upgrade_resume(body: dict):
    rows = db.query("SELECT breakdown FROM jobs WHERE status='new' AND score IS NOT NULL ORDER BY score DESC LIMIT 40")
    counter: collections.Counter = collections.Counter()
    for r in rows:
        try:
            b = json.loads(r["breakdown"] or "{}")
            counter.update(b.get("missing", []) + b.get("matched", []))
        except Exception:
            pass
    keywords = [k for k, _ in counter.most_common(30)]
    try:
        out = await resume.upgrade(body.get("notes", ""), keywords)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.kv_set("resume_draft", out["struct"])
    return {"changes": out["changes"], "keywords": keywords}


@app.post("/api/resume/draft/{action}")
async def resume_draft(action: str):
    if action == "accept":
        d = db.kv_get("resume_draft")
        if d:
            resume.update_struct(d)
    db.kv_set("resume_draft", None)
    return {"ok": True}


@app.post("/api/resume/save")
async def save_resume(body: dict):
    r = resume.current()
    template = body.get("template") or r.get("template", "classic")
    r["template"] = template
    resume.save_current(r)
    if body.get("use"):
        path = await resume_render.export_pdf(r.get("struct") or {}, template)
        r = resume.current()
        r["pdf"] = str(path)
        resume.save_current(r)
        events.logline(f"Resume saved as {path.name}; it will be uploaded with applications.")
        asyncio.create_task(asyncio.to_thread(ranker.rank_all))
        return {"ok": True, "pdf": path.name}
    return {"ok": True}


@app.get("/api/resume/pdf")
async def resume_pdf():
    p = resume.resume_file_for_upload()
    if not p or not Path(p).exists():
        raise HTTPException(404)
    return FileResponse(p, filename=Path(p).name)


@app.put("/api/resume/profile")
async def put_resume_profile(body: dict):
    r = resume.current()
    prof = r.get("profile") or {}
    for k in ("target_titles", "seniority", "skills", "years_experience"):
        if k in body:
            prof[k] = body[k]
            prof[k + "_locked"] = True
    r["profile"] = prof
    resume.save_current(r)
    asyncio.create_task(asyncio.to_thread(ranker.rank_all))
    return prof


# ------------------------------------------------------------------ profile, settings, answers
@app.get("/api/profile")
async def get_profile():
    p = CONFIG_DIR / "profile.yaml"
    return {"yaml": p.read_text(encoding="utf-8") if p.exists() else ""}


@app.put("/api/profile")
async def put_profile(body: dict):
    try:
        data = yaml.safe_load(body.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML error: {e}")
    (CONFIG_DIR / "profile.yaml").write_text(body["yaml"], encoding="utf-8")
    asyncio.create_task(asyncio.to_thread(ranker.rank_all))
    return {"ok": True, "keys": list(data)}


@app.get("/api/search-prefs")
async def get_search_prefs():
    return load_profile().get("search") or {}


@app.put("/api/search-prefs")
async def put_search_prefs(body: dict):
    """Location chips, target titles and experience from the Jobs page. Re-ranks right away."""
    prof = load_profile(raw=True)
    s = prof.setdefault("search", {})
    for k in ("target_titles", "experience", "india", "preferred_cities", "remote_india", "remote_worldwide",
              "abroad", "exclude_keywords", "exclude_companies"):
        if k in body:
            s[k] = body[k]
    save_profile(prof)
    ranker._prefs_cache = (0.0, {})  # take effect now, not in 10 s
    n = await asyncio.to_thread(ranker.rank_all)
    return {"search": s, "ranked": n}


@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.put("/api/settings")
async def put_settings(body: dict):
    return save_settings(body)


@app.get("/api/llm/ping")
async def llm_ping():
    return await llm.ping()


@app.get("/api/answers")
async def answers():
    return db.query("SELECT label, answer, source, used_count FROM answers ORDER BY updated_at DESC")


@app.put("/api/answers")
async def put_answer(body: dict):
    engine.save_user_answer(body["label"], body["answer"])
    return {"ok": True}


@app.delete("/api/answers")
async def del_answer(label: str):
    from .apply.answers import norm
    db.execute("DELETE FROM answers WHERE label_norm=?", (norm(label),))
    return {"ok": True}


@app.get("/api/logs")
async def logs():
    return events.recent_logs()


@app.exception_handler(Exception)
async def on_error(request, exc):
    events.logline(f"API error on {request.url.path}: {exc}", "error")
    return JSONResponse({"detail": str(exc)}, status_code=500)


def run() -> None:
    import uvicorn
    s = load_settings()
    uvicorn.run(app, host=s["host"], port=int(s["port"]), log_level="warning")


if __name__ == "__main__":
    run()

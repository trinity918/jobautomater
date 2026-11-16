"""The apply engine: a queue, worker pool, safety rails, proof screenshots and the Needs-you hand-off."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path

from .. import db
from ..config import SHOTS_DIR, load_profile, load_settings
from ..events import logline, publish
from ..resume import resume_file_for_upload
from ..sources.common import detect_ats
from . import filler, login
from .answers import cover_letter, remember, resolve
from .browser import LiveSession, manager

# the button for THIS job, before any generic "Apply" (job boards list other jobs' Apply links too)
APPLY_SPECIFIC_RE = re.compile(r"^\s*(apply for this (job|position|role)|apply to this (job|position|role)|apply now|"
                               r"apply on (the )?(company|employer)('?s)? (site|website)|apply on company site)\s*$", re.I)

SIGNIN_HELP = ("This site needs you to sign in. Either take over in the Live tab and sign in here (it is remembered "
               "for next time), or press “Sign in to sites” in Settings to sign in to Google once; after that I "
               "click “Continue with Google” myself.")

SUPPORTED = {"greenhouse", "lever", "ashby", "workable"}   # submit on their own
ASSISTED = {"workday", "smartrecruiters", "other", "oracle", "icims", "bamboohr", "recruitee", "zoho", "turbohire", "darwinbox"}
MAX_PARKED = 4
PARK_TIMEOUT_S = 30 * 60

_queue: asyncio.Queue = asyncio.Queue()
_workers: list[asyncio.Task] = []
_parked: dict[int, LiveSession] = {}
_watchers: dict[int, asyncio.Task] = {}


# ------------------------------------------------------------------ records
def add_event(app_id: int, step: str, message: str, level: str = "info", screenshot: str | None = None) -> None:
    db.execute("INSERT INTO events(application_id,at,level,step,message,screenshot) VALUES(?,?,?,?,?,?)",
               (app_id, time.time(), level, step, message, screenshot))
    publish("app_event", application_id=app_id, step=step, message=message, level=level)


def set_status(app_id: int, status: str, error: str | None = None, **extra) -> None:
    sets, vals = ["status=?", "updated_at=?"], [status, time.time()]
    if error is not None:
        sets.append("error=?"); vals.append(error)
    for k, v in extra.items():
        sets.append(f"{k}=?"); vals.append(v)
    db.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id=?", [*vals, app_id])
    app = db.one("SELECT job_id FROM applications WHERE id=?", (app_id,))
    if app:
        job_status = {"submitted": "applied", "confirmed": "applied", "replied": "applied", "interview": "applied",
                      "rejected": "applied", "dry_run": "new", "failed": "new", "skipped": "new"}.get(status, "queued")
        db.execute("UPDATE jobs SET status=? WHERE id=?", (job_status, app["job_id"]))
    publish("app_status", application_id=app_id, status=status, error=error)


def submitted_today() -> int:
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    r = db.one("SELECT COUNT(*) n FROM events WHERE step='submitted' AND at>=?", (start,))
    return r["n"] if r else 0


def enqueue(job_id: str, mode: str | None = None) -> int:
    mode = mode or load_settings().get("apply_mode", "review")
    existing = db.one("SELECT id,status FROM applications WHERE job_id=? AND status IN "
                      "('queued','applying','review','needs_you','submitted','confirmed') ORDER BY id DESC LIMIT 1", (job_id,))
    if existing:
        return existing["id"]
    now = time.time()
    cur = db.execute("INSERT INTO applications(job_id,status,mode,started_at,updated_at) VALUES(?,?,?,?,?)",
                     (job_id, "queued", mode, now, now))
    app_id = cur.lastrowid
    add_event(app_id, "approved", "You said yes" if mode != "auto-bot" else "Auto-apply picked this job")
    db.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
    _queue.put_nowait(app_id)
    publish("app_status", application_id=app_id, status="queued")
    return app_id


# ------------------------------------------------------------------ workers
async def start() -> None:
    # re-queue anything left mid-flight by a restart
    for r in db.query("SELECT id FROM applications WHERE status IN ('queued','applying')"):
        db.execute("UPDATE applications SET status='queued' WHERE id=?", (r["id"],))
        _queue.put_nowait(r["id"])
    n = int(load_settings().get("workers", 2))
    for i in range(n):
        _workers.append(asyncio.create_task(_worker(i)))
    _workers.append(asyncio.create_task(_auto_loop()))


async def stop() -> None:
    for t in _workers + list(_watchers.values()):
        t.cancel()


async def _worker(n: int) -> None:
    while True:
        app_id = await _queue.get()
        try:
            while len(_parked) >= MAX_PARKED:
                await asyncio.sleep(5)
            await apply_one(app_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logline(f"Application {app_id} crashed: {e}", "error")
            set_status(app_id, "failed", error=str(e)[:500])
            add_event(app_id, "failed", f"Unexpected error: {e}", "error")
        finally:
            _queue.task_done()
        lo, hi = load_settings().get("delay_between_applies_s", [20, 60])
        await asyncio.sleep(random.uniform(float(lo), float(hi)))


async def _auto_loop() -> None:
    """When auto-apply is on, keep the queue fed with the best new jobs, inside the daily cap."""
    await asyncio.sleep(20)
    while True:
        try:
            s = load_settings()
            if s.get("auto_apply_enabled"):
                room = int(s.get("daily_apply_cap", 25)) - submitted_today() - _queue.qsize()
                if room > 0:
                    jobs = db.query(
                        "SELECT id,company FROM jobs WHERE status='new' AND score>=? ORDER BY score DESC LIMIT ?",
                        (float(s.get("min_score_to_apply", 70)), room * 3))
                    picked = 0
                    for j in jobs:
                        if picked >= room:
                            break
                        if _company_cooldown(j["company"]):
                            continue
                        enqueue(j["id"], s.get("apply_mode", "review"))
                        picked += 1
                    if picked:
                        logline(f"Auto-apply queued {picked} jobs.")
        except Exception as e:
            logline(f"Auto-apply loop error: {e}", "error")
        await asyncio.sleep(600)


def _company_cooldown(company: str) -> bool:
    days = int(load_settings().get("company_cooldown_days", 30))
    if not company or days <= 0:
        return False
    r = db.one("SELECT COUNT(*) n FROM applications a JOIN jobs j ON j.id=a.job_id WHERE lower(j.company)=lower(?) "
               "AND a.status IN ('submitted','confirmed','replied','interview','rejected') AND a.updated_at>?",
               (company, time.time() - days * 86400))
    return bool(r and r["n"])


# ------------------------------------------------------------------ one application
async def apply_one(app_id: int) -> None:
    app = db.one("SELECT * FROM applications WHERE id=?", (app_id,))
    if not app or app["status"] not in ("queued",):
        return
    job = db.one("SELECT * FROM jobs WHERE id=?", (app["job_id"],))
    if not job:
        set_status(app_id, "failed", error="Job no longer exists")
        return
    mode = app["mode"] or "review"
    s = load_settings()
    if mode == "auto" and submitted_today() >= int(s.get("daily_apply_cap", 25)):
        _queue.put_nowait(app_id)  # try again tomorrow
        await asyncio.sleep(3600)
        return
    if mode == "auto" and _company_cooldown(job["company"]):
        set_status(app_id, "skipped", error=f"Applied to {job['company']} in the last {s.get('company_cooldown_days')} days")
        add_event(app_id, "skipped", "Skipped: same company applied to recently")
        return
    resume_path = resume_file_for_upload()
    if not resume_path:
        set_status(app_id, "needs_you", error="Upload a resume first (Resume tab).")
        add_event(app_id, "needs_you", "No resume file to upload", "warning")
        return
    prof = load_profile()
    if not (prof.get("personal") or {}).get("email"):
        set_status(app_id, "needs_you", error="Fill in config/profile.yaml (name, email, phone) first.")
        add_event(app_id, "needs_you", "Profile details missing", "warning")
        return

    set_status(app_id, "applying")
    session = await manager.new_session(f"{job['title']} · {job['company']}")
    session.application_id = app_id
    db.execute("UPDATE applications SET session_id=? WHERE id=?", (session.id, app_id))
    add_event(app_id, "browser", f"Opening {job['apply_url']}")
    parked = False
    try:
        result = await _drive(session, app_id, job, mode, resume_path)
        parked = result == "parked"
    finally:
        if not parked:
            await manager.close_session(session.id)


async def _drive(session: LiveSession, app_id: int, job: dict, mode: str, resume_path: str) -> str:
    page = session.page
    session.set_step("loading the application page")
    try:
        await page.goto(job["apply_url"] or job["url"], wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(1.5)

    # Get to the form. Job boards (Remote OK, Remotive...) show a listing whose "Apply" leads to the employer's
    # site, which may have its own "Apply" button and/or a "Sign in with Google" wall. Up to 3 hops.
    fields = await filler.collect(session.page)
    google_tried = False
    for hop in range(4):
        if _is_application(fields):
            break
        if not google_tried and (await filler.needs_login(session.page) or await login.has_google_button(session.page)):
            google_tried = True
            session.set_step("signing in with Google")
            res = await login.google_signin(session)
            if res == "ok":
                add_event(app_id, "signin", "Signed in with Google")
                await _settle(session.page)
                fields = await filler.collect(session.page)
                continue
            if res == "needs_user":
                return await _park(session, app_id, job, SIGNIN_HELP)
        if hop == 3:
            break
        session.set_step("looking for the Apply button")
        if not (await filler.click_matching(session.page, APPLY_SPECIFIC_RE)
                or await filler.click_matching(session.page, filler.APPLY_BTN_RE)):
            break
        await asyncio.sleep(3)
        await _settle(session.page)
        fields = await filler.collect(session.page)

    real_ats = detect_ats(session.page.url)
    if real_ats != "other" and real_ats != job["ats"]:
        job = {**job, "ats": real_ats}   # a job-board listing that led to Greenhouse/Lever/...: treat it as that
        db.execute("UPDATE jobs SET ats=?, apply_url=? WHERE id=?", (real_ats, session.page.url, job["id"]))
    if not _is_application(fields) and (await filler.needs_login(session.page) or job["ats"] in ("workday",)):
        return await _park(session, app_id, job, SIGNIN_HELP)
    if not _is_application(fields):
        return await _park(session, app_id, job, "I could not find the application form on this page. "
                                                 "Take over to open it, or skip this job.")

    answers_log: dict[str, str] = {}
    cover_path = None
    unresolved: list[str] = []
    for page_no in range(6):  # multi-step forms
        await session.wait_if_taken_over()
        session.set_step(f"filling the form (page {page_no + 1})")
        # Files first, skipping "autofill from resume" boxes (they rebuild the form). Uploads can re-render the page,
        # so the form is read again before the other fields are filled.
        files = [f for f in fields if f["type"] == "file"
                 and not re.search(r"autofill|auto-fill|parse|import", f.get("label") or "", re.I)]
        for f in files:
            res = await resolve(f, job)
            if res["value"] == "__cover_letter__" and not f.get("required"):
                continue
            if res["value"] == "__cover_letter__" and not cover_path:
                cover_path = await _cover_letter_pdf(session, app_id, job)
            ok = await filler.fill_field(session.page, f, res["value"], resume_path, cover_path)
            answers_log[(f.get("label") or "file")[:160]] = ("(resume file)" if res["value"] == "__resume__" else "(cover letter)") + ("" if ok else "  [could not fill]")
        if files:
            await asyncio.sleep(2)
            fields = await filler.collect(session.page)
        for f in [x for x in fields if x["type"] != "file"]:
            await session.wait_if_taken_over()
            if f["type"] == "combobox" and not f.get("options"):
                f["options"] = await filler.combobox_options(session.page, f)
            if filler.is_filled(f) and f["type"] not in ("file",):
                continue
            label = f.get("label") or f.get("name") or f.get("placeholder") or "(unlabelled)"
            res = await resolve(f, job)
            val = res["value"]
            if val == "__cover_letter__" and not f.get("required"):
                continue  # optional cover-letter upload: leave it empty rather than send a generic letter
            if val == "__cover_letter__":
                if not cover_path:
                    cover_path = await _cover_letter_pdf(session, app_id, job)
                ok = await filler.fill_field(session.page, f, val, resume_path, cover_path)
            elif val is None:
                if f.get("required"):
                    unresolved.append(label[:120])
                continue
            else:
                if f["type"] == "textarea" and re.search(r"cover letter", label, re.I) and res["source"] != "custom":
                    val = await cover_letter(job)
                ok = await filler.fill_field(session.page, f, val, resume_path, cover_path)
            shown = "(resume file)" if val == "__resume__" else "(cover letter)" if val == "__cover_letter__" else str(val)[:300]
            answers_log[label[:160]] = shown + ("" if ok else "  [could not fill]")
            if not ok and f.get("required"):
                unresolved.append(label[:120])
            await asyncio.sleep(random.uniform(0.15, 0.5))

        # check what is still empty
        await asyncio.sleep(1)
        after = await filler.collect(session.page)
        still = [f.get("label") or f.get("name") for f in after if f.get("required") and not filler.is_filled(f)
                 and f["type"] != "file"]
        unresolved = list(dict.fromkeys([u for u in unresolved if u] + [s for s in still if s]))
        db.execute("UPDATE applications SET answers=? WHERE id=?", (json.dumps(answers_log, ensure_ascii=False), app_id))

        if unresolved:
            break
        # multi-page: Next / Continue without a Submit on screen
        if not await _has_submit(session.page) and await filler.click_matching(session.page, filler.NEXT_RE):
            await asyncio.sleep(2.5)
            fields = await filler.collect(session.page)
            continue
        break

    form_shot = str(SHOTS_DIR / f"{app_id}_form.png")
    try:  # the filled form itself, not the job description above it
        form = session.page.locator("form:has([data-ja]), [class*='application-form' i]:has([data-ja])").first
        await form.screenshot(path=form_shot, timeout=15000)
    except Exception:
        await session.screenshot(form_shot)
    add_event(app_id, "form_filled", f"Form filled: {len(answers_log)} fields", screenshot=Path(form_shot).name)

    if unresolved:
        for u in unresolved:
            add_event(app_id, "question", f"Needs your answer: {u}", "warning")
        return await _park(session, app_id, job, "These required questions need you: " + "; ".join(unresolved[:6]),
                           unresolved=unresolved)
    if mode == "dry-run":
        set_status(app_id, "dry_run")
        add_event(app_id, "dry_run", "Dry run: the form was filled but not submitted")
        return "done"
    if mode == "review" or job["ats"] not in SUPPORTED:
        session.set_step("waiting for your review", "waiting")
        set_status(app_id, "review")
        add_event(app_id, "review", "Filled and waiting for you. Check it in the Live tab, then press Submit.")
        _parked[app_id] = session
        _watchers[app_id] = asyncio.create_task(_watch_parked(app_id, session))
        return "parked"
    return await submit(app_id, session)


def _form_size(fields: list[dict]) -> int:
    return sum(1 for f in fields if f["type"] in ("text", "email", "tel", "textarea", "file", "url", "select", "combobox"))


_APP_FIELD = re.compile(r"name|e-?mail|phone|mobile|resume|cv\b|linkedin|cover", re.I)


def _is_application(fields: list[dict]) -> bool:
    """An application form, not a search box plus a newsletter signup: a file upload, or 2+ personal fields."""
    if _form_size(fields) < 2:
        return False
    if any(f["type"] == "file" for f in fields):
        return True
    hits = sum(1 for f in fields if _APP_FIELD.search(" ".join(str(f.get(k) or "") for k in ("label", "name", "placeholder"))))
    return hits >= 2 and _form_size(fields) >= 3


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await asyncio.sleep(1)


async def _has_submit(page) -> bool:
    for frame in page.frames:
        try:
            texts = await frame.evaluate("() => Array.from(document.querySelectorAll('button, input[type=submit]'))"
                                         ".filter(b => b.offsetParent).map(b => (b.innerText || b.value || '').trim())")
            if any(filler.SUBMIT_RE.match(t or "") for t in texts):
                return True
        except Exception:
            pass
    return False


async def submit(app_id: int, session: LiveSession | None = None) -> str:
    session = session or _parked.get(app_id)
    if not session:
        return "no-session"
    session.set_step("submitting", "running")
    if not await filler.click_matching(session.page, filler.SUBMIT_RE):
        return await _park(session, app_id, None, "I could not find the Submit button. Take over and press it; "
                                                  "I will detect the confirmation.")
    add_event(app_id, "submit_clicked", "Pressed Submit")
    return await _await_confirmation(app_id, session, timeout=40)


async def _await_confirmation(app_id: int, session: LiveSession, timeout: float) -> str:
    start_url = session.page.url
    t0 = time.time()
    while time.time() - t0 < timeout:
        await asyncio.sleep(2)
        text = await filler.page_text(session.page)
        m = filler.CONFIRM_RE.search(text)
        url_changed = session.page.url != start_url and re.search(r"confirm|thank|success|submitted", session.page.url, re.I)
        if m or url_changed:
            return await _mark_submitted(app_id, session, (m.group(0) if m else session.page.url)[:200])
        if await filler.has_captcha_challenge(session.page):
            return await _park(session, app_id, None, "A captcha appeared. Solve it in the Live tab and press Submit; "
                                                      "I will detect the confirmation.")
    # no confirmation text: look for validation errors
    text = await filler.page_text(session.page)
    if filler.ERROR_RE.search(text):
        return await _park(session, app_id, None, "The form showed an error after Submit. Take over to fix it.")
    return await _park(session, app_id, None, "Submit was pressed but no confirmation appeared. Check the Live tab.")


async def _mark_submitted(app_id: int, session: LiveSession, evidence: str) -> str:
    shot = str(SHOTS_DIR / f"{app_id}_confirm.png")
    await session.screenshot(shot)
    set_status(app_id, "submitted", confirmation=evidence)
    add_event(app_id, "submitted", f"Submitted. The page said: “{evidence}”", screenshot=Path(shot).name)
    logline(f"Application {app_id} submitted.")
    _release(app_id)
    return "done"


async def _park(session: LiveSession, app_id: int, job: dict | None, reason: str, unresolved: list | None = None) -> str:
    session.set_step(reason, "waiting")
    set_status(app_id, "needs_you", error=reason)
    add_event(app_id, "needs_you", reason, "warning")
    _parked[app_id] = session
    if app_id not in _watchers:
        _watchers[app_id] = asyncio.create_task(_watch_parked(app_id, session))
    return "parked"


async def _watch_parked(app_id: int, session: LiveSession) -> None:
    """While a session waits on the user, watch for a confirmation (they may submit by hand)."""
    t0 = time.time()
    try:
        while time.time() - t0 < PARK_TIMEOUT_S and app_id in _parked:
            await asyncio.sleep(3)
            if session.state == "done":
                break
            try:
                text = await filler.page_text(session.page)
            except Exception:
                continue
            m = filler.CONFIRM_RE.search(text)
            if m:
                await _mark_submitted(app_id, session, m.group(0)[:200])
                return
        if app_id in _parked:
            add_event(app_id, "timeout", "Closed the waiting browser after 30 minutes. Press Retry to open it again.")
            row = db.one("SELECT status FROM applications WHERE id=?", (app_id,))
            if row and row["status"] in ("review", "needs_you"):
                set_status(app_id, "needs_you", error="Browser closed after waiting 30 minutes. Retry to reopen.")
            _release(app_id)
    except asyncio.CancelledError:
        pass


def _release(app_id: int) -> None:
    s = _parked.pop(app_id, None)
    w = _watchers.pop(app_id, None)
    if w and w is not asyncio.current_task():
        w.cancel()
    if s:
        asyncio.ensure_future(manager.close_session(s.id))


async def _cover_letter_pdf(session: LiveSession, app_id: int, job: dict) -> str | None:
    letter = await cover_letter(job)
    path = SHOTS_DIR / f"{app_id}_cover_letter.pdf"
    try:
        page = await session.context.new_page()
        import html as _h
        await page.set_content("<html><body style='font-family:Georgia,serif;font-size:12pt;line-height:1.5;"
                               "padding:48px;white-space:pre-wrap'>" + _h.escape(letter) + "</body></html>")
        await page.pdf(path=str(path), format="A4")
        await page.close()
        await session.page.bring_to_front()
        return str(path)
    except Exception:
        return None


# ------------------------------------------------------------------ dashboard actions
async def approve_submit(app_id: int) -> str:
    if app_id not in _parked:
        return "no-session"
    return await submit(app_id)


async def retry(app_id: int) -> None:
    _release(app_id)
    db.execute("UPDATE applications SET status='queued', error=NULL WHERE id=?", (app_id,))
    add_event(app_id, "retry", "Retrying")
    _queue.put_nowait(app_id)


async def cancel(app_id: int) -> None:
    _release(app_id)
    set_status(app_id, "skipped", error="Cancelled by you")
    add_event(app_id, "skipped", "Cancelled")


def save_user_answer(label: str, answer: str) -> None:
    remember(label, answer, "me")


def queue_size() -> int:
    return _queue.qsize()


def parked_ids() -> list[int]:
    return list(_parked)

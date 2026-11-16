"""Tracker: read company emails over IMAP (read-only), match them to applications, sort them, update status.

Only mails that match an application or come from a hiring system are stored. Personal mail is never saved.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import json
import re
import time
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from rapidfuzz import fuzz

from . import db, llm
from .config import env
from .events import logline, publish
from .sources.common import strip_html

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY, at REAL, from_name TEXT, from_addr TEXT, subject TEXT, snippet TEXT, body TEXT,
    application_id INTEGER, category TEXT, is_read INTEGER DEFAULT 0, match_score REAL
);
CREATE INDEX IF NOT EXISTS ix_emails_app ON emails(application_id);
""")

ATS_SENDERS = re.compile(r"greenhouse|lever\.co|ashbyhq|workable|myworkday|smartrecruiters|icims|taleo|successfactors|"
                         r"oraclecloud|jobvite|bamboohr|recruitee|teamtailor|personio|zohorecruit|darwinbox|turbohire|"
                         r"hackerrank|hackerearth|codility|mettl|talent|careers|recruit|hiring|jobs@|noreply.*(job|career)", re.I)

CATEGORY_RULES = [
    ("rejected", re.compile(r"unfortunately|not (be )?moving forward|move forward with other|will not be proceeding|"
                            r"decided not to|not been selected|regret to inform|position has been filled|other candidates whose|"
                            r"no longer under consideration|not a match at this time", re.I)),
    ("interview", re.compile(r"\binterview\b|schedule (a|your) (call|chat|conversation)|calendly|book a time|"
                             r"your availability|phone screen|meet the team|next round", re.I)),
    ("assessment", re.compile(r"assessment|hackerrank|hackerearth|codility|mettl|coding (challenge|test|round)|online test|"
                              r"take[- ]home|aptitude test|test link", re.I)),
    ("confirmed", re.compile(r"thank(s| you)( so much)? for (applying|your application|your interest)|received your application|"
                             r"application (has been |was )?(received|submitted)|we have your application|application confirmation|"
                             r"successfully (applied|submitted)", re.I)),
]
STATUS_FOR = {"confirmed": "confirmed", "assessment": "replied", "interview": "interview", "rejected": "rejected"}
RANK = {"queued": 0, "applying": 1, "review": 1, "needs_you": 1, "submitted": 2, "confirmed": 3, "replied": 4,
        "interview": 5, "rejected": 5}


def configured() -> bool:
    return bool(env("IMAP_USER") and env("IMAP_PASSWORD"))


def _dec(s: str | None) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def _body(msg: email.message.Message) -> str:
    text, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            s = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not text:
            text = s
        elif ctype == "text/html" and not html:
            html = s
    return (text or strip_html(html)).strip()[:20000]


def _fetch_sync(since_days: int, seen: set[str]) -> list[dict]:
    host = env("IMAP_HOST", "imap.gmail.com")
    m = imaplib.IMAP4_SSL(host, 993)
    m.login(env("IMAP_USER"), env("IMAP_PASSWORD"))
    out = []
    try:
        m.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.localtime(time.time() - since_days * 86400))
        typ, data = m.search(None, f'(SINCE "{since}")')
        ids = data[0].split()[-400:]
        for i in reversed(ids):
            typ, hdr = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])")
            if not hdr or not hdr[0]:
                continue
            h = email.message_from_bytes(hdr[0][1])
            mid = (h.get("Message-ID") or f"{i.decode()}-{h.get('Date')}").strip()
            if mid in seen:
                continue
            name, addr = parseaddr(_dec(h.get("From")))
            subject = _dec(h.get("Subject"))
            out.append({"num": i, "id": mid, "from_name": name, "from_addr": addr, "subject": subject,
                        "date": h.get("Date")})
        # fetch bodies only for plausible hiring mail
        for e in out:
            if not (ATS_SENDERS.search(e["from_addr"] + " " + e["from_name"]) or
                    re.search(r"applica|interview|candida|position|role|opportunit|thank you for|assessment", e["subject"], re.I)):
                e["skip"] = True
                continue
            typ, raw = m.fetch(e["num"], "(BODY.PEEK[])")
            if raw and raw[0]:
                e["body"] = _body(email.message_from_bytes(raw[0][1]))
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return [e for e in out if not e.get("skip")]


def _categorise(subject: str, body: str) -> str:
    text = subject + "\n" + body[:4000]
    for cat, rx in CATEGORY_RULES:
        if rx.search(text):
            return cat
    return "other"


def _match(e: dict, apps: list[dict]) -> tuple[int | None, float]:
    hay = f"{e['from_name']} {e['from_addr']} {e['subject']} {e.get('body','')[:3000]}".lower()
    domain = e["from_addr"].split("@")[-1].lower()
    best, best_score = None, 0.0
    for a in apps:
        comp = (a["company"] or "").lower().strip()
        if not comp:
            continue
        slug = re.sub(r"[^a-z0-9]", "", comp)
        s = 0.0
        if re.search(rf"\b{re.escape(comp)}\b", hay):
            s += 55
        elif slug and slug in re.sub(r"[^a-z0-9]", "", domain):
            s += 50
        elif fuzz.partial_ratio(comp, e["from_name"].lower()) >= 90:
            s += 45
        title = (a["title"] or "").lower()
        if title and title[:40] in hay:
            s += 35
        elif title and fuzz.token_set_ratio(title, e["subject"].lower()) >= 75:
            s += 20
        if e.get("at") and a.get("submitted_at") and e["at"] >= a["submitted_at"] - 600:
            s += 10
        if s > best_score:
            best, best_score = a["id"], s
    return (best, best_score) if best_score >= 55 else (None, best_score)


async def poll(since_days: int = 21) -> dict:
    if not configured():
        return {"configured": False}
    seen = {r["id"] for r in db.query("SELECT id FROM emails")}
    try:
        mails = await asyncio.to_thread(_fetch_sync, since_days, seen)
    except Exception as e:
        logline(f"Inbox check failed: {e}", "warning")
        return {"configured": True, "error": str(e)}
    apps = db.query("SELECT a.id, a.status, j.company, j.title, "
                    "(SELECT MIN(at) FROM events ev WHERE ev.application_id=a.id AND ev.step='submitted') submitted_at "
                    "FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.status NOT IN ('queued','skipped','dry_run')")
    stored = 0
    for e in mails:
        try:
            e["at"] = parsedate_to_datetime(e["date"]).timestamp() if e.get("date") else time.time()
        except Exception:
            e["at"] = time.time()
        body = e.get("body", "")
        cat = _categorise(e["subject"], body)
        app_id, score = _match(e, apps)
        if app_id is None and cat == "other":
            continue  # not about one of our applications: do not keep it
        if app_id is None and llm.enabled() and apps:
            app_id = await _llm_match(e, apps)
        db.execute("INSERT OR IGNORE INTO emails(id,at,from_name,from_addr,subject,snippet,body,application_id,category,match_score) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (e["id"], e["at"], e["from_name"], e["from_addr"], e["subject"], body[:240].replace("\n", " "),
                    body, app_id, cat, score))
        stored += 1
        if app_id:
            _apply_status(app_id, cat, e)
    if stored:
        publish("inbox", new=stored)
        logline(f"Tracker: {stored} new company emails.")
    db.kv_set("inbox_last", {"at": time.time(), "new": stored})
    return {"configured": True, "new": stored}


async def _llm_match(e: dict, apps: list[dict]) -> int | None:
    cands = [{"id": a["id"], "company": a["company"], "title": a["title"]} for a in apps[-60:]]
    out = await llm.chat("Match this email to one job application, or none. Return JSON {\"id\": int|null}.",
                         json.dumps({"email": {"from": f"{e['from_name']} <{e['from_addr']}>", "subject": e["subject"],
                                               "body": e.get("body", "")[:1500]}, "applications": cands}), max_tokens=50)
    try:
        v = (out or {}).get("id")
        return int(v) if v is not None and any(a["id"] == int(v) for a in apps) else None
    except Exception:
        return None


def _apply_status(app_id: int, cat: str, e: dict) -> None:
    from .apply.engine import add_event, set_status
    new = STATUS_FOR.get(cat)
    cur = db.one("SELECT status FROM applications WHERE id=?", (app_id,))
    if not cur:
        return
    if new and RANK.get(new, 0) > RANK.get(cur["status"], 0):
        set_status(app_id, new)
    step = {"confirmed": "confirmed", "assessment": "replied", "interview": "replied", "rejected": "rejected"}.get(cat, "email")
    add_event(app_id, step, f"Email from {e['from_name'] or e['from_addr']}: {e['subject']}")


def mark_read(email_id: str) -> None:
    db.execute("UPDATE emails SET is_read=1 WHERE id=?", (email_id,))


async def loop() -> None:
    await asyncio.sleep(15)
    while True:
        if configured():
            try:
                await poll()
            except Exception as e:
                logline(f"Inbox loop error: {e}", "warning")
        await asyncio.sleep(300)

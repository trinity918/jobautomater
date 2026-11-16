"""Answers for application form fields: rules -> answer bank -> LLM -> ask the user."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from rapidfuzz import fuzz, process

from .. import db, llm
from ..config import load_profile
from ..resume import current as current_resume
from ..ranker import INDIA_PLACES


def norm(label: str) -> str:
    s = re.sub(r"\s+", " ", (label or "").lower())
    s = re.sub(r"[*:?()\[\]✱]", "", s)
    return s.strip()[:200]


def _p(path: str, default: Any = "") -> Any:
    cur: Any = load_profile()
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return default if cur in (None, "") else cur


def _yn(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    s = str(v).strip().lower()
    return "Yes" if s in ("yes", "y", "true", "1") else "No" if s in ("no", "n", "false", "0") else str(v)


def job_in_india(job: dict) -> bool:
    loc = (job.get("location") or "").lower()
    return any(re.search(rf"\b{re.escape(p)}\b", loc) for p in INDIA_PLACES) or (
        job.get("remote") and "india" in loc)


def _full_name() -> str:
    return _p("personal.full_name") or f"{_p('personal.first_name')} {_p('personal.last_name')}".strip()


def _phone(local: bool = False) -> str:
    ph = str(_p("personal.phone"))
    if local:
        return re.sub(r"^\+?91[\s-]?", "", ph)
    cc = str(_p("personal.phone_country_code", "+91"))
    return ph if ph.startswith("+") else f"{cc} {ph}".strip()


def _address(full: bool = True) -> str:
    """The address from info.txt / profile. full=True (a single "Address" box) adds city, state and PIN when the
    address line does not already contain them; full=False ("Address line 1") is the street part only."""
    a = str(_p("personal.address") or "")
    if not full and a:
        return a
    parts = [a] if a else []
    for extra in (_p("personal.city"), _p("personal.state"), _p("personal.pincode")):
        if extra and str(extra).lower() not in a.lower():
            parts.append(str(extra))
    return ", ".join(parts)


def _relocation(field: dict) -> str:
    """'Are you in Pune? If not, will you relocate?' -> the option that is true for you."""
    city = str(_p("personal.city")).strip().lower()
    label = (field.get("label") or "").lower()
    if city and city in label:
        return f"Currently residing in {city}|I am based in {city}|Yes"
    if _p("work.willing_to_relocate", True):
        return "open to relocating|willing to relocate|Yes"
    return "Not open to relocating|No"


def _ctc(kind: str, job: dict) -> str:
    lpa = _p(f"work.{kind}_ctc_lpa")
    if lpa in ("", None):
        return ""
    try:
        rupees = int(float(lpa) * 100000)
    except ValueError:
        return str(lpa)
    return str(rupees)


PITCH_RE = (r"what makes you|why (do )?you (want|wish)|why are you interested|why (this|our) (role|company|team)|"
            r"tell us (about yourself|why)|why should we|motivat|why .* join|about yourself|cover note")

# (pattern on the normalised label, function(job, field) -> answer). First match wins.
RULES: list[tuple[str, Any]] = [
    # referral questions first: "share their full name / email" must not get your own name or email
    (r"referr(ed|al|er)|who referred",
     lambda j, f: (_p("answers_defaults.referral") or ("No" if f.get("options") else "N/A"))
     if not re.search(r"^(were|are) you|did (someone|anyone)", norm(f.get("label", ""))) else
     ("Yes" if _p("answers_defaults.referral") else "No")),
    (r"outside (business )?activit|side business|board (roles|member)|moonlight|conflict(s)? of interest|other employment",
     lambda j, f: "No"),
    (r"(have you|were you|are you)( ever)?( been)? (previously |currently )?(employed|worked) (by|for|at|with)|"
     r"previously (worked|been employed)|ever worked (for|at)|former employee|past employee",
     lambda j, f: "Yes" if (j.get("company") or "~").lower() in [c.lower() for c in (_p("work.previous_employers", []) or [])] else "No"),
    # open "why you / why us" questions: your own pitch from profile.yaml when no LLM is set (the LLM tailors one per job)
    (PITCH_RE,
     lambda j, f: "" if llm.enabled() else str(_p("answers_defaults.pitch", ""))
     .replace("{company}", j.get("company") or "your team").replace("{role}", j.get("title") or "this role")),
    (r"\bfirst name|given name|forename", lambda j, f: _p("personal.first_name")),
    (r"\blast name|surname|family name", lambda j, f: _p("personal.last_name")),
    (r"\bmiddle name", lambda j, f: _p("personal.middle_name", " ").strip() or ""),
    (r"preferred (first )?name|nickname", lambda j, f: _p("personal.preferred_name") or _p("personal.first_name")),
    (r"^(full )?name$|^your name|^legal name|^candidate name|full legal name", lambda j, f: _full_name()),
    (r"e-?mail", lambda j, f: _p("personal.email")),
    (r"country code|phone code", lambda j, f: _p("personal.phone_country_code", "+91")),
    (r"phone|mobile|contact number|whatsapp", lambda j, f: _phone(local=f.get("type") == "tel" and "country" in json.dumps(f.get("siblings", "")).lower())),
    (r"linkedin", lambda j, f: _p("personal.linkedin")),
    (r"github", lambda j, f: _p("personal.github")),
    (r"portfolio|personal (web)?site|website|blog|other (link|url)", lambda j, f: _p("personal.portfolio") or _p("personal.github")),
    (r"twitter|\bx\.com", lambda j, f: _p("personal.twitter")),
    (r"leetcode", lambda j, f: _p("personal.leetcode")),
    (r"^(current )?(location|city)|city of residence|where are you (currently )?(based|located)|current address city",
     lambda j, f: _p("personal.city") + (", " + _p("personal.state") if _p("personal.state") and f.get("type") != "combobox" else "")),
    (r"^state|province", lambda j, f: _p("personal.state")),
    (r"country of residence|^country$|which country (do )?you (currently )?(live|reside)", lambda j, f: _p("personal.country", "India")),
    (r"pin ?code|postal|zip", lambda j, f: _p("personal.pincode")),
    (r"address line ?2|address 2|street address 2|apartment|suite", lambda j, f: _p("personal.address_line2")),
    (r"address line ?1|address 1|street", lambda j, f: _p("personal.address_line1") or _address(full=False)),
    (r"address", lambda j, f: _address()),
    (r"current (company|employer|organi[sz]ation)|^company$|most recent (company|employer)", lambda j, f: _p("work.current_company")),
    (r"current (title|role|designation|position)|job title", lambda j, f: _p("work.current_title")),
    (r"(total |years of |how many years).*(experience)|experience \(years\)|^experience$",
     lambda j, f: str(_p("work.years_experience", (current_resume().get("profile") or {}).get("years_experience", 0)))),
    (r"notice period", lambda j, f: _p("work.notice_period", "Immediate")),
    (r"(earliest|when).*(start|join)|start date|joining date|availability", lambda j, f: _p("work.earliest_start", "Immediately")),
    (r"current (ctc|salary|compensation)|present (ctc|salary)",
     lambda j, f: _ctc("current", j) or ("0" if _p("education.currently_student", False) or not _p("work.current_company") else "")),
    (r"expected (ctc|salary|compensation)|salary expectation|desired (salary|compensation)|compensation expectation",
     lambda j, f: (_ctc("expected", j) or ("" if f.get("type") == "number" else "Negotiable|As per company standards|Open to discuss"))
     if job_in_india(j) else _p("work.expected_salary_abroad", "Open to discuss")),
    (r"source of (your )?right to work|basis (of|for) (your )?(right|authori[sz]ation)|type of work authori[sz]ation",
     lambda j, f: (_p("work.right_to_work_basis", "Citizen") + "|Citizen|Citizenship|National") if job_in_india(j) else "Visa|Work permit|Other"),
    (r"^(were|are) you (being )?referred|did (someone|anyone|an employee) refer|referred by an? (current )?employee\b$",
     lambda j, f: "Yes" if _p("answers_defaults.referral") else "No"),
    (r"(currently )?(residing|based|living|located) in .*(relocat|willing)|willing to relocate to",
     lambda j, f: _relocation(f)),
    (r"(legally )?authori[sz]ed to work|work authori[sz]ation|eligible to work|right to work",
     lambda j, f: "Yes" if job_in_india(j) or j.get("remote") and _p("work.remote_anywhere_ok", True) else _yn(_p("work.authorized_abroad", False))),
    (r"sponsorship|visa", lambda j, f: "No" if job_in_india(j) or j.get("remote") else _yn(_p("work.needs_sponsorship_abroad", True))),
    (r"relocat", lambda j, f: _yn(_p("work.willing_to_relocate", True))),
    (r"(on-?site|in office|hybrid|commute|work from (the )?office)", lambda j, f: _yn(_p("work.ok_onsite", True))),
    (r"(18|eighteen) years|of legal age|at least 18", lambda j, f: "Yes"),
    (r"previously (worked|been employed)|ever worked (for|at)|former employee", lambda j, f: "No"),
    (r"related to|relative|family member.*(work|employ)", lambda j, f: "No"),
    (r"background check|drug (test|screen)", lambda j, f: "Yes"),
    (r"non-?compete|bound by", lambda j, f: "No"),
    # school marks before the generic "school / percentage" rules
    (r"\b(12th|xii|hsc|higher secondary|class 12|intermediate)\b", lambda j, f: str(_p("education.twelfth_percent"))),
    (r"\b(10th|ssc|class 10|class x|secondary school|matriculation)\b", lambda j, f: str(_p("education.tenth_percent"))),
    (r"(are you|currently) (a )?(student|enrolled|pursuing)|still (studying|in college)",
     lambda j, f: _yn(_p("education.currently_student", False))),
    (r"graduation month|month of (graduation|passing)", lambda j, f: str(_p("education.graduation_month"))),
    (r"degree|qualification|highest (level of )?education", lambda j, f: _p("education.degree")),
    (r"(field|area) of study|major|discipline|specialization", lambda j, f: _p("education.field")),
    (r"university|college|school|institution", lambda j, f: _p("education.university")),
    (r"graduation (year|date)|year of (graduation|passing)|passing year|batch", lambda j, f: str(_p("education.graduation_year"))),
    (r"cgpa|gpa|percentage|grade", lambda j, f: str(_p("education.cgpa"))),
    (r"how did you (hear|find|learn)|where did you (hear|find)|referr(al|ed) source|^source$",
     lambda j, f: _p("answers_defaults.how_heard", "Company careers page")
     + "|Careers Website|Career site|Company website|Careers page|Website|Job board|Other"),
    (r"referred by|referr(er|al) name|employee referral|who referred", lambda j, f: _p("answers_defaults.referral", "") or "N/A"),
    (r"pronoun", lambda j, f: _p("eeo.pronouns", "Prefer not to say")),
    (r"gender|sex\b", lambda j, f: _p("eeo.gender", "Prefer not to say")),
    (r"race|ethnic", lambda j, f: _p("eeo.race", "Decline to self-identify")),
    (r"veteran", lambda j, f: _p("eeo.veteran", "I am not a protected veteran")),
    (r"disabilit", lambda j, f: _p("eeo.disability", "I do not wish to answer")),
    (r"hispanic|latino", lambda j, f: _p("eeo.hispanic", "Decline to self-identify")),
    (r"sexual orientation|lgbt", lambda j, f: "Prefer not to say"),
    (r"date of birth|\bdob\b|birth ?date", lambda j, f: _p("personal.date_of_birth")),
    (r"nationality|citizenship", lambda j, f: _p("personal.nationality", "Indian")),
    (r"language", lambda j, f: _p("personal.languages", "English")),
    (r"(privacy|consent|agree|acknowledge|terms|data processing|certify|confirm that)", lambda j, f: "Yes"),
    (r"whatsapp (updates|opt)|sms (updates|opt)|marketing", lambda j, f: "No"),
]

SENSITIVE = re.compile(r"criminal|convict|felony|arrest|medical|health condition|religio|caste|aadhaar|pan (card|number)|passport number|ssn|social security|bank account", re.I)


def answer_bank_lookup(label: str, options: list[str] | None) -> str | None:
    rows = db.query("SELECT label_norm, answer FROM answers")
    if not rows:
        return None
    n = norm(label)
    choices = {r["label_norm"]: r["answer"] for r in rows}
    best = process.extractOne(n, list(choices), scorer=fuzz.token_sort_ratio)
    if best and best[1] >= 88:
        ans = choices[best[0]]
        if options and not pick_option(ans, options):
            return None
        db.execute("UPDATE answers SET used_count=used_count+1 WHERE label_norm=?", (best[0],))
        return ans
    return None


def remember(label: str, answer: str, source: str) -> None:
    if not label or answer in (None, ""):
        return
    db.execute("INSERT INTO answers(label_norm,label,answer,source,used_count,updated_at) VALUES(?,?,?,?,1,?) "
               "ON CONFLICT(label_norm) DO UPDATE SET answer=excluded.answer, source=excluded.source, updated_at=excluded.updated_at",
               (norm(label), label[:300], str(answer), source, time.time()))


def pick_option(answer: str, options: list[str]) -> str | None:
    """Choose the option text that best matches an answer ("Yes" -> "Yes, I am authorized...").
    An answer like "Careers page|Company website|Other" tries each alternative in order."""
    if not options:
        return None
    if "|" in str(answer):
        for alt in str(answer).split("|"):
            got = pick_option(alt, options)
            if got:
                return got
        return None
    a = str(answer).strip().lower()
    clean = [o for o in options if o and o.strip() and not re.match(r"^(select|choose|please select|--)", o.strip().lower())]
    if not clean:
        return None
    neg = re.compile(r"\b(not|no|don't|do not|never|unable|won't)\b")
    if not neg.search(a) and a not in ("no",):
        positive = [o for o in clean if not neg.search(o.lower())]
        if positive and len(positive) < len(clean):
            # try the options that do not negate first, so "open to relocating" never picks "Not open to relocating"
            got = _pick(a, positive)
            if got:
                return got
    return _pick(a, clean)


def _pick(a: str, clean: list[str]) -> str | None:
    for o in clean:
        if o.strip().lower() == a:
            return o
    # "India" must pick "India +91", not "British Indian Ocean Territory": prefer options that start with the answer
    starts = [o for o in clean if re.match(rf"^{re.escape(a)}(\b|$)", o.strip().lower())]
    if starts:
        return min(starts, key=len)
    words = [o for o in clean if re.search(rf"\b{re.escape(a)}\b", o.lower())
             and not re.search(rf"\b(not|no|never)\s+{re.escape(a)}\b", o.lower())]
    if len(a) > 3 and words:
        return min(words, key=len)
    if a in ("yes", "no"):
        for o in clean:
            ol = o.strip().lower()
            if ol.startswith(a) or (a == "yes" and re.match(r"^(i am|i do|i have|i will|i agree|i consent|true)\b", ol)
                                    and " not " not in ol and "don't" not in ol) \
                    or (a == "no" and re.search(r"\b(not|don't|do not|no,)\b", ol)):
                return o
    best = process.extractOne(a, clean, scorer=fuzz.WRatio)
    if best and best[1] >= (70 if len(clean) <= 12 else 88):
        return best[0]
    # decline-type answers: map to any "decline / prefer not" option
    if re.search(r"decline|prefer not|not wish|don't wish", a):
        for o in clean:
            if re.search(r"decline|prefer not|not wish|don't wish|not to (say|answer|disclose)", o.lower()):
                return o
    return None


LLM_FIELD_SYS = (
    "You fill one job-application form field for the candidate. Use ONLY facts from the candidate profile and resume. "
    "Never invent employers, numbers, links or credentials. If options are given, the answer must be exactly one of them "
    "(or a list of them for multi-select). For open questions like 'Why do you want to join' write 60-120 words, first "
    "person, specific to the job and grounded in the resume. Return JSON {\"answer\": str|list|null, \"confident\": bool}. "
    "Use null and confident=false when the truthful answer is not in the data."
)


async def llm_answer(field: dict, job: dict) -> tuple[Any, bool]:
    res = current_resume()
    prof = load_profile()
    safe_prof = {k: v for k, v in prof.items() if k not in ("search",)}
    payload = json.dumps({
        "field": {"label": field.get("label"), "type": field.get("type"), "options": field.get("options") or None,
                  "required": field.get("required")},
        "job": {"title": job.get("title"), "company": job.get("company"), "location": job.get("location"),
                "description": (job.get("description") or "")[:1800]},
        "candidate_profile": safe_prof,
        "resume": (res.get("text") or "")[:3500],
    }, ensure_ascii=False, default=str)
    out = await llm.chat(LLM_FIELD_SYS, payload, max_tokens=600)
    if not isinstance(out, dict):
        return None, False
    return out.get("answer"), bool(out.get("confident"))


async def resolve(field: dict, job: dict) -> dict:
    """Returns {"value": ..., "source": rule|bank|custom|llm|none, "sensitive": bool}."""
    label = field.get("label") or field.get("name") or field.get("placeholder") or ""
    n = norm(label)
    opts = field.get("options") or []
    if field.get("type") == "file":
        if re.search(r"cover", n):
            return {"value": "__cover_letter__", "source": "rule"}
        return {"value": "__resume__", "source": "rule"}

    # 1) the user's own custom answers in profile.yaml win
    custom = load_profile().get("answers") or {}
    if custom:
        best = process.extractOne(n, [norm(k) for k in custom], scorer=fuzz.token_set_ratio)
        if best and best[1] >= 90:
            key = list(custom)[[norm(k) for k in custom].index(best[0])]
            val = str(custom[key])
            return {"value": pick_option(val, opts) if opts else val, "source": "custom"}

    if field.get("type") == "boolean":
        if re.search(r"whatsapp|sms|text message|newsletter|marketing|promotional", n):
            return {"value": "No", "source": "rule"}
        if re.search(r"consent|agree|acknowledge|confirm|certify|privacy|terms|i have read|understood|accurate|true and correct", n):
            return {"value": "Yes", "source": "rule"}

    if SENSITIVE.search(n) and not re.search(r"consent|agree|privacy|acknowledge", n):
        return {"value": None, "source": "none", "sensitive": True}

    # 2) rules
    rule_hit = False
    for pat, fn in RULES:
        if re.search(pat, n):
            rule_hit = True
            try:
                v = fn(job, field)
            except Exception:
                v = ""
            if v not in ("", None):
                if field.get("type") == "boolean":
                    return {"value": _yn(v), "source": "rule"}
                if opts:
                    picked = pick_option(str(v), opts)
                    if picked:
                        return {"value": picked, "source": "rule"}
                    break  # rule had an answer but none of the options fit; let LLM pick
                if isinstance(v, str) and "|" in v:   # alternatives only make sense for option lists
                    v = v.split("|")[0]
                return {"value": v, "source": "rule"}
            break

    # 3) any other "Question: answer" line in info.txt
    from .. import info
    got = None if rule_hit else info.lookup(label)
    if got:
        if opts:
            picked = pick_option(got, opts)
            if picked:
                return {"value": picked, "source": "info.txt"}
        else:
            return {"value": got, "source": "info.txt"}

    # 4) answer bank
    bank = answer_bank_lookup(label, opts)
    if bank is not None:
        return {"value": pick_option(bank, opts) if opts else bank, "source": "bank"}

    # 5) LLM
    if llm.enabled() and label:
        ans, confident = await llm_answer(field, job)
        if ans not in (None, "", []) and confident:
            if opts and isinstance(ans, str):
                ans = pick_option(ans, opts)
            if ans:
                remember(label, ans if isinstance(ans, str) else json.dumps(ans), "llm")
                return {"value": ans, "source": "llm"}

    # the AI was off or failed: open "why you / why us" questions get your pitch
    if re.search(PITCH_RE, n) and not opts:
        pitch = str(_p("answers_defaults.pitch", ""))
        if pitch:
            return {"value": pitch.replace("{company}", job.get("company") or "your team")
                    .replace("{role}", job.get("title") or "this role"), "source": "rule"}
    return {"value": None, "source": "none"}


COVER_SYS = ("Write a concise cover letter (150-220 words) for this job from the candidate's resume. Plain text, no "
             "placeholders, no invented facts, first person, end with the candidate's name. Return JSON {\"letter\": str}.")


async def cover_letter(job: dict) -> str:
    res = current_resume()
    name = _full_name()
    if llm.enabled():
        out = await llm.chat(COVER_SYS, json.dumps({"job": {"title": job.get("title"), "company": job.get("company"),
                                                            "description": (job.get("description") or "")[:2500]},
                                                    "resume": (res.get("text") or "")[:4000], "name": name}))
        if isinstance(out, dict) and out.get("letter"):
            return out["letter"]
    skills = ", ".join((res.get("profile") or {}).get("skills", [])[:6])
    return (f"Dear Hiring Team,\n\nI am applying for the {job.get('title')} role at {job.get('company')}. "
            f"My experience with {skills} matches what this role asks for, and I would be glad to bring it to your team. "
            f"My resume is attached with details of my recent work and projects.\n\nThank you for your time.\n\n{name}")

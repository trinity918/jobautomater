"""Compatibility ranking: a local 0-100 score from six signals, with an optional LLM rerank on top."""
from __future__ import annotations

import json
import re
import time

import math
from collections import Counter

from rapidfuzz import fuzz

from . import db, llm
from .config import load_profile, load_settings
from .events import logline, publish
from .resume import current as current_resume
from .skills import find_skills

INDIA_PLACES = [
    "india", "bengaluru", "bangalore", "mumbai", "pune", "hyderabad", "chennai", "delhi", "new delhi", "gurgaon",
    "gurugram", "noida", "kolkata", "ahmedabad", "jaipur", "kochi", "cochin", "thiruvananthapuram", "trivandrum",
    "coimbatore", "chandigarh", "indore", "nagpur", "mysore", "mysuru", "vadodara", "surat", "lucknow", "bhubaneswar",
    "visakhapatnam", "goa", "mangalore", "navi mumbai", "thane", "ncr", "karnataka", "maharashtra", "telangana",
    "tamil nadu", "kerala", "haryana", "uttar pradesh", "gujarat", "west bengal",
]
GLOBAL_REMOTE = ["worldwide", "anywhere", "global", "apac", "asia", "emea & apac", "international", "any location",
                 "india", "south asia", "gmt+5", "utc+5", "ist"]
SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|architect|vp|chief|distinguished|iii|iv)\b", re.I)
ENTRY_RE = re.compile(r"\b(intern|internship|trainee|graduate|fresher|entry[- ]level|junior|jr\.?|associate|new grad|apprentice|campus|early career|level 1|engineer i\b|sde[- ]?1|sde i\b)\b", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years|yrs)(?:'|’)?\s*(?:of)?\s*(?:\w+\s){0,3}?(?:experience|exp)", re.I)
MNC_NAMES = {"google", "microsoft", "amazon", "meta", "apple", "ibm", "oracle", "sap", "accenture", "deloitte",
             "pwc", "kpmg", "ey", "nvidia", "intel", "adobe", "salesforce", "walmart", "cisco", "dell", "hp", "visa",
             "mastercard", "bosch", "siemens", "barclays", "citi", "jpmorgan", "goldman sachs", "morgan stanley",
             "infosys", "wipro", "tcs", "hcl", "capgemini", "cognizant", "paypal", "uber", "netflix", "atlassian",
             "servicenow", "workday", "qualcomm", "boeing", "shell", "unilever", "freshworks", "experian"}


# ------------------------------------------------------------------ search prefs
_prefs_cache: tuple[float, dict] = (0.0, {})


def prefs() -> dict:
    """Search preferences from profile.yaml, cached for 10 s (discovery checks 18,000 locations in a row)."""
    global _prefs_cache
    if time.time() - _prefs_cache[0] < 10:
        return _prefs_cache[1]
    out = _read_prefs()
    _prefs_cache = (time.time(), out)
    return out


def _read_prefs() -> dict:
    p = load_profile().get("search", {}) or {}
    return {
        "india": p.get("india", True),
        "remote_india": p.get("remote_india", True),
        "remote_worldwide": p.get("remote_worldwide", True),
        "abroad": [a.lower() for a in (p.get("abroad") or [])],
        "cities": [c.lower() for c in (p.get("preferred_cities") or [])],
        "exclude_keywords": [k.lower() for k in (p.get("exclude_keywords") or [])],
        "exclude_companies": [k.lower() for k in (p.get("exclude_companies") or [])],
        "target_titles": p.get("target_titles") or [],
        "experience": p.get("experience"),  # fresher | experienced | None (derive)
    }


def location_fit(location: str, remote: bool, pf: dict | None = None) -> float:
    pf = pf or prefs()
    loc = (location or "").lower()
    in_india = any(re.search(rf"\b{re.escape(p)}\b", loc) for p in INDIA_PLACES)
    if remote:
        if in_india and pf["remote_india"]:
            return 1.0
        bare = re.sub(r"[^a-z]", "", loc) in ("remote", "", "remoteremote", "fullyremote", "remotefirst")
        if pf["remote_worldwide"] and (bare or any(re.search(rf"\b{re.escape(g)}\b", loc) for g in GLOBAL_REMOTE)):
            return 0.95
        return 0.25  # remote, but only for people in, say, the US or EU
    if in_india and pf["india"]:
        if pf["cities"] and not any(c in loc for c in pf["cities"]):
            return 0.75
        return 1.0
    if pf["abroad"] and any(a in loc for a in pf["abroad"]):
        return 0.9
    return 0.0


def location_ok(location: str, remote: bool) -> bool:
    return location_fit(location, remote) >= 0.5


def employer_type(job: dict) -> str:
    name = (job.get("company") or "").lower()
    if job.get("ats") in ("workday", "smartrecruiters") or any(n == name or name.startswith(n + " ") for n in MNC_NAMES):
        return "mnc"
    return "startup"


def job_level(job: dict) -> tuple[str, int | None]:
    title = job.get("title") or ""
    desc = (job.get("description") or "")[:6000]
    years = [int(m.group(1)) for m in YEARS_RE.finditer(desc) if int(m.group(1)) < 25]
    req = min(years) if years else None
    if SENIOR_RE.search(title):  # before entry: "Senior Software Engineer I" is senior
        return "senior", req
    if ENTRY_RE.search(title):
        return "entry", req
    if req is not None:
        return ("entry" if req <= 1 else "mid" if req <= 4 else "senior"), req
    return "unknown", None


def level_fit(job: dict, years: float, seniority: str) -> float:
    lvl, req = job_level(job)
    fresher = seniority in ("fresher", "junior") and years < 2
    if fresher:
        if lvl == "entry":
            return 1.0
        if lvl == "senior":
            return 0.05
        if req is not None:
            return 1.0 if req <= 1 else 0.6 if req == 2 else 0.3 if req == 3 else 0.05
        return 0.6
    if lvl == "entry" and "intern" in (job.get("title") or "").lower():
        return 0.2
    if req is None:
        return 0.8 if lvl != "senior" or years >= 5 else 0.5
    if req <= years + 1:
        return 1.0
    gap = req - years
    return max(0.05, 1 - 0.25 * gap)


# ------------------------------------------------------------------ TF-IDF (pure Python: no compiled ML libraries,
# so Windows Smart App Control has nothing to block)
_STOP = set("""a about above after again against all am an and any are as at be because been before being below between
both but by can did do does doing down during each few for from further had has have having he her here hers him his how
i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over own same
she should so some such than that the their theirs them then there these they this those through to too under until up
very was we were what when where which while who whom why will with you your yours us also etc may must per via within
across including include includes work working team teams role roles job jobs company candidate candidates experience
experienced year years strong ability good great excellent new using use used well based help like make able looking
join opportunity opportunities responsibilities requirements qualifications preferred required plus bonus""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9+#]{1,30}")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def tfidf_similarities(query: str, docs: list[str]) -> list[float]:
    toks = [_tokens(d) for d in docs]
    qtok = _tokens(query)
    n = len(docs) + 1
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    df.update(set(qtok))
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}

    def vec(tokens: list[str]) -> tuple[dict, float]:
        tf = Counter(tokens)
        v = {w: (1 + math.log(c)) * idf[w] for w, c in tf.items()}
        return v, math.sqrt(sum(x * x for x in v.values())) or 1.0

    qv, qn = vec(qtok)
    out = []
    for t in toks:
        dv, dn = vec(t)
        dot = sum(w * qv[k] for k, w in dv.items() if k in qv)
        out.append(dot / (qn * dn))
    return out


# ------------------------------------------------------------------ scoring
def _profile_bundle() -> dict | None:
    res = current_resume()
    if not res.get("text"):
        return None
    prof = res.get("profile") or {}
    pf = prefs()
    titles = pf["target_titles"] or prof.get("target_titles") or []
    seniority = prof.get("seniority", "junior")
    if pf["experience"] == "fresher":
        seniority = "fresher"
    elif pf["experience"] == "experienced" and seniority == "fresher":
        seniority = "junior"
    return {"text": res["text"], "skills": set(prof.get("skills") or []), "titles": titles,
            "years": float(prof.get("years_experience") or 0), "seniority": seniority, "prefs": pf}


def score_jobs(jobs: list[dict], bundle: dict) -> list[dict]:
    if not jobs:
        return []
    docs = [f"{j['title']} {j['title']} {(j.get('description') or '')[:6000]}" for j in jobs]
    sims = tfidf_similarities(bundle["text"], docs)
    top = max(max(sims, default=0), 1e-6)
    now = time.time()
    out = []
    new_skills: list[tuple] = []
    for j, sim in zip(jobs, sims):
        title = j.get("title") or ""
        tl = title.lower()
        pf = bundle["prefs"]
        if any(k in tl for k in pf["exclude_keywords"]) or (j.get("company") or "").lower() in pf["exclude_companies"]:
            out.append({**j, "local_score": 0, "breakdown": {"excluded": True}, "reason": "Excluded by your filters"})
            continue
        jskills_title = set(find_skills(title))
        if j.get("skills"):
            jskills = set(json.loads(j["skills"]))
        else:
            jskills = set(find_skills(title + " " + (j.get("description") or "")[:8000]))
            new_skills.append((json.dumps(sorted(jskills)), j["id"]))
        if jskills:
            weight_total = len(jskills) + len(jskills_title)
            hit = len(jskills & bundle["skills"]) + len(jskills_title & bundle["skills"])
            s_skills = min(1.0, hit / max(1, weight_total) * 1.6)  # matching ~60% of asks is a full mark
        else:
            s_skills = 0.4
        s_tfidf = min(1.0, float(sim) / top * 1.1)
        s_title = max((fuzz.token_set_ratio(tl, t.lower()) / 100 for t in bundle["titles"]), default=0.5)
        s_title = s_title ** 1.5
        s_loc = location_fit(j.get("location", ""), bool(j.get("remote")), pf)
        s_level = level_fit(j, bundle["years"], bundle["seniority"])
        age = (now - j["posted_at"]) / 86400 if j.get("posted_at") else None
        s_fresh = 0.5 if age is None else max(0.0, 1 - age / 30)
        score = 100 * (0.35 * s_skills + 0.20 * s_tfidf + 0.20 * s_title + 0.10 * s_loc + 0.10 * s_level + 0.05 * s_fresh)
        if s_level < 0.2:
            score *= 0.6  # a fresher should not see staff roles near the top
        if s_loc < 0.5:
            score *= 0.3  # not somewhere you can work
        if s_title < 0.35 and s_skills < 0.5:
            score *= 0.75  # a different kind of job (sales, ops, HR) that happens to share a few words
        matched = sorted(jskills & bundle["skills"], key=lambda s: s not in jskills_title)[:5]
        missing = sorted(jskills - bundle["skills"])[:3]
        lvl, req = job_level(j)
        bits = []
        if matched:
            bits.append("Matches " + ", ".join(matched))
        if missing:
            bits.append("asks for " + ", ".join(missing))
        bits.append({"entry": "entry-level", "mid": "mid-level", "senior": "senior", "unknown": "level not stated"}[lvl]
                    + (f" ({req}+ yrs)" if req else ""))
        out.append({**j, "local_score": round(score, 1), "reason": " · ".join(bits),
                    "breakdown": {"skills": round(s_skills, 2), "tfidf": round(s_tfidf, 2), "title": round(s_title, 2),
                                  "location": round(s_loc, 2), "level": round(s_level, 2), "fresh": round(s_fresh, 2),
                                  "matched": matched, "missing": missing, "level_label": lvl, "years_req": req,
                                  "employer_type": employer_type(j)}})
    if new_skills:
        db.executemany("UPDATE jobs SET skills=? WHERE id=?", new_skills)
    return out


def rank_all(only_unscored: bool = False) -> int:
    bundle = _profile_bundle()
    if not bundle:
        return 0
    where = "WHERE status != 'dismissed'" + (" AND local_score IS NULL" if only_unscored else "")
    jobs = db.query(f"SELECT id,title,company,location,remote,description,posted_at,ats,llm_score,skills FROM jobs {where}")
    if not jobs:
        return 0
    scored = score_jobs(jobs, bundle)
    rows = []
    for j in scored:
        final = j["local_score"] if j.get("llm_score") is None else round(0.6 * j["local_score"] + 0.4 * j["llm_score"], 1)
        rows.append((j["local_score"], final, j["reason"] if j.get("llm_score") is None else None,
                     json.dumps(j["breakdown"]), j["id"]))
    db.executemany("UPDATE jobs SET local_score=?, score=?, reason=COALESCE(?, reason), breakdown=? WHERE id=?", rows)
    logline(f"Ranked {len(rows)} jobs against your resume.")
    publish("jobs_ranked", count=len(rows))
    return len(rows)


RERANK_SYS = (
    "You are a strict technical recruiter in India. Score how well each job fits this candidate, 0-100. "
    "Weigh: real skill overlap, seniority fit (a fresher cannot do a senior role), domain fit, location fit. "
    "Return JSON {\"results\":[{\"id\":str,\"score\":int,\"reason\":str}]} with a reason of at most 15 words."
)


async def llm_rerank(limit: int | None = None) -> int:
    if not llm.enabled():
        return 0
    res = current_resume()
    if not res.get("profile"):
        return 0
    limit = limit or int(load_settings().get("llm_rerank_top_n", 40))
    jobs = db.query("SELECT id,title,company,location,description FROM jobs WHERE status='new' AND llm_score IS NULL "
                    "AND local_score IS NOT NULL ORDER BY local_score DESC LIMIT ?", (limit,))
    prof = res["profile"]
    cand = json.dumps({"skills": prof.get("skills", [])[:40], "years_experience": prof.get("years_experience"),
                       "seniority": prof.get("seniority"), "target_titles": prof.get("target_titles"),
                       "resume_excerpt": res.get("text", "")[:2500]}, ensure_ascii=False)
    done = 0
    for i in range(0, len(jobs), 8):
        batch = jobs[i:i + 8]
        payload = cand + "\n\nJOBS:\n" + json.dumps(
            [{"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
              "description": (j["description"] or "")[:900]} for j in batch], ensure_ascii=False)
        out = await llm.chat(RERANK_SYS, payload, max_tokens=1200)
        for r in (out or {}).get("results", []) if isinstance(out, dict) else []:
            try:
                jid, sc = str(r["id"]), max(0, min(100, int(r["score"])))
            except Exception:
                continue
            row = db.one("SELECT local_score FROM jobs WHERE id=?", (jid,))
            if not row:
                continue
            final = round(0.6 * (row["local_score"] or 0) + 0.4 * sc, 1)
            db.execute("UPDATE jobs SET llm_score=?, score=?, reason=? WHERE id=?", (sc, final, r.get("reason", "")[:200], jid))
            done += 1
    if done:
        logline(f"LLM reranked {done} top jobs.")
        publish("jobs_ranked", count=done)
    return done

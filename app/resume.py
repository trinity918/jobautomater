"""Resume parsing: file -> text -> structured resume JSON + a matching profile.

Structured resume (what the Resume studio edits and the templates render):
{
  "name": str, "headline": str, "contact": {"email","phone","location","links":[...]},
  "sections": [
     {"title": "Experience", "kind": "items", "items": [{"heading","sub","date","bullets":[...]}]},
     {"title": "Skills", "kind": "text", "text": "..."}
  ]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db, llm
from .config import RESUME_DIR
from .events import logline
from .skills import find_skills

SECTION_WORDS = {
    "summary": "Summary", "profile": "Summary", "objective": "Summary", "about": "Summary",
    "experience": "Experience", "work experience": "Experience", "professional experience": "Experience",
    "employment": "Experience", "internships": "Experience", "internship": "Experience",
    "projects": "Projects", "personal projects": "Projects", "academic projects": "Projects",
    "skills": "Skills", "technical skills": "Skills", "core skills": "Skills",
    "education": "Education", "certifications": "Certifications", "certificates": "Certifications",
    "achievements": "Achievements", "awards": "Achievements", "publications": "Publications",
    "positions of responsibility": "Leadership", "leadership": "Leadership", "extracurricular": "Activities",
    "activities": "Activities", "languages": "Languages", "interests": "Interests",
}
TEXT_SECTIONS = {"Summary", "Skills", "Languages", "Interests"}
DATE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{4}[-/](?:0[1-9]|1[0-2])(?!\d)|\b(?:19|20)\d{2}\b)"
    r"(\s*(?:-|–|—|to)\s*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{4}[-/](?:0[1-9]|1[0-2])(?!\d)|\b(?:19|20)\d{2}\b|present|current|now))?",
    re.I,
)
BULLET_RE = re.compile(r"^\s*[•●▪◦‣∙·\-\*–]\s*")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?\(?\d{3,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,5}")
LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w\-%/]+|github\.com/[\w\-]+|[a-z0-9-]{3,}\.(?:dev|io|me|tech|vercel\.app|netlify\.app)(?:/[\w\-./]*)?)", re.I)


# ---------------------------------------------------------------- text extraction
def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text(extraction_mode="layout") or "")
            except TypeError:
                parts.append(page.extract_text() or "")
        text = "\n".join(parts)
    elif suffix in (".docx", ".doc"):
        import docx
        d = docx.Document(str(path))
        text = "\n".join(p.text for p in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                text += "\n" + " | ".join(c.text for c in row.cells)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]{2,}", "  ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- heuristic structure
def _section_of(line: str) -> str | None:
    clean = re.sub(r"[^a-z ]", "", line.lower()).strip()
    if not clean or len(clean) > 32:
        return None
    return SECTION_WORDS.get(clean)


_VERB = re.compile(r"^(built|created|developed|designed|led|implemented|added|engineered|improved|reduced|increased|"
                   r"automated|managed|wrote|launched|deployed|migrated|integrated|optimi[sz]ed|connected|linked|"
                   r"collaborated|worked|delivered|owned|drove|analy[sz]ed|trained|researched|maintained|set up|used)\b", re.I)


def _sentence(line: str) -> bool:
    return bool(_VERB.match(line)) or (len(line) > 70 and line.rstrip().endswith((".", ";")))


def heuristic_structure(text: str) -> dict:
    lines = [l.rstrip() for l in text.split("\n")]
    lines = [l for l in lines if l.strip()]
    email = (EMAIL_RE.search(text) or [None])[0] if EMAIL_RE.search(text) else ""
    phone_m = PHONE_RE.search(text[:600])
    links = list(dict.fromkeys(m.group(0) for m in LINK_RE.finditer(text)))
    name = ""
    for l in lines[:5]:
        s = l.strip()
        if not EMAIL_RE.search(s) and 2 <= len(s.split()) <= 5 and not any(ch.isdigit() for ch in s):
            name = s.title() if s.isupper() else s
            break
    loc_m = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?,\s*(?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?,\s*)?India)", text[:800])

    sections: list[dict] = []
    cur: dict | None = None
    header_done = False
    for raw in lines:
        line = raw.strip()
        sec = _section_of(line)
        if sec:
            header_done = True
            cur = {"title": sec, "kind": "text" if sec in TEXT_SECTIONS else "items", "items": [], "text": ""}
            sections.append(cur)
            continue
        if not header_done or cur is None:
            continue
        if cur["kind"] == "text":
            cur["text"] = (cur["text"] + " " + BULLET_RE.sub("", line)).strip()
            continue
        is_bullet = bool(BULLET_RE.match(raw))
        date_m = DATE_RE.search(line)
        if is_bullet:
            if not cur["items"]:
                cur["items"].append({"heading": "", "sub": "", "date": "", "bullets": []})
            cur["items"][-1]["bullets"].append(BULLET_RE.sub("", line))
        elif date_m and len(line) < 140:
            date = date_m.group(0).strip()
            heading = (line[:date_m.start()] + line[date_m.end():]).strip(" |,–-")
            heading = re.sub(r"\s{2,}", " · ", heading)
            cur["items"].append({"heading": heading, "sub": "", "date": date, "bullets": []})
        elif cur["items"] and _sentence(line):
            # a full sentence under an item is a bullet even when the PDF lost its bullet glyph
            if cur["items"][-1]["bullets"] and line[:1].islower():
                cur["items"][-1]["bullets"][-1] += " " + line
            else:
                cur["items"][-1]["bullets"].append(line)
        elif cur["items"] and not cur["items"][-1]["bullets"] and not cur["items"][-1]["sub"] and len(line) < 120:
            cur["items"][-1]["sub"] = re.sub(r"\s{2,}", " · ", line)
        elif cur["items"] and cur["items"][-1]["bullets"] and line[:1].islower():
            cur["items"][-1]["bullets"][-1] += " " + line
        else:
            cur["items"].append({"heading": re.sub(r"\s{2,}", " · ", line), "sub": "", "date": "", "bullets": []})
    for s in sections:
        if s["kind"] == "text":
            s.pop("items", None)
        else:
            s.pop("text", None)
    return {
        "name": name,
        "headline": "",
        "contact": {
            "email": email or "",
            "phone": phone_m.group(0).strip() if phone_m else "",
            "location": loc_m.group(1) if loc_m else "",
            "links": links[:4],
        },
        "sections": sections,
    }


LLM_STRUCTURE_SYS = (
    "You convert resume text into JSON. Copy the person's words; do not invent facts. "
    "Schema: {\"name\":str,\"headline\":str,\"contact\":{\"email\":str,\"phone\":str,\"location\":str,\"links\":[str]},"
    "\"sections\":[{\"title\":str,\"kind\":\"items\"|\"text\",\"items\":[{\"heading\":str,\"sub\":str,\"date\":str,\"bullets\":[str]}],\"text\":str}],"
    "\"profile\":{\"target_titles\":[str],\"seniority\":\"fresher\"|\"junior\"|\"mid\"|\"senior\",\"years_experience\":number,"
    "\"skills\":[str],\"domains\":[str]}}. For Experience/Projects/Education use kind items; heading = role or project name "
    "and organisation joined by ' · '; sub = tech stack or location line. For Skills/Summary use kind text."
)


async def structure(text: str) -> dict:
    data = None
    if llm.enabled():
        data = await llm.chat(LLM_STRUCTURE_SYS, text[:12000], max_tokens=4000)
    if not data or not isinstance(data, dict) or not data.get("sections"):
        data = heuristic_structure(text)
    return data


# ---------------------------------------------------------------- matching profile
def _years_experience(text: str, struct: dict) -> float:
    m = re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years|yrs)", text, re.I)
    if m:
        return max(float(x) for x in m if float(x) < 40)
    months = 0
    for s in struct.get("sections", []):
        if s.get("title") != "Experience":
            continue
        for it in s.get("items", []):
            years = re.findall(r"(19|20)(\d{2})(?:[-/](\d{2}))?", it.get("date", ""))
            if len(years) >= 2:
                y1 = int(years[0][0] + years[0][1]); y2 = int(years[1][0] + years[1][1])
                m1 = int(years[0][2] or 1); m2 = int(years[1][2] or 12)
                months += max(1, (y2 - y1) * 12 + (m2 - m1) + 1)
            elif "present" in it.get("date", "").lower() and years:
                import datetime
                y1 = int(years[0][0] + years[0][1]); m1 = int(years[0][2] or 1)
                t = datetime.date.today()
                months += max(1, (t.year - y1) * 12 + (t.month - m1))
    return round(months / 12, 1)


def derive_profile(text: str, struct: dict) -> dict:
    llm_prof = struct.get("profile") or {}
    skills = list(dict.fromkeys((llm_prof.get("skills") or []) + find_skills(text)))
    years = llm_prof.get("years_experience")
    if years is None:
        years = _years_experience(text, struct)
    # internships count for little: a person whose experience is only internships is a fresher
    exp_heads = [
        it.get("heading", "").lower() for s in struct.get("sections", []) if s.get("title") == "Experience"
        for it in s.get("items", []) if it.get("heading")
    ]
    only_interns = bool(exp_heads) and all("intern" in h or "trainee" in h for h in exp_heads)
    seniority = llm_prof.get("seniority") or (
        "fresher" if years < 1 or only_interns else "junior" if years < 3 else "mid" if years < 6 else "senior"
    )
    titles = llm_prof.get("target_titles") or _guess_titles(skills)
    return {
        "skills": skills,
        "years_experience": float(years or 0),
        "seniority": seniority,
        "target_titles": titles,
        "domains": llm_prof.get("domains") or [],
    }


def _guess_titles(skills: list[str]) -> list[str]:
    s = set(skills)
    titles = ["Software Engineer", "Software Developer"]
    if s & {"React", "Next.js", "Angular", "Vue", "HTML", "CSS"}:
        titles += ["Frontend Engineer", "Full Stack Engineer"]
    if s & {"FastAPI", "Django", "Flask", "Node.js", "Spring Boot", "Express"}:
        titles += ["Backend Engineer"]
    if s & {"Machine Learning", "PyTorch", "TensorFlow", "LLMs", "NLP", "Deep Learning"}:
        titles += ["Machine Learning Engineer", "AI Engineer"]
    if s & {"React Native", "Flutter", "Android", "iOS"}:
        titles += ["Mobile Developer"]
    if s & {"AWS", "Kubernetes", "Docker", "Terraform"}:
        titles += ["DevOps Engineer"]
    if s & {"LLMs", "AI Agents", "RAG"}:
        titles += ["Forward Deployed Engineer", "Applied AI Engineer"]
    return list(dict.fromkeys(titles))


def struct_to_text(struct: dict) -> str:
    out = [struct.get("name", ""), struct.get("headline", "")]
    for s in struct.get("sections", []):
        out.append(s.get("title", ""))
        if s.get("kind") == "text":
            out.append(s.get("text", ""))
        for it in s.get("items", []) or []:
            out += [it.get("heading", ""), it.get("sub", ""), it.get("date", "")] + list(it.get("bullets", []))
    return "\n".join(x for x in out if x)


# ---------------------------------------------------------------- state
def current() -> dict:
    """The active resume: {file, text, struct, profile, template, pdf}."""
    return db.kv_get("resume", {}) or {}


def save_current(data: dict) -> None:
    db.kv_set("resume", data)


async def ingest(path: Path) -> dict:
    text = extract_text(path)
    if len(text) < 100:
        raise ValueError("Could not read text from this file. Is it a scanned image? Try a text-based PDF or DOCX.")
    struct = await structure(text)
    profile = derive_profile(text, struct)
    struct.pop("profile", None)
    data = {
        "file": path.name,
        "original": str(path),
        "text": text,
        "struct": struct,
        "profile": profile,
        "template": current().get("template", "classic"),
        "pdf": str(path) if path.suffix.lower() == ".pdf" else "",
    }
    save_current(data)
    logline(f"Resume parsed: {len(profile['skills'])} skills, {profile['years_experience']} yrs, {profile['seniority']}.")
    return data


def update_struct(struct: dict) -> dict:
    data = current()
    data["struct"] = struct
    data["text"] = struct_to_text(struct)
    prof = derive_profile(data["text"], struct)
    # keep the user's own edits to titles/seniority
    old = data.get("profile") or {}
    for k in ("target_titles", "seniority"):
        if old.get(k + "_locked"):
            prof[k] = old[k]
            prof[k + "_locked"] = True
    data["profile"] = prof
    save_current(data)
    return data


UPGRADE_SYS = (
    "You improve a resume for ATS screening. Input: resume JSON, the person's notes on what is new, and keywords that "
    "top-matching job descriptions ask for. Rules: keep every fact true; never invent employers, dates, numbers or skills "
    "the person does not show; add a keyword only when the resume or notes already show that skill; start bullets with a "
    "strong verb; keep each bullet under 30 words; fix grammar. Add new items from the notes. Return the same JSON schema "
    "as the input, plus \"changes\": [short strings describing each change]."
)


async def upgrade(notes: str, keywords: list[str]) -> dict:
    data = current()
    if not llm.enabled():
        raise ValueError("Upgrade needs an LLM. Set LLM_MODEL and LLM_API_KEY in .env (Grok, Groq or Ollama).")
    payload = json.dumps({"resume": data.get("struct"), "notes": notes, "keywords": keywords[:40]}, ensure_ascii=False)
    out = await llm.chat(UPGRADE_SYS, payload, max_tokens=4000, use_cache=False)
    if not out or not out.get("sections"):
        raise ValueError("The LLM did not return a usable resume. Try again.")
    changes = out.pop("changes", [])
    return {"struct": out, "changes": changes}


def resume_file_for_upload() -> str:
    data = current()
    pdf = data.get("pdf")
    if pdf and Path(pdf).exists():
        return pdf
    orig = data.get("original")
    if orig and Path(orig).exists():
        return orig
    files = sorted(RESUME_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0]) if files else ""

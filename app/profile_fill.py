"""Fill profile.yaml from the parsed resume, and describe the "Your details" form.

Everything that can be read from the resume (name, contacts, city/state, education, marks, current role,
a ready-made pitch) is copied in, so the only things left to type are facts a resume never has
(pincode, date of birth, expected salary).
"""
from __future__ import annotations

import re
import time
from typing import Any

from .config import load_profile, save_profile

CITY_STATE = {
    "mumbai": "Maharashtra", "navi mumbai": "Maharashtra", "thane": "Maharashtra", "pune": "Maharashtra",
    "nagpur": "Maharashtra", "nashik": "Maharashtra", "aurangabad": "Maharashtra",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka", "mysore": "Karnataka",
    "mangalore": "Karnataka", "hyderabad": "Telangana", "secunderabad": "Telangana",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "delhi": "Delhi", "new delhi": "Delhi", "gurgaon": "Haryana", "gurugram": "Haryana", "faridabad": "Haryana",
    "noida": "Uttar Pradesh", "greater noida": "Uttar Pradesh", "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh",
    "ghaziabad": "Uttar Pradesh", "kolkata": "West Bengal", "ahmedabad": "Gujarat", "gandhinagar": "Gujarat",
    "surat": "Gujarat", "vadodara": "Gujarat", "jaipur": "Rajasthan", "indore": "Madhya Pradesh",
    "bhopal": "Madhya Pradesh", "kochi": "Kerala", "trivandrum": "Kerala", "thiruvananthapuram": "Kerala",
    "chandigarh": "Chandigarh", "mohali": "Punjab", "bhubaneswar": "Odisha", "patna": "Bihar", "goa": "Goa",
    "panaji": "Goa", "visakhapatnam": "Andhra Pradesh", "vijayawada": "Andhra Pradesh", "dehradun": "Uttarakhand",
    "ranchi": "Jharkhand", "raipur": "Chhattisgarh", "guwahati": "Assam",
}
STATES = sorted(set(CITY_STATE.values()) | {"Andhra Pradesh", "Kerala", "Punjab", "Himachal Pradesh",
                                             "Jammu and Kashmir", "Tripura", "Manipur", "Meghalaya"})

DEGREES = [
    (r"\bb\.?\s?tech\b", "B.Tech"), (r"\bb\.?\s?e\b\.?", "B.E."), (r"\bm\.?\s?tech\b", "M.Tech"),
    (r"\bm\.?\s?e\b\.?", "M.E."), (r"\bb\.?\s?sc\b", "B.Sc"), (r"\bm\.?\s?sc\b", "M.Sc"), (r"\bbca\b", "BCA"),
    (r"\bmca\b", "MCA"), (r"\bmba\b", "MBA"), (r"\bb\.?\s?com\b", "B.Com"), (r"\bph\.?d\b", "PhD"),
    (r"bachelor", "Bachelor's"), (r"master", "Master's"), (r"diploma", "Diploma"),
]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _section(struct: dict, pattern: str) -> list[dict]:
    for s in struct.get("sections") or []:
        if re.search(pattern, s.get("title") or "", re.I) and s.get("kind") == "items":
            return s.get("items") or []
    return []


def _text_of(item: dict) -> str:
    return " ".join([item.get("heading") or "", item.get("sub") or "", item.get("date") or "",
                     *[b for b in item.get("bullets") or []]])


def _percent(text: str) -> str:
    m = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*%", text) or re.search(r"(?:percentage|score|marks)\s*[:\-]?\s*(\d{1,2}(?:\.\d+)?)", text, re.I)
    return m.group(1) if m else ""


def from_resume(struct: dict, text: str = "") -> dict[str, Any]:
    """Every profile value the resume gives, as {"section.key": value}."""
    out: dict[str, Any] = {}
    c = struct.get("contact") or {}
    name = (struct.get("name") or "").split()
    if name:
        out["personal.full_name"] = " ".join(name)
        out["personal.first_name"] = name[0]
        out["personal.last_name"] = " ".join(name[1:])
    if c.get("email"):
        out["personal.email"] = c["email"]
    phone = re.sub(r"[^\d]", "", c.get("phone") or "")
    if len(phone) >= 10:
        out["personal.phone"] = phone[-10:]
    for link in c.get("links") or []:
        url = link if link.startswith("http") else "https://" + link
        if "linkedin" in link:
            out["personal.linkedin"] = url
        elif "github" in link:
            out["personal.github"] = url
        elif "leetcode" in link:
            out["personal.leetcode"] = url
        elif not re.search(r"mailto|@", link):
            out.setdefault("personal.portfolio", url)

    loc = c.get("location") or ""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if parts:
        city = parts[0]
        out["personal.city"] = city
        state = CITY_STATE.get(city.lower()) or next((p for p in parts[1:] if p in STATES), "")
        if state:
            out["personal.state"] = state
        if any(p.lower() == "india" for p in parts) or city.lower() in CITY_STATE:
            out["personal.country"] = "India"

    # education: the degree, and 10th / 12th marks if listed
    edu = _section(struct, r"education|academic|qualification")
    for it in edu:
        t = _text_of(it)
        low = t.lower()
        if re.search(r"\b(12th|xii|hsc|higher secondary|intermediate|class 12)\b", low):
            if _percent(t):
                out["education.twelfth_percent"] = _percent(t)
            continue
        if re.search(r"\b(10th|ssc|class x\b|secondary school|icse|cbse|matric|class 10)\b", low) and not re.search(r"tech|engineering|university|college|institute", low):
            if _percent(t):
                out["education.tenth_percent"] = _percent(t)
            continue
        if "education.university" in out:
            continue
        head, sub = it.get("heading") or "", it.get("sub") or ""
        degree_line = sub if re.search("|".join(p for p, _ in DEGREES), sub, re.I) else head
        uni_line = head if degree_line is sub else sub
        out["education.university"] = re.sub(r"\s*[|·].*$", "", uni_line).strip() or uni_line
        for pat, nice in DEGREES:
            if re.search(pat, degree_line, re.I):
                out["education.degree"] = nice
                break
        m = re.search(r"\b(?:in|of)\s+([A-Za-z&,.\- ]{3,60})", degree_line)
        if m:
            out["education.field"] = re.sub(r"\s*[,(|].*$", "", m.group(1)).strip()
        years = re.findall(r"(19|20)(\d{2})", it.get("date") or t)
        if years:
            out["education.graduation_year"] = years[-1][0] + years[-1][1]
        mon = re.findall(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(?:19|20)\d{2}", (it.get("date") or "").lower())
        if len(mon) >= 2 or (mon and len(years) == 1):
            out["education.graduation_month"] = mon[-1].capitalize()
        m = re.search(r"(?:cgpa|gpa|cpi|sgpa)\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,2})?)", t, re.I) or \
            re.search(r"(\d\.\d{1,2})\s*/\s*10", t)
        if m:
            out["education.cgpa"] = m.group(1)
        elif _percent(t):
            out["education.cgpa"] = _percent(t) + "%"
    year = out.get("education.graduation_year")
    if year:
        out["education.currently_student"] = int(year) > time.localtime().tm_year or (
            int(year) == time.localtime().tm_year and time.localtime().tm_mon < 6)

    # current role: an experience entry that runs to "Present"
    for it in _section(struct, r"experience|employment|work|internship"):
        if re.search(r"present|current|now|ongoing|till date", it.get("date") or "", re.I):
            company = re.sub(r"\s*[|·].*$", "", it.get("heading") or "").strip()
            title = re.sub(r"\s*[|·,].*$", "", it.get("sub") or "").strip()
            if company:
                out["work.current_company"] = company
            if title:
                out["work.current_title"] = title
            break
    return out


def default_pitch(struct: dict, skills: list[str], prof: dict) -> str:
    edu = prof.get("education") or {}
    degree = " ".join(x for x in (edu.get("degree"), edu.get("field")) if x)
    top = ", ".join((skills or [])[:5])
    who = (f"a {degree} {'student' if edu.get('currently_student') else 'graduate'}"
           + (f" at {edu['university']}" if edu.get("university") else "")) if degree else "a software engineer"
    exp = _section(struct, r"experience|employment|internship")
    first = ""
    if exp:
        e = exp[0]
        where = re.sub(r"\s*[|·].*$", "", e.get("heading") or "")
        role = re.sub(r"\s*[|·,].*$", "", e.get("sub") or "") or "an intern"
        first = f" Most recently I worked as {role} at {where}."
    return (f"I am {who} with hands-on experience in {top or 'building software'}.{first} "
            f"I enjoy shipping real products end to end and learning fast, and I would love to bring that to the "
            f"{{role}} role at {{company}}.").replace("  ", " ")


def _get(prof: dict, path: str) -> Any:
    cur: Any = prof
    for p in path.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def _set(prof: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = prof
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def fill_profile(struct: dict, text: str = "", skills: list[str] | None = None, overwrite: bool = False) -> list[str]:
    """Copy resume facts into profile.yaml. Only blanks are filled unless overwrite=True. Returns changed paths."""
    merged = load_profile()
    prof = load_profile(raw=True)
    changed = []
    for path, val in from_resume(struct, text).items():
        cur = _get(merged, path)
        if val not in (None, "") and (overwrite or cur in (None, "", [], 0) or (path == "personal.country" and not cur)):
            if cur != val:
                _set(prof, path, val)
                changed.append(path)
    if not _get(merged, "answers_defaults.pitch"):
        _set(prof, "answers_defaults.pitch", default_pitch(struct, skills or [], merged))
        changed.append("answers_defaults.pitch")
    if changed:
        save_profile(prof)
    return changed


# ------------------------------------------------------------------ the "Your details" form
# (path, label, kind, hint). kind: text | number | bool | textarea | select:<a>/<b>
FORM: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    ("About you", "personal", [
        ("personal.full_name", "Full name", "text", ""),
        ("personal.first_name", "First name", "text", ""),
        ("personal.last_name", "Last name", "text", ""),
        ("personal.email", "Email companies will write to", "text", "Use the Gmail you'll connect to the Tracker"),
        ("personal.phone", "Phone (10 digits)", "text", ""),
        ("personal.date_of_birth", "Date of birth", "text", "DD/MM/YYYY"),
        ("personal.address", "Address", "text", "House / street / area"),
        ("personal.city", "City", "text", ""),
        ("personal.state", "State", "text", ""),
        ("personal.pincode", "PIN code", "text", ""),
        ("personal.country", "Country", "text", ""),
        ("personal.linkedin", "LinkedIn URL", "text", ""),
        ("personal.github", "GitHub URL", "text", ""),
        ("personal.portfolio", "Portfolio / website", "text", ""),
        ("personal.leetcode", "LeetCode URL", "text", ""),
        ("personal.languages", "Languages", "text", ""),
        ("eeo.gender", "Gender (for diversity forms)", "select:Prefer not to say/Male/Female/Non-binary", ""),
    ]),
    ("Education", "education", [
        ("education.university", "College / university", "text", ""),
        ("education.degree", "Degree", "text", "e.g. B.Tech"),
        ("education.field", "Branch / field", "text", ""),
        ("education.graduation_year", "Graduation year", "text", ""),
        ("education.graduation_month", "Graduation month", "text", "e.g. May"),
        ("education.cgpa", "CGPA", "text", ""),
        ("education.twelfth_percent", "12th %", "text", ""),
        ("education.tenth_percent", "10th %", "text", ""),
        ("education.currently_student", "Still studying", "bool", ""),
    ]),
    ("Work and pay", "work", [
        ("work.current_company", "Current company", "text", "Blank if none"),
        ("work.current_title", "Current title", "text", ""),
        ("work.years_experience", "Years of experience", "number", ""),
        ("work.notice_period", "Notice period", "text", ""),
        ("work.earliest_start", "Earliest start", "text", ""),
        ("work.current_ctc_lpa", "Current CTC (LPA)", "text", "0 if fresher"),
        ("work.expected_ctc_lpa", "Expected CTC (LPA)", "text", "e.g. 8"),
        ("work.expected_salary_abroad", "Expected salary for jobs abroad", "text", ""),
        ("work.willing_to_relocate", "Willing to relocate", "bool", ""),
        ("work.ok_onsite", "OK with office / hybrid", "bool", ""),
    ]),
    ("Standard answers", "answers_defaults", [
        ("answers_defaults.pitch", "Why you / why us (used when there is no AI)", "textarea", "{company} and {role} are filled in"),
        ("answers_defaults.how_heard", "How did you hear about us", "text", ""),
        ("answers_defaults.referral", "Referrer name / email", "text", "Blank if none"),
    ]),
]
# blank ones here get asked about once, on the Today screen
IMPORTANT = ["personal.email", "personal.phone", "personal.city", "personal.pincode", "personal.date_of_birth",
             "personal.address", "education.university", "education.degree", "education.graduation_year",
             "education.cgpa", "work.expected_ctc_lpa", "personal.linkedin"]


def form() -> dict:
    from . import info
    prof = load_profile()
    pinned = info.overrides()
    groups = []
    for title, key, rows in FORM:
        fields = []
        for path, label, kind, hint in rows:
            v = _get(prof, path)
            fields.append({"path": path, "label": label, "kind": kind, "hint": hint,
                           "value": v if v is not None else ("" if kind != "bool" else False),
                           "from_info": path in pinned})
        groups.append({"title": title, "key": key, "fields": fields})
    labels = {p: l for _, _, rows in FORM for p, l, _, _ in rows}
    missing = [{"path": p, "label": labels[p]} for p in IMPORTANT if _get(prof, p) in (None, "")]
    return {"groups": groups, "missing": missing}


def save_form(values: dict[str, Any]) -> None:
    from . import info
    prof = load_profile(raw=True)
    pinned = info.overrides()
    kinds = {p: k for _, _, rows in FORM for p, _, k, _ in rows}
    for path, v in values.items():
        if path not in kinds or path in pinned:   # info.txt owns that one
            continue
        if kinds[path] == "bool":
            v = bool(v) and v not in ("false", "no", "0")
        elif kinds[path] == "number":
            try:
                v = float(v) if "." in str(v) else int(v or 0)
            except ValueError:
                v = 0
        elif isinstance(v, str):
            v = v.strip()
        _set(prof, path, v)
        if path == "personal.full_name" and v:
            n = v.split()
            if not _get(prof, "personal.first_name"):
                _set(prof, "personal.first_name", n[0])
            if not _get(prof, "personal.last_name") and len(n) > 1:
                _set(prof, "personal.last_name", " ".join(n[1:]))
    save_profile(prof)

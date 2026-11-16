"""info.txt in the project root: the user's own master file of details.

Plain "Key: value" lines (a value can continue on the next lines). Whatever is written here wins over
profile.yaml and the resume. Known keys (address, pincode, date of birth, expected CTC...) map onto profile
fields; any other key is still used when a form asks a question that matches it (e.g. "Father's name: ...").
"""
from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz, process

from .config import ROOT

INFO_PATH = ROOT / "info.txt"

TEMPLATE = """# Your details. Write "Key: value", one per line. Anything here wins over the resume and the Profile form.
# A long value (like an address) can carry on to the next lines. Lines starting with # are ignored.
# Leave a value empty if you don't want to give it. Any extra "Question: answer" lines are used too.

Full name:
Email:
Phone:
Date of birth:          # DD/MM/YYYY
Gender:
Address:                # full address, e.g. Flat 12, Sunrise CHS, Andheri West, Mumbai, Maharashtra 400053
City:
State:
Pincode:
Country: India
LinkedIn:
GitHub:
Portfolio:

College:
Degree:
Branch:
Graduation year:
CGPA:
10th percentage:
12th percentage:

Current company:
Current CTC:            # lakhs per year, 0 if fresher
Expected CTC:           # lakhs per year
Notice period:
Willing to relocate: Yes

About me:               # 2-3 lines for "why should we hire you" questions; {company} and {role} get filled in
"""

# info.txt key (normalised) -> profile path
ALIASES: dict[str, str] = {}
for path, keys in {
    "personal.full_name": ["name", "full name", "legal name"],
    "personal.first_name": ["first name", "given name"],
    "personal.last_name": ["last name", "surname", "family name"],
    "personal.email": ["email", "email id", "email address", "mail"],
    "personal.phone": ["phone", "mobile", "phone number", "mobile number", "contact number", "contact"],
    "personal.date_of_birth": ["dob", "date of birth", "birth date", "birthday"],
    "eeo.gender": ["gender", "sex"],
    "personal.address": ["address", "full address", "current address", "residential address", "permanent address",
                         "home address", "postal address"],
    "personal.address_line1": ["address line 1", "address 1", "street", "street address"],
    "personal.address_line2": ["address line 2", "address 2", "area", "locality"],
    "personal.city": ["city", "current city", "town"],
    "personal.state": ["state", "province"],
    "personal.pincode": ["pincode", "pin code", "pin", "postal code", "zip", "zip code"],
    "personal.country": ["country"],
    "personal.nationality": ["nationality", "citizenship"],
    "personal.languages": ["languages", "languages known"],
    "personal.linkedin": ["linkedin", "linkedin url", "linkedin profile"],
    "personal.github": ["github", "github url"],
    "personal.portfolio": ["portfolio", "website", "personal website"],
    "personal.leetcode": ["leetcode"],
    "education.university": ["college", "university", "institute", "college name"],
    "education.degree": ["degree", "qualification"],
    "education.field": ["branch", "field", "major", "stream", "specialization"],
    "education.graduation_year": ["graduation year", "passing year", "year of graduation", "batch"],
    "education.graduation_month": ["graduation month"],
    "education.cgpa": ["cgpa", "gpa", "cpi"],
    "education.tenth_percent": ["10th", "10th percentage", "10th marks", "ssc", "ssc percentage", "class 10"],
    "education.twelfth_percent": ["12th", "12th percentage", "12th marks", "hsc", "hsc percentage", "class 12",
                                  "diploma percentage"],
    "work.current_company": ["current company", "current employer", "company"],
    "work.current_title": ["current title", "designation", "current role", "job title"],
    "work.years_experience": ["experience", "years of experience", "total experience"],
    "work.notice_period": ["notice period"],
    "work.earliest_start": ["earliest start", "joining date", "available from"],
    "work.current_ctc_lpa": ["current ctc", "current salary"],
    "work.expected_ctc_lpa": ["expected ctc", "expected salary"],
    "work.willing_to_relocate": ["willing to relocate", "relocate", "relocation"],
    "answers_defaults.pitch": ["about me", "pitch", "why hire me", "summary"],
    "answers_defaults.referral": ["referral", "referred by"],
}.items():
    for k in keys:
        ALIASES[k] = path

_cache: tuple[float, dict[str, str]] = (-1.0, {})


def norm_key(k: str) -> str:
    k = re.sub(r"\(.*?\)", "", k.lower())
    return re.sub(r"[^a-z0-9 ]+", " ", k).split("#")[0].strip().replace("  ", " ")


def read() -> dict[str, str]:
    """{normalised key: value}, re-read whenever the file changes."""
    global _cache
    try:
        mtime = INFO_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime == _cache[0]:
        return _cache[1]
    out: dict[str, str] = {}
    last = None
    for raw in INFO_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            # a blank line ends a value, unless the key is still waiting for one ("About me:" then a paragraph)
            if not line.strip() and last and out.get(last):
                last = None
            continue
        m = re.match(r"^\s*([^:=]{1,80}?)\s*[:=]\s*(.*)$", line)
        if m and not (last and raw[:1] in (" ", "\t")):
            key = norm_key(m.group(1))
            val = re.sub(r"(^|\s+)#\s.*$", "", m.group(2)).strip()
            if key:
                out[key] = val
                last = key
        elif last:   # continuation of a multi-line value
            more = re.sub(r"(^|\s+)#\s.*$", "", line).strip()
            if more:
                sep = ", " if "address" in last else " "   # address lines join with commas, prose with spaces
                out[last] = (out[last] + sep + more).strip(", ")
    for k, v in out.items():
        out[k] = re.sub(r",\s*,", ",", v).strip()
    _cache = (mtime, out)
    return out


def _cast(path: str, val: str) -> Any:
    if path in ("work.willing_to_relocate",):
        return not re.match(r"^\s*(no|n|false|0)\b", val, re.I)
    if path == "personal.phone":
        digits = re.sub(r"\D", "", val)
        return digits[-10:] if len(digits) >= 10 else val
    if path in ("work.current_ctc_lpa", "work.expected_ctc_lpa", "education.cgpa", "education.tenth_percent",
                "education.twelfth_percent"):
        # "7.95/10" -> 7.95, "87.6%" -> 87.6, "0 (Fresher)" -> 0: forms want the number
        m = re.search(r"\d+(\.\d+)?", val)
        return m.group(0) if m else val
    if path == "education.graduation_year":
        m = re.search(r"(19|20)\d{2}", val)
        return m.group(0) if m else val
    return val


def overrides() -> dict[str, Any]:
    """{profile path: value} for every known key that has a value in info.txt."""
    data = read()
    out: dict[str, Any] = {}
    for k, v in data.items():
        if v and k in ALIASES:
            out[ALIASES[k]] = _cast(ALIASES[k], v)
    # "Graduation year: June 2027" also gives the month
    gy = next((v for k, v in data.items() if ALIASES.get(k) == "education.graduation_year" and v), "")
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", gy, re.I)
    if m and "education.graduation_month" not in out:
        out["education.graduation_month"] = m.group(0).capitalize()
    # "Current company: IIT Gandhinagar – Research Intern" -> company + title
    comp = out.get("work.current_company") or ""
    parts = re.split(r"\s+[–—-]\s+|\s+as\s+", comp, maxsplit=1)
    if len(parts) == 2:
        out["work.current_company"] = parts[0].strip()
        out.setdefault("work.current_title", parts[1].strip())
    addr = out.get("personal.address") or ""
    if addr and "personal.pincode" not in out:
        m = re.search(r"\b(\d{6})\b", addr)
        if m:
            out["personal.pincode"] = m.group(1)
    if "personal.full_name" in out and "personal.first_name" not in out:
        n = out["personal.full_name"].split()
        out["personal.first_name"] = n[0]
        if len(n) > 1 and "personal.last_name" not in out:
            out["personal.last_name"] = " ".join(n[1:])
    return out


def apply(prof: dict) -> dict:
    for path, v in overrides().items():
        cur = prof
        parts = path.split(".")
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = v
    return prof


def lookup(label: str) -> str | None:
    """A form question answered by any info.txt line whose key matches it (e.g. "Father's name")."""
    data = {k: v for k, v in read().items() if v}
    if not data or not label:
        return None
    q = norm_key(label)
    best = process.extractOne(q, list(data), scorer=fuzz.token_set_ratio)
    if best and best[1] >= 92 and len(best[0]) >= 3:
        # "name" must not answer "company name": require the key to cover most of the question
        if len(best[0].split()) >= max(1, len(q.split()) - 2) or best[0] == q:
            return data[best[0]]
    return None


def ensure_template() -> None:
    try:
        if not INFO_PATH.exists() or not INFO_PATH.read_text(encoding="utf-8").strip():
            INFO_PATH.write_text(TEMPLATE, encoding="utf-8")
    except OSError:
        pass

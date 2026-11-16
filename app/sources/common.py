"""Shared helpers for job source connectors."""
from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 jobautomater/1.0"

_host_locks: dict[str, asyncio.Semaphore] = {}
_host_last: dict[str, float] = {}


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                             headers={"User-Agent": UA, "Accept": "application/json, text/xml, */*"})


async def polite(url: str, min_gap: float = 0.35) -> None:
    """About 3 requests a second per host, at most 4 at a time."""
    host = urlparse(url).netloc
    if host.endswith("myworkdayjobs.com"):  # one CDN for every tenant: pace them together
        host, min_gap = "myworkdayjobs.com", max(min_gap, 1.0)
    sem = _host_locks.setdefault(host, asyncio.Semaphore(4 if host != "myworkdayjobs.com" else 2))
    async with sem:
        gap = time.monotonic() - _host_last.get(host, 0)
        if gap < min_gap:
            await asyncio.sleep(min_gap - gap)
        _host_last[host] = time.monotonic()


async def _send(c: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """One request with polite pacing; backs off on 429/503 up to 4 times."""
    for attempt in range(5):
        await polite(url)
        r = await c.request(method, url, **kw)
        if r.status_code in (429, 503) and attempt < 4:
            wait = float(r.headers.get("Retry-After", "0") or 0) if str(r.headers.get("Retry-After", "")).isdigit() else 0
            await asyncio.sleep(max(wait, 3 * (2 ** attempt)))
            continue
        return r
    return r


async def get_json(c: httpx.AsyncClient, url: str, **kw) -> Any:
    r = await _send(c, "GET", url, **kw)
    r.raise_for_status()
    return r.json()


async def post_json(c: httpx.AsyncClient, url: str, body: Any, **kw) -> Any:
    r = await _send(c, "POST", url, json=body, **kw)
    r.raise_for_status()
    return r.json()


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section)[^>]*>", re.I)


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)  # greenhouse double-escapes
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def to_epoch(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return v / 1000 if v > 1e12 else float(v)
        s = str(v).strip()
        if s.isdigit():
            n = int(s)
            return n / 1000 if n > 1e12 else float(n)
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            s = s.replace("Z", "+00:00")
            d = datetime.fromisoformat(s[:32]) if "T" in s else datetime.fromisoformat(s[:10])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp()
        m = re.search(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", s, re.I)
        if m:
            days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
            return time.time() - days * 86400
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return None


REMOTE_RE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|worldwide)\b", re.I)


def is_remote(*parts: Any) -> bool:
    return any(REMOTE_RE.search(str(p or "")) for p in parts)


def detect_ats(url: str | None) -> str:
    u = (url or "").lower()
    for key, name in (("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"),
                      ("workable.com", "workable"), ("myworkdayjobs.com", "workday"),
                      ("smartrecruiters.com", "smartrecruiters"), ("oraclecloud.com", "oracle"),
                      ("icims.com", "icims"), ("bamboohr.com", "bamboohr"), ("recruitee.com", "recruitee"),
                      ("turbohire", "turbohire"), ("zohorecruit", "zoho"), ("darwinbox", "darwinbox")):
        if key in u:
            return name
    return "other"


def fix_text(s: str) -> str:
    """Repair UTF-8 that a feed decoded as Latin-1 (e.g. 'â€"' for an en dash)."""
    if s and any(ch in s for ch in ("â", "Ã", "Â")):
        try:
            return s.encode("cp1252", errors="strict").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                return s.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return s
    return s


def job_id(source: str, ext_id: Any) -> str:
    return hashlib.sha1(f"{source}:{ext_id}".encode()).hexdigest()[:16]


def make_job(source: str, ext_id: Any, **f: Any) -> dict:
    job = {
        "source": source,
        "id": job_id(source, ext_id),
        "company": fix_text((f.get("company") or "").strip()),
        "title": fix_text((f.get("title") or "").strip()),
        "location": (f.get("location") or "").strip(),
        "remote": bool(f.get("remote")),
        "url": f.get("url") or "",
        "apply_url": f.get("apply_url") or f.get("url") or "",
        "description": fix_text(f.get("description") or "")[:20000],
        "salary": f.get("salary") or "",
        "posted_at": f.get("posted_at"),
        "extra": f.get("extra") or {},
    }
    job["ats"] = f.get("ats") or detect_ats(job["apply_url"])
    if not job["remote"]:
        job["remote"] = is_remote(job["location"], job["title"])
    return job

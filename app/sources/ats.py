"""Company ATS connectors: read each employer's own careers system (public, no key)."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx

from .common import get_json, job_id, make_job, post_json, strip_html, to_epoch


# ------------------------------------------------------------------ Greenhouse
async def greenhouse(c: httpx.AsyncClient, token: str, company: str, ctx: dict) -> list[dict]:
    d = await get_json(c, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices", []) or [])
        url = j.get("absolute_url", "")
        out.append(make_job(
            "greenhouse", f"{token}:{j['id']}", company=company or j.get("company_name") or token,
            title=j.get("title"), location=loc or offices, url=url, apply_url=url,
            description=strip_html(j.get("content")), posted_at=to_epoch(j.get("first_published") or j.get("updated_at")),
            ats="greenhouse", extra={"board": token, "gh_id": j["id"]},
        ))
    return out


# ------------------------------------------------------------------ Lever
async def lever(c: httpx.AsyncClient, slug: str, company: str, ctx: dict) -> list[dict]:
    try:
        d = await get_json(c, f"https://api.lever.co/v0/postings/{slug}?mode=json")
    except httpx.HTTPStatusError:
        d = await get_json(c, f"https://api.eu.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in d if isinstance(d, list) else []:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        lists = "\n".join(f"{l.get('text','')}\n{strip_html(l.get('content'))}" for l in j.get("lists", []) or [])
        desc = "\n".join(x for x in [j.get("descriptionPlain", ""), lists, j.get("additionalPlain", "")] if x)
        sal = j.get("salaryRange") or {}
        out.append(make_job(
            "lever", f"{slug}:{j['id']}", company=company or slug, title=j.get("text"),
            location=", ".join(l for l in locs if l), remote=(j.get("workplaceType") == "remote"),
            url=j.get("hostedUrl"), apply_url=j.get("applyUrl") or (j.get("hostedUrl", "") + "/apply"),
            description=desc, posted_at=to_epoch(j.get("createdAt")), ats="lever",
            salary=f"{sal.get('currency','')} {sal.get('min','')}-{sal.get('max','')}".strip() if sal else "",
            extra={"commitment": cat.get("commitment"), "team": cat.get("team")},
        ))
    return out


# ------------------------------------------------------------------ Ashby
async def ashby(c: httpx.AsyncClient, org: str, company: str, ctx: dict) -> list[dict]:
    d = await get_json(c, f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(make_job(
            "ashby", f"{org}:{j['id']}", company=company or org, title=j.get("title"),
            location=", ".join(dict.fromkeys(x for x in locs + [country] if x)),
            remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
            url=j.get("jobUrl"), apply_url=(j.get("applyUrl") or j.get("jobUrl", "")).rstrip("/") + ("" if "application" in (j.get("applyUrl") or "") else "/application"),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
            posted_at=to_epoch(j.get("publishedAt")), salary=comp, ats="ashby",
            extra={"employmentType": j.get("employmentType")},
        ))
    return out


# ------------------------------------------------------------------ SmartRecruiters
async def smartrecruiters(c: httpx.AsyncClient, cid: str, company: str, ctx: dict) -> list[dict]:
    out, offset = [], 0
    raw = []
    while offset < 400:
        d = await get_json(c, f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100&offset={offset}")
        content = d.get("content", [])
        raw += content
        if len(content) < 100:
            break
        offset += 100
    keep = [j for j in raw if ctx["location_ok"](
        " ".join(str((j.get("location") or {}).get(k, "")) for k in ("city", "country", "fullLocation")),
        bool((j.get("location") or {}).get("remote")))]
    sem = asyncio.Semaphore(4)

    async def detail(j):
        async with sem:
            try:
                dd = await get_json(c, f"https://api.smartrecruiters.com/v1/companies/{cid}/postings/{j['id']}")
                secs = ((dd.get("jobAd") or {}).get("sections") or {})
                return "\n".join(strip_html((secs.get(k) or {}).get("text")) for k in
                                 ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
            except Exception:
                return ""

    known = ctx.get("known") or {}
    new = [j for j in keep if job_id("smartrecruiters", f"{cid}:{j['id']}") not in known][:60]
    descs = dict(zip([j["id"] for j in new], await asyncio.gather(*(detail(j) for j in new))))
    for j in keep:
        jid = job_id("smartrecruiters", f"{cid}:{j['id']}")
        if jid in known:
            out.append(make_job("smartrecruiters", f"{cid}:{j['id']}", company=company or cid, title=j.get("name"),
                                location=known[jid], ats="smartrecruiters", extra={"stub": True}))
            continue
        if j["id"] not in descs:
            continue
        desc = descs[j["id"]]
        loc = j.get("location") or {}
        url = f"https://jobs.smartrecruiters.com/{cid}/{j['id']}"
        out.append(make_job(
            "smartrecruiters", f"{cid}:{j['id']}", company=company or (j.get("company") or {}).get("name") or cid,
            title=j.get("name"), location=loc.get("fullLocation") or f"{loc.get('city','')}, {loc.get('country','')}",
            remote=bool(loc.get("remote")), url=url, apply_url=url, description=desc or j.get("name", ""),
            posted_at=to_epoch(j.get("releasedDate")), ats="smartrecruiters",
        ))
    return out


# ------------------------------------------------------------------ Workday (CXS JSON)
def _workday_parts(url: str) -> tuple[str, str, str]:
    p = urlparse(url)
    tenant = p.netloc.split(".")[0]
    site = [s for s in p.path.split("/") if s and not re.match(r"^[a-z]{2}-[A-Z]{2}$", s)][0]
    return f"{p.scheme}://{p.netloc}", tenant, site


async def workday(c: httpx.AsyncClient, url: str, company: str, ctx: dict) -> list[dict]:
    base, tenant, site = _workday_parts(url)
    api = f"{base}/wday/cxs/{tenant}/{site}"
    queries = ctx.get("search_terms") or [""]
    seen: dict[str, dict] = {}
    for q in queries[:2]:
        for offset in (0,):
            try:
                d = await post_json(c, f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
            except Exception:
                break
            posts = d.get("jobPostings", [])
            for p in posts:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p)
            if len(posts) < 20:
                break
    # Only open postings whose list entry hints at a location you want: the path carries the primary city
    # ("/job/India-Bengaluru/..."), and "N Locations" may include one. This keeps Workday to a few calls per company.
    def hint(p: dict) -> bool:
        text = f"{p.get('externalPath', '')} {p.get('locationsText', '')}".replace("-", " ")
        return "Locations" in text or ctx["location_ok"](text, "remote" in text.lower())
    seen = {k: v for k, v in seen.items() if hint(v)}
    sem = asyncio.Semaphore(4)

    known = ctx.get("known") or {}

    async def detail(path: str, p: dict):
        jid = job_id("workday", f"{tenant}:{path}")
        if jid in known:  # already stored: just mark it as still open, no detail call
            return make_job("workday", f"{tenant}:{path}", company=company or tenant, title=p.get("title"),
                            location=known[jid], ats="workday", extra={"stub": True})
        async with sem:
            try:
                d = await get_json(c, f"{api}{path}")
            except Exception:
                return None
            info = d.get("jobPostingInfo") or {}
            locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
            country = (info.get("country") or {}).get("descriptor", "")
            loc_text = ", ".join(dict.fromkeys(x for x in locs + [country] if x))
            remote = "remote" in (info.get("remoteType") or "").lower() or "remote" in loc_text.lower()
            if not ctx["location_ok"](loc_text, remote):
                return None
            ext = info.get("externalUrl") or f"{base}/{site}{path}"
            return make_job(
                "workday", f"{tenant}:{path}", company=company or tenant,
                title=info.get("title") or p.get("title"), location=loc_text, remote=remote,
                url=ext, apply_url=ext.rstrip("/") + "/apply", description=strip_html(info.get("jobDescription")),
                posted_at=to_epoch(info.get("startDate") or p.get("postedOn")), ats="workday",
                extra={"time_type": info.get("timeType")},
            )

    items = list(seen.items())
    fresh = [(k, v) for k, v in items if job_id("workday", f"{tenant}:{k}") not in known][:10]
    stale = [(k, v) for k, v in items if job_id("workday", f"{tenant}:{k}") in known]
    res = await asyncio.gather(*(detail(path, p) for path, p in fresh + stale))
    return [r for r in res if r]


# ------------------------------------------------------------------ Workable
async def workable(c: httpx.AsyncClient, account: str, company: str, ctx: dict) -> list[dict]:
    d = await get_json(c, f"https://apply.workable.com/api/v1/widget/accounts/{account}?details=true")
    out = []
    for j in d.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x) for l in locs)
        out.append(make_job(
            "workable", f"{account}:{j.get('shortcode')}", company=company or d.get("name") or account,
            title=j.get("title"), location=loc, remote=bool(j.get("telecommuting")),
            url=j.get("url") or j.get("shortlink"), apply_url=j.get("application_url"),
            description=strip_html(j.get("description")), posted_at=to_epoch(j.get("published_on")), ats="workable",
        ))
    return out


ATS_FETCHERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workday": workday, "workable": workable,
}


async def probe(c: httpx.AsyncClient, ats: str, key: str) -> bool:
    """Is this company slug valid for this ATS? Used to prune companies.yaml."""
    try:
        if ats == "greenhouse":
            r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{key}")
        elif ats == "lever":
            r = await c.get(f"https://api.lever.co/v0/postings/{key}?mode=json&limit=1")
            if r.status_code == 200 and r.json() == []:
                return False
        elif ats == "ashby":
            r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{key}")
        elif ats == "smartrecruiters":
            r = await c.get(f"https://api.smartrecruiters.com/v1/companies/{key}/postings?limit=1")
            return r.status_code == 200 and r.json().get("totalFound", 0) > 0
        elif ats == "workable":
            r = await c.get(f"https://apply.workable.com/api/v1/widget/accounts/{key}")
        elif ats == "workday":
            from .common import _send
            base, tenant, site = _workday_parts(key)
            r = await _send(c, "POST", f"{base}/wday/cxs/{tenant}/{site}/jobs",
                            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        else:
            return False
        return r.status_code == 200
    except Exception:
        return False

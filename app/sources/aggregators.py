"""Aggregator connectors: remote boards and keyed free-tier APIs."""
from __future__ import annotations

import feedparser
import httpx

from ..config import env
from .common import get_json, make_job, polite, post_json, strip_html, to_epoch


async def remotive(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    d = await get_json(c, "https://remotive.com/api/remote-jobs?limit=1000")
    return [make_job(
        "remotive", j["id"], company=j.get("company_name"), title=j.get("title"),
        location=f"Remote ({j.get('candidate_required_location') or 'Worldwide'})", remote=True,
        url=j.get("url"), description=strip_html(j.get("description")), salary=j.get("salary"),
        posted_at=to_epoch(j.get("publication_date")), extra={"credit": "Remotive"},
    ) for j in d.get("jobs", [])]


async def remoteok(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    d = await get_json(c, "https://remoteok.com/api")
    out = []
    for j in d if isinstance(d, list) else []:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        sal = f"USD {j.get('salary_min')}-{j.get('salary_max')}" if j.get("salary_max") else ""
        out.append(make_job(
            "remoteok", j["id"], company=j.get("company"), title=j.get("position"),
            location=f"Remote ({j.get('location') or 'Worldwide'})", remote=True,
            url=j.get("url"), apply_url=j.get("apply_url") or j.get("url"),
            description=strip_html(j.get("description")) + "\nTags: " + ", ".join(j.get("tags") or []),
            salary=sal, posted_at=to_epoch(j.get("epoch") or j.get("date")), extra={"credit": "Remote OK"},
        ))
    return out


async def arbeitnow(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    out = []
    for page in (1, 2, 3):
        d = await get_json(c, f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        for j in d.get("data", []):
            out.append(make_job(
                "arbeitnow", j["slug"], company=j.get("company_name"), title=j.get("title"),
                location=j.get("location") + (" (Remote)" if j.get("remote") else ""), remote=j.get("remote"),
                url=j.get("url"), description=strip_html(j.get("description")),
                posted_at=to_epoch(j.get("created_at")), extra={"credit": "Arbeitnow"},
            ))
        if not (d.get("links") or {}).get("next"):
            break
    return out


async def jobicy(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    d = await get_json(c, "https://jobicy.com/api/v2/remote-jobs?count=100")
    out = []
    for j in d.get("jobs", []):
        sal = ""
        if j.get("annualSalaryMax"):
            sal = f"{j.get('salaryCurrency','USD')} {j.get('annualSalaryMin')}-{j.get('annualSalaryMax')}"
        out.append(make_job(
            "jobicy", j["id"], company=j.get("companyName"), title=j.get("jobTitle"),
            location=f"Remote ({j.get('jobGeo') or 'Anywhere'})", remote=True, url=j.get("url"),
            description=strip_html(j.get("jobDescription")), salary=sal,
            posted_at=to_epoch(j.get("pubDate")), extra={"credit": "Jobicy", "level": j.get("jobLevel")},
        ))
    return out


async def himalayas(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    out, cursor = [], None
    for _ in range(25):  # 20 per page
        url = "https://himalayas.app/jobs/api?limit=20" + (f"&cursor={cursor}" if cursor else "")
        d = await get_json(c, url)
        for j in d.get("jobs", []):
            restr = j.get("locationRestrictions") or []
            loc = "Remote (" + (", ".join(restr[:6]) + ("…" if len(restr) > 6 else "") if restr else "Worldwide") + ")"
            sal = f"{j.get('currency') or ''} {j.get('minSalary')}-{j.get('maxSalary')}" if j.get("maxSalary") else ""
            out.append(make_job(
                "himalayas", j.get("guid"), company=j.get("companyName"), title=j.get("title"),
                location=loc, remote=True, url=j.get("applicationLink") or j.get("guid"),
                description=strip_html(j.get("description")), salary=sal,
                posted_at=to_epoch(j.get("pubDate")),
                extra={"credit": "Himalayas", "restrictions": restr, "seniority": j.get("seniority")},
            ))
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return out


async def workingnomads(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    d = await get_json(c, "https://www.workingnomads.com/api/exposed_jobs/")
    return [make_job(
        "workingnomads", j.get("url"), company=j.get("company_name"), title=j.get("title"),
        location=f"Remote ({j.get('location') or 'Anywhere'})", remote=True, url=j.get("url"),
        description=strip_html(j.get("description")) + "\nTags: " + str(j.get("tags") or ""),
        posted_at=to_epoch(j.get("pub_date")), extra={"credit": "Working Nomads"},
    ) for j in d if isinstance(d, list)]


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
]


async def weworkremotely(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    out = []
    for feed in WWR_FEEDS:
        await polite(feed)
        r = await c.get(feed)
        if r.status_code != 200:
            continue
        for e in feedparser.parse(r.text).entries:
            title = e.get("title", "")
            company, _, role = title.partition(": ")
            out.append(make_job(
                "weworkremotely", e.get("link"), company=company if role else "", title=role or title,
                location=f"Remote ({e.get('region') or 'Anywhere'})", remote=True, url=e.get("link"),
                description=strip_html(e.get("summary")), posted_at=to_epoch(e.get("published")),
                extra={"credit": "We Work Remotely"},
            ))
    return out


# ------------------------------------------------------------------ keyed free tiers (skipped without a key)
async def adzuna(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    app_id, app_key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    out = []
    for term in (ctx.get("search_terms") or ["software engineer"])[:4]:
        for page in (1, 2):
            d = await get_json(c, f"https://api.adzuna.com/v1/api/jobs/in/search/{page}", params={
                "app_id": app_id, "app_key": app_key, "what": term, "results_per_page": 50,
                "max_days_old": 21, "content-type": "application/json"})
            for j in d.get("results", []):
                sal = f"INR {int(j['salary_min'])}-{int(j.get('salary_max') or j['salary_min'])}" if j.get("salary_min") else ""
                out.append(make_job(
                    "adzuna", j["id"], company=(j.get("company") or {}).get("display_name"), title=j.get("title"),
                    location=(j.get("location") or {}).get("display_name", "India"), url=j.get("redirect_url"),
                    description=strip_html(j.get("description")), salary=sal, posted_at=to_epoch(j.get("created")),
                    extra={"credit": "Adzuna", "snippet_only": True},
                ))
    return out


async def jooble(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    key = env("JOOBLE_API_KEY")
    if not key:
        return []
    out = []
    for term in (ctx.get("search_terms") or ["software engineer"])[:4]:
        for loc in ("India", "Remote"):
            d = await post_json(c, f"https://jooble.org/api/{key}", {"keywords": term, "location": loc, "page": "1"})
            for j in d.get("jobs", []):
                out.append(make_job(
                    "jooble", j.get("id") or j.get("link"), company=j.get("company"), title=j.get("title"),
                    location=j.get("location") or loc, url=j.get("link"), description=strip_html(j.get("snippet")),
                    salary=j.get("salary"), posted_at=to_epoch(j.get("updated")),
                    extra={"credit": "Jooble", "snippet_only": True},
                ))
    return out


async def jsearch(c: httpx.AsyncClient, ctx: dict) -> list[dict]:
    key = env("RAPIDAPI_KEY")
    if not key:
        return []
    out = []
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    for term in (ctx.get("search_terms") or ["software engineer"])[:2]:  # 200 calls/month: stay frugal
        d = await get_json(c, "https://jsearch.p.rapidapi.com/search", headers=headers, params={
            "query": f"{term} in India", "page": 1, "num_pages": 1, "date_posted": "week"})
        for j in d.get("data", []):
            loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
            out.append(make_job(
                "jsearch", j.get("job_id"), company=j.get("employer_name"), title=j.get("job_title"),
                location=loc, remote=j.get("job_is_remote"), url=j.get("job_apply_link"),
                description=j.get("job_description"), posted_at=to_epoch(j.get("job_posted_at_timestamp")),
                extra={"credit": "JSearch"},
            ))
    return out


AGGREGATORS = {
    "remotive": remotive, "remoteok": remoteok, "arbeitnow": arbeitnow, "jobicy": jobicy,
    "himalayas": himalayas, "workingnomads": workingnomads, "weworkremotely": weworkremotely,
    "adzuna": adzuna, "jooble": jooble, "jsearch": jsearch,
}

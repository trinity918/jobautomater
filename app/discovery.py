"""Discovery: run every enabled source, filter by location and age, de-duplicate, store, rank.

Two lanes. The fast lane (every source but Workday) finishes in about 20 seconds and is what Start waits for.
Workday rate-limits hard, so it runs afterwards in a slow background lane and adds its roles when it is done.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from . import db, ranker
from .config import load_companies, load_settings
from .events import logline, publish
from .resume import current as current_resume
from .sources.aggregators import AGGREGATORS
from .sources.ats import ATS_FETCHERS
from .sources.common import client

SLOW = {"workday"}
_running = asyncio.Lock()
_slow_running = asyncio.Lock()


def _search_terms() -> list[str]:
    pf = ranker.prefs()
    titles = pf["target_titles"] or (current_resume().get("profile") or {}).get("target_titles") or ["Software Engineer"]
    return list(dict.fromkeys(titles))[:4]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


async def _run_source(name: str, coro, timeout: float) -> tuple[str, list[dict], str | None, float]:
    t0 = time.time()
    try:
        jobs = await asyncio.wait_for(coro, timeout=timeout)
        return name, jobs, None, time.time() - t0
    except Exception as e:
        return name, [], f"{type(e).__name__}: {str(e)[:120]}", time.time() - t0


def _ctx() -> dict:
    known = {r["id"]: r["location"] for r in db.query(
        "SELECT id, location FROM jobs WHERE source IN ('workday','smartrecruiters')")}
    return {"location_ok": ranker.location_ok, "search_terms": _search_terms(), "known": known}


async def _fetch(lane: set[str] | None, exclude: set[str], timeout: float, progress: bool) -> tuple[list[dict], dict]:
    """Run the enabled sources (only `lane` if given, minus `exclude`). Returns jobs and per-source stats."""
    settings = load_settings()
    enabled = settings["sources"]
    ctx = _ctx()
    companies = load_companies()
    stats: dict[str, dict] = {}
    all_jobs: list[dict] = []

    def want(name: str) -> bool:
        return enabled.get(name, True) and name not in exclude and (lane is None or name in lane)

    async with client() as c:
        tasks = []
        for ats, fetch in ATS_FETCHERS.items():
            if want(ats):
                for comp in companies.get(ats, []) or []:
                    tasks.append(_run_source(ats, fetch(c, comp["key"], comp.get("name", ""), ctx), timeout))
        for name, fetch in AGGREGATORS.items():
            if want(name):
                tasks.append(_run_source(name, fetch(c, ctx), timeout))
        done = 0
        for fut in asyncio.as_completed(tasks):
            name, jobs, err, secs = await fut
            done += 1
            st = stats.setdefault(name, {"fetched": 0, "errors": 0, "last_error": None, "slowest_s": 0})
            st["fetched"] += len(jobs)
            st["slowest_s"] = round(max(st["slowest_s"], secs), 1)
            if err:
                st["errors"] += 1
                st["last_error"] = err
            all_jobs.extend(jobs)
            if progress and (done % 5 == 0 or done == len(tasks)):
                publish("discovery", state="running", progress=done, total=len(tasks))
    return all_jobs, stats


def _store(all_jobs: list[dict]) -> dict:
    """Filter, de-duplicate and insert. Returns counts."""
    settings = load_settings()
    max_age = settings.get("max_job_age_days", 30) * 86400
    now = time.time()
    kept = [j for j in all_jobs
            if j["title"] and ranker.location_ok(j["location"], j["remote"])
            and (not j.get("posted_at") or now - j["posted_at"] < max_age)]

    # same company + same title + same city from several sources -> keep the company-ATS copy
    priority = {"greenhouse": 0, "lever": 0, "ashby": 0, "workable": 0, "workday": 1, "smartrecruiters": 1}
    best: dict[str, dict] = {}
    for j in kept:
        key = f"{_norm(j['company'])}|{_norm(j['title'])}|{_norm(j['location'])[:20]}"
        prev = best.get(key)
        if prev is None or priority.get(j["source"], 5) < priority.get(prev["source"], 5):
            best[key] = j
    unique = list(best.values())

    existing = {r["id"] for r in db.query("SELECT id FROM jobs")}
    new_rows, seen_ids = [], []
    for j in unique:
        if j["id"] in existing or (j.get("extra") or {}).get("stub"):
            seen_ids.append(j["id"])
            continue
        new_rows.append((
            j["id"], j["source"], j["ats"], j["company"], j["title"], j["location"], int(j["remote"]),
            j["url"], j["apply_url"], j["description"], j["salary"], j["posted_at"], now, now,
            json.dumps(j.get("extra") or {}),
        ))
    if new_rows:
        db.executemany(
            "INSERT OR IGNORE INTO jobs(id,source,ats,company,title,location,remote,url,apply_url,description,salary,"
            "posted_at,first_seen,last_seen,extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", new_rows)
    for i in range(0, len(seen_ids), 500):
        chunk = seen_ids[i:i + 500]
        db.execute(f"UPDATE jobs SET last_seen=? WHERE id IN ({','.join('?' * len(chunk))})", [now, *chunk])
    return {"fetched": len(all_jobs), "relevant": len(kept), "unique": len(unique), "new": len(new_rows)}


async def discover() -> dict:
    if _running.locked():
        return {"skipped": "already running"}
    async with _running:
        t0 = time.time()
        publish("discovery", state="running")
        logline(f"Discovery started: searching for {', '.join(_search_terms())}.")
        jobs, stats = await _fetch(lane=None, exclude=SLOW, timeout=120, progress=True)
        counts = _store(jobs)
        # a fast-lane job no source has listed for 3 days is treated as closed
        db.execute("UPDATE jobs SET status='closed' WHERE status='new' AND source NOT IN ('workday') AND last_seen < ?",
                   (time.time() - 3 * 86400,))
        prev = db.kv_get("discovery_last") or {}
        for name in SLOW:  # keep the slow lane's last numbers visible in Settings
            if name in (prev.get("sources") or {}):
                stats[name] = prev["sources"][name]
        summary = {**counts, "seconds": round(time.time() - t0, 1), "at": time.time(), "sources": stats}
        db.kv_set("discovery_last", summary)
        logline(f"Discovery done in {summary['seconds']}s: {counts['fetched']} fetched, {counts['relevant']} in your "
                f"locations, {counts['new']} new.")
        await asyncio.to_thread(ranker.rank_all)
        try:
            await ranker.llm_rerank()
        except Exception as e:
            logline(f"LLM rerank skipped: {e}", "warning")
        publish("discovery", state="done", summary=summary)
    if any(load_settings()["sources"].get(n, True) for n in SLOW) and not _slow_running.locked():
        asyncio.create_task(discover_slow())
    return summary


async def discover_slow() -> dict:
    """Workday, in the background: paced at about one request a second across all tenants."""
    async with _slow_running:
        t0 = time.time()
        publish("discovery_slow", state="running")
        jobs, stats = await _fetch(lane=SLOW, exclude=set(), timeout=900, progress=False)
        counts = _store(jobs)
        db.execute("UPDATE jobs SET status='closed' WHERE status='new' AND source='workday' AND last_seen < ?",
                   (time.time() - 5 * 86400,))
        last = db.kv_get("discovery_last") or {}
        last.setdefault("sources", {}).update(stats)
        db.kv_set("discovery_last", last)
        logline(f"Workday boards done in {round(time.time() - t0)}s: {counts['new']} new roles.")
        if counts["new"]:
            await asyncio.to_thread(ranker.rank_all)
        publish("discovery_slow", state="done", new=counts["new"])
        return counts

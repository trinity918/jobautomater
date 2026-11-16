"""Paths, settings and profile loading. Everything the user edits lives in config/ and .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resumes"
SHOTS_DIR = DATA_DIR / "shots"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "jobautomater.db"

for d in (DATA_DIR, RESUME_DIR, SHOTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# personal files are not in git: create them from the shipped templates on first run
for _src, _dst in ((CONFIG_DIR / "profile.example.yaml", CONFIG_DIR / "profile.yaml"),
                   (ROOT / ".env.example", ROOT / ".env")):
    if _src.exists() and not _dst.exists():
        _dst.write_text(_src.read_text(encoding="utf-8"), encoding="utf-8")

load_dotenv(ROOT / ".env")

DEFAULT_SETTINGS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8765,
    "discovery_interval_hours": 4,
    "discover_on_start": True,
    "auto_apply_enabled": False,
    "apply_mode": "review",          # auto | review | dry-run
    "min_score_to_apply": 70,
    "daily_apply_cap": 25,
    "company_cooldown_days": 30,
    "workers": 2,
    "headless": True,
    "delay_between_applies_s": [20, 60],
    "llm_rerank_top_n": 40,
    "llm_daily_token_budget": 200000,
    "max_job_age_days": 30,
    "sources": {
        "greenhouse": True, "lever": True, "ashby": True, "smartrecruiters": True,
        "workday": True, "workable": True, "remotive": True, "remoteok": True,
        "arbeitnow": True, "jobicy": True, "himalayas": True, "workingnomads": True,
        "weworkremotely": True, "adzuna": True, "jooble": True, "jsearch": True,
    },
}


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    user = _read_yaml(CONFIG_DIR / "settings.yaml")
    for k, v in user.items():
        if k == "sources" and isinstance(v, dict):
            s["sources"] = {**DEFAULT_SETTINGS["sources"], **v}
        else:
            s[k] = v
    return s


def save_settings(patch: dict) -> dict:
    cur = _read_yaml(CONFIG_DIR / "settings.yaml")
    for k, v in patch.items():
        if k == "sources" and isinstance(v, dict):
            cur["sources"] = {**cur.get("sources", {}), **v}
        else:
            cur[k] = v
    _write_yaml(CONFIG_DIR / "settings.yaml", cur)
    return load_settings()


def load_profile(raw: bool = False) -> dict:
    """profile.yaml with info.txt on top (info.txt always wins). raw=True: profile.yaml alone, for editing it."""
    prof = _read_yaml(CONFIG_DIR / "profile.yaml")
    if raw:
        return prof
    from . import info
    return info.apply(prof)


def save_profile(data: dict) -> None:
    _write_yaml(CONFIG_DIR / "profile.yaml", data)


def load_companies() -> dict:
    return _read_yaml(CONFIG_DIR / "companies.yaml")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()

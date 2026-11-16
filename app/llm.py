"""OpenAI-compatible chat client. Works with xAI Grok, Groq, OpenRouter, Ollama, or nothing at all.

Every call is cached by prompt hash and counted against a daily token budget.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

from . import db
from .config import env, load_settings
from .events import logline

PRESETS = {
    "xai": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


def llm_config() -> dict:
    base = env("LLM_BASE_URL") or PRESETS.get(env("LLM_PROVIDER", "xai").lower(), PRESETS["xai"])
    return {"base_url": base.rstrip("/"), "api_key": env("LLM_API_KEY"), "model": env("LLM_MODEL")}


def enabled() -> bool:
    c = llm_config()
    local = "localhost" in c["base_url"] or "127.0.0.1" in c["base_url"]
    return bool(c["model"]) and (bool(c["api_key"]) or local)


def _today_key() -> str:
    return "llm_tokens_" + time.strftime("%Y-%m-%d")


def tokens_used_today() -> int:
    return int(db.kv_get(_today_key(), 0))


def _add_tokens(n: int) -> None:
    db.kv_set(_today_key(), tokens_used_today() + int(n))


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


async def chat(system: str, user: str, *, json_mode: bool = True, max_tokens: int = 2000,
               temperature: float = 0.2, use_cache: bool = True):
    """Returns parsed JSON (json_mode) or a string. Returns None when the LLM is off or fails."""
    if not enabled():
        return None
    cfg = llm_config()
    budget = int(load_settings().get("llm_daily_token_budget", 200000))
    key = hashlib.sha256(f"{cfg['model']}|{system}|{user}|{json_mode}".encode()).hexdigest()
    if use_cache:
        hit = db.one("SELECT response FROM llm_cache WHERE prompt_hash=?", (key,))
        if hit:
            return _extract_json(hit["response"]) if json_mode else hit["response"]
    if tokens_used_today() >= budget:
        logline("LLM daily token budget used up; falling back to rules.", "warning")
        return None

    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(f"{cfg['base_url']}/chat/completions", json=body, headers=headers)
            if r.status_code == 400 and json_mode and "response_format" in body:
                body.pop("response_format")  # some models reject JSON mode; prompt still asks for JSON
                continue
            if r.status_code == 429:
                await _sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            used = (data.get("usage") or {}).get("total_tokens") or (len(system) + len(user) + len(text)) // 4
            _add_tokens(used)
            db.execute("INSERT OR REPLACE INTO llm_cache(prompt_hash,response,tokens,at) VALUES(?,?,?,?)",
                       (key, text, used, time.time()))
            return _extract_json(text) if json_mode else text
        except Exception as e:  # network, auth, parse
            logline(f"LLM call failed ({type(e).__name__}: {str(e)[:160]})", "warning")
            await _sleep(2)
    return None


async def _sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)


async def ping() -> dict:
    if not enabled():
        return {"ok": False, "error": "LLM not configured (set LLM_MODEL and LLM_API_KEY in .env)"}
    out = await chat("Reply with JSON only.", 'Return {"ok": true}', use_cache=False, max_tokens=400)
    return {"ok": bool(out and out.get("ok")), "model": llm_config()["model"], "base_url": llm_config()["base_url"]}

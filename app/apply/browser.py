"""Playwright browser sessions with a live CDP screencast and remote input (take over).

All sessions share one saved browser profile (data/browser_profile). The user signs in to Google, LinkedIn,
Workday etc. once in a plain browser window on that profile, and every application reuses those sign-ins.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from playwright.async_api import BrowserContext, CDPSession, Page, Playwright, async_playwright

from ..config import DATA_DIR, load_settings
from ..events import publish

VIEWPORT = {"width": 1280, "height": 860}
PROFILE_DIR = DATA_DIR / "browser_profile"

_EXE = {
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
               r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
    "msedge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
               r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
}


def browser_exe(channel: str | None) -> str | None:
    for raw in _EXE.get(channel or "", []):
        path = os.path.expandvars(raw)
        if os.path.exists(path):
            return path
    return None


def installed_version(channel: str | None) -> str | None:
    """Chrome/Edge keep a folder named after their version next to the exe."""
    exe = browser_exe(channel)
    if not exe:
        return None
    vers = [d.name for d in Path(exe).parent.iterdir() if re.match(r"^\d+\.\d+\.\d+\.\d+$", d.name)]
    return max(vers, key=lambda v: [int(x) for x in v.split(".")]) if vers else None


class LiveSession:
    def __init__(self, sid: str, context: BrowserContext, page: Page, label: str):
        self.id = sid
        self.context = context
        self.page = page
        self.pages: list[Page] = [page]
        self.label = label
        self.step = "starting"
        self.state = "running"          # running | waiting | done
        self.taken_over = False
        self.created = time.time()
        self.application_id: int | None = None
        self.last_frame: str | None = None
        self.frame_meta: dict = {}
        self._cdp: CDPSession | None = None
        self._subs: set[asyncio.Queue] = set()
        self.resume_event = asyncio.Event()
        self.resume_event.set()

    # ---- screencast
    async def start_cast(self) -> None:
        await self._stop_cast()
        try:
            self._cdp = await self.context.new_cdp_session(self.page)
            self._cdp.on("Page.screencastFrame", self._on_frame)
            await self._cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60,
                                                          "maxWidth": 1280, "maxHeight": 900, "everyNthFrame": 1})
        except Exception:
            self._cdp = None

    async def _stop_cast(self) -> None:
        if self._cdp:
            try:
                await self._cdp.send("Page.stopScreencast")
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

    def _on_frame(self, params: dict) -> None:
        self.last_frame = params["data"]
        self.frame_meta = params.get("metadata", {})
        msg = {"type": "frame", "data": params["data"], "meta": self.frame_meta, "url": self.page.url,
               "step": self.step, "state": self.state, "taken_over": self.taken_over}
        for q in list(self._subs):
            if q.qsize() > 2:          # drop frames for slow viewers rather than lag
                try:
                    q.get_nowait()
                except Exception:
                    pass
            q.put_nowait(msg)
        if self._cdp:
            asyncio.ensure_future(self._ack(params["sessionId"]))

    async def _ack(self, session_id: int) -> None:
        try:
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:
            pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        if self.last_frame:
            q.put_nowait({"type": "frame", "data": self.last_frame, "meta": self.frame_meta, "url": self.page.url,
                          "step": self.step, "state": self.state, "taken_over": self.taken_over})
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    # ---- pages: follow popups (Apply buttons, "Sign in with Google") and come back when they close
    def watch(self, page: Page) -> None:
        page.on("popup", lambda p: asyncio.ensure_future(self.switch_page(p)))
        page.on("close", lambda p: asyncio.ensure_future(self._on_close(p)))

    async def switch_page(self, page: Page) -> None:
        if page not in self.pages:
            self.pages.append(page)
            self.watch(page)
        self.page = page
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await self.start_cast()

    async def _on_close(self, page: Page) -> None:
        if page in self.pages:
            self.pages.remove(page)
        if self.page is page and self.pages and self.state != "done":
            await self.switch_page(self.pages[-1])

    # ---- status
    def set_step(self, step: str, state: str | None = None) -> None:
        self.step = step
        if state:
            self.state = state
        publish("session", session=self.info())

    def info(self) -> dict:
        return {"id": self.id, "label": self.label, "step": self.step, "state": self.state, "url": self.page.url,
                "taken_over": self.taken_over, "application_id": self.application_id, "created": self.created}

    # ---- take over
    def take_over(self, on: bool) -> None:
        self.taken_over = on
        if on:
            self.resume_event.clear()
        else:
            self.resume_event.set()
        publish("session", session=self.info())

    async def wait_if_taken_over(self) -> None:
        await self.resume_event.wait()

    async def input(self, ev: dict) -> None:
        """Mouse/keyboard from the dashboard canvas. x,y arrive as 0..1 fractions of the frame."""
        meta = self.frame_meta or {}
        w = meta.get("deviceWidth") or VIEWPORT["width"]
        h = meta.get("deviceHeight") or VIEWPORT["height"]
        kind = ev.get("type")
        try:
            if kind in ("click", "down", "up", "move"):
                x, y = float(ev["x"]) * w, float(ev["y"]) * h
                if kind == "click":
                    await self.page.mouse.click(x, y)
                elif kind == "move":
                    await self.page.mouse.move(x, y)
            elif kind == "wheel":
                await self.page.mouse.wheel(0, float(ev.get("dy", 0)))
            elif kind == "text":
                await self.page.keyboard.insert_text(ev.get("text", ""))
            elif kind == "key":
                await self.page.keyboard.press(ev.get("key", ""))
            elif kind == "navigate" and ev.get("url"):
                await self.page.goto(ev["url"])
            elif kind == "back":
                await self.page.go_back()
        except Exception as e:
            publish("log", level="warning", message=f"Live input failed: {e}")

    async def screenshot(self, path: str, full_page: bool = True) -> str | None:
        try:
            await self.page.screenshot(path=path, full_page=full_page, timeout=20000)
            return path
        except Exception:
            try:
                await self.page.screenshot(path=path, full_page=False, timeout=10000)
                return path
            except Exception:
                return None

    async def close(self) -> None:
        self.state = "done"
        await self._stop_cast()
        for pg in list(self.pages):
            try:
                await pg.close()
            except Exception:
                pass
        for q in list(self._subs):
            q.put_nowait({"type": "closed"})
        publish("session_closed", id=self.id)


class BrowserManager:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self._headless: bool | None = None
        self.channel: str | None = None
        self.sessions: dict[str, LiveSession] = {}
        self._lock = asyncio.Lock()
        self._login: subprocess.Popen | None = None

    def login_open(self) -> bool:
        return bool(self._login and self._login.poll() is None)

    async def _ensure(self) -> BrowserContext:
        while self.login_open():          # the sign-in window has the profile: wait for the user to close it
            await asyncio.sleep(2)
        headless = bool(load_settings().get("headless", True))
        async with self._lock:
            if self._ctx and (self._headless == headless or self.sessions):
                return self._ctx
            await self._close_ctx()
            if not self._pw:
                self._pw = await async_playwright().start()
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            # Prefer the signed Chrome/Edge already on this PC: Windows Smart App Control blocks Playwright's own
            # unsigned Chromium build. Fall back to it only if neither is installed.
            pref = str(load_settings().get("browser_channel", "auto"))
            channels = [pref] if pref not in ("auto", "") else ["chrome", "msedge", None]
            last_err = None
            for ch in channels:
                ver = installed_version(ch)
                edge = f" Edg/{ver}" if ch == "msedge" and ver else ""
                ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{ver or '131.0.0.0'} Safari/537.36{edge}")
                try:
                    self._ctx = await self._pw.chromium.launch_persistent_context(
                        str(PROFILE_DIR), channel=ch, headless=headless, viewport=VIEWPORT, locale="en-IN",
                        timezone_id="Asia/Kolkata", user_agent=ua, accept_downloads=True,
                        args=["--disable-blink-features=AutomationControlled", "--no-default-browser-check",
                              "--no-first-run"],
                        ignore_default_args=["--enable-automation"])
                    await self._ctx.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    for pg in list(self._ctx.pages):   # the blank tab a persistent profile starts with
                        try:
                            await pg.close()
                        except Exception:
                            pass
                    self._headless = headless
                    self.channel = ch
                    return self._ctx
                except Exception as e:  # not installed, blocked, or the profile is locked: try the next
                    last_err = e
            raise RuntimeError(f"No browser could start (tried {channels}): {last_err}")

    async def _close_ctx(self) -> None:
        if self._ctx:
            try:
                await self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    async def new_session(self, label: str) -> LiveSession:
        ctx = await self._ensure()
        page = await ctx.new_page()
        sid = uuid.uuid4().hex[:10]
        s = LiveSession(sid, ctx, page, label)
        s.watch(page)
        self.sessions[sid] = s
        await s.start_cast()
        publish("session", session=s.info())
        return s

    async def open_login_window(self, url: str = "https://accounts.google.com/") -> dict:
        """Open a normal, not automated, browser window on the app's profile so the user can sign in once.
        Google refuses sign-ins inside automated browsers; the cookies this window saves are used by every
        application afterwards."""
        if self.login_open():
            return {"ok": True, "already": True}
        if self.sessions:
            return {"ok": False, "error": "An application is using the browser right now. Close the sessions in the "
                                          "Live tab (or wait for them to finish), then try again."}
        async with self._lock:
            await self._close_ctx()
        pref = str(load_settings().get("browser_channel", "auto"))
        order = [self.channel] if self.channel else ([pref] if pref not in ("auto", "") else ["chrome", "msedge"])
        exe = next((browser_exe(c) for c in order if browser_exe(c)), None)
        if not exe:
            return {"ok": False, "error": "Chrome or Edge was not found on this PC."}
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._login = subprocess.Popen([exe, f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
                                        "--no-default-browser-check", "--new-window", url])
        publish("log", level="info", message="Sign-in window opened. Sign in, then close that window.")
        asyncio.ensure_future(self._watch_login())
        return {"ok": True}

    async def _watch_login(self) -> None:
        while self.login_open():
            await asyncio.sleep(2)
        publish("login_closed")
        publish("log", level="info", message="Sign-in window closed. Your sign-ins are saved for applications.")

    async def close_session(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s:
            await s.close()

    def list(self) -> list[dict]:
        return [s.info() for s in self.sessions.values()]

    async def shutdown(self) -> None:
        for sid in list(self.sessions):
            await self.close_session(sid)
        await self._close_ctx()
        if self._pw:
            await self._pw.stop()


manager = BrowserManager()


def frame_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)

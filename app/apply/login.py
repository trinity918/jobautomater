"""Get past sign-in walls using the saved browser profile.

"Sign in / Continue with Google" is clicked, and in Google's account chooser the account is picked (the one
matching the profile email if it is there, else the first). That only works once the user has signed in to
Google in the app's browser profile (Settings -> Sign in to sites); otherwise the wall goes to the user.
"""
from __future__ import annotations

import asyncio
import re

from playwright.async_api import Page

from ..config import load_profile

GOOGLE_BTN = re.compile(r"(sign ?in|log ?in|continue|sign ?up|apply|register) (in )?with google|^google$", re.I)
LOGIN_WORDS = re.compile(r"\b(sign ?in|log ?in|create (an )?account|sign ?up|register)\b", re.I)


async def _google_button(page: Page):
    for frame in page.frames:
        # Google's own "Sign in with Google" button is an iframe from accounts.google.com/gsi
        if "accounts.google.com/gsi/button" in (frame.url or ""):
            try:
                btn = frame.locator("div[role=button], button").first
                if await btn.count() and await btn.is_visible():
                    return btn
            except Exception:
                pass
    for frame in page.frames:
        try:
            cands = frame.locator("button, a, [role=button], div[tabindex]")
            n = min(await cands.count(), 300)
            for i in range(n):
                el = cands.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    text = ((await el.inner_text(timeout=400)) or "").strip()
                    aria = (await el.get_attribute("aria-label")) or ""
                except Exception:
                    continue
                if GOOGLE_BTN.search(text[:80]) or GOOGLE_BTN.search(aria):
                    return el
        except Exception:
            continue
    return None


async def has_google_button(page: Page) -> bool:
    return await _google_button(page) is not None


async def google_signin(session) -> str:
    """Returns 'ok' (signed in or the account was picked), 'needs_user' (Google wants a password: the profile is
    not signed in), or 'none' (no Google button on the page)."""
    btn = await _google_button(session.page)
    if not btn:
        return "none"
    start_pages = len(session.pages)
    try:
        await btn.click(timeout=5000)
    except Exception:
        return "none"
    # the account chooser opens as a popup (session follows it) or in the same tab
    for _ in range(20):
        await asyncio.sleep(0.5)
        if len(session.pages) > start_pages or "accounts.google.com" in session.page.url:
            break
    g = session.page
    if "accounts.google.com" not in g.url:
        return "ok"   # one-tap style: signed in without a chooser
    email = str((load_profile().get("personal") or {}).get("email") or "").lower()
    for _ in range(4):
        try:
            await g.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1.2)
        if g.is_closed() or "accounts.google.com" not in g.url:
            return "ok"
        try:
            if await g.locator("input[type=email]:visible, input[type=password]:visible").count():
                return "needs_user"
            accounts = g.locator("[data-identifier], [data-email]")
            if await accounts.count():
                target = None
                for i in range(await accounts.count()):
                    a = accounts.nth(i)
                    ident = ((await a.get_attribute("data-identifier")) or (await a.get_attribute("data-email")) or "").lower()
                    if email and ident == email:
                        target = a
                        break
                await (target or accounts.first).click(timeout=5000)
                continue
            # consent / "Continue" / "Allow" screens
            for label in ("Continue", "Allow", "Confirm", "Next"):
                b = g.get_by_role("button", name=label, exact=True)
                if await b.count() and await b.first.is_visible():
                    await b.first.click(timeout=5000)
                    break
        except Exception:
            if g.is_closed():
                return "ok"
    await asyncio.sleep(2)
    return "ok" if g.is_closed() or "accounts.google.com" not in g.url else "needs_user"

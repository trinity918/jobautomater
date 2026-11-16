"""Generic, label-driven form filler. Works on Greenhouse, Lever, Ashby, Workable and most plain HTML forms."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from playwright.async_api import Page

from .answers import pick_option

COLLECT_JS = r"""
() => {
  // Custom-styled radios/checkboxes are often opacity:0 under a drawn circle: size alone counts for them.
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    const box = (r.width > 0 || r.height > 0) && s.visibility !== 'hidden' && s.display !== 'none';
    return box && (s.opacity !== '0' || ['radio', 'checkbox'].includes(el.type)); };
  const shown = el => vis(el) || (el.labels && Array.from(el.labels).some(vis));
  const txt = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const labelFor = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => txt(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    const al = el.getAttribute('aria-label'); if (al && al.length > 2) return al;
    const wrap = el.closest('label'); if (wrap && txt(wrap)) return txt(wrap);
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg && txt(lg)) return txt(lg); }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="label" i], [class*="question" i], [class*="title" i]');
      if (l && !l.contains(el) && txt(l) && txt(l).length < 400) return txt(l);
    }
    return el.getAttribute('placeholder') || el.name || '';
  };
  // The question above a group of options: walk up to the element that holds every option, then take the nearest
  // title-like element before it that is not itself an option label. Returns [text, element].
  const TITLE_SEL = 'legend, label, [class*="title" i], [class*="question" i], [class*="heading" i], [class*="label" i], h2, h3, h4, h5, p, span';
  const isOptionLabel = x => {
    // an option's own label sits right next to its input; a question title that merely points at the first option does not
    if (x.tagName === 'LABEL' && x.htmlFor) { const t = document.getElementById(x.htmlFor);
      if (t && ['radio', 'checkbox'].includes(t.type) && x.parentElement && x.parentElement.contains(t)) return true; }
    if (x.closest('label') && x.closest('label').querySelector('input[type=radio],input[type=checkbox]')) return true;
    return !!x.closest('[class*="option" i]');
  };
  const STRONG = 'legend, label, [class*="title" i], [class*="question" i], [class*="heading" i], [class*="label" i], h2, h3, h4, h5';
  // Nearest question text before the first option: label/heading-like elements win over plain paragraphs
  // (Ashby puts a description <p> between the title and the options; Lever puts a bare ✱ span after its title).
  const titleFor = first => {
    let c = first.parentElement;
    for (let i = 0; i < 7 && c; i++, c = c.parentElement) {
      const all = Array.from(c.querySelectorAll(TITLE_SEL)).filter(x =>
        !x.contains(first) && !x.querySelector('input,button,textarea,select') && !isOptionLabel(x) &&
        (first.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_PRECEDING) &&
        txt(x) && txt(x).length < 400 && !/^[✱*\s]+$/.test(txt(x)));
      const strong = all.filter(x => x.matches(STRONG));
      if (strong.length) return strong[strong.length - 1];
      if (all.length && i >= 3) return all[all.length - 1];
    }
    return null;
  };
  const groupInfo = el => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg && txt(lg)) return [txt(lg), lg]; }
    const rg = el.closest('[role="radiogroup"], [role="group"]');
    if (rg) { const lb = rg.getAttribute('aria-labelledby');
              if (lb) { const t = document.getElementById(lb); if (t && txt(t)) return [txt(t), t]; }
              const al = rg.getAttribute('aria-label'); if (al) return [al, rg]; }
    const t = titleFor(el);
    return t ? [txt(t), t] : [el.name || '', null];
  };
  const groupLabel = el => groupInfo(el)[0];
  const titleReq = t => !!t && (/[*✱]\s*$/.test(txt(t)) || /required/i.test(t.className || '') || !!t.querySelector('[class*="required" i]'));
  const optLabel = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    const w = el.closest('label'); if (w && txt(w)) return txt(w);
    const al = el.getAttribute('aria-label'); if (al) return al;
    const n = el.nextElementSibling; if (n && txt(n)) return txt(n);
    return el.value || '';
  };
  const isReq = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label || '') ||
      !!(el.closest('[class*="required" i]'));
  const skip = el => {
    const n = (el.name || '') + ' ' + (el.id || '') + ' ' + (el.className || '');
    return /honeypot|captcha|g-recaptcha|search/i.test(n) || el.closest('[aria-hidden="true"]') || el.tabIndex === -1 && !vis(el);
  };
  const comboValue = el => {
    if (el.value) return el.value;
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const v = p.querySelector('[class*="single-value" i], [class*="singleValue" i], [class*="multi-value" i], [class*="multiValue" i], [class*="selected-value" i]');
      if (v && txt(v)) return txt(v);
    }
    return '';
  };
  const fields = []; let idx = 0; const groups = {};
  document.querySelectorAll('[data-ja]').forEach(e => e.removeAttribute('data-ja'));
  const els = Array.from(document.querySelectorAll('input, textarea, select'));
  for (const el of els) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(type)) continue;
    if (skip(el)) continue;
    if (type !== 'file' && !shown(el)) continue;
    if (type === 'file' && !vis(el) && !vis(el.parentElement) && !vis(el.closest('div'))) continue;
    if (type === 'radio' || type === 'checkbox') {
      const key = (el.name || groupLabel(el)) + '|' + type;
      if (!groups[key]) {
        const [gl, gt] = groupInfo(el);
        groups[key] = { id: String(idx++), type: type === 'radio' ? 'radio' : 'checkbox', label: gl,
                        name: el.name || '', required: titleReq(gt), options: [], checked: [] };
        fields.push(groups[key]);
      }
      const g = groups[key];
      el.setAttribute('data-ja', g.id + ':' + g.options.length);
      const ol = optLabel(el);
      g.options.push(ol);
      if (el.checked) g.checked.push(ol);
      if (el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(g.label)) g.required = true;
      continue;
    }
    const id = String(idx++);
    el.setAttribute('data-ja', id);
    let label = labelFor(el);
    if (type === 'file' && /^(attach|upload|browse|choose( a)? file|select file|drop|add file|enter manually)?\s*$/i.test(label || '')) {
      // "Attach" buttons: the input's own id/name says which slot it is (resume, cover_letter), else the section heading
      const key = ((el.id || '') + ' ' + (el.name || '')).replace(/[_-]+/g, ' ').trim();
      const t = titleFor(el);
      label = /resume|cv|cover/i.test(key) ? key : (t ? txt(t) : label);
    }
    const lab = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    const f = { id, type: el.getAttribute('role') === 'combobox' ? 'combobox' : (el.tagName === 'SELECT' ? 'select' :
                (el.tagName === 'TEXTAREA' ? 'textarea' : type)),
                label, name: el.name || '', placeholder: el.getAttribute('placeholder') || '',
                required: isReq(el, label) || titleReq(lab), value: el.type === 'file' ? (el.files && el.files.length ? 'file' : '') :
                  (el.getAttribute('role') === 'combobox' ? comboValue(el) : (el.value || '')),
                multiple: !!el.multiple, accept: el.getAttribute('accept') || '' };
    if (el.tagName === 'SELECT') f.options = Array.from(el.options).map(o => txt(o)).filter(Boolean);
    fields.push(f);
  }
  // single checkboxes are booleans
  for (const f of fields) if (f.type === 'checkbox' && f.options.length === 1) { f.type = 'boolean'; f.label = f.label && f.label !== f.name ? f.label + ' ' + f.options[0] : f.options[0]; }
  // Yes/No (and similar) button groups, as on Ashby: sibling buttons with short texts including Yes and No.
  const seenBox = new Set();
  Array.from(document.querySelectorAll('button')).filter(b => vis(b) && /^yes$/i.test(txt(b))).forEach(yes => {
    const box = yes.parentElement;
    if (!box || seenBox.has(box)) return;
    const btns = Array.from(box.querySelectorAll(':scope > button')).filter(vis);
    const opts = btns.map(txt);
    if (btns.length < 2 || btns.length > 6 || !opts.some(o => /^no$/i.test(o))) return;
    seenBox.add(box);
    const id = String(idx++);
    box.setAttribute('data-ja', id);
    btns.forEach((b, i) => b.setAttribute('data-ja-btn', id + ':' + i));
    const t = titleFor(btns[0]);
    const pressed = btns.filter(b => /selected|active|checked|_on_|pressed/i.test(b.className) || b.getAttribute('aria-pressed') === 'true').map(txt);
    fields.push({ id, type: 'buttons', label: t ? txt(t) : '', options: opts, required: titleReq(t), checked: pressed });
  });
  return fields;
}
"""

SUBMIT_RE = re.compile(r"^\s*(submit( your)?( application)?|send application|apply( now)?|finish|complete application|submit & apply)\s*$", re.I)
NEXT_RE = re.compile(r"^\s*(next|continue|save (and|&) continue|proceed|next step)\s*$", re.I)
APPLY_BTN_RE = re.compile(r"^\s*(apply( now| for this (job|position|role)| to this job| here| online)?|i'?m interested|start application)\s*$", re.I)
CONFIRM_RE = re.compile(r"(thank(s| you)( so much)? for (applying|your (application|interest))|application (has been |was )?(successfully )?(submitted|received|sent|complete)|"
                        r"we('ve| have) received your application|successfully (submitted|applied)|your application is (in|complete)|application submitted)", re.I)
ERROR_RE = re.compile(r"(this field is required|is required|please (fill|complete|enter|select)|can't be blank|invalid (email|phone)|must be)", re.I)


async def collect(page: Page) -> list[dict]:
    fields: list[dict] = []
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame and _form_frame(f.url)]:
        try:
            got = await frame.evaluate(COLLECT_JS)
        except Exception:
            continue
        for f in got:
            f["_frame"] = frame
        fields += got
    return fields


def _form_frame(url: str) -> bool:
    return bool(re.search(r"greenhouse|lever|ashby|workable|smartrecruiters|myworkdayjobs|apply", url or "", re.I))


async def combobox_options(page: Page, field: dict) -> list[str]:
    frame = field["_frame"]
    try:
        el = frame.locator(f'[data-ja="{field["id"]}"]')
        await el.click(timeout=4000)
        await asyncio.sleep(0.5)
        opts = await frame.evaluate(
            "() => Array.from(document.querySelectorAll('[role=option], [class*=\"option\" i]')).filter(o => o.offsetParent)"
            ".map(o => (o.innerText||'').trim()).filter(t => t && t.length < 120).slice(0, 60)")
        await page.keyboard.press("Escape")
        opts = list(dict.fromkeys(opts))
        return opts if len(opts) <= 25 else []
    except Exception:
        return []


async def fill_field(page: Page, field: dict, value: Any, resume_path: str, cover_path: str | None) -> bool:
    frame = field["_frame"]
    fid = field["id"]
    t = field["type"]
    try:
        if t == "file":
            path = resume_path if value == "__resume__" else cover_path
            if not path:
                return False
            await frame.locator(f'[data-ja="{fid}"]').set_input_files(path, timeout=8000)
            await asyncio.sleep(1.5)  # let the ATS parse/upload
            return True
        if t in ("text", "email", "tel", "url", "number", "textarea", "date", "search", "input"):
            loc = frame.locator(f'[data-ja="{fid}"]')
            val = str(value)
            if t == "number":
                m = re.search(r"\d+(\.\d+)?", val)
                val = m.group(0) if m else val
            await loc.fill(val, timeout=6000)
            # some React inputs only register real keystrokes
            if (await loc.input_value()) != val:
                await loc.click()
                await loc.press("Control+A")
                await loc.type(val, delay=15)
            return True
        if t == "select":
            choice = pick_option(str(value), field.get("options") or []) or str(value)
            loc = frame.locator(f'[data-ja="{fid}"]')
            try:
                await loc.select_option(label=choice, timeout=5000)
            except Exception:
                await loc.select_option(value=choice, timeout=5000)
            return True
        if t == "combobox":
            loc = frame.locator(f'[data-ja="{fid}"]')
            await loc.click(timeout=5000)
            await loc.fill("", timeout=3000)
            typed = str(value)[:40]
            await loc.type(typed, delay=25)
            opts = []
            for _ in range(12):  # up to ~6 s for async suggestions
                await asyncio.sleep(0.5)
                opts = await frame.evaluate(
                    "() => Array.from(document.querySelectorAll('[role=option]')).filter(o => o.offsetParent).map(o => (o.innerText||'').trim())")
                opts = [o for o in opts if o and not re.match(r"^(no options|loading|searching)", o, re.I)]
                if opts:
                    break
            if opts:
                choice = pick_option(str(value), opts) or opts[0]
                await frame.locator("[role=option]").filter(has_text=choice).first.click(timeout=4000)
            else:
                await page.keyboard.press("Enter")
            return True
        if t in ("radio", "buttons"):
            choice = pick_option(str(value), field["options"])
            if not choice:
                return False
            i = field["options"].index(choice)
            sel = f'[data-ja="{fid}:{i}"]' if t == "radio" else f'[data-ja-btn="{fid}:{i}"]'
            loc = frame.locator(sel)
            try:
                await loc.check(timeout=4000) if t == "radio" else await loc.click(timeout=4000)
            except Exception:
                await loc.click(force=True, timeout=4000)
            return True
        if t == "boolean":
            want = str(value).strip().lower() in ("yes", "true", "1", "y")
            loc = frame.locator(f'[data-ja="{fid}:0"]')
            if want:
                try:
                    await loc.check(timeout=4000)
                except Exception:
                    await loc.click(force=True, timeout=4000)
            return True
        if t == "checkbox":
            wanted = value if isinstance(value, list) else [value]
            ok = False
            for w in wanted:
                choice = pick_option(str(w), field["options"])
                if choice:
                    i = field["options"].index(choice)
                    loc = frame.locator(f'[data-ja="{fid}:{i}"]')
                    try:
                        await loc.check(timeout=4000)
                    except Exception:
                        await loc.click(force=True, timeout=4000)
                    ok = True
            return ok
    except Exception:
        return False
    return False


def is_filled(f: dict) -> bool:
    if f["type"] in ("radio", "checkbox", "buttons"):
        return bool(f.get("checked"))
    if f["type"] == "boolean":
        return bool(f.get("checked"))
    return bool(str(f.get("value") or "").strip())


async def click_matching(page: Page, pattern: re.Pattern, scope_form: bool = True) -> bool:
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame and _form_frame(f.url)]:
        try:
            cands = frame.locator("button, input[type=submit], a[role=button], a")
            n = min(await cands.count(), 200)
            for i in range(n):
                el = cands.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    text = (await el.inner_text(timeout=500)).strip() or (await el.get_attribute("value") or "")
                except Exception:
                    continue
                if pattern.match(text or "") and not re.search(r"linkedin|indeed|google|seek", text, re.I):
                    await el.scroll_into_view_if_needed(timeout=3000)
                    await el.click(timeout=5000)
                    return True
        except Exception:
            continue
    return False


async def page_text(page: Page) -> str:
    out = ""
    for frame in page.frames:
        try:
            out += " " + await frame.evaluate("() => document.body ? document.body.innerText.slice(0, 20000) : ''")
        except Exception:
            pass
    return out


async def has_captcha_challenge(page: Page) -> bool:
    for frame in page.frames:
        u = frame.url or ""
        if re.search(r"hcaptcha\.com/.*(challenge|hcaptcha.html#frame=challenge)|recaptcha/(api2|enterprise)/bframe|challenges\.cloudflare", u):
            try:
                el = await frame.frame_element()
                box = await el.bounding_box()
                if box and box["width"] > 100 and box["height"] > 100:
                    return True
            except Exception:
                return True
    return False


async def needs_login(page: Page) -> bool:
    try:
        pw = page.locator("input[type=password]")
        return await pw.count() > 0 and await pw.first.is_visible()
    except Exception:
        return False

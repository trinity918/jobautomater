"""Resume studio: render the structured resume into one of 8 ATS-friendly templates, and print it to PDF."""
from __future__ import annotations

import html
import re

from pathlib import Path

from .config import RESUME_DIR

TEMPLATES = {
    "engineering": {"name": "Engineering", "note": "ATS-safe"},
    "classic": {"name": "Classic", "note": "ATS-safe"},
    "centered": {"name": "Centered", "note": "ATS-safe"},
    "sidebar": {"name": "Sidebar", "note": "Two columns"},
    "banner": {"name": "Banner", "note": "ATS-safe"},
    "side_headings": {"name": "Side headings", "note": "ATS-safe"},
    "campus": {"name": "Campus", "note": "ATS-safe"},
    "modern": {"name": "Modern", "note": "ATS-safe"},
}

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
.rs{font-family:var(--font);color:#1a1a1a;font-size:10.2pt;line-height:1.38;background:#fff;padding:var(--pad)}
.rs h1{font-size:22pt;font-weight:700;color:var(--accent);letter-spacing:.2px}
.rs .headline{font-size:10.5pt;color:#444;margin-top:2px}
.rs .contact{font-size:9pt;color:#333;margin-top:4px}
.rs .contact span+span:before{content:" · ";color:#888}
.rs .sec{margin-top:10px}
.rs .sec-title{font-size:11.5pt;font-weight:700;color:var(--accent);display:flex;align-items:center;gap:8px;margin-bottom:4px}
.rs .sec-title:after{content:"";flex:1;border-bottom:1px solid var(--rule)}
.rs .item{margin-bottom:6px;break-inside:avoid}
.rs .ihead{display:flex;justify-content:space-between;gap:12px}
.rs .ihead .h{font-weight:700}
.rs .ihead .d{white-space:nowrap;font-size:9.3pt;color:#333}
.rs .sub{font-size:9.3pt;color:#555;font-style:italic}
.rs ul{margin:2px 0 0 16px}
.rs li{margin:1px 0}
.rs .txt{white-space:pre-wrap}
.rs [contenteditable]:hover{outline:1px dashed #9db4ff;outline-offset:2px;cursor:text}
.rs [contenteditable]:focus{outline:2px solid #2f55d4;outline-offset:2px}
"""

THEME_CSS = {
    "engineering": ".rs{--font:'Arial',sans-serif;--accent:#111;--rule:#111;--pad:28px 34px}.rs h1{font-size:20pt}"
                   ".rs .sec-title{text-transform:uppercase;font-size:10.5pt;letter-spacing:.8px}",
    "classic": ".rs{--font:'Arial',sans-serif;--accent:#1d4e9e;--rule:#1d4e9e;--pad:30px 36px}.rs header{text-align:center}",
    "centered": ".rs{--font:'Georgia',serif;--accent:#222;--rule:#bbb;--pad:30px 40px}.rs header{text-align:center}"
                ".rs .sec-title{justify-content:center;text-transform:uppercase;letter-spacing:1.5px;font-size:10pt}"
                ".rs .sec-title:after{display:none}.rs .sec-title{border-bottom:1px solid #bbb;padding-bottom:2px}",
    "sidebar": ".rs{--font:'Helvetica',Arial,sans-serif;--accent:#0f5c4d;--rule:#cfe3de;--pad:0}"
               ".rs .cols{display:grid;grid-template-columns:30% 70%}.rs aside{background:#eef6f4;padding:28px 18px;min-height:100%}"
               ".rs main{padding:28px 28px}.rs aside .sec-title:after{display:none}.rs aside .txt{font-size:9.3pt}",
    "banner": ".rs{--font:'Arial',sans-serif;--accent:#2f55d4;--rule:#c9d4f7;--pad:0}.rs header{background:#2f55d4;color:#fff;padding:22px 34px}"
              ".rs header h1,.rs header .headline,.rs header .contact{color:#fff}.rs .body{padding:14px 34px 28px}",
    "side_headings": ".rs{--font:'Arial',sans-serif;--accent:#333;--rule:#ddd;--pad:30px 34px}"
                     ".rs .sec{display:grid;grid-template-columns:120px 1fr;gap:14px;border-top:1px solid #ddd;padding-top:8px}"
                     ".rs .sec-title{text-transform:uppercase;font-size:9.5pt;letter-spacing:1px}.rs .sec-title:after{display:none}",
    "campus": ".rs{--font:'Times New Roman',serif;--accent:#000;--rule:#000;--pad:28px 36px}.rs header{text-align:center}"
              ".rs h1{font-size:19pt}.rs .sec-title{text-transform:uppercase;font-size:10.5pt}",
    "modern": ".rs{--font:'Segoe UI',Arial,sans-serif;--accent:#6b3fd4;--rule:#e2d9f7;--pad:30px 36px}"
              ".rs h1{font-weight:800}.rs .sec-title{font-size:10.5pt;letter-spacing:.5px;text-transform:uppercase}"
              ".rs .ihead .h{color:#2a1a55}",
}


def _e(s: str) -> str:
    return html.escape(str(s or ""))


def _ed(text: str, path: str, editable: bool, tag: str = "span", cls: str = "") -> str:
    attrs = f' class="{cls}"' if cls else ""
    if editable:
        attrs += f' contenteditable="true" data-path="{path}" spellcheck="true"'
    return f"<{tag}{attrs}>{_e(text)}</{tag}>"


def _section(sec: dict, si: int, editable: bool) -> str:
    out = [f'<section class="sec"><div class="sec-title">{_ed(sec.get("title",""), f"sections.{si}.title", editable)}</div><div>']
    if sec.get("kind") == "text":
        out.append(_ed(sec.get("text", ""), f"sections.{si}.text", editable, "div", "txt"))
    else:
        for ii, it in enumerate(sec.get("items") or []):
            p = f"sections.{si}.items.{ii}"
            out.append('<div class="item"><div class="ihead">')
            out.append(_ed(it.get("heading", ""), f"{p}.heading", editable, "span", "h"))
            if it.get("date") or editable:
                out.append(_ed(it.get("date", ""), f"{p}.date", editable, "span", "d"))
            out.append("</div>")
            if it.get("sub"):
                out.append(_ed(it["sub"], f"{p}.sub", editable, "div", "sub"))
            if it.get("bullets"):
                out.append("<ul>" + "".join(_ed(b, f"{p}.bullets.{bi}", editable, "li")
                                            for bi, b in enumerate(it["bullets"])) + "</ul>")
            out.append("</div>")
    out.append("</div></section>")
    return "".join(out)


def render(struct: dict, template: str = "classic", editable: bool = False) -> str:
    template = template if template in TEMPLATES else "classic"
    c = struct.get("contact") or {}
    contact_bits = [c.get("email"), c.get("phone"), c.get("location")] + list(c.get("links") or [])
    contact = "".join(f"<span>{_e(b)}</span>" for b in contact_bits if b)
    header = (f'<header>{_ed(struct.get("name",""), "name", editable, "h1")}'
              + (_ed(struct.get("headline", ""), "headline", editable, "div", "headline") if struct.get("headline") or editable else "")
              + f'<div class="contact">{contact}</div></header>')
    secs = struct.get("sections") or []
    if template == "sidebar":
        side_names = {"Skills", "Education", "Certifications", "Languages", "Interests", "Achievements"}
        side = "".join(_section(s, i, editable) for i, s in enumerate(secs) if s.get("title") in side_names)
        main = "".join(_section(s, i, editable) for i, s in enumerate(secs) if s.get("title") not in side_names)
        body = f'<div class="cols"><aside>{header}{side}</aside><main>{main}</main></div>'
    elif template == "banner":
        body = header + '<div class="body">' + "".join(_section(s, i, editable) for i, s in enumerate(secs)) + "</div>"
    else:
        body = header + "".join(_section(s, i, editable) for i, s in enumerate(secs))
    css = BASE_CSS + THEME_CSS[template]
    return f'<style>{css}</style><div class="rs tpl-{template}">{body}</div>'


def full_document(struct: dict, template: str) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'><style>@page{size:A4;margin:0}html,body{margin:0}</style>"
            f"</head><body>{render(struct, template, editable=False)}</body></html>")


async def export_pdf(struct: dict, template: str) -> Path:
    from .apply.browser import manager
    browser = await manager._ensure()
    ctx = await browser.new_context()
    page = await ctx.new_page()
    try:
        await page.set_content(full_document(struct, template), wait_until="load")
        name = re.sub(r"[^A-Za-z0-9]+", "_", struct.get("name") or "Resume").strip("_") or "Resume"
        path = RESUME_DIR / f"{name}_Resume_{template}.pdf"
        await page.pdf(path=str(path), format="A4", print_background=True,
                       margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        return path
    finally:
        await ctx.close()


def set_path(struct: dict, path: str, value: str) -> dict:
    parts = path.split(".")
    cur = struct
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur.setdefault(p, {})
    last = parts[-1]
    if isinstance(cur, list):
        idx = int(last)
        if value.strip() == "" and parts[-2] == "bullets":
            cur.pop(idx)
        else:
            cur[idx] = value
    else:
        cur[last] = value
    return struct



"""Direct mail: mail-merge lists of owner mailing addresses and printable letters."""
from __future__ import annotations

import csv
import datetime as dt
import html
import re
from pathlib import Path

from .fetch import load_sources
from .skiptrace import ENTITY_RE, split_owner_names

LETTERS = Path(__file__).resolve().parent.parent / "config" / "letters"
ESTATE_RE = re.compile(r"\b(EST(ATE)? OF|ESTATE|HEIRS?|DECD|DECEASED|UNKNOWN HEIRS)\b")


def salutation(owner: str) -> tuple[str, str]:
    """(addressee line for the envelope, name for 'Dear ...')."""
    owner = (owner or "").upper()
    if ENTITY_RE.search(owner):
        name = " ".join(owner.split()).title().replace("Llc", "LLC").replace("Inc", "Inc.")
        return name, "Owner"
    people = [n for n in split_owner_names(owner) if "entity" not in n]
    names = [f"{n['first']} {n['last']}".strip().title() for n in people]
    if ESTATE_RE.search(owner):
        return f"Estate of {names[0] if names else owner.title()}", "Family Member or Personal Representative"
    if not names:
        return owner.title(), "Homeowner"
    if len(names) == 2 and people[0]["last"] == people[1]["last"]:
        both = f"{people[0]['first'].title()} and {people[1]['first'].title()} {people[0]['last'].title()}"
        return both, f"{people[0]['first'].title()} and {people[1]['first'].title()}"
    return " & ".join(names), " and ".join(n.split()[0] for n in names)


def mailing_rows(parcels: list[dict]) -> list[dict]:
    """One row per property, deduped by owner + mailing address, incomplete addresses dropped."""
    seen, out = set(), []
    for p in parcels:
        addr, city, st, z = (p.get(k) or "" for k in ("owner_addr1", "owner_city", "owner_state", "owner_zip"))
        if not (addr and city and z):
            continue
        key = (p.get("owner_name"), addr.upper(), z[:5], p.get("site_addr"))
        if key in seen:
            continue
        seen.add(key)
        to, dear = salutation(p.get("owner_name"))
        out.append({"addressee": to, "dear": dear, "mail_addr": addr.title(), "mail_addr2": (p.get("owner_addr2") or "").title(),
                    "mail_city": city.title(), "mail_state": st.upper(), "mail_zip": z[:5],
                    "property_address": (p.get("site_addr") or "").title(),
                    "property_city": (p.get("site_city") or "").title(), "property_zip": (p.get("site_zip") or "")[:5],
                    "county": p.get("county"), "parcel_id": p.get("parcel_id"), "owner_name": p.get("owner_name")})
    return out


def export_mailing_list(parcels: list[dict], path: str | Path) -> int:
    rows = mailing_rows(parcels)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]) if rows else ["addressee"])
        w.writeheader(); w.writerows(rows)
    return len(rows)


def templates() -> dict[str, str]:
    return {f.stem: f.read_text() for f in sorted(LETTERS.glob("*.txt"))}


def business() -> dict:
    b = {"your_name": "Your Name", "your_company": "Your Company", "your_phone": "(555) 555-5555",
         "your_email": "you@example.com", "your_website": ""}
    b.update({k: v for k, v in load_sources().get("business", {}).items() if not k.startswith("_")})
    return b


def render_letters(parcels: list[dict], template: str) -> str:
    """Printable HTML: one letter per page, the addressee block positioned for a #10 window envelope."""
    body = templates()[template]
    vals_base = {**business(), "date": dt.date.today().strftime("%B %d, %Y").replace(" 0", " ")}
    pages = []
    for r in mailing_rows(parcels):
        vals = {**vals_base, **r}
        text = re.sub(r"\{(\w+)\}", lambda m: str(vals.get(m.group(1), m.group(0))), body)
        text = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        addr = "<br>".join(html.escape(x) for x in (r["addressee"], r["mail_addr"], r["mail_addr2"],
                                                   f"{r['mail_city']}, {r['mail_state']} {r['mail_zip']}") if x)
        pages.append(f'<section class="letter"><div class="addr">{addr}</div>'
                     f'<div class="body">{html.escape(text.strip())}</div></section>')
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Letters</title><style>"
            "@page{size:letter;margin:0}body{margin:0;font:12pt/1.5 Georgia,serif;color:#111;background:#fff}"
            ".letter{width:8.5in;height:11in;padding:0.75in 0.9in;box-sizing:border-box;page-break-after:always}"
            ".addr{margin-top:1.35in;margin-left:0.1in;height:1.4in}.body{white-space:pre-wrap;margin-top:0.3in}"
            "@media screen{body{background:#ddd}.letter{background:#fff;margin:20px auto;box-shadow:0 2px 8px #0003}}"
            "</style></head><body>" + "".join(pages) + "</body></html>")

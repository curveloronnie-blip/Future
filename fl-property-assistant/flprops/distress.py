"""Distress scoring: combines owner/parcel indicators with recorded events into a 0-100 score."""
from __future__ import annotations

import datetime as dt
import re

from .db import address_key

ESTATE_RE = re.compile(r"\b(EST OF|ESTATE OF|ESTATE|HEIRS?|DECEASED|DECD|DEC'D|PERS(ONAL)? REP|P/R|"
                       r"UNKNOWN HEIRS|LIFE EST|LIFE ESTATE|REM:?|REMAINDER)\b")
ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|LP|LTD|HOLDINGS|PROPERTIES|INVESTMENTS)\b")
TRUST_RE = re.compile(r"\b(TRUST|TR|TRUSTEE|TRS|REV TR|LIVING TR)\b")
BANK_RE = re.compile(r"\b(BANK|MORTGAGE|FEDERAL NATIONAL|FANNIE|FREDDIE|HUD|SECRETARY OF HOUSING|"
                     r"TRUST COMPANY|LOAN SERVICING|NATIONAL ASSOCIATION|N A)\b")

# points per recorded event type (first occurrence), and per extra occurrence
EVENT_WEIGHTS = {
    "lis_pendens": (30, 5), "foreclosure": (30, 5), "foreclosure_sale": (35, 0),
    "tax_delinquent": (20, 5), "tax_deed": (30, 0), "lien": (12, 4), "code_violation": (12, 4),
    "probate": (25, 0), "death": (20, 0), "bankruptcy": (20, 0), "eviction": (10, 3),
    "divorce": (12, 0), "utility_shutoff": (12, 0), "vacant": (15, 0), "other": (3, 1),
}


def owner_signals(p: dict, today: dt.date | None = None) -> list[tuple[str, int]]:
    today = today or dt.date.today()
    sig: list[tuple[str, int]] = []
    owner = (p.get("owner_name") or "").upper()
    state = (p.get("owner_state") or "").strip().upper()
    mail = address_key(p.get("owner_addr1"))
    site = p.get("site_key") or address_key(p.get("site_addr"))

    if ESTATE_RE.search(owner):
        sig.append(("Owner name indicates estate / heirs / deceased owner", 25))
    if BANK_RE.search(owner):
        sig.append(("Bank / lender / HUD owned (REO)", 15))
    if state and state not in ("FL", "FLORIDA"):
        sig.append((f"Out-of-state owner ({state})", 12))
    elif mail and site and mail != site:
        sig.append(("Absentee owner (mailing address differs from property)", 8))
    if p.get("homestead") == 0 and not ENTITY_RE.search(owner):
        sig.append(("No homestead exemption (not owner-occupied)", 5))
    if ENTITY_RE.search(owner):
        sig.append(("Owned by a business entity", 2))
    if TRUST_RE.search(owner):
        sig.append(("Held in trust", 3))
    ly = p.get("last_sale_year")
    if ly and ly > 1900 and today.year - ly >= 20:
        sig.append((f"Long-term ownership (last sale {ly})", 8))
    elif ly and ly > 1900 and today.year - ly >= 12:
        sig.append((f"Long-term ownership (last sale {ly})", 4))
    yb = p.get("year_built")
    if yb and 1800 < yb < 1970:
        sig.append((f"Older structure (built {yb})", 4))
    if (p.get("last_sale_price") or 0) in (0, 100) and ly:
        sig.append(("Last transfer for nominal value (inheritance / quitclaim)", 4))
    return sig


def event_signals(events: list[dict]) -> list[tuple[str, int]]:
    sig: list[tuple[str, int]] = []
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    from .db import EVENT_TYPES
    for et, n in counts.items():
        first, extra = EVENT_WEIGHTS.get(et, (3, 1))
        pts = first + extra * (n - 1)
        label = EVENT_TYPES.get(et, et) + (f" (x{n})" if n > 1 else "")
        sig.append((label, pts))
    return sig


def score(p: dict, events: list[dict], today: dt.date | None = None) -> dict:
    sig = owner_signals(p, today) + event_signals(events)
    total = min(100, sum(s for _, s in sig))
    tier = "high" if total >= 50 else "medium" if total >= 25 else "low"
    return {"score": total, "tier": tier, "signals": [{"reason": r, "points": s} for r, s in sig]}

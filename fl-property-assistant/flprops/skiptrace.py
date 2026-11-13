"""Skip tracing: owner / decision-maker phones, emails and mailing addresses.

Contact data comes from a skip-trace provider you have an account with (BatchSkipTracing,
REISkip, PropStream, BatchData, DataTree, TLOxp, etc.). Two ways in:

1. CSV round trip (works with every provider):
     export_for_skiptrace()  -> upload the file to your provider
     import_skiptrace_results() <- import the results file they give back
2. Direct API: configure "skiptrace" in config/sources.json with your provider's endpoint,
   request template and response field paths (see docs there). The API key is read from an
   environment variable, never stored in the config.

Compliance built in: numbers flagged Do-Not-Call or as known TCPA litigators are stored but
excluded from dialer exports by default. Before cold calling or texting you must scrub against
the National DNC Registry and Florida's own DNC list, and Florida's Telemarketing Act (FTSA)
restricts automated calls/texts without prior express written consent.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from pathlib import Path

import requests

from . import db
from .fetch import load_sources
from .importers import _open_rows

_SUFFIX = {"JR", "SR", "II", "III", "IV"}
_NOISE = re.compile(r"\b(EST(ATE)? OF|ESTATE|HEIRS?( OF)?|UNKNOWN HEIRS|DECD|DECEASED|LIFE EST(ATE)?|"
                    r"TR(USTEE)?S?|TTEES?|REV(OCABLE)? (LIVING )?TR(UST)?|LIVING TR(UST)?|H/E|JTRS|ET AL|"
                    r"ETAL|ET UX|ET VIR|REM:?|C/O)\b")
ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|LP|LLP|LTD|COMPANY|HOLDINGS|PROPERTIES|"
                       r"INVESTMENTS|GROUP|PARTNERS|BANK|ASSOCIATION|ASSN|CHURCH|CITY OF|COUNTY|STATE OF)\b")


def split_owner_names(owner: str) -> list[dict]:
    """Florida rolls write owners 'LAST FIRST MIDDLE', co-owners joined by '&'.

    'DOE JOHN A & MARY B' -> [{first: JOHN, last: DOE}, {first: MARY, last: DOE}]
    Entity owners (LLC, INC, ...) are returned as {'entity': name}.
    """
    owner = (owner or "").upper().strip()
    if not owner:
        return []
    if ENTITY_RE.search(owner):
        return [{"entity": " ".join(owner.split())}]
    clean = " ".join(_NOISE.sub(" ", owner).replace(",", " ").split())
    people, last = [], None
    for part in [p.strip() for p in re.split(r"&|\bAND\b|;", clean) if p.strip()]:
        w = [x for x in part.split() if x not in _SUFFIX]
        if not w:
            continue
        # '& MARY' / '& MARY B' share the first owner's last name; '& ROE RICHARD' is a new LAST FIRST
        if last and (len(w) == 1 or (len(w) == 2 and len(w[1]) == 1)):
            people.append({"first": w[0], "middle": " ".join(w[1:]), "last": last})
        elif len(w) == 1:
            people.append({"first": "", "middle": "", "last": w[0]})
        else:
            last = w[0]
            people.append({"first": w[1], "middle": " ".join(w[2:]), "last": w[0]})
    return people


EXPORT_COLS = ["lead_id", "first_name", "last_name", "property_address", "property_city", "property_state",
               "property_zip", "mailing_address", "mailing_city", "mailing_state", "mailing_zip", "role"]


def targets_for(conn: sqlite3.Connection, p: dict) -> list[dict]:
    """People worth tracing for a parcel: individual owners, or the managers/officers of an entity owner,
    or the personal representative named in a probate case."""
    out = []
    for n in split_owner_names(p.get("owner_name")):
        if "entity" in n:
            for dm in decision_makers(conn, n["entity"]):
                last, first = _last_first(dm["person_name"])
                if last and first:
                    out.append({"first": first, "last": last, "role": f"{dm['title'] or 'officer'} of {n['entity']}",
                                "mail": dm.get("person_addr") or ""})
        else:
            out.append({**n, "role": "owner"})
    for e in conn.execute("SELECT party FROM events WHERE county=? AND parcel_key=? AND event_type='probate' "
                          "AND party != ''", (p["county"], p["parcel_key"])):
        w = e["party"].replace(",", " ").split()
        if len(w) >= 2:
            out.append({"first": w[0], "last": w[-1], "role": "personal representative / probate party"})
    return out


def _last_first(name: str) -> tuple[str, str]:
    """Sunbiz lists people 'LAST, FIRST M' or 'LAST FIRST M'."""
    name = (name or "").upper().strip()
    if "," in name:
        last, rest = name.split(",", 1)
        return last.strip(), (rest.split() or [""])[0]
    w = [x for x in name.split() if x not in _SUFFIX]
    return (w[0], w[1]) if len(w) >= 2 else ("", "")


def export_for_skiptrace(conn: sqlite3.Connection, parcels: list[dict], path: str | Path,
                         skip_already_traced: bool = True) -> int:
    n = 0
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, EXPORT_COLS)
        w.writeheader()
        for p in parcels:
            if skip_already_traced and conn.execute(
                    "SELECT 1 FROM contacts WHERE county=? AND parcel_key=? AND (phone!='' OR email!='') LIMIT 1",
                    (p["county"], p["parcel_key"])).fetchone():
                continue
            for t in targets_for(conn, p):
                w.writerow({
                    "lead_id": f"{p['county']}|{p['parcel_key']}", "first_name": t.get("first", ""),
                    "last_name": t.get("last", ""), "property_address": p.get("site_addr"),
                    "property_city": p.get("site_city"), "property_state": "FL", "property_zip": p.get("site_zip"),
                    "mailing_address": p.get("owner_addr1"), "mailing_city": p.get("owner_city"),
                    "mailing_state": p.get("owner_state"), "mailing_zip": p.get("owner_zip"), "role": t["role"],
                })
                n += 1
    return n


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1", "dnc", "x")


def _phone(v) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    d = d[1:] if len(d) == 11 and d.startswith("1") else d
    return d if len(d) == 10 else ""


def import_skiptrace_results(conn: sqlite3.Connection, path: str | Path, source: str | None = None) -> int:
    """Import a provider's results file. Recognises common layouts:
    Phone1/Phone 1/Mobile1/Landline1 ..., matching '... Type' and '... DNC' columns, Email1..N,
    and the lead_id column we exported (or property address to match back)."""
    n = 0
    source = source or Path(path).name
    for r in _open_rows(path):
        low = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        lead = low.get("lead_id") or low.get("input_lead_id") or ""
        county, pkey = (lead.split("|", 1) + [""])[:2] if "|" in lead else (None, "")
        if not pkey:
            addr = low.get("property_address") or low.get("input_property_address") or low.get("address")
            hit = conn.execute("SELECT county, parcel_key FROM parcels WHERE site_key=? LIMIT 1",
                               (db.address_key(addr),)).fetchone() if addr else None
            if not hit:
                continue
            county, pkey = hit
        name = " ".join(x for x in (low.get("first_name") or low.get("input_first_name"),
                                    low.get("last_name") or low.get("input_last_name")) if x)
        role = low.get("role") or "owner"
        mail = " ".join(x for x in (low.get("mailing_address"), low.get("mailing_city"),
                                    low.get("mailing_state"), low.get("mailing_zip")) if x)
        phones, emails = [], []
        for k, v in low.items():
            m = re.fullmatch(r"(phone|mobile|cell|landline|wireless)[ _]?(\d+)( number)?", k)
            if m and _phone(v):
                i, kind = m.group(2), m.group(1)
                ptype = low.get(f"{kind}{i} type") or low.get(f"{kind} {i} type") or low.get(f"{kind}_{i}_type") \
                    or ({"mobile": "mobile", "cell": "mobile", "wireless": "mobile", "landline": "landline"}.get(kind, ""))
                dnc = any(_truthy(low.get(x)) for x in (f"{kind}{i} dnc", f"{kind} {i} dnc", f"{kind}_{i}_dnc",
                                                         f"{kind}{i}_dnc"))
                lit = any(_truthy(low.get(x)) for x in (f"{kind}{i} litigator", f"{kind}_{i}_litigator"))
                phones.append((_phone(v), ptype.lower(), int(dnc), int(lit)))
            if re.fullmatch(r"e-?mail[ _]?\d*( address)?", k) and "@" in v:
                emails.append(v.lower())
        for ph, pt, dnc, lit in phones:
            n += conn.execute("INSERT OR IGNORE INTO contacts (county, parcel_key, name, role, phone, phone_type, dnc, "
                              "litigator, email, mail_addr, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (county, pkey, name, role, ph, pt, dnc, lit, "", mail, source)).rowcount
        for em in emails:
            n += conn.execute("INSERT OR IGNORE INTO contacts (county, parcel_key, name, role, phone, email, mail_addr, "
                              "source) VALUES (?,?,?,?,?,?,?,?)", (county, pkey, name, role, "", em, mail, source)).rowcount
    db.log_import(conn, str(path), "skiptrace", n)
    return n


def contacts_for(conn: sqlite3.Connection, county: str, parcel_key: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM contacts WHERE county=? AND parcel_key=? ORDER BY dnc, litigator, "
        "CASE phone_type WHEN 'mobile' THEN 0 WHEN 'wireless' THEN 0 ELSE 1 END", (county, parcel_key))]


def export_dialer_list(conn: sqlite3.Connection, path: str | Path, include_dnc: bool = False) -> int:
    """Phone list for leads in your pipeline. DNC and litigator numbers are excluded unless include_dnc."""
    rows = conn.execute(
        "SELECT c.*, p.site_addr, p.site_city, p.site_zip, l.status FROM contacts c "
        "JOIN leads l ON l.county=c.county AND l.parcel_key=c.parcel_key "
        "JOIN parcels p ON p.county=c.county AND p.parcel_key=c.parcel_key "
        "WHERE c.phone != '' AND l.status NOT IN ('dead','closed')" +
        ("" if include_dnc else " AND c.dnc=0 AND c.litigator=0")).fetchall()
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "role", "phone", "phone_type", "dnc", "property", "city", "zip", "status"])
        for r in rows:
            w.writerow([r["name"], r["role"], r["phone"], r["phone_type"], r["dnc"], r["site_addr"],
                        r["site_city"], r["site_zip"], r["status"]])
    return len(rows)


# ---------------------------------------------------------------- decision makers (entity owners)

def import_entities(conn: sqlite3.Connection, path: str | Path, source: str | None = None) -> int:
    """Import entity officers/managers/registered agents (e.g. from Sunbiz data downloads or your
    own research). Columns: entity name, doc number, status, person name, title, address."""
    alias = {"entity_name": ["entity name", "entity", "corporation name", "company", "cor_name", "name of entity"],
             "doc_number": ["doc number", "document number", "cor_number", "doc_number"],
             "status": ["status", "cor_status"],
             "person_name": ["person name", "officer name", "principal", "manager", "officer", "name", "ra name",
                             "registered agent"],
             "title": ["title", "officer title", "role", "position"],
             "person_addr": ["address", "officer address", "person address", "principal address"]}
    n = 0
    for r in _open_rows(path):
        low = {re.sub(r"[_\s]+", " ", k.strip().lower()): (v or "").strip() for k, v in r.items() if k}
        g = {f: next((low[a] for a in al if low.get(a)), "") for f, al in alias.items()}
        if not g["entity_name"] or not g["person_name"]:
            continue
        n += conn.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?,?,?,?,?,?)",
                          (g["doc_number"], g["entity_name"], db.entity_key(g["entity_name"]), g["status"],
                           g["person_name"].upper(), g["title"].upper(), g["person_addr"],
                           source or Path(path).name)).rowcount
    db.log_import(conn, str(path), "entities", n)
    return n


def decision_makers(conn: sqlite3.Connection, entity_name: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM entities WHERE entity_key=?", (db.entity_key(entity_name),))]
    rank = lambda t: 0 if re.search(r"\b(MGR|MANAGER|MGRM|AMBR|MBR|P|PRES|CEO|OWNER)\b", t or "") else \
        1 if re.search(r"\b(VP|D|DIR|T|S|SEC|TREAS)\b", t or "") else 2  # noqa: E731
    return sorted(rows, key=lambda r: rank(r["title"]))


# ---------------------------------------------------------------- direct provider API

def _dig(obj, path: str):
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)] if part.isdigit() and int(part) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        if obj is None:
            return None
    return obj


def _fill(tmpl, values: dict):
    if isinstance(tmpl, dict):
        return {k: _fill(v, values) for k, v in tmpl.items()}
    if isinstance(tmpl, list):
        return [_fill(v, values) for v in tmpl]
    if isinstance(tmpl, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "") or ""), tmpl)
    return tmpl


def trace_via_api(conn: sqlite3.Connection, p: dict) -> int:
    """Skip trace one parcel through the provider configured in config/sources.json -> "skiptrace"."""
    cfg = load_sources().get("skiptrace") or {}
    if not cfg.get("enabled"):
        raise RuntimeError("No skip-trace API configured. Use the CSV export/import, or fill in "
                           "'skiptrace' in config/sources.json.")
    key = os.environ.get(cfg.get("api_key_env", "SKIPTRACE_API_KEY"), "")
    if not key:
        raise RuntimeError(f"Set the {cfg.get('api_key_env', 'SKIPTRACE_API_KEY')} environment variable.")
    headers = {"Content-Type": "application/json",
               cfg.get("auth_header", "Authorization"): cfg.get("auth_prefix", "Bearer ") + key}
    n = 0
    resp_map = cfg["response"]
    for t in targets_for(conn, p):
        vals = {"first_name": t.get("first"), "last_name": t.get("last"), "address": p.get("site_addr"),
                "city": p.get("site_city"), "state": "FL", "zip": p.get("site_zip"),
                "mailing_address": p.get("owner_addr1"), "mailing_city": p.get("owner_city"),
                "mailing_state": p.get("owner_state"), "mailing_zip": p.get("owner_zip")}
        r = requests.request(cfg.get("method", "POST"), cfg["url"], headers=headers,
                             json=_fill(cfg["request_template"], vals), timeout=60)
        r.raise_for_status()
        data = r.json()
        name = f"{t.get('first', '')} {t.get('last', '')}".strip()
        for ph in _dig(data, resp_map["phones"]) or []:
            num = _phone(_dig(ph, resp_map.get("phone_number", "number")) if isinstance(ph, dict) else ph)
            if not num:
                continue
            pt = (_dig(ph, resp_map.get("phone_type", "type")) or "") if isinstance(ph, dict) else ""
            dnc = _truthy(_dig(ph, resp_map.get("phone_dnc", "dnc"))) if isinstance(ph, dict) else False
            n += conn.execute("INSERT OR IGNORE INTO contacts (county, parcel_key, name, role, phone, phone_type, "
                              "dnc, email, source) VALUES (?,?,?,?,?,?,?,?,?)",
                              (p["county"], p["parcel_key"], name, t["role"], num, str(pt).lower(), int(dnc), "",
                               "api:" + cfg.get("name", "skiptrace"))).rowcount
        for em in _dig(data, resp_map.get("emails", "emails")) or []:
            em = _dig(em, resp_map.get("email_address", "email")) if isinstance(em, dict) else em
            if em and "@" in str(em):
                n += conn.execute("INSERT OR IGNORE INTO contacts (county, parcel_key, name, role, phone, email, "
                                  "source) VALUES (?,?,?,?,?,?,?)", (p["county"], p["parcel_key"], name, t["role"],
                                                                     "", str(em).lower(), "api")).rowcount
    conn.commit()
    return n

"""Importers.

1. Florida DOR NAL files - the official statewide parcel roll (all 67 counties: owner, mailing
   address, site address, values, homestead, sales). Free from the Florida Department of Revenue
   Property Tax Data Portal.
2. Record-event CSVs - anything you export from a clerk, tax collector, auction site or code
   enforcement portal (lis pendens, liens, probate, violations, ...). Columns are auto-detected.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterable, Iterator

from . import db
from .counties import normalize_county

csv.field_size_limit(10_000_000)


def _num(v) -> float | None:
    try:
        s = str(v).replace(",", "").replace("$", "").strip()
        return float(s) if s else None
    except ValueError:
        return None


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def _open_rows(path: str | Path) -> Iterator[dict]:
    """Yield dict rows from a .csv / .txt or a .zip containing one."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".csv", ".txt")):
                    with zf.open(name) as fh:
                        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline=""))
        return
    with open(path, encoding="latin-1", newline="") as fh:
        yield from csv.DictReader(fh)


# ---------------------------------------------------------------- NAL (statewide parcel roll)

def nal_row_to_parcel(r: dict, county_override: str | None = None) -> dict | None:
    g = lambda k: (r.get(k) or "").strip()  # noqa: E731
    county = county_override or normalize_county(g("CO_NO"))
    if not county or not g("PARCEL_ID"):
        return None
    hmstd = (_num(g("JV_HMSTD")) or 0) > 0 or (_num(g("EXMPT_01")) or 0) > 0
    return {
        "county": county,
        "parcel_id": g("PARCEL_ID"),
        "owner_name": g("OWN_NAME"),
        "owner_addr1": g("OWN_ADDR1"), "owner_addr2": g("OWN_ADDR2"),
        "owner_city": g("OWN_CITY"), "owner_state": g("OWN_STATE") or g("OWN_STATE_DOM"),
        "owner_zip": g("OWN_ZIPCD"),
        "site_addr": " ".join(x for x in (g("PHY_ADDR1"), g("PHY_ADDR2")) if x),
        "site_city": g("PHY_CITY"), "site_zip": g("PHY_ZIPCD"),
        "use_code": g("DOR_UC"),
        "just_value": _num(g("JV")), "land_value": _num(g("LND_VAL")),
        "homestead": 1 if hmstd else 0,
        "year_built": _int(g("ACT_YR_BLT")) or _int(g("EFF_YR_BLT")),
        "living_sqft": _int(g("TOT_LVG_AREA")), "units": _int(g("NO_RES_UNTS")),
        "last_sale_price": _num(g("SALE_PRC1")), "last_sale_year": _int(g("SALE_YR1")),
        "last_sale_month": _int(g("SALE_MO1")),
        "prev_sale_price": _num(g("SALE_PRC2")), "prev_sale_year": _int(g("SALE_YR2")),
        "legal": g("S_LEGAL"), "source": "FL DOR NAL",
    }


def import_nal(conn: sqlite3.Connection, path: str | Path, county: str | None = None,
               batch: int = 5000, progress=None) -> int:
    county = normalize_county(county) if county else None
    buf: list[dict] = []
    total = 0
    for r in _open_rows(path):
        p = nal_row_to_parcel(r, county)
        if p:
            buf.append(p)
        if len(buf) >= batch:
            total += db.upsert_parcels(conn, buf)
            conn.commit()
            buf.clear()
            if progress:
                progress(total)
    total += db.upsert_parcels(conn, buf)
    db.log_import(conn, str(path), "nal", total)
    return total


# ---------------------------------------------------------------- generic record events

# Header aliases seen in clerk / auction / code-enforcement exports.
EVENT_ALIASES = {
    "parcel_id": ["parcel_id", "parcel", "parcel number", "parcel no", "folio", "folio number", "pin",
                  "strap", "account", "account number", "property id", "pid", "apn", "parcelid"],
    "site_addr": ["site_addr", "address", "property address", "site address", "situs", "situs address",
                  "location", "street address", "prop address"],
    "event_date": ["event_date", "date", "filing date", "file date", "recorded date", "record date",
                   "sale date", "auction date", "case date", "open date", "violation date", "date filed"],
    "case_number": ["case_number", "case", "case number", "case no", "case #", "instrument",
                    "instrument number", "cfn", "book page", "certificate", "certificate number",
                    "violation number", "record id"],
    "amount": ["amount", "judgment amount", "final judgment", "opening bid", "lien amount",
               "amount due", "face amount", "balance"],
    "party": ["party", "plaintiff", "grantor", "defendant", "grantee", "owner", "decedent", "name"],
    "description": ["description", "doc type", "document type", "case type", "violation", "status",
                    "violation description", "type"],
    "url": ["url", "link"],
    "county": ["county"],
}


def _map_headers(headers: Iterable[str]) -> dict[str, str]:
    norm = {re.sub(r"[_\s]+", " ", h.strip().lower()): h for h in headers if h}
    out: dict[str, str] = {}
    for field, aliases in EVENT_ALIASES.items():
        for a in aliases:
            a = a.replace("_", " ")
            if a in norm:
                out[field] = norm[a]
                break
    return out


def import_events(conn: sqlite3.Connection, path: str | Path, event_type: str, county: str | None = None,
                  source: str | None = None) -> int:
    if event_type not in db.EVENT_TYPES:
        raise ValueError(f"event_type must be one of: {', '.join(db.EVENT_TYPES)}")
    county = normalize_county(county) if county else None
    rows = list(_open_rows(path))
    if not rows:
        return 0
    m = _map_headers(rows[0].keys())
    if "parcel_id" not in m and "site_addr" not in m:
        raise ValueError(f"{path}: need a parcel/folio column or an address column; found {list(rows[0])}")
    events = []
    for r in rows:
        g = lambda f: (r.get(m[f]) or "").strip() if f in m else ""  # noqa: E731
        events.append({
            "county": normalize_county(g("county")) or county,
            "parcel_id": g("parcel_id"), "site_addr": g("site_addr"),
            "event_type": event_type, "event_date": g("event_date"), "case_number": g("case_number"),
            "amount": _num(g("amount")), "party": g("party"), "description": g("description"),
            "url": g("url"), "source": source or Path(path).name,
        })
    n = db.add_events(conn, events)
    db.log_import(conn, str(path), event_type, n)
    return n

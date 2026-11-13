"""SQLite storage: parcels (from FL DOR NAL / county GIS) and record events (liens, lis pendens, ...)."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

DEFAULT_DB = os.environ.get("FLPROPS_DB", str(Path(__file__).resolve().parent.parent / "data" / "flprops.db"))

# Record types that the distress engine understands.
EVENT_TYPES = {
    "lis_pendens": "Lis pendens (pre-foreclosure) filed",
    "foreclosure": "Foreclosure case / judgment",
    "foreclosure_sale": "Scheduled foreclosure auction",
    "tax_delinquent": "Delinquent property taxes / tax certificate",
    "tax_deed": "Tax deed application or sale",
    "lien": "Lien (code, municipal, mechanic's, HOA, IRS)",
    "code_violation": "Code enforcement violation",
    "probate": "Probate case",
    "death": "Owner death / obituary",
    "bankruptcy": "Bankruptcy filing",
    "eviction": "Eviction filing",
    "divorce": "Divorce filing",
    "utility_shutoff": "Water / utility shutoff",
    "vacant": "Vacant / unsafe structure",
    "other": "Other",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
    county TEXT NOT NULL,
    parcel_id TEXT NOT NULL,
    parcel_key TEXT NOT NULL,
    owner_name TEXT, owner_addr1 TEXT, owner_addr2 TEXT, owner_city TEXT, owner_state TEXT, owner_zip TEXT,
    site_addr TEXT, site_city TEXT, site_zip TEXT, site_key TEXT,
    use_code TEXT, just_value REAL, land_value REAL, homestead INTEGER,
    year_built INTEGER, living_sqft INTEGER, units INTEGER,
    last_sale_price REAL, last_sale_year INTEGER, last_sale_month INTEGER,
    prev_sale_price REAL, prev_sale_year INTEGER,
    legal TEXT, source TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (county, parcel_key)
);
CREATE INDEX IF NOT EXISTS ix_parcels_owner ON parcels(owner_name);
CREATE INDEX IF NOT EXISTS ix_parcels_site ON parcels(site_key);
CREATE INDEX IF NOT EXISTS ix_parcels_zip ON parcels(site_zip);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    county TEXT, parcel_key TEXT, site_key TEXT,
    event_type TEXT NOT NULL, event_date TEXT, case_number TEXT, amount REAL,
    party TEXT, description TEXT, source TEXT, url TEXT,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (county, event_type, case_number, parcel_key, site_key)
);
CREATE INDEX IF NOT EXISTS ix_events_parcel ON events(county, parcel_key);
CREATE INDEX IF NOT EXISTS ix_events_site ON events(site_key);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, kind TEXT, rows INTEGER, ran_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_ABBREV = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "COURT": "CT",
    "LANE": "LN", "PLACE": "PL", "TERRACE": "TER", "CIRCLE": "CIR", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW", "TRAIL": "TRL", "WAY": "WAY", "APARTMENT": "APT", "UNIT": "UNIT",
}


def parcel_key(parcel_id: str | None) -> str:
    """Folio / parcel numbers are written with and without dashes, dots and spaces."""
    return re.sub(r"[^0-9A-Z]", "", (parcel_id or "").upper())


def address_key(addr: str | None) -> str:
    """Normalize a street address for matching ('123 North Main Street' == '123 N MAIN ST')."""
    words = re.sub(r"[^0-9A-Z ]", " ", (addr or "").upper()).split()
    words = [_ABBREV.get(w, w) for w in words]
    # drop unit designators and anything after them
    for i, w in enumerate(words):
        if w in ("APT", "UNIT", "STE") or w.startswith("#"):
            words = words[:i]
            break
    return " ".join(words)


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or DEFAULT_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
    conn.executescript(SCHEMA)
    return conn


PARCEL_COLS = [
    "county", "parcel_id", "parcel_key", "owner_name", "owner_addr1", "owner_addr2", "owner_city",
    "owner_state", "owner_zip", "site_addr", "site_city", "site_zip", "site_key", "use_code",
    "just_value", "land_value", "homestead", "year_built", "living_sqft", "units", "last_sale_price",
    "last_sale_year", "last_sale_month", "prev_sale_price", "prev_sale_year", "legal", "source",
]


def upsert_parcels(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    for r in rows:
        r["parcel_key"] = parcel_key(r.get("parcel_id"))
        r["site_key"] = address_key(r.get("site_addr"))
    placeholders = ",".join("?" for _ in PARCEL_COLS)
    updates = ",".join(f"{c}=excluded.{c}" for c in PARCEL_COLS if c not in ("county", "parcel_key"))
    conn.executemany(
        f"INSERT INTO parcels ({','.join(PARCEL_COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(county, parcel_key) DO UPDATE SET {updates}, updated_at=CURRENT_TIMESTAMP",
        [tuple(r.get(c) for c in PARCEL_COLS) for r in rows],
    )
    return len(rows)


EVENT_COLS = ["county", "parcel_key", "site_key", "event_type", "event_date", "case_number", "amount",
              "party", "description", "source", "url"]


def add_events(conn: sqlite3.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        r = dict(r)
        r["parcel_key"] = parcel_key(r.get("parcel_id") or r.get("parcel_key"))
        r["site_key"] = address_key(r.get("site_addr") or r.get("site_key"))
        if r.get("event_type") not in EVENT_TYPES:
            r["event_type"] = "other"
        r["case_number"] = r.get("case_number") or ""
        cur = conn.execute(
            f"INSERT OR IGNORE INTO events ({','.join(EVENT_COLS)}) VALUES ({','.join('?' for _ in EVENT_COLS)})",
            tuple(r.get(c) for c in EVENT_COLS),
        )
        n += cur.rowcount
    return n


def log_import(conn: sqlite3.Connection, source: str, kind: str, rows: int) -> None:
    conn.execute("INSERT INTO imports (source, kind, rows) VALUES (?,?,?)", (source, kind, rows))
    conn.commit()

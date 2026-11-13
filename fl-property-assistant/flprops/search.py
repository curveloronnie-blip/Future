"""Query layer used by the CLI, web UI and AI assistant."""
from __future__ import annotations

import sqlite3

from . import distress
from .counties import normalize_county
from .db import address_key, parcel_key
from .links import property_links


def _events_for(conn: sqlite3.Connection, p: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM events WHERE (county IS ? OR county IS NULL) AND "
        "((parcel_key != '' AND parcel_key = ?) OR (site_key != '' AND site_key = ?)) "
        "ORDER BY event_date DESC",
        (p["county"], p["parcel_key"], p["site_key"] or "\x00"),
    ).fetchall()
    return [dict(r) for r in rows]


def search(conn: sqlite3.Connection, query: str = "", county: str | None = None, zip_code: str | None = None,
           limit: int = 25) -> list[dict]:
    """Find parcels by address, owner name or parcel/folio number."""
    where, args = [], []
    c = normalize_county(county) if county else None
    if c:
        where.append("county = ?"); args.append(c)
    if zip_code:
        where.append("site_zip LIKE ?"); args.append(zip_code[:5] + "%")
    q = (query or "").strip()
    if q:
        pk = parcel_key(q)
        ak = address_key(q)
        where.append("(parcel_key = ? OR site_key LIKE ? OR owner_name LIKE ?)")
        args += [pk, ak + "%", "%" + q.upper() + "%"]
    sql = "SELECT * FROM parcels" + (" WHERE " + " AND ".join(where) if where else "") + " LIMIT ?"
    rows = [dict(r) for r in conn.execute(sql, args + [limit]).fetchall()]
    for r in rows:
        ev = _events_for(conn, r)
        r["distress"] = distress.score(r, ev)
        r["event_count"] = len(ev)
    return rows


def get_property(conn: sqlite3.Connection, parcel_id: str, county: str | None = None) -> dict | None:
    args = [parcel_key(parcel_id)]
    sql = "SELECT * FROM parcels WHERE parcel_key = ?"
    c = normalize_county(county) if county else None
    if c:
        sql += " AND county = ?"; args.append(c)
    row = conn.execute(sql, args).fetchone()
    if not row:
        return None
    p = dict(row)
    p["events"] = _events_for(conn, p)
    p["distress"] = distress.score(p, p["events"])
    p["links"] = property_links(p)
    return p


def distressed(conn: sqlite3.Connection, county: str | None = None, zip_code: str | None = None,
               min_score: int = 25, require: list[str] | None = None, limit: int = 100,
               scan_limit: int = 200_000) -> list[dict]:
    """Rank properties by distress score.

    Properties with recorded events are always considered; owner-only signals (estate names,
    out-of-state, long ownership) are scanned across up to scan_limit parcels.
    """
    c = normalize_county(county) if county else None
    where, args = [], []
    if c:
        where.append("p.county = ?"); args.append(c)
    if zip_code:
        where.append("p.site_zip LIKE ?"); args.append(zip_code[:5] + "%")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(f"SELECT p.* FROM parcels p{w} LIMIT ?", args + [scan_limit]).fetchall()

    # group events once instead of per-parcel queries
    ev_by_parcel: dict[tuple, list] = {}
    ev_by_site: dict[str, list] = {}
    for e in conn.execute("SELECT * FROM events"):
        e = dict(e)
        if e["parcel_key"]:
            ev_by_parcel.setdefault((e["county"], e["parcel_key"]), []).append(e)
        elif e["site_key"]:
            ev_by_site.setdefault(e["site_key"], []).append(e)

    out = []
    req = set(require or [])
    for r in rows:
        p = dict(r)
        ev = ev_by_parcel.get((p["county"], p["parcel_key"]), []) + ev_by_parcel.get((None, p["parcel_key"]), [])
        if p["site_key"]:
            ev += [e for e in ev_by_site.get(p["site_key"], []) if e["county"] in (None, p["county"])]
        if req and not req.issubset({e["event_type"] for e in ev}):
            continue
        d = distress.score(p, ev)
        if d["score"] >= min_score:
            p["distress"] = d
            p["event_types"] = sorted({e["event_type"] for e in ev})
            out.append(p)
    out.sort(key=lambda x: x["distress"]["score"], reverse=True)
    return out[:limit]


def unmatched_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """Events whose parcel/address isn't in the parcel table (import that county's NAL to match them)."""
    rows = conn.execute(
        "SELECT e.* FROM events e WHERE NOT EXISTS (SELECT 1 FROM parcels p WHERE "
        "(e.parcel_key != '' AND p.parcel_key = e.parcel_key) OR (e.site_key != '' AND p.site_key = e.site_key)) "
        "LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    return {
        "parcels_by_county": {r[0]: r[1] for r in conn.execute(
            "SELECT county, COUNT(*) FROM parcels GROUP BY county ORDER BY 2 DESC")},
        "events_by_type": {r[0]: r[1] for r in conn.execute(
            "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC")},
        "recent_imports": [dict(r) for r in conn.execute(
            "SELECT source, kind, rows, ran_at FROM imports ORDER BY id DESC LIMIT 10")],
    }

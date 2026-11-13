"""Lead pipeline (a light CRM): statuses, notes, follow-ups, numbers per deal."""
from __future__ import annotations

import sqlite3

STATUSES = ["new", "skip_traced", "contacted", "appointment", "offer_made", "under_contract",
            "assigned", "closed", "dead"]
STRATEGIES = ["wholesale", "flip", "brrrr", "wholetail", "creative", "undecided"]


def add_leads(conn: sqlite3.Connection, parcels: list[dict], list_name: str | None = None) -> int:
    n = 0
    for p in parcels:
        n += conn.execute("INSERT OR IGNORE INTO leads (county, parcel_key, list_name) VALUES (?,?,?)",
                          (p["county"], p["parcel_key"], list_name)).rowcount
    conn.commit()
    return n


def update_lead(conn: sqlite3.Connection, county: str, parcel_key: str, **fields) -> None:
    allowed = {"status", "strategy", "arv", "repairs", "rent", "offer", "notes", "next_follow_up", "list_name"}
    fields = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if not fields:
        return
    conn.execute("INSERT OR IGNORE INTO leads (county, parcel_key) VALUES (?,?)", (county, parcel_key))
    conn.execute(f"UPDATE leads SET {', '.join(f'{k}=?' for k in fields)}, updated_at=CURRENT_TIMESTAMP "
                 "WHERE county=? AND parcel_key=?", [*fields.values(), county, parcel_key])
    if "status" in fields:
        log(conn, county, parcel_key, "status", fields["status"])
    conn.commit()


def log(conn: sqlite3.Connection, county: str, parcel_key: str, kind: str, note: str) -> None:
    conn.execute("INSERT INTO activity (county, parcel_key, kind, note) VALUES (?,?,?,?)",
                 (county, parcel_key, kind, note))
    conn.commit()


def get_lead(conn: sqlite3.Connection, county: str, parcel_key: str) -> dict | None:
    r = conn.execute("SELECT * FROM leads WHERE county=? AND parcel_key=?", (county, parcel_key)).fetchone()
    if not r:
        return None
    lead = dict(r)
    lead["activity"] = [dict(a) for a in conn.execute(
        "SELECT kind, note, at FROM activity WHERE county=? AND parcel_key=? ORDER BY id DESC", (county, parcel_key))]
    return lead


def board(conn: sqlite3.Connection, status: str | None = None, due_only: bool = False) -> list[dict]:
    sql = ("SELECT l.*, p.parcel_id, p.site_addr, p.site_city, p.site_zip, p.owner_name, "
           "(SELECT COUNT(*) FROM contacts c WHERE c.county=l.county AND c.parcel_key=l.parcel_key AND c.phone!='' "
           " AND c.dnc=0 AND c.litigator=0) AS callable_phones "
           "FROM leads l JOIN parcels p ON p.county=l.county AND p.parcel_key=l.parcel_key")
    where, args = [], []
    if status:
        where.append("l.status=?"); args.append(status)
    if due_only:
        where.append("l.next_follow_up IS NOT NULL AND l.next_follow_up <= date('now')")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(l.next_follow_up, '9999'), l.updated_at DESC"
    return [dict(r) for r in conn.execute(sql, args)]

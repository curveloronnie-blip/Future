"""Cash-buyer / investor list for dispositions (who to sell your contract or property to).

discover() mines the parcel roll for owners who bought several non-homestead properties in the
last 24 months: active investors (flippers, landlords, funds). The roll doesn't say whether a sale
was cash, so treat these as investor buyers and confirm. Add your own buyers with import_buyers()
or the web form, then match() a deal to buyers by ZIP / county and price.
"""
from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from pathlib import Path

from . import db
from .importers import _num, _open_rows

BANKS = ("BANK", "MORTGAGE", "FEDERAL NATIONAL", "FEDERAL HOME LOAN", "SECRETARY OF HOUSING", "HUD",
         "COUNTY", "CITY OF", "STATE OF", "SCHOOL BOARD", "HOUSING AUTHORITY", "CHURCH")


def discover(conn: sqlite3.Connection, county: str | None = None, min_purchases: int = 2,
             months: int = 24, min_price: float = 20000) -> int:
    since = dt.date.today().year - (months // 12)
    args: list = [since, min_price]
    sql = ("SELECT county, owner_name, owner_addr1, owner_city, owner_state, owner_zip, site_zip, "
           "last_sale_price, last_sale_year, last_sale_month FROM parcels "
           "WHERE last_sale_year >= ? AND last_sale_price >= ? AND COALESCE(homestead,0) = 0 AND owner_name != ''")
    if county:
        sql += " AND county = ?"; args.append(county)
    groups: dict[str, list] = {}
    for r in conn.execute(sql, args):
        name = r["owner_name"]
        if any(b in name.upper() for b in BANKS):
            continue
        groups.setdefault(db.entity_key(name), []).append(dict(r))
    n = 0
    for key, buys in groups.items():
        if len(buys) < min_purchases:
            continue
        b0 = buys[0]
        prices = [b["last_sale_price"] for b in buys]
        zips = sorted({(b["site_zip"] or "")[:5] for b in buys if b["site_zip"]})
        counties = sorted({b["county"] for b in buys})
        last = max(f"{b['last_sale_year']}-{(b['last_sale_month'] or 1):02d}" for b in buys)
        mail = ", ".join(x for x in (b0["owner_addr1"], b0["owner_city"],
                                     f"{b0['owner_state'] or ''} {b0['owner_zip'] or ''}".strip()) if x)
        cur = conn.execute(
            "INSERT INTO buyers (name, buyer_key, mail_addr, zips, counties, min_price, max_price, purchases_24mo, "
            "last_purchase, source) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(buyer_key) DO UPDATE SET "
            "zips=excluded.zips, counties=excluded.counties, min_price=excluded.min_price, "
            "max_price=excluded.max_price, purchases_24mo=excluded.purchases_24mo, "
            "last_purchase=excluded.last_purchase, mail_addr=COALESCE(buyers.mail_addr, excluded.mail_addr)",
            (b0["owner_name"], key, mail, ",".join(zips), ",".join(counties), min(prices), max(prices), len(buys),
             last, "parcel roll"))
        n += cur.rowcount
    conn.commit()
    return n


def import_buyers(conn: sqlite3.Connection, path: str | Path) -> int:
    """CSV columns (any order): name, phone, email, mailing address, zips, counties, min price, max price,
    strategies, notes."""
    n = 0
    for r in _open_rows(path):
        low = {k.strip().lower().replace("_", " "): (v or "").strip() for k, v in r.items() if k}
        name = low.get("name") or low.get("buyer") or low.get("company")
        if not name:
            continue
        n += conn.execute(
            "INSERT INTO buyers (name, buyer_key, phone, email, mail_addr, zips, counties, min_price, max_price, "
            "strategies, notes, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(buyer_key) DO UPDATE SET "
            "phone=COALESCE(NULLIF(excluded.phone,''), buyers.phone), email=COALESCE(NULLIF(excluded.email,''), buyers.email), "
            "zips=COALESCE(NULLIF(excluded.zips,''), buyers.zips), strategies=excluded.strategies, notes=excluded.notes, "
            "min_price=COALESCE(excluded.min_price, buyers.min_price), max_price=COALESCE(excluded.max_price, buyers.max_price)",
            (name, db.entity_key(name), low.get("phone", ""), low.get("email", ""),
             low.get("mailing address") or low.get("address", ""), low.get("zips") or low.get("zip", ""),
             low.get("counties") or low.get("county", ""), _num(low.get("min price")), _num(low.get("max price")),
             low.get("strategies") or low.get("strategy", ""), low.get("notes", ""), Path(path).name)).rowcount
    conn.commit()
    return n


def list_buyers(conn: sqlite3.Connection, zip_code: str | None = None, county: str | None = None,
                limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM buyers", []
    where = []
    if zip_code:
        where.append("(',' || zips || ',') LIKE ?"); args.append(f"%,{zip_code[:5]},%")
    if county:
        where.append("(',' || counties || ',') LIKE ?"); args.append(f"%,{county},%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(purchases_24mo,0) DESC, name LIMIT ?"
    return [dict(r) for r in conn.execute(sql, args + [limit])]


def match(conn: sqlite3.Connection, p: dict, price: float | None = None, limit: int = 50) -> list[dict]:
    """Buyers for a deal: same ZIP first, then same county; within their price range when known."""
    z = (p.get("site_zip") or "")[:5]
    out = []
    for b in conn.execute("SELECT * FROM buyers"):
        b = dict(b)
        zips = {x.strip() for x in (b["zips"] or "").split(",") if x.strip()}
        counties = {x.strip() for x in (b["counties"] or "").split(",") if x.strip()}
        if z in zips:
            fit = 2
        elif p.get("county") in counties:
            fit = 1
        else:
            continue
        if price and b["max_price"] and price > b["max_price"] * 1.25:
            continue
        if price and b["min_price"] and price < b["min_price"] * 0.5:
            continue
        b["fit"] = "same ZIP" if fit == 2 else "same county"
        b["_rank"] = (fit, b["purchases_24mo"] or 0)
        out.append(b)
    out.sort(key=lambda b: b.pop("_rank"), reverse=True)
    return out[:limit]


def export_buyers(rows: list[dict], path: str | Path) -> int:
    cols = ["name", "phone", "email", "mail_addr", "zips", "counties", "min_price", "max_price",
            "purchases_24mo", "last_purchase", "strategies", "notes"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)

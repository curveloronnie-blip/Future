"""Live data from official open-data APIs (ArcGIS REST feature services and Socrata).

This client is deliberately polite: it identifies itself, honours robots.txt, rate-limits
itself and backs off on 429. If a site answers with a CAPTCHA, a bot challenge or a login
wall, it stops and raises ManualStepRequired with the URL for you to open in a browser.
It never tries to defeat those controls.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import db
from .counties import normalize_county

USER_AGENT = os.environ.get("FLPROPS_USER_AGENT", "FL-Property-Research/0.1 (public-records research tool)")
MIN_INTERVAL = float(os.environ.get("FLPROPS_MIN_INTERVAL", "1.0"))  # seconds between requests per host
CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.json"

_BLOCK_MARKERS = ("captcha", "cf-challenge", "challenge-platform", "are you a robot",
                  "access denied", "request unsuccessful. incapsula", "verify you are human")


class ManualStepRequired(RuntimeError):
    """The site requires a human (CAPTCHA, login, disclaimer click, or it refused automated access)."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{reason}. Open it yourself: {url}")
        self.url = url
        self.reason = reason


class PoliteSession:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=15)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        rp = self._robots[base]
        return rp.can_fetch(USER_AGENT, url) if rp else True

    def get(self, url: str, params: dict | None = None, retries: int = 4) -> requests.Response:
        if not self._allowed(url):
            raise ManualStepRequired(url, "robots.txt disallows automated access to this path")
        host = urlparse(url).netloc
        for attempt in range(retries):
            wait = MIN_INTERVAL - (time.monotonic() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            r = self.s.get(url, params=params, timeout=60)
            if r.status_code in (429, 503):
                time.sleep(float(r.headers.get("Retry-After", 2 ** (attempt + 1))))
                continue
            if r.status_code in (401, 403):
                raise ManualStepRequired(r.url, f"site refused automated access (HTTP {r.status_code})")
            head = r.text[:4000].lower() if "html" in r.headers.get("Content-Type", "") else ""
            if any(m in head for m in _BLOCK_MARKERS):
                raise ManualStepRequired(r.url, "site shows a CAPTCHA / bot check")
            r.raise_for_status()
            return r
        raise ManualStepRequired(url, "site keeps rate-limiting; try later")


SESSION = PoliteSession()


# ---------------------------------------------------------------- ArcGIS REST

def arcgis_query(layer_url: str, where: str = "1=1", out_fields: str = "*", page_size: int = 1000,
                 max_records: int | None = None, session: PoliteSession | None = None):
    """Yield attribute dicts from an ArcGIS FeatureServer/MapServer layer, paging through results."""
    session = session or SESSION
    url = layer_url.rstrip("/") + "/query"
    offset, got = 0, 0
    while True:
        params = {"where": where, "outFields": out_fields, "returnGeometry": "false", "f": "json",
                  "resultOffset": offset, "resultRecordCount": page_size}
        data = session.get(url, params).json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error from {layer_url}: {data['error']}")
        feats = data.get("features", [])
        for f in feats:
            yield f.get("attributes", {})
            got += 1
            if max_records and got >= max_records:
                return
        if not feats or not data.get("exceededTransferLimit"):
            return
        offset += len(feats)


# ---------------------------------------------------------------- Socrata (SODA)

def socrata_query(domain: str, dataset_id: str, where: str | None = None, page_size: int = 5000,
                  max_records: int | None = None, session: PoliteSession | None = None):
    session = session or SESSION
    url = f"https://{domain}/resource/{dataset_id}.json"
    offset, got = 0, 0
    while True:
        params = {"$limit": page_size, "$offset": offset, "$order": ":id"}
        if where:
            params["$where"] = where
        rows = session.get(url, params).json()
        for r in rows:
            yield r
            got += 1
            if max_records and got >= max_records:
                return
        if len(rows) < page_size:
            return
        offset += page_size


# ---------------------------------------------------------------- configured sources

def load_sources(path: Path = CONFIG) -> dict:
    if not path.exists():
        return {"sources": [], "county_overrides": {}}
    return json.loads(path.read_text())


def _apply_map(rec: dict, field_map: dict) -> dict:
    out = {}
    for ours, theirs in field_map.items():
        if isinstance(theirs, list):  # join several source fields
            out[ours] = " ".join(str(rec.get(t) or "").strip() for t in theirs).strip()
        else:
            out[ours] = rec.get(theirs)
    return out


def refresh_source(conn: sqlite3.Connection, src: dict, max_records: int | None = None) -> int:
    """Pull one configured source into the database. Returns rows written."""
    kind = src["kind"]
    if kind == "arcgis":
        it = arcgis_query(src["url"], src.get("where", "1=1"), max_records=max_records)
    elif kind == "socrata":
        it = socrata_query(src["domain"], src["dataset"], src.get("where"), max_records=max_records)
    else:
        raise ValueError(f"unknown source kind {kind!r}")
    county = normalize_county(src.get("county"))
    fmap = src["field_map"]
    batch, total = [], 0
    for rec in it:
        row = _apply_map(rec, fmap)
        row.setdefault("county", county)
        row["county"] = normalize_county(row.get("county")) or county
        row["source"] = src["name"]
        batch.append(row)
        if len(batch) >= 2000:
            total += _write(conn, src, batch)
            batch = []
    total += _write(conn, src, batch)
    db.log_import(conn, src["name"], src.get("target", "events"), total)
    return total


def _write(conn, src, rows) -> int:
    if not rows:
        return 0
    if src.get("target") == "parcels":
        n = db.upsert_parcels(conn, [r for r in rows if r.get("parcel_id")])
    else:
        for r in rows:
            r["event_type"] = src.get("event_type", "other")
            if r.get("event_date") and isinstance(r["event_date"], (int, float)):  # ArcGIS epoch ms
                r["event_date"] = time.strftime("%Y-%m-%d", time.gmtime(r["event_date"] / 1000))
        n = db.add_events(conn, rows)
    conn.commit()
    return n


def refresh_all(conn: sqlite3.Connection, only: str | None = None, max_records: int | None = None) -> list[dict]:
    results = []
    for src in load_sources()["sources"]:
        if not src.get("enabled", False) or (only and src["name"] != only):
            continue
        try:
            n = refresh_source(conn, src, max_records)
            results.append({"source": src["name"], "rows": n, "ok": True})
        except ManualStepRequired as e:
            results.append({"source": src["name"], "ok": False, "manual": True, "url": e.url, "error": e.reason})
        except Exception as e:  # keep going with other sources
            results.append({"source": src["name"], "ok": False, "error": str(e)})
    return results

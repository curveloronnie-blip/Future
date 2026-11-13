"""Command line: python -m flprops <command> ..."""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import db, fetch, importers, links, search
from .counties import COUNTIES


def _print_table(rows: list[dict]) -> None:
    for p in rows:
        d = p["distress"]
        print(f"[{d['score']:>3} {d['tier']:<6}] {p['county']:<12} {p['parcel_id']:<22} "
              f"{(p.get('site_addr') or '')[:34]:<34} {p.get('site_zip') or '':<6} {(p.get('owner_name') or '')[:36]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flprops", description="Florida property public-records assistant")
    ap.add_argument("--db", default=None, help="SQLite path (default data/flprops.db or $FLPROPS_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-nal", help="Import a FL DOR NAL parcel file (.csv or .zip)")
    s.add_argument("path"); s.add_argument("--county")

    s = sub.add_parser("import-events", help="Import a CSV of records (lis pendens, liens, probate, ...)")
    s.add_argument("path"); s.add_argument("--type", required=True, choices=list(db.EVENT_TYPES))
    s.add_argument("--county"); s.add_argument("--source")

    s = sub.add_parser("refresh", help="Pull enabled live sources from config/sources.json")
    s.add_argument("--source"); s.add_argument("--max", type=int)

    s = sub.add_parser("search", help="Search by address, owner or parcel number")
    s.add_argument("query"); s.add_argument("--county"); s.add_argument("--zip"); s.add_argument("--limit", type=int, default=25)

    s = sub.add_parser("show", help="Full property record with events, distress breakdown, links")
    s.add_argument("parcel_id"); s.add_argument("--county")

    s = sub.add_parser("distressed", help="Ranked distressed-property list")
    s.add_argument("--county"); s.add_argument("--zip"); s.add_argument("--min-score", type=int, default=25)
    s.add_argument("--require", nargs="*", default=[], choices=list(db.EVENT_TYPES))
    s.add_argument("--limit", type=int, default=100); s.add_argument("--csv", help="write results to CSV")

    s = sub.add_parser("links", help="Official portal links for a county")
    s.add_argument("county", nargs="?")

    sub.add_parser("counties", help="List the 67 counties and DOR numbers")
    sub.add_parser("status", help="Database contents")
    sub.add_parser("unmatched", help="Imported events that don't match a loaded parcel")
    sub.add_parser("chat", help="Ask questions in plain English (needs ANTHROPIC_API_KEY)")
    s = sub.add_parser("web", help="Run the local web app"); s.add_argument("--port", type=int, default=8765)

    a = ap.parse_args(argv)
    conn = db.connect(a.db)

    if a.cmd == "import-nal":
        n = importers.import_nal(conn, a.path, a.county, progress=lambda n: print(f"  {n:,} rows...", file=sys.stderr))
        print(f"Imported {n:,} parcels.")
    elif a.cmd == "import-events":
        print(f"Added {importers.import_events(conn, a.path, a.type, a.county, a.source):,} new {a.type} records.")
    elif a.cmd == "refresh":
        for r in fetch.refresh_all(conn, a.source, a.max):
            print(json.dumps(r))
        if not any(s.get("enabled") for s in fetch.load_sources()["sources"]):
            print("No sources enabled. Edit config/sources.json.")
    elif a.cmd == "search":
        _print_table(search.search(conn, a.query, a.county, a.zip, a.limit))
    elif a.cmd == "show":
        p = search.get_property(conn, a.parcel_id, a.county)
        if not p:
            print("Not found in local database."); return 1
        print(json.dumps(p, indent=2, default=str))
    elif a.cmd == "distressed":
        rows = search.distressed(conn, a.county, a.zip, a.min_score, a.require, a.limit)
        _print_table(rows)
        if a.csv:
            cols = ["county", "parcel_id", "site_addr", "site_city", "site_zip", "owner_name", "owner_addr1",
                    "owner_city", "owner_state", "owner_zip", "just_value", "homestead", "last_sale_year",
                    "last_sale_price", "year_built"]
            with open(a.csv, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols + ["distress_score", "event_types", "reasons"])
                for p in rows:
                    w.writerow([p.get(c) for c in cols] + [p["distress"]["score"], ";".join(p["event_types"]),
                               "; ".join(s["reason"] for s in p["distress"]["signals"])])
            print(f"Wrote {len(rows)} rows to {a.csv}")
    elif a.cmd == "links":
        if a.county:
            for k, v in links.county_links(a.county).items():
                print(f"{k:<52} {v}")
        else:
            for k, v in links.STATEWIDE.items():
                print(f"{k:<72} {v}")
    elif a.cmd == "counties":
        for no, name in COUNTIES:
            print(no, name)
    elif a.cmd == "status":
        print(json.dumps(search.stats(conn), indent=2))
    elif a.cmd == "unmatched":
        for e in search.unmatched_events(conn):
            print(e["event_type"], e["county"], e["case_number"], e["site_key"] or e["parcel_key"])
    elif a.cmd == "chat":
        from .assistant import Assistant
        bot = Assistant(conn)
        print("Ask about Florida properties (Ctrl-D to quit).")
        while True:
            try:
                q = input("\n> ").strip()
            except EOFError:
                break
            if q:
                print("\n" + bot.ask(q))
    elif a.cmd == "web":
        from .web import create_app
        create_app(a.db).run(host="127.0.0.1", port=a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

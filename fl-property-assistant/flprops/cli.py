"""Command line: python -m flprops <command> ..."""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import buyers, db, deals, mail, fetch, importers, links, pipeline, search, skiptrace
from .counties import COUNTIES


def _print_table(rows: list[dict]) -> None:
    for p in rows:
        d = p["distress"]
        print(f"[{d['score']:>3} {d['tier']:<6}] {p['county']:<12} {p['parcel_id']:<22} "
              f"{(p.get('site_addr') or '')[:34]:<34} {p.get('site_zip') or '':<6} {(p.get('owner_name') or '')[:36]}")


def _need(conn, a):
    p = search.get_property(conn, a.parcel_id, a.county)
    if not p:
        raise SystemExit("Parcel not found in local database.")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flprops", description="Florida property public-records assistant")
    ap.add_argument("--db", default=None, help="SQLite path (default data/flprops.db or $FLPROPS_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-nal", help="Import FL DOR NAL parcel file(s): a .csv/.zip or a folder of them")
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

    s = sub.add_parser("skiptrace-export", help="Write a skip-trace upload file for distressed leads / your pipeline")
    s.add_argument("out"); s.add_argument("--county"); s.add_argument("--zip"); s.add_argument("--min-score", type=int, default=40)
    s.add_argument("--pipeline", action="store_true", help="export leads in your pipeline instead")
    s.add_argument("--limit", type=int, default=500)
    s = sub.add_parser("skiptrace-import", help="Import your skip-trace provider's results file")
    s.add_argument("path")
    s = sub.add_parser("skiptrace-api", help="Skip trace one parcel through the API in config/sources.json")
    s.add_argument("parcel_id"); s.add_argument("--county")
    s = sub.add_parser("import-entities", help="Import LLC/corp officers & registered agents (decision makers)")
    s.add_argument("path")
    s = sub.add_parser("contacts", help="Phones/emails/decision makers for a parcel")
    s.add_argument("parcel_id"); s.add_argument("--county")
    s = sub.add_parser("dialer-export", help="Callable numbers for pipeline leads (DNC/litigators removed)")
    s.add_argument("out"); s.add_argument("--include-dnc", action="store_true")

    s = sub.add_parser("analyze", help="Comps, ARV, wholesale MAO, flip and BRRRR numbers for a parcel")
    s.add_argument("parcel_id"); s.add_argument("--county"); s.add_argument("--arv", type=float)
    s.add_argument("--repairs", type=float); s.add_argument("--rehab", default="medium",
                                                           choices=["light", "medium", "heavy", "gut"])
    s.add_argument("--rent", type=float); s.add_argument("--purchase", type=float)

    s = sub.add_parser("lead", help="Add/update a lead in your pipeline")
    s.add_argument("parcel_id"); s.add_argument("--county"); s.add_argument("--status", choices=pipeline.STATUSES)
    s.add_argument("--strategy", choices=pipeline.STRATEGIES); s.add_argument("--offer", type=float)
    s.add_argument("--follow-up", help="YYYY-MM-DD"); s.add_argument("--note")
    s = sub.add_parser("add-leads", help="Add the distressed list to your pipeline")
    s.add_argument("--county"); s.add_argument("--zip"); s.add_argument("--min-score", type=int, default=40)
    s.add_argument("--require", nargs="*", default=[]); s.add_argument("--limit", type=int, default=200)
    s.add_argument("--list-name")
    s = sub.add_parser("pipeline", help="Show your pipeline")
    s.add_argument("--status", choices=pipeline.STATUSES); s.add_argument("--due", action="store_true")

    s = sub.add_parser("buyers-discover", help="Find active investor buyers in the parcel roll")
    s.add_argument("--county"); s.add_argument("--min-purchases", type=int, default=2)
    s = sub.add_parser("buyers-import", help="Import your own buyers list CSV"); s.add_argument("path")
    s = sub.add_parser("buyers", help="List buyers (optionally by ZIP/county), or export with --csv")
    s.add_argument("--zip"); s.add_argument("--county"); s.add_argument("--csv")
    s = sub.add_parser("match-buyers", help="Buyers for a deal"); s.add_argument("parcel_id")
    s.add_argument("--county"); s.add_argument("--price", type=float); s.add_argument("--csv")

    s = sub.add_parser("mail-export", help="Mail-merge CSV of owner mailing addresses")
    s.add_argument("out"); s.add_argument("--pipeline", action="store_true"); s.add_argument("--county")
    s.add_argument("--zip"); s.add_argument("--min-score", type=int, default=40); s.add_argument("--limit", type=int, default=1000)
    s = sub.add_parser("letters", help="Printable letters (HTML, print to PDF)")
    s.add_argument("out"); s.add_argument("--template", default="cash_offer"); s.add_argument("--pipeline", action="store_true")
    s.add_argument("--county"); s.add_argument("--zip"); s.add_argument("--min-score", type=int, default=40)
    s.add_argument("--limit", type=int, default=200)

    s = sub.add_parser("daily", help="Refresh live feeds, update buyers, add new hot leads (schedule this)")
    s.add_argument("--min-score", type=int, default=50); s.add_argument("--county", action="append")

    sub.add_parser("counties", help="List the 67 counties and DOR numbers")
    sub.add_parser("status", help="Database contents")
    sub.add_parser("unmatched", help="Imported events that don't match a loaded parcel")
    sub.add_parser("chat", help="Ask questions in plain English (needs ANTHROPIC_API_KEY)")
    s = sub.add_parser("web", help="Run the local web app"); s.add_argument("--port", type=int, default=8765)

    a = ap.parse_args(argv)
    conn = db.connect(a.db)

    if a.cmd == "import-nal":
        from pathlib import Path
        paths = sorted(Path(a.path).glob("*.zip")) + sorted(Path(a.path).glob("*.csv")) if Path(a.path).is_dir() else [a.path]
        for path in paths:
            n = importers.import_nal(conn, path, a.county, progress=lambda n: print(f"  {n:,} rows...", file=sys.stderr))
            print(f"{path}: imported {n:,} parcels.")
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
    elif a.cmd == "skiptrace-export":
        if a.pipeline:
            ps = [search.get_property(conn, r["parcel_id"], r["county"]) for r in pipeline.board(conn)]
        else:
            ps = search.distressed(conn, a.county, a.zip, a.min_score, None, a.limit)
        n = skiptrace.export_for_skiptrace(conn, [p for p in ps if p], a.out)
        print(f"Wrote {n} people to {a.out}. Upload it to your skip-trace provider, then run skiptrace-import.")
    elif a.cmd == "skiptrace-import":
        n = skiptrace.import_skiptrace_results(conn, a.path); conn.commit()
        print(f"Added {n} phone numbers / emails.")
    elif a.cmd == "skiptrace-api":
        p = _need(conn, a)
        print(f"Added {skiptrace.trace_via_api(conn, p)} contacts.")
        pipeline.update_lead(conn, p["county"], p["parcel_key"], status="skip_traced")
    elif a.cmd == "import-entities":
        n = skiptrace.import_entities(conn, a.path); conn.commit()
        print(f"Imported {n} entity officers / agents.")
    elif a.cmd == "contacts":
        p = _need(conn, a)
        print(f"{p['owner_name']}  ({p['site_addr']}, {p['site_city']})")
        print(f"Mailing: {p['owner_addr1']}, {p['owner_city']} {p['owner_state']} {p['owner_zip']}")
        for dm in p["decision_makers"]:
            print(f"  decision maker: {dm['person_name']} ({dm['title']}) {dm['person_addr']}")
        for c in p["contacts"]:
            flag = (" DNC" if c["dnc"] else "") + (" LITIGATOR" if c["litigator"] else "")
            print(f"  {c['name']:<24} {c['role']:<18} {c['phone'] or '':<11} {c['phone_type'] or '':<9} "
                  f"{c['email'] or ''}{flag}")
        if not p["contacts"]:
            print("  No contacts yet: run skiptrace-export / skiptrace-import, or skiptrace-api.")
    elif a.cmd == "dialer-export":
        print(f"Wrote {skiptrace.export_dialer_list(conn, a.out, a.include_dnc)} numbers to {a.out}.")
    elif a.cmd == "analyze":
        p = _need(conn, a)
        lead = p.get("lead") or {}
        r = deals.analyze(conn, p, a.arv or lead.get("arv"), a.repairs if a.repairs is not None else lead.get("repairs"),
                          a.rehab, a.rent or lead.get("rent"), a.purchase)
        r.pop("assumptions", None)
        print(json.dumps(r, indent=2, default=str))
    elif a.cmd == "lead":
        p = _need(conn, a)
        pipeline.update_lead(conn, p["county"], p["parcel_key"], status=a.status, strategy=a.strategy,
                             offer=a.offer, next_follow_up=a.follow_up)
        if a.note:
            pipeline.log(conn, p["county"], p["parcel_key"], "note", a.note)
        pipeline.add_leads(conn, [p])
        print(json.dumps(pipeline.get_lead(conn, p["county"], p["parcel_key"]), indent=2, default=str))
    elif a.cmd == "add-leads":
        rows = search.distressed(conn, a.county, a.zip, a.min_score, a.require, a.limit)
        print(f"Added {pipeline.add_leads(conn, rows, a.list_name)} new leads (of {len(rows)} matching).")
    elif a.cmd == "pipeline":
        for r in pipeline.board(conn, a.status, a.due):
            print(f"{r['status']:<15} {r['county']:<12} {r['parcel_id']:<22} {(r['site_addr'] or '')[:30]:<30} "
                  f"{(r['owner_name'] or '')[:28]:<28} phones:{r['callable_phones']} follow-up:{r['next_follow_up'] or '-'}")
    elif a.cmd == "buyers-discover":
        print(f"Found/updated {buyers.discover(conn, a.county, a.min_purchases)} investor buyers.")
    elif a.cmd == "buyers-import":
        print(f"Imported {buyers.import_buyers(conn, a.path)} buyers.")
    elif a.cmd == "buyers":
        rows = buyers.list_buyers(conn, a.zip, a.county)
        if a.csv:
            print(f"Wrote {buyers.export_buyers(rows, a.csv)} buyers to {a.csv}")
        for b in rows[:100]:
            print(f"{b['name'][:36]:<36} buys:{b['purchases_24mo'] or '-':<3} ${b['min_price'] or 0:>9,.0f}-${b['max_price'] or 0:<9,.0f} "
                  f"{b['phone'] or '':<12} {(b['zips'] or '')[:40]}")
    elif a.cmd == "match-buyers":
        p = _need(conn, a)
        rows = buyers.match(conn, p, a.price)
        if a.csv:
            buyers.export_buyers(rows, a.csv)
        for b in rows:
            print(f"{b['fit']:<12} {b['name'][:36]:<36} buys:{b['purchases_24mo'] or '-'} {b['phone'] or ''} {b['email'] or ''}")
    elif a.cmd in ("mail-export", "letters"):
        if a.pipeline:
            ps = [search.get_property(conn, r["parcel_id"], r["county"]) for r in pipeline.board(conn)
                  if r["status"] not in ("dead", "closed")]
        else:
            ps = search.distressed(conn, a.county, a.zip, a.min_score, None, a.limit)
        ps = [p for p in ps if p]
        if a.cmd == "mail-export":
            print(f"Wrote {mail.export_mailing_list(ps, a.out)} addresses to {a.out}")
        else:
            if a.template not in mail.templates():
                raise SystemExit(f"Templates: {', '.join(mail.templates())}")
            open(a.out, "w").write(mail.render_letters(ps, a.template))
            print(f"Wrote {len(mail.mailing_rows(ps))} letters to {a.out}. Open it in a browser and print / save as PDF.")
    elif a.cmd == "daily":
        for r in fetch.refresh_all(conn):
            print(json.dumps(r))
        print(f"Buyers updated: {buyers.discover(conn)}")
        added = 0
        for c in (a.county or [None]):
            added += pipeline.add_leads(conn, search.distressed(conn, c, None, a.min_score, None, 1000),
                                        f"auto {__import__('datetime').date.today()}")
        print(f"New hot leads added to pipeline: {added}")
        due = pipeline.board(conn, due_only=True)
        print(f"Follow-ups due today: {len(due)}")
        for r in due[:25]:
            print(f"  {r['status']:<14} {r['site_addr']}, {r['site_city']}  {r['owner_name']}")
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

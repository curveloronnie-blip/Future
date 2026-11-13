"""Local web app: search, property detail, distressed lists, imports, and the AI assistant."""
from __future__ import annotations

import csv
import io
import os
import secrets
import tempfile

from flask import Flask, Response, redirect, render_template_string, request, session, url_for

from . import db, fetch, importers, links, search
from .counties import COUNTIES

BASE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FL Property Records</title><style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e3e2dd;--acc:#0b6e6e;--hi:#b42318;--md:#b54708;--lo:#3f6212}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--mute:#9c9b95;--line:#33332f;--acc:#4fb3b3;--hi:#f97066;--md:#fdb022;--lo:#a6d36b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;gap:18px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header b{font-size:16px}header a{color:var(--mute);text-decoration:none}header a:hover{color:var(--acc)}
main{max-width:1200px;margin:0 auto;padding:20px 16px}a{color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
form.row{display:flex;gap:8px;flex-wrap:wrap;align-items:end}label{display:flex;flex-direction:column;font-size:12px;color:var(--mute);gap:3px}
input,select,textarea{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink)}
button{font:inherit;padding:8px 14px;border-radius:7px;border:0;background:var(--acc);color:#fff;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--mute);font-weight:600}.wrap{overflow-x:auto}
.pill{display:inline-block;min-width:38px;text-align:center;padding:2px 7px;border-radius:99px;font-weight:600;font-size:13px;color:#fff}
.high{background:var(--hi)}.medium{background:var(--md)}.low{background:var(--lo)}
.tag{display:inline-block;font-size:11px;padding:1px 6px;margin:1px;border:1px solid var(--line);border-radius:5px;color:var(--mute)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.mute{color:var(--mute)}
.msg{white-space:pre-wrap;padding:10px 12px;border-radius:8px;margin:8px 0}.u{background:var(--bg)}.a{border:1px solid var(--line)}
.flash{padding:10px;border-radius:8px;border:1px solid var(--line);margin-bottom:12px}
</style></head><body><header><b>FL Property Records</b><a href="/">Search</a><a href="/distressed">Distressed</a>
<a href="/ask">Assistant</a><a href="/import">Import data</a><a href="/resources">County portals</a></header><main>
{% if flash %}<div class="flash">{{ flash }}</div>{% endif %}{{ body|safe }}</main></body></html>"""

COUNTY_SELECT = """<label>County<select name="county"><option value="">All</option>{% for no,c in counties %}
<option {{ 'selected' if c==county else '' }}>{{ c }}</option>{% endfor %}</select></label>"""

RESULTS = """<div class="wrap"><table><tr><th>Score</th><th>County</th><th>Parcel</th><th>Property</th><th>Owner</th>
<th>Mailing</th><th>Just value</th><th>Last sale</th><th>Signals</th></tr>
{% for p in rows %}<tr><td><span class="pill {{ p.distress.tier }}">{{ p.distress.score }}</span></td><td>{{ p.county }}</td>
<td><a href="{{ url_for('prop', county=p.county, pid=p.parcel_id) }}">{{ p.parcel_id }}</a></td>
<td>{{ p.site_addr }}<br><span class="mute">{{ p.site_city }} {{ p.site_zip }}</span></td><td>{{ p.owner_name }}</td>
<td class="mute">{{ p.owner_addr1 }}<br>{{ p.owner_city }} {{ p.owner_state }} {{ p.owner_zip }}</td>
<td>{{ "${:,.0f}".format(p.just_value) if p.just_value else "" }}</td>
<td>{{ p.last_sale_year or "" }}{% if p.last_sale_price %}<br><span class="mute">${{ "{:,.0f}".format(p.last_sale_price) }}</span>{% endif %}</td>
<td>{% for s in p.distress.signals %}<span class="tag">{{ s.reason }}</span>{% endfor %}</td></tr>
{% else %}<tr><td colspan="9" class="mute">No matches. Have you imported this county's NAL file? See Import data.</td></tr>{% endfor %}
</table></div>"""


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLPROPS_SECRET", secrets.token_hex(16))
    conn = db.connect(db_path)
    bots: dict[str, object] = {}

    def page(body: str, flash: str = "", **ctx):
        inner = render_template_string(body, counties=COUNTIES, **ctx)
        return render_template_string(BASE, body=inner, flash=flash)

    @app.route("/")
    def home():
        q, county, z = request.args.get("q", ""), request.args.get("county", ""), request.args.get("zip", "")
        rows = search.search(conn, q, county or None, z or None, 100) if (q or county or z) else []
        st = search.stats(conn)
        body = """<div class="card"><form class="row"><label>Address, owner or parcel #<input name="q" size="40" value="{{ q }}"></label>
""" + COUNTY_SELECT + """<label>ZIP<input name="zip" size="6" value="{{ z }}"></label><button>Search</button></form>
<p class="mute">{{ "{:,}".format(st.parcels_by_county.values()|sum) }} parcels in {{ st.parcels_by_county|length }} counties ·
{{ "{:,}".format(st.events_by_type.values()|sum) }} recorded events</p></div>""" + (RESULTS if rows or q else "")
        return page(body, q=q, county=county, z=z, rows=rows, st=st)

    @app.route("/property/<county>/<path:pid>")
    def prop(county, pid):
        p = search.get_property(conn, pid, county)
        if not p:
            return page("<p>Parcel not found.</p>"), 404
        body = """<div class="card"><h2 style="margin:0">{{ p.site_addr }} <span class="mute" style="font-weight:400">{{ p.site_city }} {{ p.site_zip }}</span></h2>
<p class="mute">{{ p.county }} County · Parcel {{ p.parcel_id }} · Source: {{ p.source }} · Updated {{ p.updated_at }}</p></div>
<div class="grid"><div class="card"><h3>Owner</h3><p><b>{{ p.owner_name }}</b><br>{{ p.owner_addr1 }} {{ p.owner_addr2 or "" }}<br>
{{ p.owner_city }} {{ p.owner_state }} {{ p.owner_zip }}</p><p>Homestead: {{ "Yes" if p.homestead else "No" }}</p></div>
<div class="card"><h3>Property</h3><p>Just value: {{ "${:,.0f}".format(p.just_value) if p.just_value else "n/a" }}<br>
Built: {{ p.year_built or "n/a" }} · {{ p.living_sqft or "?" }} sq ft · Use code {{ p.use_code }}<br>
Last sale: {{ p.last_sale_month or "" }}/{{ p.last_sale_year or "" }} {{ "${:,.0f}".format(p.last_sale_price) if p.last_sale_price else "" }}<br>
Prior sale: {{ p.prev_sale_year or "" }} {{ "${:,.0f}".format(p.prev_sale_price) if p.prev_sale_price else "" }}</p>
<p class="mute">{{ p.legal }}</p></div>
<div class="card"><h3>Distress <span class="pill {{ p.distress.tier }}">{{ p.distress.score }}</span></h3>
{% for s in p.distress.signals %}<div>+{{ s.points }} {{ s.reason }}</div>{% else %}<p class="mute">No signals.</p>{% endfor %}
<p class="mute" style="font-size:12px">Heuristic from public records, not a statement about the owner.</p></div></div>
<div class="card"><h3>Recorded events</h3><div class="wrap"><table><tr><th>Type</th><th>Date</th><th>Case / instrument</th><th>Amount</th><th>Party</th><th>Description</th><th>Source</th></tr>
{% for e in p.events %}<tr><td>{{ e.event_type }}</td><td>{{ e.event_date }}</td><td>{% if e.url %}<a href="{{ e.url }}">{{ e.case_number }}</a>{% else %}{{ e.case_number }}{% endif %}</td>
<td>{{ "${:,.0f}".format(e.amount) if e.amount else "" }}</td><td>{{ e.party }}</td><td>{{ e.description }}</td><td class="mute">{{ e.source }}</td></tr>
{% else %}<tr><td colspan="7" class="mute">None imported. Check the clerk and auction links below, then import what you find.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>Verify at the source</h3>{% for k,v in p.links.items() %}<div><a href="{{ v }}" target="_blank" rel="noopener">{{ k }}</a></div>{% endfor %}</div>"""
        return page(body, p=p)

    @app.route("/distressed")
    def distressed_view():
        county, z = request.args.get("county", ""), request.args.get("zip", "")
        ms = int(request.args.get("min", 25) or 25)
        req = request.args.getlist("require")
        rows = search.distressed(conn, county or None, z or None, ms, req, 500)
        if request.args.get("format") == "csv":
            out = io.StringIO(); w = csv.writer(out)
            w.writerow(["score", "county", "parcel_id", "site_addr", "site_city", "site_zip", "owner_name",
                        "owner_addr1", "owner_city", "owner_state", "owner_zip", "just_value", "events", "reasons"])
            for p in rows:
                w.writerow([p["distress"]["score"], p["county"], p["parcel_id"], p["site_addr"], p["site_city"],
                            p["site_zip"], p["owner_name"], p["owner_addr1"], p["owner_city"], p["owner_state"],
                            p["owner_zip"], p["just_value"], ";".join(p["event_types"]),
                            "; ".join(s["reason"] for s in p["distress"]["signals"])])
            return Response(out.getvalue(), mimetype="text/csv",
                            headers={"Content-Disposition": "attachment; filename=distressed.csv"})
        body = """<div class="card"><form class="row">""" + COUNTY_SELECT + """<label>ZIP<input name="zip" size="6" value="{{ z }}"></label>
<label>Min score<input name="min" type="number" value="{{ ms }}" style="width:70px"></label>
<label>Must have<select name="require" multiple size="3">{% for t in types %}<option {{ 'selected' if t in req else '' }}>{{ t }}</option>{% endfor %}</select></label>
<button>Filter</button><a href="?{{ request.query_string.decode() }}&format=csv">Download CSV</a></form>
<p class="mute">{{ rows|length }} properties</p></div>""" + RESULTS
        return page(body, county=county, z=z, ms=ms, req=req, rows=rows, types=list(db.EVENT_TYPES))

    @app.route("/import", methods=["GET", "POST"])
    def import_view():
        flash = ""
        if request.method == "POST":
            f = request.files.get("file")
            if f and f.filename:
                suffix = os.path.splitext(f.filename)[1] or ".csv"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    f.save(tmp.name)
                try:
                    kind = request.form["kind"]
                    county = request.form.get("county") or None
                    if kind == "nal":
                        flash = f"Imported {importers.import_nal(conn, tmp.name, county):,} parcels."
                    else:
                        flash = f"Added {importers.import_events(conn, tmp.name, kind, county, f.filename):,} {kind} records."
                except Exception as e:
                    flash = f"Import failed: {e}"
                finally:
                    os.unlink(tmp.name)
            elif request.form.get("refresh"):
                res = fetch.refresh_all(conn)
                flash = "; ".join(f"{r['source']}: {r.get('rows', r.get('error'))}" for r in res) or \
                    "No live sources enabled in config/sources.json."
        st = search.stats(conn)
        body = """<div class="grid"><div class="card"><h3>Upload</h3><form method="post" enctype="multipart/form-data">
<p><label>File (.csv or .zip)<input type="file" name="file"></label></p>
<p><label>What is it?<select name="kind"><option value="nal">FL DOR NAL parcel file</option>
{% for t in types %}<option value="{{ t }}">{{ t }} records</option>{% endfor %}</select></label></p>
<p>""" + COUNTY_SELECT + """</p><button>Import</button></form>
<p class="mute">Event CSVs need a parcel/folio column or an address column. Date, case number, amount, party and description are picked up automatically.</p></div>
<div class="card"><h3>Live feeds</h3><p class="mute">Pulls every enabled ArcGIS / Socrata source in config/sources.json.</p>
<form method="post"><button name="refresh" value="1">Refresh now</button></form>
<h3>Loaded</h3>{% for c,n in st.parcels_by_county.items() %}<div>{{ c }}: {{ "{:,}".format(n) }} parcels</div>{% endfor %}
{% for t,n in st.events_by_type.items() %}<div>{{ t }}: {{ "{:,}".format(n) }}</div>{% endfor %}</div></div>"""
        return page(body, flash=flash, types=list(db.EVENT_TYPES), st=st, county="")

    @app.route("/resources")
    def resources():
        county = request.args.get("county", "")
        lk = links.county_links(county) if county else {}
        body = """<div class="card"><form class="row">""" + COUNTY_SELECT + """<button>Show</button></form></div>
{% if lk %}<div class="card"><h3>{{ county }} County</h3>{% for k,v in lk.items() %}<div><a href="{{ v }}" target="_blank" rel="noopener">{{ k }}</a></div>{% endfor %}</div>{% endif %}
<div class="card"><h3>Statewide</h3>{% for k,v in sw.items() %}<div><a href="{{ v }}" target="_blank" rel="noopener">{{ k }}</a></div>{% endfor %}</div>"""
        return page(body, county=county, lk=lk, sw=links.STATEWIDE)

    @app.route("/ask", methods=["GET", "POST"])
    def ask():
        sid = session.setdefault("sid", secrets.token_hex(8))
        hist = session.get("hist", [])
        flash = ""
        if request.method == "POST" and request.form.get("q"):
            try:
                from .assistant import Assistant
                bot = bots.get(sid) or bots.setdefault(sid, Assistant(conn))
                answer = bot.ask(request.form["q"])
                hist = hist + [("u", request.form["q"]), ("a", answer)]
                session["hist"] = hist[-20:]
            except Exception as e:
                flash = f"Assistant unavailable: {e}. Set ANTHROPIC_API_KEY and restart."
        body = """<div class="card">{% for r,t in hist %}<div class="msg {{ r }}">{{ t }}</div>{% else %}
<p class="mute">Try: "Show me high-distress properties in 33147 with a lis pendens" or "Who owns 123 Main St, Tampa?"</p>{% endfor %}
<form method="post" class="row"><input name="q" style="flex:1" autofocus><button>Ask</button></form></div>"""
        return page(body, flash=flash, hist=hist)

    @app.route("/ask/reset")
    def ask_reset():
        bots.pop(session.get("sid"), None); session.pop("hist", None)
        return redirect(url_for("ask"))

    return app

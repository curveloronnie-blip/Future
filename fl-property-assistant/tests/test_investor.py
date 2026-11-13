import csv
from pathlib import Path

import pytest

from flprops import db, deals, importers, pipeline, search, skiptrace

FX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    importers.import_nal(c, FX / "sample_nal.csv")
    importers.import_nal(c, FX / "comps_nal.csv")
    importers.import_events(c, FX / "lis_pendens.csv", "lis_pendens", "Miami-Dade")
    return c


@pytest.mark.parametrize("owner,expected", [
    ("DOE JOHN A & MARY B", [("JOHN", "DOE"), ("MARY", "DOE")]),
    ("SAMPLE JANE EST OF", [("JANE", "SAMPLE")]),
    ("SMITH ROBERT TR", [("ROBERT", "SMITH")]),
    ("DOE JOHN & ROE RICHARD", [("JOHN", "DOE"), ("RICHARD", "ROE")]),
])
def test_split_people(owner, expected):
    assert [(n["first"], n["last"]) for n in skiptrace.split_owner_names(owner)] == expected


def test_split_entity():
    assert skiptrace.split_owner_names("EXAMPLE HOLDINGS LLC") == [{"entity": "EXAMPLE HOLDINGS LLC"}]


def test_skiptrace_roundtrip(conn, tmp_path):
    p = search.get_property(conn, "A-0001-000-0010", "Hillsborough")
    out = tmp_path / "up.csv"
    assert skiptrace.export_for_skiptrace(conn, [p], out) == 1
    row = next(csv.DictReader(open(out)))
    assert (row["first_name"], row["last_name"], row["lead_id"]) == ("JANE", "SAMPLE", "Hillsborough|A00010000010")

    assert skiptrace.import_skiptrace_results(conn, FX / "skiptrace_results.csv") == 3
    cs = skiptrace.contacts_for(conn, "Hillsborough", "A00010000010")
    phones = {c["phone"]: c for c in cs if c["phone"]}
    assert phones["8135550101"]["phone_type"] == "mobile" and phones["8135550101"]["dnc"] == 0
    assert phones["8135550102"]["dnc"] == 1
    assert any(c["email"] == "jane.sample@example.com" for c in cs)
    # already traced -> skipped on next export
    assert skiptrace.export_for_skiptrace(conn, [p], out) == 0

    pipeline.add_leads(conn, [p])
    dial = tmp_path / "dial.csv"
    assert skiptrace.export_dialer_list(conn, dial) == 1          # DNC number removed
    assert skiptrace.export_dialer_list(conn, dial, include_dnc=True) == 2


def test_decision_makers(conn, tmp_path):
    assert skiptrace.import_entities(conn, FX / "entities.csv") == 2
    p = search.get_property(conn, "A-0001-000-0020", "Hillsborough")
    assert p["decision_makers"][0]["person_name"] == "MANAGER, PAT"   # manager ranked before RA
    targets = skiptrace.targets_for(conn, p)
    assert ("PAT", "MANAGER") in [(t["first"], t["last"]) for t in targets]


def test_math():
    assert round(deals.mortgage_payment(200000, 0.075, 30), 2) == 1398.43
    w = deals.wholesale(300000, 40000, 10000)
    assert w["end_buyer_max_price"] == 170000 and w["your_max_offer"] == 160000
    f = deals.flip(160000, 300000, 40000, months=6)
    assert f["net_profit"] == 300000 - f["total_cost"]
    assert f["costs"]["selling"] == 24000
    b = deals.brrrr(160000, 40000, 300000, 2600)
    assert b["refi_loan"] == 225000
    assert b["monthly"]["principal_interest"] == round(deals.mortgage_payment(225000, 0.075, 30))


def test_comps_and_analyze(conn):
    p = search.get_property(conn, "A-0001-000-0010", "Hillsborough")   # 1400 sqft, 1962, 33601
    c = deals.comps(conn, p)
    ids = {x["parcel_id"] for x in c["comps"]}
    assert ids == {"C-1", "C-2", "C-3", "C-4"}       # unqualified bank sale (code 11) excluded
    assert c["arv"] and 300000 < c["arv"] < 350000
    r = deals.analyze(conn, p, rent=2400)
    assert r["wholesale"]["your_max_offer"] > 0 and "brrrr" in r and r["verdict"]


def test_pipeline(conn):
    rows = search.distressed(conn, min_score=25)
    assert pipeline.add_leads(conn, rows, "test") == len(rows)
    p = rows[0]
    pipeline.update_lead(conn, p["county"], p["parcel_key"], status="contacted", next_follow_up="2000-01-01")
    due = pipeline.board(conn, due_only=True)
    assert [d["parcel_id"] for d in due] == [p["parcel_id"]]
    assert pipeline.get_lead(conn, p["county"], p["parcel_key"])["activity"][0]["note"] == "contacted"
    with pytest.raises(ValueError):
        pipeline.update_lead(conn, p["county"], p["parcel_key"], status="bogus")


def test_migrates_old_db(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("CREATE TABLE IF NOT EXISTS parcels", "CREATE TABLE IF NOT EXISTS parcels", 1))
    old.close()
    c = db.connect(str(path))
    assert "sale_qual" in {r[1] for r in c.execute("PRAGMA table_info(parcels)")}


def test_web_investor_pages(conn, monkeypatch, tmp_path):
    from flprops import web
    monkeypatch.setattr(web.db, "connect", lambda *_: conn)
    skiptrace.import_skiptrace_results(conn, FX / "skiptrace_results.csv")
    cl = web.create_app(":memory:").test_client()
    page = cl.get("/property/Hillsborough/A-0001-000-0010").data
    assert b"813-555-0101" in page and b"DNC" in page
    r = cl.get("/property/Hillsborough/A-0001-000-0010/analyze?rent=2400")
    assert r.status_code == 200 and b"Wholesale" in r.data and b"BRRRR" in r.data
    cl.post("/property/Hillsborough/A-0001-000-0010/lead", data={"status": "contacted", "strategy": "flip"})
    assert b"contacted" in cl.get("/pipeline").data
    assert b"8135550101" in cl.get("/pipeline/dialer.csv").data
    assert cl.post("/distressed/add", data={"min": "25"}).status_code == 302
    assert b"API" in cl.post("/property/Hillsborough/A-0001-000-0010/trace", follow_redirects=True).data


def test_assistant_tools(conn):
    from flprops.assistant import run_tool, TOOLS
    names = {t["name"] for t in TOOLS}
    assert {"get_contacts", "analyze_deal", "update_lead", "list_pipeline"} <= names
    r = run_tool(conn, "analyze_deal", {"parcel_id": "A-0001-000-0010", "county": "Hillsborough"})
    assert r["wholesale"]["your_max_offer"] > 0
    lead = run_tool(conn, "update_lead", {"parcel_id": "A-0001-000-0010", "status": "offer_made", "offer": 150000,
                                          "note": "called"})
    assert lead["status"] == "offer_made" and lead["offer"] == 150000
    assert run_tool(conn, "get_contacts", {"parcel_id": "A-0001-000-0010"})["contacts"] == []

from pathlib import Path

import pytest

from flprops import buyers, db, importers, mail, pipeline, search
from flprops.cli import main

FX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    importers.import_nal(c, FX / "sample_nal.csv")
    importers.import_nal(c, FX / "comps_nal.csv")
    return c


def test_discover_buyers(conn):
    # make one owner an active investor: buys 2 non-homestead properties recently
    conn.execute("UPDATE parcels SET owner_name='SAMPLE FLIPS LLC', homestead=0 WHERE parcel_id IN ('C-1','C-2')")
    conn.execute("UPDATE parcels SET owner_name='FIRST SAMPLE BANK', homestead=0 WHERE parcel_id IN ('C-3','C-4')")
    assert buyers.discover(conn) == 1
    b = buyers.list_buyers(conn)[0]
    assert b["name"] == "SAMPLE FLIPS LLC" and b["purchases_24mo"] == 2 and b["zips"] == "33601"
    assert buyers.discover(conn) == 1  # upsert, not duplicate
    assert len(buyers.list_buyers(conn)) == 1


def test_import_and_match_buyers(conn):
    assert buyers.import_buyers(conn, FX / "buyers.csv") == 2
    p = search.get_property(conn, "A-0001-000-0010", "Hillsborough")
    m = buyers.match(conn, p, price=200000)
    assert [b["name"] for b in m] == ["Example Flip Co"] and m[0]["fit"] == "same ZIP"
    assert buyers.match(conn, p, price=900000) == []  # above their max


@pytest.mark.parametrize("owner,to,dear", [
    ("SAMPLE JANE EST OF", "Estate of Jane Sample", "Family Member or Personal Representative"),
    ("DOE JOHN & MARY", "John and Mary Doe", "John and Mary"),
    ("EXAMPLE HOLDINGS LLC", "Example Holdings LLC", "Owner"),
    ("PLACEHOLDER ROBERT", "Robert Placeholder", "Robert"),
])
def test_salutation(owner, to, dear):
    assert mail.salutation(owner) == (to, dear)


def test_mailing_and_letters(conn, tmp_path):
    ps = [search.get_property(conn, pid) for pid in ("A-0001-000-0010", "A-0001-000-0020")]
    rows = mail.mailing_rows(ps + ps)  # duplicates removed
    assert len(rows) == 2 and rows[1]["mail_state"] == "NY"
    assert set(mail.templates()) >= {"cash_offer", "estate_probate", "pre_foreclosure", "absentee_landlord"}
    html = mail.render_letters(ps, "estate_probate")
    assert html.count('class="letter"') == 2 and "Estate of Jane Sample" in html
    assert "{property_address}" not in html and "Lines starting" not in html


def test_cli_end_to_end(tmp_path, capsys):
    d = str(tmp_path / "t.db")
    assert main(["--db", d, "import-nal", str(FX)]) == 0           # whole folder
    assert main(["--db", d, "import-events", str(FX / "lis_pendens.csv"), "--type", "lis_pendens",
                 "--county", "Miami-Dade"]) == 0
    assert main(["--db", d, "daily", "--min-score", "40"]) == 0
    out = capsys.readouterr().out
    assert "New hot leads added to pipeline: " in out
    assert main(["--db", d, "letters", str(tmp_path / "l.html"), "--pipeline"]) == 0
    assert (tmp_path / "l.html").read_text().count('class="letter"') >= 1
    assert main(["--db", d, "mail-export", str(tmp_path / "m.csv"), "--min-score", "1"]) == 0
    assert main(["--db", d, "buyers-import", str(FX / "buyers.csv")]) == 0
    assert main(["--db", d, "match-buyers", "A-0001-000-0010", "--county", "Hillsborough"]) == 0
    assert "Example Flip Co" in capsys.readouterr().out


def test_web_system_pages(conn, monkeypatch, tmp_path):
    from flprops import fetch, web
    cfg = tmp_path / "sources.json"
    cfg.write_text('{"sources": []}')
    monkeypatch.setattr(fetch, "CONFIG", cfg)
    monkeypatch.setattr(web.db, "connect", lambda *_: conn)
    importers.import_events(conn, FX / "lis_pendens.csv", "lis_pendens", "Miami-Dade")
    cl = web.create_app(":memory:").test_client()
    home = cl.get("/").data
    assert b"Leads in pipeline" in home and b"Hottest leads" in home
    assert cl.post("/buyers", data={"name": "Manual Buyer", "zips": "33601", "counties": "Hillsborough"}).status_code == 200
    assert b"Manual Buyer" in cl.get("/buyers").data
    assert b"Manual Buyer" in cl.get("/property/Hillsborough/A-0001-000-0010/analyze").data
    pipeline.add_leads(conn, search.distressed(conn, min_score=1))
    assert b'class="letter"' in cl.get("/pipeline/letters?template=cash_offer").data
    assert b"addressee" in cl.get("/pipeline/mail.csv").data
    cl.post("/settings", data={"your_name": "Pat Investor", "assignment_fee": "15000"})
    assert "Pat Investor" in cfg.read_text()
    from flprops import deals
    assert deals.defaults()["assignment_fee"] == 15000


def test_assistant_find_buyers(conn):
    from flprops.assistant import run_tool
    buyers.import_buyers(conn, FX / "buyers.csv")
    r = run_tool(conn, "find_buyers", {"parcel_id": "A-0001-000-0010", "county": "Hillsborough"})
    assert r[0]["name"] == "Example Flip Co"

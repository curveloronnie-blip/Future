import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from flprops import db, distress, importers, links, search
from flprops.counties import COUNTIES, normalize_county

FX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    importers.import_nal(c, FX / "sample_nal.csv")
    importers.import_events(c, FX / "lis_pendens.csv", "lis_pendens", "Miami-Dade")
    importers.import_events(c, FX / "violations.csv", "code_violation", "Hillsborough")
    return c


def test_counties():
    assert len(COUNTIES) == 67 and len({n for n, _ in COUNTIES}) == 67
    assert normalize_county("23") == "Miami-Dade"
    assert normalize_county("dade county") == "Miami-Dade"
    assert normalize_county("st lucie") == "St. Lucie"
    assert normalize_county("Nowhere") is None


def test_keys():
    assert db.parcel_key("30-1234-000-0040") == "3012340000040"
    assert db.address_key("100 North Test Street Apt 4") == "100 N TEST ST"


def test_nal_import(conn):
    assert search.stats(conn)["parcels_by_county"] == {"Hillsborough": 3, "Miami-Dade": 1}


def test_search_by_address_owner_parcel(conn):
    assert search.search(conn, "100 test street")[0]["owner_name"] == "SAMPLE JANE EST OF"
    assert search.search(conn, "holdings")[0]["parcel_id"] == "A-0001-000-0020"
    assert search.search(conn, "30123400000 40")[0]["county"] == "Miami-Dade"


def test_events_match_by_parcel_and_address(conn):
    p = search.get_property(conn, "30-1234-000-0040")
    assert [e["event_type"] for e in p["events"]] == ["lis_pendens"]
    assert p["events"][0]["amount"] == 180000
    p2 = search.get_property(conn, "A-0001-000-0020", "Hillsborough")
    assert len(p2["events"]) == 2  # unit suffix stripped when matching addresses
    assert "Sunbiz (owner entity)" in p2["links"]


def test_reimport_is_idempotent(conn):
    assert importers.import_events(conn, FX / "violations.csv", "code_violation", "Hillsborough") == 0


def test_distress_ranking(conn):
    rows = search.distressed(conn, min_score=1)
    ids = [r["parcel_id"] for r in rows]
    assert ids[0] == "30-1234-000-0040"            # lis pendens + absentee + old + long-held
    assert "A-0001-000-0030" not in ids or rows[-1]["parcel_id"] == "A-0001-000-0030"
    est = next(r for r in rows if r["parcel_id"] == "A-0001-000-0010")
    reasons = " ".join(s["reason"] for s in est["distress"]["signals"])
    assert "estate" in reasons and "nominal" in reasons
    only_lp = search.distressed(conn, require=["lis_pendens"], min_score=0)
    assert [r["parcel_id"] for r in only_lp] == ["30-1234-000-0040"]


def test_owner_occupied_low_score():
    p = {"owner_name": "DOE JOHN", "owner_state": "FL", "owner_addr1": "1 A ST", "site_addr": "1 A ST",
         "homestead": 1, "last_sale_year": 2022, "year_built": 2010, "last_sale_price": 300000}
    assert distress.score(p, [], dt.date(2026, 1, 1))["score"] == 0


def test_links():
    lk = links.county_links("Miami-Dade")
    assert lk["Property appraiser"].startswith("https://www.miamidade.gov")
    assert lk["Foreclosure auction calendar"] == "https://www.miamidade.realforeclose.com/"
    assert "google.com" in links.county_links("Liberty")["Property appraiser"]


def test_bad_event_csv(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        importers.import_events(db.connect(":memory:"), f, "lien")


def test_web(conn, monkeypatch):
    from flprops import web
    monkeypatch.setattr(web.db, "connect", lambda *_: conn)
    app = web.create_app(":memory:")
    cl = app.test_client()
    assert b"SAMPLE JANE" in cl.get("/?q=sample").data
    assert b"lis_pendens" in cl.get("/property/Miami-Dade/30-1234-000-0040").data
    r = cl.get("/distressed?min=1&format=csv")
    assert r.status_code == 200 and b"30-1234-000-0040" in r.data
    assert cl.get("/resources?county=Orange").status_code == 200
    assert cl.get("/import").status_code == 200


class _FakeMessages:
    """Scripted stand-in for client.beta.messages: one tool call, then an answer."""
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
                type="tool_use", id="t1", name="list_distressed", input={"county": "Miami-Dade", "min_score": 1})])
        tool_result = kw["messages"][-1]["content"][0]
        assert "30-1234-000-0040" in tool_result["content"]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="One property.")])


def test_assistant_loop(conn):
    from flprops.assistant import Assistant
    fake = _FakeMessages()
    bot = Assistant(conn, client=SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    assert bot.ask("distressed in miami-dade?") == "One property."
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    assert len(bot.messages) == 4  # user, assistant(tool_use), user(tool_result), assistant


def test_fetch_stops_on_captcha(monkeypatch):
    from flprops import fetch
    s = fetch.PoliteSession()
    monkeypatch.setattr(s, "_allowed", lambda url: True)

    class R:
        status_code = 200; headers = {"Content-Type": "text/html"}; url = "https://x.example/"
        text = "<html>Please complete the CAPTCHA</html>"
        def raise_for_status(self): pass
    monkeypatch.setattr(s.s, "get", lambda *a, **k: R())
    with pytest.raises(fetch.ManualStepRequired):
        s.get("https://x.example/")

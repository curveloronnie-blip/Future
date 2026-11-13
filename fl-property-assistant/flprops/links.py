"""Deep links to the official portals for a county / property.

Several Florida clerk and auction sites require a person to accept a disclaimer or solve a
CAPTCHA before searching. Those are given as links for you to open, not automated.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .counties import (PROPERTY_APPRAISER, REALAUCTION_FORECLOSURE, REALAUCTION_TAXDEED,
                       normalize_county, realauction_slug)
from .fetch import load_sources

STATEWIDE = {
    "FL DOR property tax data portal (NAL/SDF downloads, all 67 counties)":
        "https://floridarevenue.com/property/dataportal/Pages/default.aspx",
    "FL DOR directory of property appraisers / tax collectors / clerks":
        "https://floridarevenue.com/property/Pages/LocalOfficials.aspx",
    "Sunbiz (FL corporations / LLC owners, registered agents)":
        "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName",
    "Florida Geographic Information Office open data (statewide parcels)":
        "https://geodata.floridagio.gov/",
    "PACER (federal bankruptcy filings)": "https://pcl.uscourts.gov/pcl/index.jsf",
}

ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|LP|LLP|LTD|COMPANY|CO|HOLDINGS|PROPERTIES|INVESTMENTS)\b")


def _google(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


def county_links(county: str) -> dict[str, str]:
    c = normalize_county(county)
    if not c:
        raise ValueError(f"unknown Florida county: {county!r}")
    over = load_sources().get("county_overrides", {}).get(c, {})
    slug = realauction_slug(c)
    links = {
        "Property appraiser": over.get("property_appraiser") or PROPERTY_APPRAISER.get(c)
        or _google(f"{c} County Florida property appraiser property search"),
        "Clerk official records (liens, lis pendens, deeds)": over.get("clerk_official_records")
        or _google(f"{c} County Florida clerk official records search"),
        "Clerk court records (foreclosure, probate, eviction)": over.get("clerk_court_records")
        or _google(f"{c} County Florida clerk court records search"),
        "Tax collector (delinquent taxes)": over.get("tax_collector")
        or _google(f"{c} County Florida tax collector property tax search"),
        "Code enforcement": over.get("code_enforcement")
        or _google(f"{c} County Florida code enforcement violation search"),
    }
    if c in REALAUCTION_FORECLOSURE or over.get("foreclosure_auction"):
        links["Foreclosure auction calendar"] = over.get("foreclosure_auction") or f"https://www.{slug}.realforeclose.com/"
    else:
        links["Foreclosure sales"] = _google(f"{c} County Florida clerk foreclosure sales")
    if c in REALAUCTION_TAXDEED or over.get("tax_deed_auction"):
        links["Tax deed auction calendar"] = over.get("tax_deed_auction") or f"https://www.{slug}.realtaxdeed.com/"
    else:
        links["Tax deed sales"] = _google(f"{c} County Florida clerk tax deed sales")
    return links


def property_links(p: dict) -> dict[str, str]:
    c = p.get("county")
    links = county_links(c) if c else {}
    addr = ", ".join(x for x in (p.get("site_addr"), p.get("site_city"), "FL", p.get("site_zip")) if x)
    owner = (p.get("owner_name") or "").strip()
    if addr:
        links["Map"] = "https://www.google.com/maps/search/" + quote_plus(addr)
    if owner and ENTITY_RE.search(owner.upper()):
        links["Sunbiz (owner entity)"] = STATEWIDE["Sunbiz (FL corporations / LLC owners, registered agents)"]
    if owner and not ENTITY_RE.search(owner.upper()):
        person = re.sub(r"\b(EST(ATE)? OF|HEIRS?|TR(USTEE)?|LIFE EST(ATE)?|&.*|ET AL|H/E|JTRS)\b", "", owner.upper())
        person = " ".join(person.split())
        links["Obituary search (owner)"] = _google(f'"{person.title()}" obituary {p.get("site_city") or c} Florida')
    return links

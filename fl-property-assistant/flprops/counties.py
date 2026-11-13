"""All 67 Florida counties with Florida DOR county numbers and known public-records portals.

URLs change. Every portal URL here can be overridden in config/sources.json
("county_overrides"). Where no URL is known, links.py falls back to a search link.
"""
from __future__ import annotations

import re

# (DOR county number used in NAL files, county name)
COUNTIES: list[tuple[int, str]] = [
    (11, "Alachua"), (12, "Baker"), (13, "Bay"), (14, "Bradford"), (15, "Brevard"),
    (16, "Broward"), (17, "Calhoun"), (18, "Charlotte"), (19, "Citrus"), (20, "Clay"),
    (21, "Collier"), (22, "Columbia"), (23, "Miami-Dade"), (24, "DeSoto"), (25, "Dixie"),
    (26, "Duval"), (27, "Escambia"), (28, "Flagler"), (29, "Franklin"), (30, "Gadsden"),
    (31, "Gilchrist"), (32, "Glades"), (33, "Gulf"), (34, "Hamilton"), (35, "Hardee"),
    (36, "Hendry"), (37, "Hernando"), (38, "Highlands"), (39, "Hillsborough"), (40, "Holmes"),
    (41, "Indian River"), (42, "Jackson"), (43, "Jefferson"), (44, "Lafayette"), (45, "Lake"),
    (46, "Lee"), (47, "Leon"), (48, "Levy"), (49, "Liberty"), (50, "Madison"),
    (51, "Manatee"), (52, "Marion"), (53, "Martin"), (54, "Monroe"), (55, "Nassau"),
    (56, "Okaloosa"), (57, "Okeechobee"), (58, "Orange"), (59, "Osceola"), (60, "Palm Beach"),
    (61, "Pasco"), (62, "Pinellas"), (63, "Polk"), (64, "Putnam"), (65, "St. Johns"),
    (66, "St. Lucie"), (67, "Santa Rosa"), (68, "Sarasota"), (69, "Seminole"), (70, "Sumter"),
    (71, "Suwannee"), (72, "Taylor"), (73, "Union"), (74, "Volusia"), (75, "Wakulla"),
    (76, "Walton"), (77, "Washington"),
]

# Property appraiser search sites for the larger counties (verify; override in config).
PROPERTY_APPRAISER: dict[str, str] = {
    "Alachua": "https://www.acpafl.org/",
    "Brevard": "https://www.bcpao.us/",
    "Broward": "https://web.bcpa.net/",
    "Collier": "https://www.collierappraiser.com/",
    "Duval": "https://paopropertysearch.coj.net/",
    "Hillsborough": "https://www.hcpafl.org/",
    "Lee": "https://www.leepa.org/",
    "Leon": "https://www.leonpa.gov/",
    "Manatee": "https://www.manateepao.gov/",
    "Miami-Dade": "https://www.miamidade.gov/pa/",
    "Orange": "https://ocpaweb.ocpafl.org/",
    "Osceola": "https://www.property-appraiser.org/",
    "Palm Beach": "https://pbcpao.gov/",
    "Pasco": "https://www.pascopa.com/",
    "Pinellas": "https://www.pcpao.gov/",
    "Polk": "https://www.polkpa.org/",
    "Sarasota": "https://www.sc-pa.com/",
    "Seminole": "https://www.scpafl.org/",
    "Volusia": "https://vcpa.vcgov.org/",
}

# Counties believed to run online foreclosure / tax-deed auctions on RealAuction
# (www.<slug>.realforeclose.com / www.<slug>.realtaxdeed.com). Verify before relying on it.
REALAUCTION_FORECLOSURE = {
    "Alachua", "Baker", "Bay", "Brevard", "Broward", "Calhoun", "Charlotte", "Citrus", "Clay",
    "Duval", "Escambia", "Flagler", "Gilchrist", "Gulf", "Hendry", "Hernando", "Hillsborough",
    "Indian River", "Jackson", "Lake", "Lee", "Leon", "Manatee", "Marion", "Martin",
    "Miami-Dade", "Nassau", "Okaloosa", "Okeechobee", "Orange", "Osceola", "Palm Beach",
    "Pasco", "Pinellas", "Polk", "Putnam", "St. Lucie", "Santa Rosa", "Sarasota", "Seminole",
    "Volusia", "Walton", "Washington",
}
REALAUCTION_TAXDEED = REALAUCTION_FORECLOSURE | {"Collier", "Monroe", "Sumter"}

_BY_NAME = {name.lower(): (no, name) for no, name in COUNTIES}
_ALIASES = {"dade": "miami-dade", "miami dade": "miami-dade", "st johns": "st. johns",
            "saint johns": "st. johns", "st lucie": "st. lucie", "saint lucie": "st. lucie",
            "de soto": "desoto"}


def normalize_county(value: str | int | None) -> str | None:
    """Return the canonical county name for a name or DOR county number, else None."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        for no, name in COUNTIES:
            if no == n:
                return name
        return None
    key = re.sub(r"\s+county$", "", s.lower()).strip()
    key = _ALIASES.get(key, key)
    hit = _BY_NAME.get(key)
    return hit[1] if hit else None


def county_number(name: str) -> int | None:
    c = normalize_county(name)
    return _BY_NAME[c.lower()][0] if c else None


def realauction_slug(county: str) -> str:
    return re.sub(r"[^a-z]", "", county.lower())

"""Deal analysis: rough comps / ARV, repair estimates, wholesale MAO, fix-and-flip, BRRRR.

All numbers are estimates for screening. The defaults below are typical, not yours: change them
in config/sources.json -> "deal_defaults" or pass your own values. Always confirm ARV with real
sold comps (MLS / agent) and repairs with a walkthrough or contractor bid.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import statistics

from .fetch import load_sources

DEFAULTS = {
    "wholesale_pct": 0.70,          # the 70% rule
    "assignment_fee": 10000,
    "rehab_psf": {"light": 20, "medium": 40, "heavy": 65, "gut": 90},
    "buy_closing_pct": 0.02, "sell_closing_pct": 0.08,   # incl. agent commissions
    "hold_months": 6, "hard_money_rate": 0.11, "hard_money_points": 0.02, "hard_money_ltc": 0.90,
    "tax_rate": 0.018, "insurance_yr_per_1k": 9.0, "utilities_mo": 250,
    "refi_ltv": 0.75, "refi_rate": 0.075, "refi_years": 30, "refi_closing_pct": 0.03,
    "vacancy_pct": 0.06, "maintenance_pct": 0.06, "capex_pct": 0.05, "management_pct": 0.09,
    "comp_years": 2, "comp_sqft_band": 0.25, "comp_year_band": 20, "min_comp_price": 25000,
}


def defaults() -> dict:
    d = dict(DEFAULTS)
    d.update(load_sources().get("deal_defaults", {}))
    return d


def _qualified(code: str | None) -> bool:
    """FL DOR sale qualification: '01'-'05' style codes or 'Q' are arm's-length sales; others
    (family transfers, foreclosures, quitclaims) are excluded. Empty codes are allowed."""
    c = (code or "").strip().upper()
    if not c:
        return True
    if c in ("Q",):
        return True
    return c.isdigit() and int(c) <= 5


def comps(conn: sqlite3.Connection, p: dict, d: dict | None = None, limit: int = 12) -> dict:
    """Comparable sales from the parcel roll: same ZIP, similar size and age, sold recently, qualified."""
    d = d or defaults()
    sq = p.get("living_sqft") or 0
    if not sq or not p.get("site_zip"):
        return {"comps": [], "arv": None, "note": "Need living area and ZIP to pull comps."}
    since = dt.date.today().year - d["comp_years"]
    yb = p.get("year_built") or 0
    rows = conn.execute(
        "SELECT county, parcel_id, site_addr, living_sqft, year_built, last_sale_price, last_sale_year, "
        "last_sale_month, sale_qual, use_code FROM parcels WHERE site_zip LIKE ? AND parcel_key != ? "
        "AND living_sqft BETWEEN ? AND ? AND last_sale_year >= ? AND last_sale_price >= ? "
        "AND (use_code IS NULL OR use_code = ? OR ? IS NULL)",
        ((p["site_zip"] or "")[:5] + "%", p["parcel_key"], sq * (1 - d["comp_sqft_band"]),
         sq * (1 + d["comp_sqft_band"]), since, d["min_comp_price"], p.get("use_code"), p.get("use_code")),
    ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        if not _qualified(r["sale_qual"]):
            continue
        if yb and r["year_built"] and abs(r["year_built"] - yb) > d["comp_year_band"]:
            continue
        r["price_psf"] = round(r["last_sale_price"] / r["living_sqft"], 2)
        out.append(r)
    out.sort(key=lambda r: (-(r["last_sale_year"] or 0), -(r["last_sale_month"] or 0)))
    out = out[:limit]
    if len(out) < 3:
        return {"comps": out, "arv": None, "note": f"Only {len(out)} comps found; get comps from MLS/agent."}
    psfs = sorted(r["price_psf"] for r in out)
    # renovated value: use the upper half of comp $/sqft as an ARV proxy
    upper = psfs[len(psfs) // 2:]
    return {
        "comps": out,
        "median_psf": round(statistics.median(psfs), 2),
        "arv_psf": round(statistics.median(upper), 2),
        "arv": round(statistics.median(upper) * sq, -3),
        "as_is_estimate": round(statistics.median(psfs) * sq, -3),
        "note": "Rough: parcel-roll sales, no condition data. Confirm with MLS sold comps.",
    }


def repair_estimate(sqft: float, level: str = "medium", d: dict | None = None) -> float:
    d = d or defaults()
    return round((sqft or 0) * d["rehab_psf"].get(level, d["rehab_psf"]["medium"]), -2)


def mortgage_payment(principal: float, rate: float, years: int) -> float:
    r, n = rate / 12, years * 12
    return principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)


def wholesale(arv: float, repairs: float, assignment_fee: float | None = None, d: dict | None = None) -> dict:
    d = d or defaults()
    fee = d["assignment_fee"] if assignment_fee is None else assignment_fee
    buyer_max = arv * d["wholesale_pct"] - repairs
    return {
        "arv": arv, "repairs": repairs, "rule": f"{int(d['wholesale_pct'] * 100)}% rule",
        "end_buyer_max_price": round(buyer_max),
        "assignment_fee": fee,
        "your_max_offer": round(buyer_max - fee),
        "opening_offer_suggestion": round((buyer_max - fee) * 0.85, -3),
    }


def flip(purchase: float, arv: float, repairs: float, months: float | None = None, d: dict | None = None,
         financed: bool = True) -> dict:
    d = d or defaults()
    months = months or d["hold_months"]
    buy_close = purchase * d["buy_closing_pct"]
    loan = (purchase + repairs) * d["hard_money_ltc"] if financed else 0
    points = loan * d["hard_money_points"]
    interest = loan * d["hard_money_rate"] / 12 * months
    taxes = arv * d["tax_rate"] / 12 * months
    ins = arv / 1000 * d["insurance_yr_per_1k"] / 12 * months
    util = d["utilities_mo"] * months
    sell = arv * d["sell_closing_pct"]
    total_cost = purchase + repairs + buy_close + points + interest + taxes + ins + util + sell
    profit = arv - total_cost
    cash_in = purchase + repairs + buy_close + points + interest + taxes + ins + util - loan
    return {
        "purchase": purchase, "repairs": repairs, "arv": arv, "months": months,
        "costs": {"buy_closing": round(buy_close), "loan_points": round(points), "interest": round(interest),
                  "taxes": round(taxes), "insurance": round(ins), "utilities": round(util),
                  "selling": round(sell)},
        "total_cost": round(total_cost), "net_profit": round(profit),
        "cash_needed": round(cash_in),
        "roi_on_cash": round(profit / cash_in, 3) if cash_in > 0 else None,
        "profit_margin_on_arv": round(profit / arv, 3) if arv else None,
        "max_purchase_for_target_profit": round(purchase + profit - max(30000, arv * 0.10)),
    }


def brrrr(purchase: float, repairs: float, arv: float, rent: float, d: dict | None = None,
          months: float | None = None) -> dict:
    d = d or defaults()
    months = months or d["hold_months"]
    rehab_hold = flip(purchase, arv, repairs, months, d)
    all_in = purchase + repairs + sum(v for k, v in rehab_hold["costs"].items() if k != "selling")
    refi_loan = arv * d["refi_ltv"]
    refi_cost = refi_loan * d["refi_closing_pct"]
    cash_left = all_in + refi_cost - refi_loan
    pi = mortgage_payment(refi_loan, d["refi_rate"], d["refi_years"])
    taxes = arv * d["tax_rate"] / 12
    ins = arv / 1000 * d["insurance_yr_per_1k"] / 12
    reserves = rent * (d["vacancy_pct"] + d["maintenance_pct"] + d["capex_pct"] + d["management_pct"])
    cash_flow = rent - pi - taxes - ins - reserves
    noi = (rent - taxes - ins - reserves) * 12
    return {
        "purchase": purchase, "repairs": repairs, "arv": arv, "rent": rent,
        "all_in_cost": round(all_in), "refi_loan": round(refi_loan), "refi_closing": round(refi_cost),
        "cash_left_in_deal": round(cash_left),
        "monthly": {"principal_interest": round(pi), "taxes": round(taxes), "insurance": round(ins),
                    "vacancy_maint_capex_mgmt": round(reserves), "cash_flow": round(cash_flow)},
        "annual_cash_flow": round(cash_flow * 12),
        "cash_on_cash": (round(cash_flow * 12 / cash_left, 3) if cash_left > 0 else "infinite (all cash out)"),
        "cap_rate_on_arv": round(noi / arv, 3) if arv else None,
        "rent_to_price_1pct_rule": round(rent / all_in, 4) if all_in else None,
        "dscr": round((rent - taxes - ins - reserves) / pi, 2) if pi else None,
    }


def analyze(conn: sqlite3.Connection, p: dict, arv: float | None = None, repairs: float | None = None,
            rehab_level: str = "medium", rent: float | None = None, purchase: float | None = None) -> dict:
    """Run every strategy for a parcel and say which looks best."""
    d = defaults()
    c = comps(conn, p, d)
    arv = arv or c.get("arv")
    sq = p.get("living_sqft") or 0
    repairs = repairs if repairs is not None else repair_estimate(sq, rehab_level, d)
    out = {"parcel_id": p["parcel_id"], "county": p["county"], "comps": c, "arv_used": arv,
           "repairs_used": repairs, "rehab_level": rehab_level, "assumptions": d}
    if not arv:
        out["note"] = "No ARV: pass arv= (from MLS comps) to run the numbers."
        return out
    ws = wholesale(arv, repairs, d=d)
    purchase = purchase or ws["your_max_offer"]
    out["wholesale"] = ws
    out["flip"] = flip(purchase, arv, repairs, d=d)
    if rent:
        out["brrrr"] = brrrr(purchase, repairs, arv, rent, d)
    verdict = []
    if out["flip"]["net_profit"] >= max(25000, arv * 0.10):
        verdict.append("flip works at this price")
    if rent:
        b = out["brrrr"]
        if b["monthly"]["cash_flow"] > 150 and b["cash_left_in_deal"] < 0.2 * purchase:
            verdict.append("BRRRR works (cash flows, most capital recovered)")
        elif b["monthly"]["cash_flow"] <= 0:
            verdict.append(f"BRRRR doesn't cash flow at ${rent:,.0f}/mo rent "
                           f"(${b['monthly']['cash_flow']:,}/mo); needs a lower price or higher rent")
        else:
            verdict.append(f"BRRRR is marginal: ${b['monthly']['cash_flow']:,}/mo cash flow, "
                           f"${b['cash_left_in_deal']:,} left in the deal")
    verdict.append(f"wholesale: offer up to ${ws['your_max_offer']:,} to keep a ${ws['assignment_fee']:,} fee")
    out["verdict"] = verdict
    return out

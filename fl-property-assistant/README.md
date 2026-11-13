# Florida Property Records Assistant

Search Florida property records statewide and find distressed properties (pre-foreclosure,
foreclosure, tax delinquency, liens, code violations, probate / estate-owned, absentee and
out-of-state owners). Includes a plain-English AI assistant powered by Claude.

Runs on your own computer. Data lives in a local SQLite database (`data/flprops.db`).

## How it gets the data (and what it won't do)

| Record | Where it comes from | How it gets in |
|---|---|---|
| Owner, mailing address, site address, value, homestead, year built, last 2 sales, legal description, **all 67 counties** | FL Dept. of Revenue **NAL** parcel files (official, free) | `import-nal` |
| Fresher parcel data, code violations, permits, unsafe structures | County / city **ArcGIS** and **Socrata** open-data APIs | `config/sources.json` + `refresh` |
| Lis pendens, liens, foreclosure cases, probate, evictions | County **clerk** official records / court records | Export or save search results as CSV, then `import-events` |
| Foreclosure and tax-deed auction lists | RealForeclose / RealTaxDeed county calendars | CSV, then `import-events --type foreclosure_sale` / `tax_deed` |
| Delinquent taxes / tax certificates | County **tax collector** delinquent lists | CSV, then `import-events --type tax_delinquent` |
| Owner deaths | Probate filings, plus estate wording in owner names ("EST OF", "HEIRS"); obituary search links per owner | automatic + `import-events --type death` |
| LLC owners | Sunbiz links per entity owner | automatic |

The live client identifies itself, follows `robots.txt`, waits 1 second between requests to a
site and backs off when rate-limited. **It does not get around CAPTCHAs, bot checks, logins or
terms of use.** When a site requires a person, it stops and gives you the link
(`ManualStepRequired`). Many Florida clerk sites are set up this way on purpose. Getting around
them can get your IP blocked and can break computer-access laws. They also have a legal
alternative: most clerks and tax collectors will sell or email bulk lists (lis pendens,
delinquent taxes, probate filings) on request under Florida's public records law (Ch. 119).
Ask the records department. Then import the file you receive.

## Setup

```bash
cd fl-property-assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

1. **Load parcels.** Download NAL files from the FL DOR Property Tax Data Portal:
   https://floridarevenue.com/property/dataportal/ (Tax Roll Data Files, then NAL, then the
   county). Import as many counties as you want:
   ```bash
   python -m flprops import-nal ~/Downloads/NAL23F202501.zip      # Miami-Dade
   ```
2. **Add distress records** you exported from clerk / tax collector / auction sites:
   ```bash
   python -m flprops import-events lis_pendens_oct.csv --type lis_pendens --county Broward
   python -m flprops import-events delinquent_2025.csv --type tax_delinquent --county Broward
   ```
   Columns are detected automatically. The file needs a parcel/folio column or an address
   column; date, case number, amount, party and description are picked up when present.
   Re-importing the same file adds nothing new.
3. **(Optional) Live feeds.** Add open-data endpoints to `config/sources.json`
   (instructions are inside the file), set `"enabled": true`, then
   `python -m flprops refresh`. Schedule it daily with cron to keep up to date.

## Use it

```bash
python -m flprops search "123 Main St" --county Hillsborough
python -m flprops show 30-1234-000-0040 --county Miami-Dade
python -m flprops distressed --county Duval --min-score 40 --csv leads.csv
python -m flprops distressed --zip 33147 --require lis_pendens
python -m flprops links "St. Lucie"          # every official portal for a county
python -m flprops web                         # http://127.0.0.1:8765
```

**AI assistant** (set `ANTHROPIC_API_KEY` first):
```bash
python -m flprops chat
> Which properties in 32208 have a lis pendens and an out-of-state owner?
> Pull up everything on parcel 30-1234-000-0040 and tell me where to verify it.
```
The assistant answers only from your database and gives official links for anything it
doesn't have. It never makes up owners or case numbers.

## Distress score (0–100)

The score adds up points from recorded events and owner signals. Recorded events: lis pendens
or foreclosure (30), auction scheduled (35), tax deed (30), probate (25), tax delinquent (20),
death (20), bankruptcy (20), lien or code violation (12 each, more for repeats). Owner signals:
estate / heirs owner (25), bank / REO owner (15), out-of-state owner (12), absentee owner (8),
held 20+ years (8), and smaller ones. Tiers: **high** is 50 or more, **medium** is 25 or more.
Weights are in `flprops/distress.py`.
The score is a lead-ranking heuristic, not a fact about anyone's situation. Verify everything
at the source before you act on it.

## Notes

- Portal URLs change. Fix or add them under `county_overrides` in `config/sources.json`. Counties
  without a known URL get a search link instead.
- NAL files are annual (preliminary mid-year, final later). Pair them with live feeds and recent
  clerk exports for current status.
- Tests: `python -m pytest -q` (uses synthetic sample data only).

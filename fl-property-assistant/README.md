# Florida Property Records Assistant

Lead generation and deal analysis for wholesaling, fix-and-flip and BRRRR. Search Florida property records statewide and find distressed properties (pre-foreclosure,
foreclosure, tax delinquency, liens, code violations, probate / estate-owned, absentee and
out-of-state owners). Includes a plain-English AI assistant powered by Claude.

Runs on your own computer. Data lives in a local SQLite database (`data/flprops.db`).

## Start here (no coding needed)

1. Install Python 3.10+ from python.org (Windows: tick "Add Python to PATH").
2. Download this folder, then double-click **start.command** (Mac) or **start.bat** (Windows).
   On Linux, run `./start.sh`. The first run sets everything up, then the app opens in your
   browser at http://127.0.0.1:8765.
3. In the app:
   - **Settings**: your name, phone and company (printed on letters), plus deal assumptions.
   - **Import data**: upload your counties' NAL files (link on the home page) and any clerk or
     tax collector lists.
   - **Distressed**: filter, then "Add all to pipeline".
   - **Pipeline**: download the skip-trace upload file, mailing list, letters and dialer list.
   - **Property page**: contacts, deal analyzer (wholesale / flip / BRRR), matching buyers, notes
     and follow-ups.
   - **Buyers**: find active investor buyers in the records, or upload your buyers list.
   - **Assistant**: ask anything in plain English. Needs `ANTHROPIC_API_KEY`, see below.
4. Keep it fresh: schedule **daily.sh** once a day (cron on Mac/Linux, Task Scheduler on Windows
   running `.venv\Scripts\python -m flprops daily`). It pulls live feeds, updates buyers, adds
   new hot leads to the pipeline and lists today's follow-ups.

Docker: `docker build -t flprops . && docker run -p 127.0.0.1:8765:8765 -v flprops:/data flprops`

## The full workflow

| Step | Where | What you get |
|---|---|---|
| 1. Find | Import data, Distressed | Stacked distress lists: pre-foreclosure, foreclosure, tax, liens, code, probate/estate, absentee, out-of-state, long-held |
| 2. Contact | Pipeline, then skip-trace export/import | Owner and LLC decision-maker phones/emails, DNC-scrubbed dialer list |
| 3. Market | Pipeline, then mailing list / letters | Mail-merge CSV and print-ready letters (cash offer, estate, pre-foreclosure, absentee) |
| 4. Analyze | Property, then Deal analyzer | Comps/ARV, wholesale MAO, flip profit, BRRR cash flow, verdict |
| 5. Track | Pipeline, Dashboard | Status, offers, follow-ups, activity log |
| 6. Sell | Property analyzer, Buyers | Matched investor buyers for an assignment or wholetail |

Letter templates live in `config/letters/`; edit them or add your own `.txt` files. The
pre-foreclosure template is written to comply with Florida's foreclosure-rescue law (F.S. 501.1377).
Have a Florida real-estate attorney review your marketing and contracts.

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

## Investor workflow: leads → contacts → numbers → deal

```bash
# 1. Build a list and put it in your pipeline
python -m flprops add-leads --county Duval --min-score 40 --list-name "Duval Nov"

# 2. Skip trace owners and decision makers (CSV round trip works with any provider)
python -m flprops skiptrace-export upload.csv --pipeline      # upload to your skip-trace service
python -m flprops skiptrace-import results.csv                # phones (type, DNC), emails
python -m flprops import-entities sunbiz_officers.csv         # LLC managers/officers -> decision makers
python -m flprops contacts 30-1234-000-0040 --county Miami-Dade

# 3. Run the numbers: comps/ARV, wholesale MAO, fix & flip, BRRRR
python -m flprops analyze 30-1234-000-0040 --county Miami-Dade --rent 2400
python -m flprops analyze 30-1234-000-0040 --arv 340000 --repairs 55000 --rent 2400

# 4. Work the pipeline
python -m flprops lead 30-1234-000-0040 --status offer_made --strategy flip --offer 185000 --follow-up 2026-11-20
python -m flprops pipeline --due
python -m flprops dialer-export calls.csv                     # DNC + litigator numbers removed
```

In the web app, every property page has Contacts, Pipeline and Deal analyzer boxes. Pipeline
also downloads the skip-trace upload file and the dialer list. The assistant can do all of it
from a chat ("run the numbers on 100 Test St at $2,400 rent and move it to offer_made at $150k").

**Skip tracing.** Phones and emails come from a skip-trace provider you pay for, such as
BatchSkipTracing, REISkip, PropStream, BatchData or DataTree. Use the CSV export/import with any
of them, or set up direct API calls under `"skiptrace"` in `config/sources.json`. For direct API
calls, copy the endpoint and field names from your provider's API docs and put the key in the
`SKIPTRACE_API_KEY` environment variable. The tool decides who to trace:
- individual owners: co-owners are split apart, and estate/trust wording is stripped from names
- LLC owners: their managers and officers, once you've imported them (`import-entities`)
- estates: the probate party, once you've imported probate records.

**Decision makers for LLCs.** Sunbiz lists every Florida entity's managers, officers and
registered agent. The FL Division of Corporations offers bulk data downloads. You can also save
officers from Sunbiz detail pages into a CSV with entity name, officer name, title and address.

**Calling and texting rules.** Numbers flagged DNC or litigator by your provider are kept for
reference but left out of dialer exports. You are still responsible for scrubbing against the
National and Florida DNC lists. Florida's Telemarketing Act (FTSA) and the federal TCPA require
prior express written consent before autodialed calls or texts. Wholesalers get sued under these
laws often.

**Deal math defaults.** Defaults are the 70% rule, a $10k assignment fee, $20/$40/$65/$90 per
sq ft rehab, 11% hard money at 2 points, 75% LTV refinance at 7.5%, and 26% of rent reserved for
vacancy, maintenance, capex and management. Change them in `config/sources.json` →
`deal_defaults`. ARV from parcel-roll sales is rough because the roll has no condition data.
Confirm with MLS sold comps before you make an offer.

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

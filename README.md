# Global Top 1000 Stocks

A sortable screener of the ~1000 largest publicly traded companies worldwide —
market cap, P/E, multi-year growth, quality and risk metrics — built entirely
from free data sources and hosted for free on GitHub Pages.

No server, no database, no API keys. A Python pipeline writes a static JSON
file; a React table reads it.

---

## What the data actually covers

This is the part worth reading before trusting any number on the page.

| Metric | 10 years | 20 years | Coverage |
|---|---|---|---|
| Share-price total return (CAGR) | yes | yes | ~all 1000 — Yahoo history reaches back to the 1980s |
| Revenue / net-income growth | yes | no | SEC & IFRS filers only (roughly half the universe) |
| Revenue / net-income growth (3–5y) | yes | — | ~all 1000, via Yahoo's income statements |

**There is no free source for 20 years of global fundamentals.** SEC XBRL
structured data begins around 2009, and covers only companies that file with the
SEC. So the 20-year column is *share-price return*, and the fundamental-growth
columns stop at 10 years. Where a value does not exist it renders as `—`, never
as zero — hover any dash and the table tells you why it is missing.

Two correctness details that are easy to get wrong and are handled here:

- **Growth uses net income, not EPS.** XBRL reports EPS as-reported, unadjusted
  for splits, so an EPS growth rate spanning Apple's 2020 4:1 split or Alphabet's
  2022 20:1 split comes out *negative*. Net income is split-proof.
- **Revenue is merged across XBRL tags.** Filers switched tags when ASC 606 took
  effect in 2018, so no single tag spans the full history. Reading only one tag
  silently loses a decade for companies like Apple and Microsoft.

Delisted issuers (Toyota dropped its US ADR in 2024) leave real but stale figures
on EDGAR; anything whose latest year is more than two years old is discarded
rather than presented as current.

### Cross-listings are kept, deliberately

About 11 of the 1000 rows are a second listing of a company already in the table
— TSMC appears as both `TSM` (US ADR) and `2330.TW`, Alphabet as both share
classes. These are genuinely distinct tradeable securities, so they are kept.

They are *not* auto-merged by name, because that quietly gets real cases wrong:
`9984.T` and `9434.T` normalise to the same string but are SoftBank **Group** and
SoftBank **Corp**, two different companies; likewise Samsung's ordinary
(`005930.KS`) and preferred (`005935.KS`) shares. Collapsing those would be a
worse error than showing a duplicate. Filter or dedupe downstream if you want one
row per company.

---

## Data sources

| Purpose | Source | Notes |
|---|---|---|
| Universe | [SPDR Portfolio MSCI Global Stock Market ETF (SPGM)](https://www.ssga.com/us/en/institutional/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spgm.xlsx) holdings | Official State Street file, no auth. ~2900 global equities with index weights. The top 1000 covers ~94% of global market cap |
| Prices, ratios, classification | Yahoo Finance via `yfinance` | Unofficial and rate-limited — see below |
| Fundamentals (US GAAP + IFRS) | [SEC EDGAR XBRL `companyfacts`](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free, official, no API key. Covers foreign 20-F filers too |
| FX to USD | Yahoo FX pairs | Market cap stored in both local currency and USD |

The iShares ACWI holdings file is *not* used: its download endpoint returns an
HTML interstitial rather than the CSV. SSGA publishes an equivalent file openly.

---

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt

cd web && npm install && cd ..
```

### Set your SEC contact (required for fundamentals)

The SEC requires a User-Agent naming a real contact. Without it you get a 403 and
a ~10 minute IP block, and the pipeline silently falls back to Yahoo's four years.

```bash
export SEC_USER_AGENT="Your Name your@email.com"
```

For GitHub Actions, add this as a repository **variable** named `SEC_USER_AGENT`
(Settings → Secrets and variables → Actions → Variables).

---

## Running it

```bash
python -m pipeline.run                 # full run, resumes from cache
python -m pipeline.run --limit 25      # fast smoke test (~30 seconds)
python -m pipeline.run --max-new 300   # cap fresh fetches this run
python -m pipeline.run --skip-sec      # prices only

cd web && npm run dev                  # http://localhost:5173
```

A cold run takes roughly 15–25 minutes, most of it waiting politely between
Yahoo requests. Everything is cached per symbol under `data/cache/`, so a run
that gets cut short can simply be run again — it picks up where it stopped.

### Why quotes are fetched in two tiers

Yahoo's endpoints differ enormously in cost, and getting this wrong is what
rate-limits the pipeline:

- **Batch quotes** (`v7/finance/quote`) return market cap, P/E, P/B, price,
  currency, exchange and region for **50 symbols in one request** — about 20
  requests for the entire universe. Everything the main table needs comes from
  here, so the site is fully functional on this tier alone.
- **Profiles** (`.info`) are the only source of sector, industry, margins, ROE,
  debt/equity and beta, but cost several requests *per symbol*. So they are
  fetched best-effort, capped at `PROFILE_BUDGET` (200) symbols per run and
  cached for a month. A nightly schedule fills all 1000 within about a week, and
  a failure here never blocks a publish — those columns just show `—` meanwhile.

Use `--profile-budget N` to change how much of that backlog a run works through.

### When Yahoo starts returning 429

`yfinance` is unofficial and rate-limits aggressively. In practice a persistent
stream of 429s is usually a **stale session crumb, not an IP block** — the
pipeline detects this and clears yfinance's cache automatically on the first
rate-limit of a run. To do it by hand:

```bash
rm -rf ~/AppData/Local/py-yfinance     # Windows
rm -rf ~/.cache/py-yfinance            # macOS / Linux
```

---

## How it is kept honest

The pipeline refuses to publish a degraded dataset. `pipeline/schema.py` aborts
the run if fewer than 950 rows were built, if more than 15% are missing market
cap, or if more than 30% are missing a P/E. On abort, the previously published
data is left exactly as it was, so the site goes *stale* rather than *wrong*.

Separately, a field that had a good value and now resolves to null keeps its
previous value and is flagged `stale`, so one bad fetch cannot blank a column.

---

## Tests

```bash
python -m pytest tests/ -q      # CAGR maths, ticker mapping, SEC extraction
cd web && npm test              # sorting: missing values must sort last
```

The sorting test is not incidental. Nulls must fall to the bottom in **both**
sort directions — if they sorted as zero, a company with no 20-year history
would rank alongside one that genuinely returned 0% a year.

---

## Deployment

Two GitHub Actions workflows:

- **`update-data.yml`** — weekday cron (06:20 UTC) plus manual dispatch. Restores
  the symbol cache, runs the pipeline, and commits the refreshed JSON. On failure
  it opens an issue instead of publishing anything.
- **`deploy.yml`** — builds the Vite app and publishes to GitHub Pages.

Keep the repository **public** so Actions minutes stay free; a private repo would
consume its 2000 min/month allowance in roughly two months at this cadence.

> A push made with `GITHUB_TOKEN` deliberately cannot trigger another workflow,
> so `deploy.yml` also listens for `workflow_run` on the data workflow. Without
> that, refreshed data would be committed but never reach the site.

To enable: Settings → Pages → Source → **GitHub Actions**.

---

## Project layout

```
pipeline/
  universe.py       SSGA holdings -> top 1000 + Yahoo ticker resolution
  exchange_map.py   currency -> candidate Yahoo suffixes
  yahoo.py          price history, income statements, FX
  quotes.py         batched quotes + best-effort profile enrichment
  fundamentals.py   SEC EDGAR XBRL, US GAAP + IFRS
  metrics.py        CAGR / drawdown / volatility — pure, unit-tested
  schema.py         row model + publish guard rails
  build.py          merge -> stocks.json / stocks.csv / meta.json
  run.py            CLI orchestrator
web/
  src/columns.tsx   column definitions, formatting, null handling
  src/StockTable.tsx  virtualised sortable table
  public/data/      pipeline output (committed)
```

## Licence and fair use

Yahoo Finance data is for personal, non-commercial use. SSGA and SEC data are
freely redistributable. This project is not affiliated with any of them, and
nothing here is investment advice.

# Global Top 1000 Stocks

A sortable screener of the ~1,000 largest publicly traded companies worldwide,
covering market capitalisation, valuation multiples, multi-year growth, quality
and risk metrics.

The project is built entirely on free, public data sources and requires no
server, database or API keys: a Python pipeline produces static JSON, and a
React table renders it. Both halves are deployed from GitHub Actions to GitHub
Pages.

## Contents

- [Data coverage and limitations](#data-coverage-and-limitations)
- [Data sources](#data-sources)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Data quality controls](#data-quality-controls)
- [Testing](#testing)
- [Deployment](#deployment)
- [Architecture](#architecture)
- [Licence and fair use](#licence-and-fair-use)

## Data coverage and limitations

Coverage varies by metric, and the limits are inherent to the free sources
rather than to the implementation:

| Metric | 10 years | 20 years | Coverage |
|---|---|---|---|
| Share-price total return (CAGR) | Yes | Yes | ~all 1,000; Yahoo history reaches back to the 1980s |
| Revenue / net-income growth | Yes | No | SEC and IFRS filers only, roughly half the universe |
| Revenue / net-income growth (3–5y) | Yes | — | ~all 1,000, via Yahoo income statements |

No free source provides 20 years of global fundamentals. SEC XBRL structured
data begins around 2009 and covers only SEC filers, so the 20-year column
reports share-price return, and fundamental-growth columns stop at 10 years.

Missing values render as an em dash rather than zero, and each dash carries a
tooltip explaining why the value is unavailable.

### Correctness details

Two calculations are easy to get wrong and are handled explicitly:

- **Growth is computed from net income, not EPS.** XBRL reports EPS
  as-reported, unadjusted for splits, so an EPS growth rate spanning Apple's
  2020 4:1 split or Alphabet's 2022 20:1 split resolves negative. Net income is
  unaffected by splits.
- **Revenue is merged across XBRL tags.** Filers changed tags when ASC 606 took
  effect in 2018, so no single tag spans the full history. Reading one tag alone
  drops roughly a decade for issuers such as Apple and Microsoft.

Delisted issuers retain real but stale figures on EDGAR — Toyota withdrew its US
ADR in 2024, for example. Any series whose most recent year is more than two
years old is discarded rather than presented as current.

### Ticker resolution

The holdings file identifies companies by local ticker and currency, which is
not a unique key. `SAN` in euros is Banco Santander in Madrid *and* Sanofi in
Paris; `LIN` in euros is both Linde and Linedata Services. Resolution therefore
probes candidate Yahoo symbols for a company *name*, not merely for a symbol
that returns data, and prefers a candidate whose name agrees with the holding.

A name disagreement is not treated as fatal. Companies rename themselves — DHL
Group answers to "Deutsche Post AG", General Electric to "GE Aerospace" — so
when no candidate agrees, the first that returns a usable quote is accepted, as
before. Those resolutions are written to `data/mismatches.json` as a review
worklist for `overrides.yaml`.

A candidate must carry a market capitalisation to be considered. Several
exchanges expose stub symbols that answer with a company name and nothing else:
`NOVOB.CO` and `MT.PA` are Novo Nordisk and ArcelorMittal by name, with no
price, currency or cap. Resolving to one loses the company at the next stage,
which drops rows without a market cap.

### Universe construction

The universe is drawn from an ETF's *holdings*, not from its index's constituent
list, and the distinction has consequences. SPGM is a sampled portfolio: it holds
roughly 2,900 of the index's names rather than all of them, and index weights are
free-float adjusted. A company with a small public float can therefore be absent
from the file entirely, at which point no amount of ticker resolution will find
it. Saudi Aramco — ninth largest company in the world, roughly 2% float — is not
in the file at all.

`pipeline/supplemental.yaml` closes that gap. It is a short, manually curated
list of companies that belong in a global top 1,000 but are absent from the
holdings file. Entries carry a Yahoo symbol directly, so they skip ticker
resolution, and no index weight, so `index_weight` publishes as null rather than
a fabricated zero. Everything downstream — quotes, price history, fundamentals,
deduplication and ranking — treats them identically to index-derived rows.

Because these rows have no weight, they are exempt from the weight-ordered
truncation to 1,000 and take their slots from the bottom of the weighted set. Each
entry therefore displaces a genuine constituent, so the list should stay short and
every entry should be justified in place. A company that appears to be missing is
more often mis-resolved than absent; verify against the holdings file before
adding it.

### Duplicate listings

The source index lists several companies more than once: TSMC as both its Taiwan
line and a US ADR, Alphabet as two share classes, Rio Tinto as a dual-listed
pair. Yahoo reports whole-company market capitalisation on each line, so
retaining all of them would double-count the company and allow it to occupy two
ranks simultaneously.

`universe.deduplicate` collapses these, preferring an ordinary share over a
depositary receipt and the larger listing otherwise. Dropped tickers are retained
on the surviving row in the `also_listed_as` field.

Matching is deliberately conservative, as name similarity produces false
positives: `9984.T` and `9434.T` are SoftBank Group and SoftBank Corp
respectively — separate companies — and both are correctly retained.

## Data sources

| Purpose | Source | Notes |
|---|---|---|
| Universe | [SPDR Portfolio MSCI Global Stock Market ETF (SPGM)](https://www.ssga.com/us/en/institutional/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spgm.xlsx) holdings | Official State Street file, no authentication. ~2,900 global equities with index weights; the top 1,000 covers ~94% of global market capitalisation |
| Prices, ratios, classification | Yahoo Finance via `yfinance` | Unofficial and rate-limited; see [Rate limiting](#rate-limiting) |
| Fundamentals (US GAAP and IFRS) | [SEC EDGAR XBRL `companyfacts`](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free, official, no API key; includes foreign 20-F filers |
| FX to USD | Yahoo FX pairs | Market capitalisation is stored in both local currency and USD |

The iShares ACWI holdings file is not used: its download endpoint returns an HTML
interstitial rather than CSV. SSGA publishes an equivalent file openly.

## Requirements

- Python 3.11 or later
- Node.js 22 or later (Vite 7 and React 19; the test suite strips TypeScript natively)

## Installation

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
.venv\Scripts\activate.bat      # cmd / PowerShell
source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
cd web && npm install && cd ..
```

Virtual environment activation applies only to the shell it is run in, indicated
by a `(.venv)` prefix in the prompt. Without it, `python` on Windows commonly
resolves to the Windows Store stub and the pipeline fails with
`ModuleNotFoundError: No module named 'numpy'`.

The interpreter may also be invoked by path, which requires no activation:

```bash
.venv/Scripts/python.exe -m pipeline.run     # Windows
.venv/bin/python -m pipeline.run             # macOS / Linux
```

Re-running `python -m venv .venv` will not repair a broken activation: Windows
cannot overwrite `python.exe` while the environment exists, and the command fails
with `[Errno 13] Permission denied`. Remove `.venv` first to rebuild it.

## Configuration

### SEC contact string

SEC EDGAR requires a `User-Agent` header identifying a real contact. This is a
header value rather than a credential — there is no registration — but requests
without one receive HTTP 403 and a roughly 10-minute IP block.

```bash
export SEC_USER_AGENT="Your Name your@email.com"
```

The variable applies to the current shell only. Add it to your shell profile to
persist it, or set it per invocation:

```bash
SEC_USER_AGENT="Your Name your@email.com" python -m pipeline.run
```

If the variable is unset, the run aborts rather than publishing with the 10-year
revenue and profit columns empty. Pass `--skip-sec` to omit those columns
deliberately.

For GitHub Actions, add `SEC_USER_AGENT` as a repository variable under
Settings → Secrets and variables → Actions → Variables.

### Tuning

Pipeline constants are defined in `pipeline/config.py`, including the universe
size (1,000), per-source cache TTLs, request concurrency, retry and backoff
settings, and the publish thresholds described under
[Data quality controls](#data-quality-controls).

## Usage

With the virtual environment active, or substituting `.venv/Scripts/python.exe`
for `python`:

```bash
python -m pipeline.run                  # full run, resumes from cache
python -m pipeline.run --limit 25       # smoke test, approximately 30 seconds
python -m pipeline.run --max-new 300    # cap fresh fetches for this run
python -m pipeline.run --profile-budget 400   # widen the profile backlog
python -m pipeline.run --skip-sec       # prices only
python -m pipeline.run --refresh-holdings     # re-download the SSGA file
```

To serve the frontend locally against the generated data:

```bash
cd web && npm run dev                   # http://localhost:5173
```

A cold run takes approximately 15–25 minutes, the majority of which is the
enforced delay between Yahoo requests. All responses are cached per symbol under
`data/cache/`, so an interrupted run can be repeated and resumes from where it
stopped.

### Two-tier quote fetching

Yahoo's endpoints differ substantially in cost per symbol, and the pipeline
treats them differently:

- **Batch quotes** (`v7/finance/quote`) return market capitalisation, P/E, P/B,
  price, currency, exchange and region for 50 symbols per request —
  approximately 20 requests for the entire universe. The main table is fully
  populated from this tier alone.
- **Profiles** (`.info`) are the only source of sector, industry, margins, ROE,
  debt/equity and beta, but cost several requests per symbol. They are therefore
  fetched best-effort, capped at `PROFILE_BUDGET` (200) symbols per run and
  cached for a month. A nightly schedule completes the universe in about a week,
  and a failure at this tier never blocks a publish; the affected columns show an
  em dash in the interim.

### Rate limiting

`yfinance` is an unofficial client and is rate-limited aggressively. A sustained
stream of HTTP 429 responses usually indicates a stale session crumb rather than
an IP block. The pipeline detects this and clears the yfinance cache on the first
rate-limited request of a run. To clear it manually:

```bash
rm -rf ~/AppData/Local/py-yfinance     # Windows
rm -rf ~/.cache/py-yfinance            # macOS / Linux
```

## Data quality controls

The pipeline is designed to fail rather than publish a degraded dataset. When
validation fails, previously published data is left untouched, so the site
becomes stale rather than incorrect.

`pipeline/schema.py` aborts the run if fewer than 950 rows were built, if more
than 15% are missing market capitalisation, or if more than 30% are missing a
trailing P/E. Three further checks target errors that remain plausible-looking in
the output and so cannot be caught by inspection:

| Check | Failure it prevents |
|---|---|
| Rank order | Rows are selected by index weight but must be ranked by market capitalisation. Weight is free-float adjusted, so the orders differ materially — publishing weight order placed SpaceX at rank 338 with the seventh-largest market capitalisation globally. Validation requires a dense 1..N sequence in descending order of market capitalisation. |
| Rate units | Yahoo returns `dividendYield` and `debtToEquity` as percentages, while margins, returns and ROE are fractions. All published rates are fractions; mixing conventions rendered NVIDIA's 0.44% dividend yield as 44%. Validation fails if a source changes convention. |
| Minor units | Market capitalisation for pence, agorot and fils-quoted listings is reconstructed from price × shares, independently of the conversion under test. |

Price history is filtered before any metric reads it. Yahoo's adjusted closes
can be negative on long histories, where decades of dividend adjustments are
applied to a small early price; one such bar produced a reported maximum
drawdown of −747%. A small number of tickers also carry individual bars quoted in
the wrong unit, which produced an annualised volatility of 537% for one issuer. A
bar is dropped only when it is non-positive, or when it diverges sharply from
both neighbouring bars while those neighbours agree with each other — so a
genuine crash, which persists, is retained.

Separately, a field that previously held a valid value and now resolves to null
retains the earlier value and is flagged `stale`, so a single failed fetch cannot
blank a column.

### Derived price/earnings

Yahoo omits `trailingPE` wherever it has no EPS for a listing, and for some
markets it never has one: every Korean line in the universe returns
`epsTrailingTwelveMonths` null, so Samsung, SK hynix and Hyundai published
without a P/E while their peers elsewhere had one.

`build._derive_pe` fills those from market capitalisation over the most recent
annual net income — the same ratio by another route — for 21 rows. Three guards
apply, because a wrong P/E is worse than an absent one: the statement must be
denominated in the currency the market cap is denominated in (a London listing
reporting in dollars would otherwise divide pounds by dollars and publish a
plausible-looking number wrong by the exchange rate), the fiscal year must be
recent, and net income must be positive and not so small that the ratio
explodes. Rows that fail a guard keep an empty cell.

A derived value is an annual figure rather than a trailing twelve months, so it
will not exactly match a P/E quoted elsewhere. It carries `trailing_pe_derived`
and renders underlined, so it is never silently mixed with a quoted one. The
remaining 101 blanks are mostly genuine: 82 are lossmaking companies, for which
the ratio is undefined.

## Testing

```bash
python -m pytest tests/ -q      # metrics, ticker mapping, SEC extraction,
                                # cache and retry machinery, publish guards
cd web && npm test              # sort ordering of missing values,
                                # column registry and preset integrity
```

The frontend sort test is not incidental: nulls must sort last in both
directions. Were they treated as zero, a company with no 20-year history would
rank alongside one that genuinely returned 0% per annum.

### Benchmarks

An absolute number is hard to judge, so the table carries a summary row, the
detail drawer shows sector medians beside each metric, and an optional shading
mode colours each cell by where it sits within its own sector. All three are
computed in the browser from the published JSON; nothing is precomputed.

The statistic is a **median, never a mean**. These distributions are
right-skewed with unbounded tails — trailing P/E reaches 4368, price/sales 3329
— so the mean sits 1.8-3.1x above the median across the valuation columns.
EV/EBITDA settles it on its own: loss-making companies drag its mean to -9.4,
against a median of 14.5. An "average EV/EBITDA" of -9.4x would be worse than
showing nothing.

The comparison is **sector-relative**, because a universe-wide figure inverts
the answer. Sector medians span 3-8x — P/E from Energy's 14.2 to Technology's
45.0, P/B from Real Estate's 1.1 to Technology's 8.7. Samsung's P/E of 38.1
reads 69% expensive against the universe median of 22.6, and cheap against the
Technology median it actually competes with.

Three details are load-bearing rather than cosmetic:

| Rule | Reason |
|---|---|
| `price` carries no statistic | The column holds 29 listing currencies at once. An average of KRW, JPY and USD is meaningless, and would look entirely plausible sitting under the table. |
| Fewer than `MIN_SAMPLE` (10) values shows a dash | A median of five is not a benchmark. Filtering to one row shows dashes, not that row's own values relabelled as medians. |
| Shading is blue/amber, not green/red | Green and red already mean positive and negative return in the same rows. One colour must not carry two meanings. Shading marks position within a sector, never quality — a low P/E is not "good". |

Shading ranks by percentile within the sector rather than distance from the
median: a z-score on a distribution whose maximum is 4368 tells the reader
nothing, while a percentile is outlier-immune. Every figure discloses the `n`
it was computed from, since coverage runs from 100% to 35% by column.

The column registry has a single definition for the same reason the row schema
does. `src/columns.def.ts` declares each column once — its group, format and
width — and the presets are expressed as group membership rather than as
hand-written id lists, so a column added to a group joins its preset
automatically. The accompanying test rejects a preset that names a column the
table does not define; when the two were maintained separately, such a preset
silently hid nothing and left the previous preset's columns on screen.

## Deployment

Two GitHub Actions workflows:

- **`update-data.yml`** — weekday cron (06:20 UTC) and manual dispatch. Restores
  the symbol cache, runs the pipeline and commits the refreshed JSON. On failure
  it opens an issue rather than publishing.
- **`deploy.yml`** — builds the Vite application and publishes to GitHub Pages.

Enable Pages under Settings → Pages → Source → GitHub Actions.

The repository should remain public so that Actions minutes are unmetered; at
this cadence a private repository would exhaust its 2,000 minute monthly
allowance in approximately two months.

> A push made with `GITHUB_TOKEN` cannot trigger another workflow. `deploy.yml`
> therefore also listens for `workflow_run` on the data workflow; without it,
> refreshed data would be committed but never published.

## Architecture

```
pipeline/
  universe.py       SSGA holdings -> top 1000 + Yahoo ticker resolution
  exchange_map.py   currency -> candidate Yahoo suffixes
  overrides.yaml    manual ticker fixes for holdings the mapper cannot resolve
  supplemental.yaml curated constituents absent from the holdings file
  fetcher.py        disk cache and retry policy, shared by every remote source
  yahoo.py          price history, income statements, FX
  quotes.py         batched quotes and best-effort profile enrichment
  fundamentals.py   SEC EDGAR XBRL, US GAAP and IFRS
  metrics.py        CAGR, drawdown, volatility, price filtering (pure functions)
  currency.py       minor units (pence, agorot, fils) and USD conversion
  schema.py         row model and publish guard rails
  build.py          merge -> stocks.json / stocks.csv / meta.json
  emit_types.py     schema.Stock -> web/src/types.ts
  run.py            CLI orchestrator
web/
  src/types.ts      generated; do not edit
  src/columns.def.ts  the column taxonomy: groups, presets, widths, aggregates
  src/stats.ts      medians, sector benchmarks and percentiles
  src/columns.tsx   TanStack definitions built from it, formatting, null handling
  src/StockTable.tsx  virtualised sortable table, two-tier headers, pinned spine
  public/data/      pipeline output (committed)
```

Two boundaries are load-bearing:

**Transport is separated from source.** Price history, income statements, batch
quotes, profiles and SEC facts share one workflow: check a per-symbol cache,
fetch what is missing in chunks or across a small thread pool, retry throttled
requests, abandon a stage the source is clearly refusing, and write each success
back to disk. That logic lives once in `fetcher.py`. The source modules retain
only what is specific to their source — endpoints, field names and response
parsing.

**The row schema has a single definition.** `web/src/types.ts` is generated from
`schema.Stock`, and `tests/test_types.py` fails if it is out of date. Regenerate
after any schema change:

```bash
python -m pipeline.emit_types
```

## Licence and fair use

Yahoo Finance data is licensed for personal, non-commercial use. SSGA and SEC
data are freely redistributable. This project is not affiliated with any of these
providers, and nothing here constitutes investment advice.

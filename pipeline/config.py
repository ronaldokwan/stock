"""Shared configuration, paths and constants for the pipeline."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
SEED = DATA / "seed"
CACHE = DATA / "cache"
HISTORY_CACHE = CACHE / "history"
OUT = ROOT / "web" / "public" / "data"

for _d in (SEED, CACHE, HISTORY_CACHE, OUT):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- universe ---
# SPDR Portfolio MSCI Global Stock Market ETF (SPGM) daily holdings.
# Official State Street issuer file: free, no auth, ~3000 global equities with
# index weights. Top 1000 by weight covers ~94% of global market cap.
SPGM_URL = (
    "https://www.ssga.com/us/en/institutional/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-spgm.xlsx"
)
SEED_HOLDINGS = SEED / "spgm_holdings.xlsx"

TARGET_UNIVERSE = 1000
# Resolve more than we need so failed ticker mappings don't shrink the table.
RESOLVE_BUFFER = 1150

# ------------------------------------------------------------------ yahoo ---
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Yahoo rate-limits hard and without warning. These settings are deliberately
# conservative: a full cold run takes ~40-60 minutes, but it completes. Data is
# cached per symbol, so subsequent runs only fetch what changed.
YF_THREADS = 4          # bulk-download concurrency
YF_QUOTE_THREADS = 2    # .info is the heaviest endpoint; keep this very low
YF_CHUNK = 50           # symbols per bulk history request
YF_PAUSE = 1.5          # base seconds between chunks (jittered up to 2x)
YF_RETRIES = 4          # attempts before giving up on a chunk/symbol
YF_BACKOFF_BASE = 20.0  # seconds; doubles each retry (20, 40, 80...)
YF_BREAKER_CHUNKS = 3   # consecutive failed history chunks before stopping
YF_BREAKER_QUOTES = 25  # consecutive rate-limited quotes before stopping

QUOTE_BATCH = 50        # symbols per batched v7/finance/quote request

# Sector/industry/ratios need the expensive per-symbol .info endpoint, so each
# run only tops up a slice of the universe. A nightly schedule covers all 1000
# within about a week, and the table works fully without it in the meantime.
PROFILE_BUDGET = 200

# How long cached data stays fresh. History barely moves; quotes move daily;
# sector and industry effectively never change.
HISTORY_TTL = 7 * 86_400
QUOTE_TTL = 20 * 3_600
PROFILE_TTL = 30 * 86_400
INCOME_TTL = 14 * 86_400

# -------------------------------------------------------------------- sec ---
# The SEC requires a descriptive User-Agent naming a real contact. A generic UA
# gets a 403 and a ~10 minute IP block. Set this before running:
#   export SEC_USER_AGENT="Your Name your@email.com"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_RATE_LIMIT = 5.0    # requests/sec (SEC allows 10; we stay well under)

# ------------------------------------------------------------ validation ---
# The pipeline aborts rather than publishing a degraded dataset.
MIN_ROWS = 950
MAX_MISSING_MARKET_CAP = 0.15
MAX_MISSING_PE = 0.30

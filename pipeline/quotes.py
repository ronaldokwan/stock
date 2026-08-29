"""Stage 3: quotes.

Split into two tiers, because Yahoo's endpoints differ enormously in cost:

* **Batch quotes** (``v7/finance/quote``) return market cap, P/E, P/B, price,
  currency, exchange and region for ~50 symbols in a *single* request. Twenty
  requests cover the whole universe. Everything the main table needs comes from
  here, so the site is fully functional on this tier alone.

* **Profile enrichment** (``.info``) is the only source for sector, industry,
  margins, ROE, debt/equity and beta -- but it costs several HTTP requests *per
  symbol*, which is exactly what gets the pipeline rate-limited. So it runs
  best-effort, capped per run, cached for a month, and its failure never blocks
  a publish. Successive nightly runs fill it in.
"""
from __future__ import annotations

import logging

import yfinance as yf
from yfinance.data import YfData

from . import config as C
from . import fetcher
from .fetcher import Breaker
from .yahoo import reset_session

log = logging.getLogger(__name__)

QUOTE_CACHE = C.CACHE / "quotes"
PROFILE_CACHE = C.CACHE / "profiles"
for _d in (QUOTE_CACHE, PROFILE_CACHE):
    _d.mkdir(parents=True, exist_ok=True)

QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"

# Fields taken from the batch endpoint.
_BATCH_FIELDS = (
    "longName", "shortName", "marketCap", "currency", "financialCurrency",
    "trailingPE", "forwardPE", "priceToBook", "dividendYield", "bookValue",
    "sharesOutstanding", "epsTrailingTwelveMonths", "regularMarketPrice",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fullExchangeName", "exchange",
    "region", "quoteType",
)
# Fields only ``.info`` provides.
_PROFILE_FIELDS = (
    "sector", "industry", "country", "profitMargins", "returnOnEquity",
    "debtToEquity", "beta", "priceToSalesTrailing12Months", "enterpriseToEbitda",
    "revenueGrowth", "earningsGrowth",
)

# Yahoo returns a region code; the table wants a readable country.
REGION_NAMES = {
    "US": "United States", "JP": "Japan", "GB": "United Kingdom", "CN": "China",
    "HK": "Hong Kong", "TW": "Taiwan", "KR": "South Korea", "IN": "India",
    "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France",
    "CH": "Switzerland", "NL": "Netherlands", "SE": "Sweden", "DK": "Denmark",
    "NO": "Norway", "FI": "Finland", "IT": "Italy", "ES": "Spain",
    "BE": "Belgium", "IE": "Ireland", "AT": "Austria", "PT": "Portugal",
    "BR": "Brazil", "MX": "Mexico", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "AR": "Argentina", "ZA": "South Africa", "SA": "Saudi Arabia",
    "AE": "United Arab Emirates", "QA": "Qatar", "KW": "Kuwait", "IL": "Israel",
    "TR": "Turkey", "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand",
    "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam", "NZ": "New Zealand",
    "PL": "Poland", "CZ": "Czechia", "HU": "Hungary", "GR": "Greece", "EG": "Egypt",
}


# ------------------------------------------------------------- batch quotes
def fetch_batch(symbols: list[str], max_new: int | None = None) -> dict[str, dict]:
    """Core quote fields for every symbol, ~50 symbols per HTTP request."""
    out, missing = fetcher.partition(symbols, QUOTE_CACHE, C.QUOTE_TTL)

    if max_new is not None:
        missing = missing[:max_new]
    log.info("quotes: %d cached, %d to fetch (%d requests)",
             len(out), len(missing), -(-len(missing) // C.QUOTE_BATCH))
    if not missing:
        return out

    data = YfData()

    def one_chunk(chunk: list[str]) -> list[dict]:
        payload = data.get_raw_json(QUOTE_URL, params={"symbols": ",".join(chunk)})
        return (payload or {}).get("quoteResponse", {}).get("result", []) or []

    breaker = Breaker(C.YF_BREAKER_CHUNKS)
    for results in fetcher.chunked(missing, C.QUOTE_BATCH, one_chunk,
                                   breaker=breaker, label="quotes",
                                   on_rate_limit=reset_session):
        for item in results or []:
            symbol = item.get("symbol")
            if not symbol:
                continue
            row = {k: item.get(k) for k in _BATCH_FIELDS}
            row["country"] = REGION_NAMES.get(str(item.get("region") or "").upper())
            out[symbol] = row
            fetcher.store(symbol, row, QUOTE_CACHE)

    log.info("quotes: %d symbols have data", len(out))
    return out


def identify(symbols: list[str]) -> dict[str, str]:
    """Symbol -> company name, for symbols Yahoo serves a *usable* quote for.

    This is what ticker resolution probes with. Existence alone cannot resolve a
    ticker, because a ticker plus a currency is not unique -- ``SAN`` in euros is
    Banco Santander in Madrid and Sanofi in Paris, and both return data.

    Carrying a market cap is part of being usable. Several exchanges expose stub
    symbols that answer with a company name but no cap, price or currency:
    ``NOVOB.CO`` and ``MT.PA`` are Novo Nordisk and ArcelorMittal by name and
    empty in every other respect. Resolving to one of those loses the company
    entirely at the next stage, which drops rows with no market cap. Excluding
    them here applies that same rule while there is still another candidate to
    fall back to.

    The batch quote endpoint answers 50 symbols per request and carries both the
    name and the cap, so this is cheaper than the 5-day history download it
    replaced, and it warms the quote cache that stage 3 reads immediately after.
    """
    if not symbols:
        return {}
    got = fetch_batch(symbols)
    return {s: (q.get("longName") or q.get("shortName") or "")
            for s, q in got.items() if q.get("marketCap")}


# ------------------------------------------------------- profile enrichment
def fetch_profiles(symbols: list[str], max_new: int | None = None) -> dict[str, dict]:
    """Sector, industry and quality ratios. Best-effort and never fatal.

    Capped per run and cached for a month, so a nightly schedule fills the whole
    universe in over a week without ever tripping Yahoo's limits hard.
    """
    out, missing = fetcher.partition(symbols, PROFILE_CACHE, C.PROFILE_TTL)

    budget = max_new if max_new is not None else C.PROFILE_BUDGET
    missing = missing[:budget]
    log.info("profiles: %d cached, %d to fetch this run (budget %d)",
             len(out), len(missing), budget)
    if not missing:
        return out

    def one(symbol: str):
        try:
            info = yf.Ticker(symbol).info or {}
            if not info:
                return {}
            return {k: info.get(k) for k in _PROFILE_FIELDS}
        except Exception as e:                       # noqa: BLE001
            return None if fetcher.is_rate_limit(e) else {}

    breaker = Breaker(C.YF_BREAKER_QUOTES)
    for symbol, profile in fetcher.each(missing, one, breaker=breaker,
                                        workers=C.YF_QUOTE_THREADS,
                                        label="profiles"):
        out[symbol] = profile
        fetcher.store(symbol, profile, PROFILE_CACHE)

    if breaker.tripped:
        log.warning("profiles: rate limited, stopped early. The table still "
                    "works; sector and quality columns fill in on later runs.")
    log.info("profiles: %d symbols have data", len(out))
    return out


def merge(batch: dict[str, dict], profiles: dict[str, dict]) -> dict[str, dict]:
    """Combine both tiers into one quote record per symbol."""
    out: dict[str, dict] = {}
    for sym in set(batch) | set(profiles):
        row = dict(batch.get(sym) or {})
        row.update({k: v for k, v in (profiles.get(sym) or {}).items() if v is not None})
        out[sym] = row
    return out

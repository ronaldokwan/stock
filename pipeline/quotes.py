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

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import yfinance as yf
from yfinance.data import YfData

from . import config as C
from .yahoo import _Breaker, _fresh, _is_rate_limit, _pause, _safe, reset_session

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


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ------------------------------------------------------------- batch quotes
def fetch_batch(symbols: list[str], max_new: int | None = None) -> dict[str, dict]:
    """Core quote fields for every symbol, ~50 symbols per HTTP request."""
    out: dict[str, dict] = {}
    missing: list[str] = []
    for s in symbols:
        path = QUOTE_CACHE / f"{_safe(s)}.json"
        if _fresh(path, C.QUOTE_TTL):
            try:
                out[s] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except Exception:                        # noqa: BLE001
                pass
        missing.append(s)

    if max_new is not None:
        missing = missing[:max_new]
    log.info("quotes: %d cached, %d to fetch (%d requests)",
             len(out), len(missing), -(-len(missing) // C.QUOTE_BATCH))
    if not missing:
        return out

    data = YfData()
    breaker = _Breaker(C.YF_BREAKER_CHUNKS)

    for n, chunk in enumerate(_chunks(missing, C.QUOTE_BATCH), 1):
        if breaker.tripped:
            log.warning("quotes: rate limited repeatedly, stopping early "
                        "(%d symbols left for the next run)",
                        len(missing) - (n - 1) * C.QUOTE_BATCH)
            break

        results = []
        for attempt in range(C.YF_RETRIES):
            try:
                payload = data.get_raw_json(QUOTE_URL,
                                            params={"symbols": ",".join(chunk)})
                results = (payload or {}).get("quoteResponse", {}).get("result", []) or []
                break
            except Exception as e:                   # noqa: BLE001
                if _is_rate_limit(e) and attempt < C.YF_RETRIES - 1:
                    if attempt == 0:
                        reset_session()
                    time.sleep(C.YF_BACKOFF_BASE * (2 ** attempt))
                    continue
                log.warning("quote batch %d failed: %s", n, str(e)[:110])
                break

        breaker.record(limited=not results)
        for item in results:
            sym = item.get("symbol")
            if not sym:
                continue
            row = {k: item.get(k) for k in _BATCH_FIELDS}
            row["country"] = REGION_NAMES.get(str(item.get("region") or "").upper())
            out[sym] = row
            try:
                (QUOTE_CACHE / f"{_safe(sym)}.json").write_text(
                    json.dumps(row), encoding="utf-8")
            except Exception:                        # noqa: BLE001
                pass

        log.info("  quotes %d/%d fetched (%d total)",
                 min(n * C.QUOTE_BATCH, len(missing)), len(missing), len(out))
        _pause()

    log.info("quotes: %d symbols have data", len(out))
    return out


# ------------------------------------------------------- profile enrichment
def fetch_profiles(symbols: list[str], max_new: int | None = None) -> dict[str, dict]:
    """Sector, industry and quality ratios. Best-effort and never fatal.

    Capped per run and cached for a month, so a nightly schedule fills the whole
    universe in over a week without ever tripping Yahoo's limits hard.
    """
    out: dict[str, dict] = {}
    missing: list[str] = []
    for s in symbols:
        path = PROFILE_CACHE / f"{_safe(s)}.json"
        if _fresh(path, C.PROFILE_TTL):
            try:
                out[s] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except Exception:                        # noqa: BLE001
                pass
        missing.append(s)

    budget = max_new if max_new is not None else C.PROFILE_BUDGET
    missing = missing[:budget]
    log.info("profiles: %d cached, %d to fetch this run (budget %d)",
             len(out), len(missing), budget)
    if not missing:
        return out

    breaker = _Breaker(C.YF_BREAKER_QUOTES)
    lock = Lock()
    done = 0

    def one(symbol: str):
        if breaker.tripped:
            return symbol, None
        try:
            info = yf.Ticker(symbol).info or {}
            if not info:
                return symbol, {}
            return symbol, {k: info.get(k) for k in _PROFILE_FIELDS}
        except Exception as e:                       # noqa: BLE001
            return symbol, (None if _is_rate_limit(e) else {})

    with ThreadPoolExecutor(max_workers=C.YF_QUOTE_THREADS) as pool:
        for fut in as_completed([pool.submit(one, s) for s in missing]):
            sym, profile = fut.result()
            breaker.record(limited=profile is None)
            if profile:
                out[sym] = profile
                try:
                    (PROFILE_CACHE / f"{_safe(sym)}.json").write_text(
                        json.dumps(profile), encoding="utf-8")
                except Exception:                    # noqa: BLE001
                    pass
            with lock:
                done += 1
                if done % 50 == 0:
                    log.info("  profiles %d/%d", done, len(missing))

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

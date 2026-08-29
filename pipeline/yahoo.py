"""Yahoo Finance access layer: symbol probing, price history and quote fields.

Everything Yahoo-specific lives here so it can be swapped wholesale (for stooq
or a paid feed) without touching the rest of the pipeline. The caching, retry
and circuit-breaker mechanics this leans on are generic and live in ``fetcher``.

Yahoo is an unofficial, undocumented source that rate-limits aggressively, so
this module is built to be *resumable* rather than fast: every symbol's data is
cached on disk, a run only fetches what is missing or stale, and a run that
starts getting 429s stops early instead of burning through the remaining
symbols collecting nothing. Several modest runs converge on a full dataset.
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from threading import Lock

import pandas as pd
import yfinance as yf

from . import config as C
from . import fetcher
from .fetcher import Breaker
from .metrics import sanitise_prices

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

INCOME_CACHE = C.CACHE / "income"
INCOME_CACHE.mkdir(parents=True, exist_ok=True)


def _extract_close(df: pd.DataFrame, symbols: list[str]) -> dict[str, pd.Series]:
    """Normalise yfinance's single- vs multi-symbol frame shapes."""
    if df is None or df.empty:
        return {}
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" not in df.columns.get_level_values(0):
            return {}
        close = df["Close"]
    else:
        if "Close" not in df.columns:
            return {}
        close = df[["Close"]].rename(columns={"Close": symbols[0]})
    out = {}
    for s in symbols:
        if s in close.columns:
            series = close[s].dropna()
            if not series.empty:
                out[s] = series
    return out


# ------------------------------------------------------------------- session
_session_reset = False
_reset_lock = Lock()


def reset_session() -> bool:
    """Drop yfinance's cached cookie/crumb.

    A persistent stream of 429s is usually a stale crumb rather than a real IP
    block: clearing this directory restores access immediately, where waiting
    does not. Done at most once per run so we don't thrash it.
    """
    global _session_reset
    with _reset_lock:
        if _session_reset:
            return False
        _session_reset = True
    import shutil

    targets = []
    try:
        from yfinance import cache as yf_cache

        yf_cache._TzDBManager.close_db()
        targets.append(yf_cache._TzDBManager.get_location())
    except Exception as e:                           # noqa: BLE001
        log.debug("could not query yfinance cache location: %s", e)

    # Fallbacks for the platform default, in case the internal API moves.
    targets += [
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "py-yfinance"),
        os.path.join(os.path.expanduser("~"), ".cache", "py-yfinance"),
    ]

    for target in targets:
        if target and os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            log.info("    cleared yfinance session cache (stale crumb)")
            return True
    return False


def _download(chunk: list[str], **kwargs) -> dict[str, pd.Series]:
    """One bulk download, reduced to ``{symbol: close series}``."""
    df = yf.download(chunk, threads=C.YF_THREADS, progress=False,
                     auto_adjust=True, **kwargs)
    return _extract_close(df, chunk)


# --------------------------------------------------------------------- probe
def probe(symbols: list[str]) -> set[str]:
    """Return the subset of ``symbols`` Yahoo actually serves data for.

    Only called for genuinely ambiguous tickers (mostly EUR listings that could
    sit on any of a dozen exchanges). Unambiguous symbols skip this entirely and
    are validated for free by the history fetch.
    """
    valid: set[str] = set()
    if not symbols:
        return valid

    breaker = Breaker(C.YF_BREAKER_CHUNKS)
    for got in fetcher.chunked(
            symbols, C.YF_CHUNK,
            lambda chunk: _download(chunk, period="5d", interval="1d"),
            breaker=breaker, label="probed", on_rate_limit=reset_session):
        valid |= set(got or {})
    log.info("  probe: %d/%d valid", len(valid), len(symbols))
    return valid


# ------------------------------------------------------------------- history
def fetch_history(symbols: list[str], max_new: int | None = None) -> dict[str, pd.Series]:
    """Monthly adjusted-close history (max range), cached to Parquet per symbol.

    Monthly bars are all the CAGR maths needs and are ~10x smaller than daily.
    """
    cached, missing = fetcher.partition(
        symbols, C.HISTORY_CACHE, C.HISTORY_TTL, suffix=".parquet",
        load=lambda path: pd.read_parquet(path)["close"])

    if max_new is not None:
        missing = missing[:max_new]
    log.info("history: %d cached, %d to fetch", len(cached), len(missing))

    breaker = Breaker(C.YF_BREAKER_CHUNKS)
    for got in fetcher.chunked(
            missing, C.YF_CHUNK,
            lambda chunk: _download(chunk, period="max", interval="1mo"),
            breaker=breaker, label="history", on_rate_limit=reset_session):
        for symbol, series in (got or {}).items():
            cached[symbol] = series
            fetcher.store(symbol, series, C.HISTORY_CACHE, suffix=".parquet",
                          dump=lambda p, v: v.rename("close").to_frame().to_parquet(p))

    # Cleaned on the way out rather than before caching, so the Parquet files
    # stay a faithful copy of what Yahoo served and a change to the filter takes
    # effect on the next run instead of needing the whole cache re-fetched.
    cleaned, dropped = {}, 0
    for symbol, series in cached.items():
        out = sanitise_prices(series)
        dropped += len(series.dropna()) - len(out)
        cleaned[symbol] = out
    if dropped:
        log.info("history: dropped %d impossible price bars "
                 "(non-positive or isolated 100x)", dropped)
    return cleaned


# ------------------------------------------------------------ income history
def fetch_income_history(symbols: list[str], max_new: int | None = None) -> dict[str, dict]:
    """Yahoo annual income statements: 4-5y revenue/net-income fallback.

    Net income rather than EPS for the same split-adjustment reason as the SEC
    path -- see the note on NET_INCOME_TAGS in fundamentals.py.
    """
    out, missing = fetcher.partition(symbols, INCOME_CACHE, C.INCOME_TTL)

    if max_new is not None:
        missing = missing[:max_new]
    log.info("income statements: %d cached, %d to fetch", len(out), len(missing))
    if not missing:
        return out

    def one(symbol: str):
        try:
            stmt = yf.Ticker(symbol).income_stmt
            if stmt is None or stmt.empty:
                return {}
            rows = {str(i).strip().lower(): i for i in stmt.index}
            res: dict[str, dict] = {}
            for key, label in (("total revenue", "revenue"), ("net income", "net_income")):
                if key in rows:
                    series = stmt.loc[rows[key]].dropna()
                    res[label] = {str(k.year): float(v) for k, v in series.items()}
            return res
        except Exception as e:                       # noqa: BLE001
            return None if fetcher.is_rate_limit(e) else {}

    breaker = Breaker(C.YF_BREAKER_QUOTES)
    for symbol, data in fetcher.each(missing, one, breaker=breaker,
                                     workers=C.YF_QUOTE_THREADS, label="income"):
        out[symbol] = data
        fetcher.store(symbol, data, INCOME_CACHE)

    log.info("income statements: %d symbols have data", len(out))
    return out


# ------------------------------------------------------------------------ fx
def fetch_fx(currencies: list[str]) -> dict[str, float]:
    """Spot FX rates to USD, keyed by currency code. USD maps to 1.0.

    ``GBp`` (London pence) is handled by the caller, not here.
    """
    cache = C.CACHE / "fx.json"
    rates = {"USD": 1.0}
    wanted = sorted({c for c in currencies if c and c.upper() != "USD"})
    pairs = {c: f"{c.upper()}USD=X" for c in wanted}
    if not pairs:
        return rates
    try:
        closes = _download(list(pairs.values()), period="5d", interval="1d")
        for cur, sym in pairs.items():
            series = closes.get(sym)
            if series is not None and not series.empty:
                rates[cur] = float(series.iloc[-1])
    except Exception as e:                           # noqa: BLE001
        log.warning("FX fetch failed: %s", str(e)[:110])

    # FX moves slowly; a stale rate beats no rate at all.
    if cache.exists():
        try:
            previous = json.loads(cache.read_text(encoding="utf-8"))
            for cur, rate in previous.items():
                rates.setdefault(cur, rate)
        except Exception:                            # noqa: BLE001
            pass
    try:
        cache.write_text(json.dumps(rates), encoding="utf-8")
    except Exception:                                # noqa: BLE001
        pass

    missing = [c for c in wanted if c not in rates]
    if missing:
        log.warning("no FX rate for: %s (market cap left in local currency)",
                    ", ".join(missing))
    log.info("FX: resolved %d/%d currencies", len(rates) - 1, len(wanted))
    return rates

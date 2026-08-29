"""Yahoo Finance access layer: symbol probing, price history and quote fields.

Everything Yahoo-specific lives here so it can be swapped wholesale (for stooq
or a paid feed) without touching the rest of the pipeline.

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
import random
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import yfinance as yf

from . import config as C
from .metrics import sanitise_prices

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

INCOME_CACHE = C.CACHE / "income"
INCOME_CACHE.mkdir(parents=True, exist_ok=True)


class _Breaker:
    """Stops a stage once Yahoo starts refusing, instead of burning the queue.

    Latching matters: once tripped, the in-flight tasks that get skipped report
    no failure, and a non-latching counter would reset on them and let the stage
    start hammering Yahoo again.
    """

    def __init__(self, threshold: int):
        self.threshold = threshold
        self._consecutive = 0
        self._tripped = False
        self._lock = Lock()

    @property
    def tripped(self) -> bool:
        return self._tripped

    def record(self, *, limited: bool) -> None:
        with self._lock:
            if self._tripped:
                return
            self._consecutive = self._consecutive + 1 if limited else 0
            if self._consecutive >= self.threshold:
                self._tripped = True


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "too many requests" in text or "429" in text


def _safe(symbol: str) -> str:
    return symbol.replace("/", "_").replace("\\", "_")


def _fresh(path, ttl: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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
    total = len(symbols)
    for n, chunk in enumerate(_chunks(symbols, C.YF_CHUNK), 1):
        for attempt in range(C.YF_RETRIES):
            try:
                df = yf.download(chunk, period="5d", interval="1d",
                                 threads=C.YF_THREADS, progress=False, auto_adjust=True)
                valid |= set(_extract_close(df, chunk))
                break
            except Exception as e:                   # noqa: BLE001
                if _is_rate_limit(e) and attempt < C.YF_RETRIES - 1:
                    _backoff(attempt)
                    continue
                log.warning("probe chunk %d failed: %s", n, str(e)[:110])
                break
        log.info("  probed %d/%d (%d valid)", min(n * C.YF_CHUNK, total), total, len(valid))
        _pause()
    return valid


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


def _backoff(attempt: int) -> None:
    # First 429 of a run is far more often a stale crumb than a real block.
    if attempt == 0:
        reset_session()
    delay = C.YF_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1.5)
    log.info("    rate limited, backing off %.1fs", delay)
    time.sleep(delay)


def _pause() -> None:
    time.sleep(C.YF_PAUSE + random.uniform(0, C.YF_PAUSE))


# ------------------------------------------------------------------- history
def fetch_history(symbols: list[str], max_new: int | None = None) -> dict[str, pd.Series]:
    """Monthly adjusted-close history (max range), cached to Parquet per symbol.

    Monthly bars are all the CAGR maths needs and are ~10x smaller than daily.
    """
    cached: dict[str, pd.Series] = {}
    missing: list[str] = []
    for s in symbols:
        path = C.HISTORY_CACHE / f"{_safe(s)}.parquet"
        if _fresh(path, C.HISTORY_TTL):
            try:
                cached[s] = pd.read_parquet(path)["close"]
                continue
            except Exception:                        # noqa: BLE001
                pass
        missing.append(s)

    if max_new is not None:
        missing = missing[:max_new]
    log.info("history: %d cached, %d to fetch", len(cached), len(missing))

    breaker = _Breaker(C.YF_BREAKER_CHUNKS)
    for n, chunk in enumerate(_chunks(missing, C.YF_CHUNK), 1):
        if breaker.tripped:
            log.warning("history: rate limited repeatedly, stopping early "
                        "(%d symbols left for the next run)",
                        len(missing) - (n - 1) * C.YF_CHUNK)
            break
        got = {}
        for attempt in range(C.YF_RETRIES):
            try:
                df = yf.download(chunk, period="max", interval="1mo",
                                 threads=C.YF_THREADS, progress=False, auto_adjust=True)
                got = _extract_close(df, chunk)
                break
            except Exception as e:                   # noqa: BLE001
                if _is_rate_limit(e) and attempt < C.YF_RETRIES - 1:
                    _backoff(attempt)
                    continue
                log.warning("history chunk %d failed: %s", n, str(e)[:110])
                break

        breaker.record(limited=not got)
        for s, series in got.items():
            cached[s] = series
            try:
                series.rename("close").to_frame().to_parquet(
                    C.HISTORY_CACHE / f"{_safe(s)}.parquet")
            except Exception:                        # noqa: BLE001
                pass
        log.info("  history %d/%d fetched (%d total)",
                 min(n * C.YF_CHUNK, len(missing)), len(missing), len(cached))
        _pause()

    # Cleaned on the way out rather than before caching, so the Parquet files
    # stay a faithful copy of what Yahoo served and a change to the filter takes
    # effect on the next run instead of needing the whole cache re-fetched.
    cleaned, dropped = {}, 0
    for s, series in cached.items():
        out = sanitise_prices(series)
        dropped += len(series.dropna()) - len(out)
        cleaned[s] = out
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
    out: dict[str, dict] = {}
    missing: list[str] = []
    for s in symbols:
        path = INCOME_CACHE / f"{_safe(s)}.json"
        if _fresh(path, C.INCOME_TTL):
            try:
                out[s] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except Exception:                        # noqa: BLE001
                pass
        missing.append(s)

    if max_new is not None:
        missing = missing[:max_new]
    log.info("income statements: %d cached, %d to fetch", len(out), len(missing))
    if not missing:
        return out

    breaker = _Breaker(C.YF_BREAKER_QUOTES)

    def one(symbol: str):
        if breaker.tripped:
            return symbol, None
        try:
            stmt = yf.Ticker(symbol).income_stmt
            if stmt is None or stmt.empty:
                return symbol, {}
            rows = {str(i).strip().lower(): i for i in stmt.index}
            res: dict[str, dict] = {}
            for key, label in (("total revenue", "revenue"), ("net income", "net_income")):
                if key in rows:
                    series = stmt.loc[rows[key]].dropna()
                    res[label] = {str(k.year): float(v) for k, v in series.items()}
            return symbol, res
        except Exception as e:                       # noqa: BLE001
            if _is_rate_limit(e):
                return symbol, None
            return symbol, {}

    with ThreadPoolExecutor(max_workers=C.YF_QUOTE_THREADS) as pool:
        futures = {pool.submit(one, s): s for s in missing}
        for fut in as_completed(futures):
            sym, data = fut.result()
            breaker.record(limited=data is None)
            if data:
                out[sym] = data
                try:
                    (INCOME_CACHE / f"{_safe(sym)}.json").write_text(
                        json.dumps(data), encoding="utf-8")
                except Exception:                    # noqa: BLE001
                    pass

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
        df = yf.download(list(pairs.values()), period="5d", interval="1d",
                         threads=C.YF_THREADS, progress=False, auto_adjust=True)
        closes = _extract_close(df, list(pairs.values()))
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

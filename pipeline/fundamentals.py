"""Stage 4: revenue and net-income history.

Primary source is the SEC's XBRL ``companyfacts`` API — free, official, no API
key — which reaches back to roughly 2007-2010 for most filers. It only covers
US/SEC registrants, so everything else falls back to Yahoo's 4-5 year income
statements. Every row records which source was used and how many years it had.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import config as C

log = logging.getLogger(__name__)

CIK_CACHE = C.CACHE / "company_tickers.json"
FACTS_CACHE = C.CACHE / "sec_facts"
FACTS_CACHE.mkdir(parents=True, exist_ok=True)

_ANNUAL_FRAME = re.compile(r"^CY\d{4}$")

# Companies switched revenue tags when ASC 606 took effect in 2018, so no single
# tag spans 20 years. These are merged into one continuous series.
REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
)
# Deliberately NOT earnings per share: XBRL reports EPS as-reported, unadjusted
# for splits, so an EPS CAGR spanning Apple's 2020 4:1 or Alphabet's 2022 20:1
# split is meaningless (it reads as negative growth). Net income is split-proof.
NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic")

# Foreign private issuers file 20-Fs under the IFRS taxonomy rather than US GAAP.
# Covering it pulls in the likes of TSMC, SAP, Shell, Toyota and Novo Nordisk,
# typically with ~10 years of history.
IFRS_REVENUE_TAGS = ("Revenue", "RevenueFromContractsWithCustomers",
                     "RevenueFromSaleOfGoods", "RevenueFromRenderingOfServices")
IFRS_PROFIT_TAGS = ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss")

# Discard fundamentals whose most recent year is older than this many years.
STALE_YEARS = 2

# Legal-form noise to strip before matching a holding name to an SEC registrant.
_NAME_NOISE = re.compile(
    r"\b(INC|CORP|CORPORATION|COMPANY|CO|LTD|LIMITED|PLC|AG|NV|SA|SE|AB|AS|OYJ|"
    r"SPA|NPV|HOLDINGS?|GROUP|THE|ADR|ADS|SPON|SPONSORED|REPRESENTING|CLASS|CL|"
    r"ORD|SHS|REG|COMMON|STOCK|SHARES?)\b", re.I)


class _RateLimiter:
    """Simple global throttle; the SEC allows 10 req/s, we use half that."""

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._interval


_limiter = _RateLimiter(C.SEC_RATE_LIMIT)


def sec_enabled() -> bool:
    return bool(C.SEC_USER_AGENT)


def _headers() -> dict[str, str]:
    return {"User-Agent": C.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def load_cik_map() -> dict[str, int]:
    """Map uppercase US ticker -> CIK number."""
    if not CIK_CACHE.exists() or (time.time() - CIK_CACHE.stat().st_mtime) > 7 * 86_400:
        _limiter.wait()
        r = requests.get(C.SEC_TICKERS_URL, headers=_headers(), timeout=60)
        r.raise_for_status()
        CIK_CACHE.write_bytes(r.content)
    raw = json.loads(CIK_CACHE.read_text(encoding="utf-8"))
    return {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}


def normalise_name(name: str) -> str:
    """Reduce a company name to a comparable core token string."""
    s = re.sub(r"[^A-Za-z0-9 ]", " ", str(name or "").upper())
    s = _NAME_NOISE.sub(" ", s)
    return " ".join(s.split())


MIN_NAME_LEN = 3            # exact matches this short are fine once collisions are dropped
MIN_PREFIX_LEN = 12         # prefix matching needs far more evidence


def build_name_index(raw: dict) -> dict[str, int]:
    """Normalised SEC registrant name -> CIK, with ambiguous names removed.

    If two different registrants normalise to the same string we cannot tell
    them apart, so the key is dropped rather than risking a wrong attribution.
    """
    seen: dict[str, set[int]] = {}
    for entry in raw.values():
        key = normalise_name(entry.get("title", ""))
        if len(key) >= MIN_NAME_LEN:
            seen.setdefault(key, set()).add(int(entry["cik_str"]))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def match_cik_by_name(holding_name: str, index: dict[str, int]) -> int | None:
    """Conservative name match to an SEC registrant.

    Only exact normalised equality, or a long prefix with exactly one candidate,
    counts. A wrong match would attach another company's financials to the row,
    so anything doubtful is left unmatched and falls back to Yahoo.
    """
    key = normalise_name(holding_name)
    if len(key) < MIN_NAME_LEN:
        return None
    if key in index:
        return index[key]
    if len(key) >= MIN_PREFIX_LEN:
        hits = {cik for name, cik in index.items()
                if len(name) >= MIN_PREFIX_LEN
                and (name.startswith(key) or key.startswith(name))}
        if len(hits) == 1:
            return next(iter(hits))
    return None


def _tag_series(gaap: dict, tag: str) -> dict[str, float]:
    """One tag's {year: value} annual series, from the SEC's own CY frames.

    Entries carrying a ``frame`` of the form ``CY2023`` are the SEC's
    deduplicated annual figures, which avoids double-counting the comparative
    figures that appear in every 10-K.
    """
    if tag not in gaap:
        return {}
    units = gaap[tag].get("units") or {}
    if not units:
        return {}
    unit_key = next((u for u in units if u in ("USD", "USD/shares")), next(iter(units)))
    series: dict[str, float] = {}
    for entry in units[unit_key]:
        frame = entry.get("frame", "")
        if _ANNUAL_FRAME.match(frame) and entry.get("val") is not None:
            series[frame[2:]] = float(entry["val"])
    return series


def _annual_series(facts: dict, gaap_tags: tuple[str, ...],
                   ifrs_tags: tuple[str, ...]) -> dict[str, float]:
    """Merge several tags into one continuous annual series.

    Filers migrate between tags over time (the ASC 606 revenue change in 2018 is
    the big one), so no single tag spans two decades. Tags are merged oldest-
    coverage first, letting the most modern tag win any overlapping year.
    US GAAP is preferred; IFRS is used for foreign private issuers.
    """
    all_facts = facts.get("facts") or {}
    for taxonomy, tags in (("us-gaap", gaap_tags), ("ifrs-full", ifrs_tags)):
        book = all_facts.get(taxonomy) or {}
        if not book:
            continue
        per_tag = [s for s in (_tag_series(book, t) for t in tags) if s]
        if not per_tag:
            continue
        per_tag.sort(key=lambda s: max(int(y) for y in s))
        merged: dict[str, float] = {}
        for series in per_tag:
            merged.update(series)
        if len(merged) >= 2 and _is_current(merged):
            return merged
    return {}


def _is_current(series: dict[str, float]) -> bool:
    """Reject series that stop years ago.

    A company that deregistered its US listing (Toyota dropped its ADR in 2024)
    still has filings on EDGAR, but its last figures may be from 2019. Growth
    computed off that would be presented as if it were current, so we discard it
    and let the Yahoo fallback supply fresher numbers.
    """
    if not series:
        return False
    return max(int(y) for y in series) >= datetime.now(timezone.utc).year - STALE_YEARS


def _fetch_facts(symbol: str, cik: int) -> dict:
    path = FACTS_CACHE / f"{cik}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < 7 * 86_400:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:                            # noqa: BLE001
            pass
    _limiter.wait()
    r = requests.get(C.SEC_FACTS_URL.format(cik=cik), headers=_headers(), timeout=60)
    if r.status_code == 403:
        raise PermissionError(
            "SEC returned 403. Set a descriptive SEC_USER_AGENT "
            '(e.g. "Your Name your@email.com") before running.'
        )
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.json()


def resolve_ciks(symbols: list[str], names: dict[str, str]) -> dict[str, int]:
    """Map each symbol to an SEC CIK, by ticker first and company name second.

    Direct ticker matching only catches US listings. Name matching additionally
    picks up foreign private issuers that file a 20-F under a US-listed ADR
    ticker (TSMC, SAP, Shell, ...), whose IFRS facts we can then read.
    """
    raw = json.loads(CIK_CACHE.read_text(encoding="utf-8"))
    by_ticker = {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}
    by_name = build_name_index(raw)

    out: dict[str, int] = {}
    direct = 0
    for sym in symbols:
        base = sym.split(".")[0].upper()
        if base in by_ticker and "." not in sym:
            out[sym] = by_ticker[base]
            direct += 1
        else:
            cik = match_cik_by_name(names.get(sym, ""), by_name)
            if cik is not None:
                out[sym] = cik
    log.info("SEC: %d symbols matched (%d by ticker, %d by name)",
             len(out), direct, len(out) - direct)
    return out


def fetch_sec(symbols: list[str], names: dict[str, str] | None = None) -> dict[str, dict]:
    """Pull revenue and net-income annual series for every SEC-registered symbol."""
    if not sec_enabled():
        log.warning("SEC_USER_AGENT not set - skipping SEC fundamentals. "
                    "Growth columns will fall back to Yahoo's ~4 years. See README.")
        return {}

    load_cik_map()                       # ensure the cache file exists
    targets = resolve_ciks(symbols, names or {})

    out: dict[str, dict] = {}
    done = 0

    def one(item):
        sym, cik = item
        try:
            facts = _fetch_facts(sym, cik)
            if not facts:
                return sym, {}
            return sym, {
                "revenue": _annual_series(facts, REVENUE_TAGS, IFRS_REVENUE_TAGS),
                "net_income": _annual_series(facts, NET_INCOME_TAGS, IFRS_PROFIT_TAGS),
            }
        except PermissionError:
            raise
        except Exception as e:                       # noqa: BLE001
            log.debug("SEC fetch failed %s: %s", sym, str(e)[:80])
            return sym, {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, it) for it in targets.items()]
        for fut in as_completed(futures):
            sym, data = fut.result()
            if data and (data.get("revenue") or data.get("net_income")):
                out[sym] = data
            done += 1
            if done % 100 == 0:
                log.info("  SEC %d/%d", done, len(targets))

    log.info("SEC: %d symbols with usable fundamentals", len(out))
    return out

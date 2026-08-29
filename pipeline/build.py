"""Stage 5: merge every source into the published dataset."""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone

from . import config as C
from . import schema
from .metrics import (annualised_vol, cagr, clean, max_drawdown, pct_from_high,
                      series_cagr)

log = logging.getLogger(__name__)

STOCKS_JSON = C.OUT / "stocks.json"
STOCKS_CSV = C.OUT / "stocks.csv"
META_JSON = C.OUT / "meta.json"
SPARK_JSON = C.OUT / "sparklines.json"

# Fields we refuse to regress to null once a good value has been published.
PROTECTED = ("market_cap_usd", "trailing_pe", "return_10y", "return_20y",
             "sector", "country", "name")


def _prev_rows() -> dict[str, dict]:
    if not STOCKS_JSON.exists():
        return {}
    try:
        return {r["symbol"]: r for r in json.loads(STOCKS_JSON.read_text(encoding="utf-8"))}
    except Exception:                                # noqa: BLE001
        return {}


# Some exchanges quote PRICES in a minor unit (London in pence, Tel Aviv in
# agorot, Kuwait in fils) while Yahoo reports MARKET CAP for the same ticker in
# the major unit. So a market cap converts at the major-currency rate, and only
# a price needs dividing -- applying the divisor to the cap as well is what
# once made Shell look like a $2B company. ``schema.validate`` now checks these
# rows end to end so that mistake cannot ship again.
#   currency -> (major currency, minor units per major unit)
MINOR_UNITS: dict[str, tuple[str, int]] = {
    "GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100),
    "ILA": ("ILS", 100), "KWF": ("KWD", 1000),
}


def major_currency(currency) -> str:
    """The currency a market cap quoted against ``currency`` is denominated in."""
    cur = str(currency)
    entry = MINOR_UNITS.get(cur)
    return entry[0] if entry else cur


def _to_usd(value, currency, fx):
    """Convert a local-currency market cap to USD."""
    if value is None or not currency:
        return None
    cur = major_currency(currency)
    rate = fx.get(cur) or fx.get(cur.upper())
    return None if rate is None else float(value) * rate


def _sparkline(series, points: int = 60):
    """Downsample history to a compact sparkline for the detail drawer."""
    if series is None or series.empty:
        return []
    s = series.dropna()
    if len(s) <= points:
        return [round(float(v), 4) for v in s.tolist()]
    step = len(s) / points
    return [round(float(s.iloc[int(i * step)]), 4) for i in range(points)]


def build(universe, quotes, history, sec, income, fx):
    rows = []
    sparks = {}
    previous = _prev_rows()

    for rank, (_, holding) in enumerate(universe.iterrows(), start=1):
        symbol = holding["symbol"]
        q = quotes.get(symbol, {})
        hist = history.get(symbol)

        currency = q.get("currency") or holding.get("currency")
        market_cap = clean(q.get("marketCap"))

        row = {
            "symbol": symbol,
            "name": (q.get("longName") or q.get("shortName")
                     or str(holding.get("name", "")).title()),
            "rank": rank,
            "local_ticker": str(holding.get("local_ticker") or "") or None,
            "sedol": str(holding.get("sedol") or "") or None,
            # Other listings of the same company that were merged into this row.
            "also_listed_as": list(holding.get("merged_symbols") or []),
            "index_weight": clean(holding.get("weight")),
            "country": q.get("country"),
            "sector": q.get("sector"),
            "industry": q.get("industry"),
            "exchange": q.get("fullExchangeName") or q.get("exchange"),
            "currency": currency,
            "price": clean(q.get("regularMarketPrice")),
            "market_cap": market_cap,
            "market_cap_usd": clean(_to_usd(market_cap, currency, fx)),
            "shares_outstanding": clean(q.get("sharesOutstanding")),
            "trailing_pe": clean(q.get("trailingPE")),
            "forward_pe": clean(q.get("forwardPE")),
            "price_to_book": clean(q.get("priceToBook")),
            "price_to_sales": clean(q.get("priceToSalesTrailing12Months")),
            "ev_to_ebitda": clean(q.get("enterpriseToEbitda")),
            "dividend_yield": clean(q.get("dividendYield")),
            "profit_margin": clean(q.get("profitMargins")),
            "return_on_equity": clean(q.get("returnOnEquity")),
            "debt_to_equity": clean(q.get("debtToEquity")),
            "beta": clean(q.get("beta")),
            "revenue_growth_ttm": clean(q.get("revenueGrowth")),
            "earnings_growth_ttm": clean(q.get("earningsGrowth")),
            "pct_from_52w_high": clean(pct_from_high(
                q.get("regularMarketPrice"), q.get("fiftyTwoWeekHigh"))),
        }

        # ---- price growth (annualised total return) ------------------------
        for years in (1, 3, 5, 10, 20):
            row[f"return_{years}y"] = clean(cagr(hist, years)) if hist is not None else None
        row["max_drawdown"] = clean(max_drawdown(hist)) if hist is not None else None
        row["volatility_5y"] = clean(annualised_vol(hist)) if hist is not None else None
        row["history_start"] = (str(hist.index[0].date())
                                if hist is not None and len(hist) else None)
        if hist is not None:
            sparks[symbol] = _sparkline(hist)

        # ---- business growth ----------------------------------------------
        facts = sec.get(symbol)
        if facts and (facts.get("revenue") or facts.get("net_income")):
            rev = facts.get("revenue") or {}
            ni = facts.get("net_income") or {}
            row.update({
                "revenue_cagr_3y": clean(series_cagr(rev, 3)),
                "revenue_cagr_5y": clean(series_cagr(rev, 5)),
                "revenue_cagr_10y": clean(series_cagr(rev, 10)),
                "revenue_cagr_20y": clean(series_cagr(rev, 20)),
                "net_income_cagr_3y": clean(series_cagr(ni, 3)),
                "net_income_cagr_5y": clean(series_cagr(ni, 5)),
                "net_income_cagr_10y": clean(series_cagr(ni, 10)),
                "fundamentals_source": "sec",
                "fundamentals_years": len(rev or ni),
            })
        else:
            stmt = income.get(symbol) or {}
            rev = stmt.get("revenue") or {}
            ni = stmt.get("net_income") or {}
            row.update({
                "revenue_cagr_3y": clean(series_cagr(rev, 3)),
                "revenue_cagr_5y": clean(series_cagr(rev, 5)),
                "revenue_cagr_10y": clean(series_cagr(rev, 10)),
                "revenue_cagr_20y": None,
                "net_income_cagr_3y": clean(series_cagr(ni, 3)),
                "net_income_cagr_5y": clean(series_cagr(ni, 5)),
                "net_income_cagr_10y": clean(series_cagr(ni, 10)),
                "fundamentals_source": "yahoo" if (rev or ni) else "none",
                "fundamentals_years": len(rev or ni),
            })

        # ---- carry values over only when the fetch itself failed -------------
        # A null from a *successful* fetch is real information: a company that
        # turns lossmaking genuinely has no trailing P/E, and freezing the old
        # one would be wrong. So values are only carried over when this symbol
        # returned nothing at all this run.
        prior = previous.get(symbol)
        if prior and not q:
            recovered = [f for f in PROTECTED
                         if row.get(f) is None and prior.get(f) is not None]
            for field in recovered:
                row[field] = prior[field]
            row["stale"] = bool(recovered)

        rows.append(schema.Stock(**row).model_dump())

    rows = _rank_by_market_cap(rows)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(rows),
        "universe_source": "SPDR Portfolio MSCI Global Stock Market ETF (SPGM) holdings",
        "price_source": "Yahoo Finance (yfinance)",
        "fundamentals_sources": ["SEC EDGAR XBRL companyfacts", "Yahoo Finance"],
        "fundamentals_breakdown": {
            src: sum(1 for r in rows if r["fundamentals_source"] == src)
            for src in ("sec", "yahoo", "none")
        },
        "stale_rows": sum(1 for r in rows if r["stale"]),
        "fx_rates": {k: round(v, 6) for k, v in sorted(fx.items())},
        "coverage": schema.coverage(rows),
    }
    return rows, meta, sparks


def _rank_by_market_cap(rows: list[dict]) -> list[dict]:
    """Renumber ``rank`` by market cap, largest first.

    Rows arrive in index-weight order, which is how the universe is *selected*
    but not what the table says it shows. Index weight is free-float adjusted
    and, for China A-shares, cut by a foreign-inclusion factor, so a state- or
    family-controlled company sits far below its size -- ordering by it puts a
    $53B chipmaker at rank 900 next to a $0.5B REIT. Rank is therefore assigned
    here, once real market caps are known.

    Rows without a market cap cannot be ranked against the rest and go last.
    """
    ranked = sorted(rows, key=lambda r: (r.get("market_cap_usd") is None,
                                         -(r.get("market_cap_usd") or 0)))
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return ranked


def _compact(rows):
    """Round floats before serialising.

    Full float64 precision on a rate of return is noise, and it roughly doubles
    the payload the browser has to download.
    """
    out = []
    for row in rows:
        trimmed = {}
        for key, value in row.items():
            if isinstance(value, float):
                trimmed[key] = round(value, 2) if abs(value) >= 1000 else round(value, 6)
            else:
                trimmed[key] = value
        out.append(trimmed)
    return out


def write(rows, meta, sparks) -> None:
    STOCKS_JSON.write_text(json.dumps(_compact(rows), separators=(",", ":")),
                           encoding="utf-8")
    META_JSON.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    SPARK_JSON.write_text(json.dumps(sparks, separators=(",", ":")), encoding="utf-8")

    if rows:
        with STOCKS_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    log.info("wrote stocks.json (%d KB), stocks.csv, meta.json, sparklines.json",
             STOCKS_JSON.stat().st_size // 1024)

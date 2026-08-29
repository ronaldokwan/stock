"""Stage 5: merge every source into the published dataset.

One row is assembled from four independent sources, and each contributes a
distinct slice: the holdings file and batch quote give identity and size, the
price history gives returns and risk, SEC or Yahoo statements give business
growth, and the previously published file supplies carry-over for anything this
run failed to fetch. Those four slices are four functions, so each is testable
on its own; ``build`` is only the loop that joins them.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone

from . import config as C
from . import schema
from .currency import MINOR_UNITS, as_fraction, major_currency, to_usd
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

# Above this, the earnings base is too near zero for the ratio to mean anything,
# and a derived P/E is withheld rather than published as a headline number.
PE_DERIVED_MAX = 1000.0

# A completed fiscal year this far back no longer describes the current
# business, the same cut-off the growth columns apply to a stale series.
PE_MAX_STALE_YEARS = 2

# Re-exported: currency handling moved to its own module so ``schema`` could
# import it without a cycle, but these names are part of build's public surface.
__all__ = ["MINOR_UNITS", "build", "major_currency", "write"]


def _prev_rows() -> dict[str, dict]:
    if not STOCKS_JSON.exists():
        return {}
    try:
        return {r["symbol"]: r for r in json.loads(STOCKS_JSON.read_text(encoding="utf-8"))}
    except Exception:                                # noqa: BLE001
        return {}


def _sparkline(series, points: int = 60):
    """Downsample history to a compact sparkline for the detail drawer."""
    if series is None or series.empty:
        return []
    s = series.dropna()
    if len(s) <= points:
        return [round(float(v), 4) for v in s.tolist()]
    step = len(s) / points
    return [round(float(s.iloc[int(i * step)]), 4) for i in range(points)]


# ------------------------------------------------------------ row assembly ---
def _identity(holding, quote: dict, rank: int, fx: dict) -> dict:
    """Who the company is, what it is worth, and how it is valued.

    Everything here comes from the holdings row and the batch quote, which is
    the cheap tier -- so this is the part of the table that is always complete.
    """
    currency = quote.get("currency") or holding.get("currency")
    market_cap = clean(quote.get("marketCap"))
    return {
        "symbol": holding["symbol"],
        "name": (quote.get("longName") or quote.get("shortName")
                 or str(holding.get("name", "")).title()),
        "rank": rank,
        "local_ticker": str(holding.get("local_ticker") or "") or None,
        "sedol": str(holding.get("sedol") or "") or None,
        # Other listings of the same company that were merged into this row.
        "also_listed_as": list(holding.get("merged_symbols") or []),
        "index_weight": clean(holding.get("weight")),
        "country": quote.get("country"),
        "sector": quote.get("sector"),
        "industry": quote.get("industry"),
        "exchange": quote.get("fullExchangeName") or quote.get("exchange"),
        "currency": currency,
        "price": clean(quote.get("regularMarketPrice")),
        "market_cap": market_cap,
        "market_cap_usd": clean(to_usd(market_cap, currency, fx)),
        "shares_outstanding": clean(quote.get("sharesOutstanding")),
        "trailing_pe": clean(quote.get("trailingPE")),
        "forward_pe": clean(quote.get("forwardPE")),
        "price_to_book": clean(quote.get("priceToBook")),
        "price_to_sales": clean(quote.get("priceToSalesTrailing12Months")),
        "ev_to_ebitda": clean(quote.get("enterpriseToEbitda")),
        # Percent-quoted by Yahoo; everything published here is a fraction.
        "dividend_yield": clean(as_fraction(quote.get("dividendYield"))),
        "profit_margin": clean(quote.get("profitMargins")),
        "return_on_equity": clean(quote.get("returnOnEquity")),
        "debt_to_equity": clean(as_fraction(quote.get("debtToEquity"))),
        "beta": clean(quote.get("beta")),
        "revenue_growth_ttm": clean(quote.get("revenueGrowth")),
        "earnings_growth_ttm": clean(quote.get("earningsGrowth")),
        "pct_from_52w_high": clean(pct_from_high(
            quote.get("regularMarketPrice"), quote.get("fiftyTwoWeekHigh"))),
    }


def _price_metrics(hist) -> dict:
    """Annualised total return, drawdown and volatility from the price series."""
    row = {f"return_{years}y": clean(cagr(hist, years)) if hist is not None else None
           for years in (1, 3, 5, 10, 20)}
    row["max_drawdown"] = clean(max_drawdown(hist)) if hist is not None else None
    row["volatility_5y"] = clean(annualised_vol(hist)) if hist is not None else None
    row["history_start"] = (str(hist.index[0].date())
                            if hist is not None and len(hist) else None)
    return row


def _growth_metrics(facts: dict | None, statement: dict | None) -> dict:
    """Revenue and net-income CAGRs, preferring SEC filings over Yahoo.

    SEC XBRL reaches back to ~2009; Yahoo's income statements cover four or five
    years. Both are read through the same code so the two paths cannot drift --
    only the source and the reachable horizon differ. A 20-year fundamental CAGR
    exists for neither, and Yahoo's four years cannot support one at all, so it
    is null on the Yahoo path rather than computed from too short a series.
    """
    from_sec = bool(facts and (facts.get("revenue") or facts.get("net_income")))
    source = facts if from_sec else (statement or {})
    revenue = source.get("revenue") or {}
    net_income = source.get("net_income") or {}

    row = {f"revenue_cagr_{y}y": clean(series_cagr(revenue, y)) for y in (3, 5, 10)}
    row["revenue_cagr_20y"] = clean(series_cagr(revenue, 20)) if from_sec else None
    row.update({f"net_income_cagr_{y}y": clean(series_cagr(net_income, y))
                for y in (3, 5, 10)})
    row["fundamentals_source"] = ("sec" if from_sec
                                  else "yahoo" if (revenue or net_income) else "none")
    row["fundamentals_years"] = len(revenue or net_income)
    return row


def _derive_pe(row: dict, quote: dict, statement: dict | None) -> dict:
    """Fill a missing trailing P/E from market cap and annual net income.

    Yahoo omits ``trailingPE`` wherever it has no EPS for a listing, and for
    some markets it never has one: every Korean line in the universe comes back
    with ``epsTrailingTwelveMonths`` null, so Samsung, SK hynix and Hyundai all
    published without a P/E while their peers elsewhere had one. Market cap over
    net income is the same ratio reached by another route.

    Three guards keep it honest, because a wrong P/E is worse than no P/E:

    - The statement must be denominated in the currency the market cap is
      denominated in. A company listed in London and reporting in dollars would
      otherwise divide pounds by dollars and publish a plausible-looking number
      that is wrong by the exchange rate.
    - The fiscal year must be recent. This is an annual figure, not a trailing
      twelve months, so it is already the staler of the two -- and a delisted or
      dormant issuer's last filing must not be presented as current.
    - Net income must be positive and not so small that the ratio explodes. A
      lossmaking company has no meaningful P/E, which is why the cell is blank
      in the first place.

    Only the Yahoo statement path is used. SEC facts carry no currency in the
    cache, so the first guard could not be applied to them.
    """
    if row.get("trailing_pe") is not None:
        return row

    cap = row.get("market_cap")
    net_income = (statement or {}).get("net_income") or {}
    if not cap or not net_income:
        return row
    if major_currency(row.get("currency")) != quote.get("financialCurrency"):
        return row

    year = max(net_income, key=int)
    if int(year) < datetime.now(timezone.utc).year - PE_MAX_STALE_YEARS:
        return row

    latest = net_income[year]
    if not latest or latest <= 0:
        return row

    pe = float(cap) / float(latest)
    if pe > PE_DERIVED_MAX:
        return row

    row["trailing_pe"] = pe
    row["trailing_pe_derived"] = True
    return row


def _carry_over(row: dict, prior: dict | None, quote: dict) -> dict:
    """Restore fields from the last publish, but only when this fetch failed.

    A null from a *successful* fetch is real information: a company that turns
    lossmaking genuinely has no trailing P/E, and freezing the old one would be
    wrong. So values are only carried over when this symbol returned nothing at
    all this run.
    """
    if not prior or quote:
        return row
    recovered = [f for f in PROTECTED
                 if row.get(f) is None and prior.get(f) is not None]
    for field in recovered:
        row[field] = prior[field]
    row["stale"] = bool(recovered)
    return row


def build(universe, quotes, history, sec, income, fx):
    rows = []
    sparks = {}
    previous = _prev_rows()

    for rank, (_, holding) in enumerate(universe.iterrows(), start=1):
        symbol = holding["symbol"]
        quote = quotes.get(symbol, {})
        hist = history.get(symbol)

        row = _identity(holding, quote, rank, fx)
        row.update(_price_metrics(hist))
        row.update(_growth_metrics(sec.get(symbol), income.get(symbol)))
        _derive_pe(row, quote, income.get(symbol))
        _carry_over(row, previous.get(symbol), quote)

        if hist is not None:
            sparks[symbol] = _sparkline(hist)
        rows.append(schema.Stock(**row).model_dump())

    rows = _rank_by_market_cap(rows)
    return rows, _meta(rows, fx), sparks


def _meta(rows: list[dict], fx: dict) -> dict:
    return {
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
        "derived_pe_rows": sum(1 for r in rows if r["trailing_pe_derived"]),
        "fx_rates": {k: round(v, 6) for k, v in sorted(fx.items())},
        "coverage": schema.coverage(rows),
    }


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

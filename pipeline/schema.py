"""Row schema and dataset-level guard rails."""
from __future__ import annotations

import logging

from typing import Literal

from pydantic import BaseModel, Field

from . import config as C
from .currency import MINOR_UNITS

log = logging.getLogger(__name__)


class Stock(BaseModel):
    """One row of the screener. Every metric is optional — absent data is null."""

    # identity
    symbol: str
    name: str
    rank: int
    local_ticker: str | None = None
    sedol: str | None = None
    index_weight: float | None = None
    # Sibling listings (ADR, dual listing, preferred class) folded into this row.
    also_listed_as: list[str] = Field(default_factory=list)

    # classification
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    currency: str | None = None

    # size & price
    price: float | None = None
    market_cap: float | None = None
    market_cap_usd: float | None = None
    shares_outstanding: float | None = None

    # valuation
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    dividend_yield: float | None = None

    # quality
    profit_margin: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    beta: float | None = None

    # price growth (annualised total return)
    return_1y: float | None = None
    return_3y: float | None = None
    return_5y: float | None = None
    return_10y: float | None = None
    return_20y: float | None = None

    # business growth
    revenue_cagr_3y: float | None = None
    revenue_cagr_5y: float | None = None
    revenue_cagr_10y: float | None = None
    revenue_cagr_20y: float | None = None
    net_income_cagr_3y: float | None = None
    net_income_cagr_5y: float | None = None
    net_income_cagr_10y: float | None = None
    revenue_growth_ttm: float | None = None
    earnings_growth_ttm: float | None = None

    # risk
    max_drawdown: float | None = None
    volatility_5y: float | None = None
    pct_from_52w_high: float | None = None

    # provenance
    # True when trailing_pe came from build._derive_pe rather than the quote.
    trailing_pe_derived: bool = False
    history_start: str | None = None
    fundamentals_source: Literal["sec", "yahoo", "none"] = "none"
    fundamentals_years: int = 0
    stale: bool = False


class ValidationError(RuntimeError):
    pass


def coverage(rows: list[dict]) -> dict[str, float]:
    """Fraction of rows carrying a non-null value, per field."""
    if not rows:
        return {}
    total = len(rows)
    return {
        key: round(sum(1 for r in rows if r.get(key) is not None) / total, 4)
        for key in Stock.model_fields
    }


def _check_minor_units(rows: list[dict], fx: dict[str, float]) -> None:
    """Verify the USD cap of pence/agorot/fils-quoted rows end to end.

    London, Tel Aviv, Johannesburg and Kuwait quote prices in a minor unit but
    report market cap in the major one. Getting that wrong is invisible in the
    output -- the caps stay internally consistent, they are just uniformly 100x
    off -- so it once shipped a dataset where Shell was a $2B company.

    Reconstructing the USD cap from price x shares, independently of the
    conversion under test, catches it. Rows are skipped rather than failed when
    Yahoo's share count covers only one line of a cross-listed company.
    """
    checked, bad = 0, []
    for r in rows:
        entry = MINOR_UNITS.get(str(r.get("currency")))
        if entry is None:
            continue
        major, divisor = entry
        rate = fx.get(major) or fx.get(major.upper())
        price, shares, usd = r.get("price"), r.get("shares_outstanding"), r.get("market_cap_usd")
        if not (rate and price and shares and usd):
            continue
        expected = price * shares * rate / divisor
        checked += 1
        ratio = usd / expected
        if not 0.5 <= ratio <= 2.0:
            bad.append(f"{r['symbol']} ({r.get('currency')}) is {ratio:.3g}x expected")

    if checked and len(bad) > max(2, 0.1 * checked):
        raise ValidationError(
            f"{len(bad)}/{checked} minor-unit rows have an implausible USD market "
            f"cap - the pence/major-unit conversion is wrong: " + "; ".join(bad[:5])
        )
    if bad:
        log.warning("%d/%d minor-unit rows off vs price x shares: %s",
                    len(bad), checked, "; ".join(bad[:5]))
    elif checked:
        log.info("minor-unit market caps verified (%d rows)", checked)


def _check_ranking(rows: list[dict]) -> None:
    """The table's headline promise: rank 1 is the largest company.

    Rows are *selected* by index weight but must be *ranked* by market cap, and
    the two orders differ a lot -- index weight is free-float adjusted, so a
    recent listing with a small float (SpaceX) sat at rank 338 with the seventh
    largest market cap on the planet. Publishing weight order under a heading
    that says market cap is the one error a reader cannot spot for themselves.
    """
    ranks = [r.get("rank") for r in rows]
    if ranks != list(range(1, len(rows) + 1)):
        raise ValidationError(
            "rank is not a dense 1..N sequence in row order - _rank_by_market_cap "
            "did not run, or something reordered the rows after it"
        )
    caps = [r.get("market_cap_usd") for r in rows]
    out_of_order = [
        f"#{i + 1} {rows[i]['symbol']} (${caps[i] / 1e9:.0f}B) above "
        f"#{i + 2} {rows[i + 1]['symbol']} (${caps[i + 1] / 1e9:.0f}B)"
        for i in range(len(caps) - 1)
        if caps[i] is not None and caps[i + 1] is not None and caps[i] < caps[i + 1]
    ]
    if out_of_order:
        raise ValidationError(
            f"{len(out_of_order)} rows are not in descending market-cap order: "
            + "; ".join(out_of_order[:3])
        )
    first_null = next((i for i, c in enumerate(caps) if c is None), None)
    if first_null is not None and any(c is not None for c in caps[first_null:]):
        raise ValidationError("rows without a market cap must rank last")


# Fields Yahoo quotes as a percentage and the pipeline converts to a fraction.
# The typical value each should land near once converted; an order-of-magnitude
# miss means the upstream convention changed under us.
_RATE_SANITY = {"dividend_yield": 0.25, "debt_to_equity": 10.0}


def _check_rate_units(rows: list[dict]) -> None:
    """Catch a silent unit flip in the fields that arrive as percentages.

    yfinance has changed ``dividendYield`` between percent and fraction before.
    Either mistake is invisible in the JSON -- the numbers stay plausible-looking
    -- and only shows up on the page as a 44% dividend yield.
    """
    for field, ceiling in _RATE_SANITY.items():
        values = sorted(r[field] for r in rows if r.get(field) is not None)
        if not values:
            continue
        median = values[len(values) // 2]
        if median > ceiling:
            raise ValidationError(
                f"median {field} is {median:.3g}, above {ceiling:g} - Yahoo has "
                f"probably switched this field between percent and fraction; "
                f"check the conversion in build._rate"
            )


def validate(rows: list[dict], fx: dict[str, float] | None = None) -> dict[str, float]:
    """Abort the run rather than publish a degraded dataset."""
    if len(rows) < C.MIN_ROWS:
        raise ValidationError(
            f"only {len(rows)} rows built, minimum is {C.MIN_ROWS}. "
            "Upstream source likely failed - refusing to overwrite good data."
        )
    cov = coverage(rows)

    missing_mcap = 1 - cov.get("market_cap_usd", 0)
    if missing_mcap > C.MAX_MISSING_MARKET_CAP:
        raise ValidationError(
            f"{missing_mcap:.1%} of rows missing market cap "
            f"(limit {C.MAX_MISSING_MARKET_CAP:.0%})"
        )

    missing_pe = 1 - cov.get("trailing_pe", 0)
    if missing_pe > C.MAX_MISSING_PE:
        raise ValidationError(
            f"{missing_pe:.1%} of rows missing trailing P/E "
            f"(limit {C.MAX_MISSING_PE:.0%})"
        )

    _check_ranking(rows)
    _check_rate_units(rows)

    if fx:
        _check_minor_units(rows, fx)

    log.info("validation passed: %d rows, %.0f%% market cap, %.0f%% P/E, %.0f%% 10y return",
             len(rows), cov.get("market_cap_usd", 0) * 100,
             cov.get("trailing_pe", 0) * 100, cov.get("return_10y", 0) * 100)
    return cov

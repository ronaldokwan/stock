"""Currency units and USD conversion.

Both ``build`` (which converts) and ``schema`` (which verifies the conversion)
need this, and it belongs to neither: it used to live in ``build``, which forced
``schema`` into a function-local ``from .build import MINOR_UNITS`` to dodge the
import cycle. Its own module makes the cycle go away rather than working around
it.
"""
from __future__ import annotations

# Some exchanges quote PRICES in a minor unit (London in pence, Tel Aviv in
# agorot, Kuwait in fils) while Yahoo reports MARKET CAP for the same ticker in
# the major unit. So a market cap converts at the major-currency rate, and only
# a price needs dividing -- applying the divisor to the cap as well is what once
# made Shell look like a $2B company. ``schema.validate`` checks these rows end
# to end so that mistake cannot ship again.
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


def to_usd(value, currency, fx):
    """Convert a local-currency market cap to USD."""
    if value is None or not currency:
        return None
    cur = major_currency(currency)
    rate = fx.get(cur) or fx.get(cur.upper())
    return None if rate is None else float(value) * rate


def as_fraction(value):
    """Yahoo's percent-quoted rates -> the fraction every other rate field uses.

    ``dividendYield`` and ``debtToEquity`` come back as percentages (2.2 means
    2.2%, 55.9 means 0.56x) while ``profitMargins``, ``returnOnEquity`` and the
    returns come back as fractions. Storing both conventions in one row is what
    made the table render NVIDIA's 0.44% dividend as 44%. Everything published
    here is a fraction; ``schema.validate`` fails the run if a source silently
    switches convention.
    """
    return None if value is None else float(value) / 100.0

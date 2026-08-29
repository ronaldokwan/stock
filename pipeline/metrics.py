"""Pure metric calculations. No I/O — these are the unit-tested core."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# A monthly series may not have a bar exactly N years back; allow this much slack
# before declaring the window unavailable.
TOLERANCE_DAYS = 45


def cagr(series: pd.Series, years: int) -> float | None:
    """Annualised total return over the trailing ``years``.

    Returns ``None`` — never 0.0 — when the series does not reach far enough
    back, so a 2024 IPO cannot masquerade as having a 20-year track record.
    """
    if series is None or len(series) < 2:
        return None
    s = series.dropna().sort_index()
    if s.empty or len(s) < 2:
        return None

    end_date = s.index[-1]
    target = end_date - pd.DateOffset(years=years)

    if s.index[0] > target + pd.Timedelta(days=TOLERANCE_DAYS):
        return None                                  # history too short

    window = s.loc[:target]
    start_val = float(window.iloc[-1]) if not window.empty else float(s.iloc[0])
    start_date = window.index[-1] if not window.empty else s.index[0]
    end_val = float(s.iloc[-1])

    if start_val <= 0 or end_val <= 0:
        return None

    elapsed = (end_date - start_date).days / 365.25
    if elapsed < years - (TOLERANCE_DAYS / 365.25):
        return None
    if elapsed <= 0:
        return None

    try:
        return (end_val / start_val) ** (1.0 / elapsed) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def series_cagr(values: dict[str, float], years: int) -> float | None:
    """CAGR over a ``{fiscal_year: value}`` mapping (revenue, EPS, ...).

    Negative or zero endpoints make growth rates meaningless, so those return
    ``None`` rather than a misleading number.
    """
    if not values:
        return None
    pairs = sorted((int(y), float(v)) for y, v in values.items() if v is not None)
    if len(pairs) < 2:
        return None

    end_year, end_val = pairs[-1]
    target_year = end_year - years
    older = [p for p in pairs if p[0] <= target_year]
    if not older:
        return None
    start_year, start_val = older[-1]

    span = end_year - start_year
    if span <= 0 or start_val <= 0 or end_val <= 0:
        return None
    try:
        return (end_val / start_val) ** (1.0 / span) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def max_drawdown(series: pd.Series) -> float | None:
    """Largest peak-to-trough decline, as a negative fraction."""
    if series is None or len(series) < 2:
        return None
    s = series.dropna()
    if s.empty:
        return None
    peak = s.cummax()
    dd = (s / peak) - 1.0
    val = float(dd.min())
    return val if math.isfinite(val) else None


def annualised_vol(series: pd.Series, years: int = 5) -> float | None:
    """Annualised stdev of monthly log returns over the trailing window."""
    if series is None or len(series) < 13:
        return None
    s = series.dropna().sort_index()
    cutoff = s.index[-1] - pd.DateOffset(years=years)
    s = s.loc[s.index >= cutoff]
    if len(s) < 13:
        return None
    rets = np.log(s / s.shift(1)).dropna()
    if len(rets) < 12:
        return None
    val = float(rets.std() * math.sqrt(12))
    return val if math.isfinite(val) else None


def pct_from_high(price: float | None, high: float | None) -> float | None:
    if not price or not high or high <= 0:
        return None
    return (price / high) - 1.0


def clean(value):
    """Coerce NaN/inf/pandas-NA to None so the JSON output stays valid."""
    if value is None:
        return None
    if isinstance(value, (int, float, np.floating, np.integer)):
        f = float(value)
        return None if not math.isfinite(f) else f
    if pd.isna(value):
        return None
    return value

"""Unit tests for the CAGR/risk maths — the part that must never be silently wrong."""
import numpy as np
import pandas as pd
import pytest

from pipeline.metrics import (annualised_vol, cagr, clean, max_drawdown,
                              pct_from_high, sanitise_prices, series_cagr)


def monthly(start: str, months: int, monthly_rate: float, first: float = 100.0):
    idx = pd.date_range(start=start, periods=months, freq="ME")
    return pd.Series([first * (1 + monthly_rate) ** i for i in range(months)], index=idx)


class TestCAGR:
    def test_exact_doubling_over_ten_years(self):
        # 10 years of exactly 2x growth -> 2^(1/10)-1 = 7.177%
        idx = pd.date_range("2016-01-31", periods=121, freq="ME")
        vals = [100 * (2 ** (i / 120)) for i in range(121)]
        got = cagr(pd.Series(vals, index=idx), 10)
        assert got == pytest.approx(0.07177, abs=1e-4)

    def test_flat_series_is_zero(self):
        assert cagr(monthly("2006-01-31", 250, 0.0), 20) == pytest.approx(0.0, abs=1e-9)

    def test_short_history_returns_none_not_zero(self):
        """A 3-year-old listing must not report a 10- or 20-year CAGR."""
        s = monthly("2023-01-31", 36, 0.01)
        assert cagr(s, 10) is None
        assert cagr(s, 20) is None
        assert cagr(s, 1) is not None

    def test_boundary_just_inside_tolerance(self):
        # ~10 years minus 1 month of data still resolves via the 45-day tolerance
        s = monthly("2016-02-29", 121, 0.005)
        assert cagr(s, 10) is not None

    def test_negative_and_zero_prices_return_none(self):
        idx = pd.date_range("2006-01-31", periods=250, freq="ME")
        assert cagr(pd.Series([0.0] * 250, index=idx), 10) is None

    def test_empty_and_single_point(self):
        assert cagr(pd.Series(dtype=float), 5) is None
        assert cagr(pd.Series([100.0], index=pd.to_datetime(["2026-01-31"])), 5) is None

    def test_none_input(self):
        assert cagr(None, 10) is None


class TestSeriesCAGR:
    def test_revenue_doubling_over_ten_years(self):
        vals = {str(2016 + i): 1000 * (2 ** (i / 10)) for i in range(11)}
        assert series_cagr(vals, 10) == pytest.approx(0.07177, abs=1e-4)

    def test_insufficient_years_returns_none(self):
        assert series_cagr({"2023": 100, "2024": 110, "2025": 120}, 10) is None

    def test_negative_earnings_returns_none(self):
        vals = {str(2016 + i): -50.0 for i in range(11)}
        assert series_cagr(vals, 10) is None

    def test_empty(self):
        assert series_cagr({}, 5) is None


class TestRisk:
    def test_max_drawdown_of_fifty_percent(self):
        idx = pd.date_range("2024-01-31", periods=4, freq="ME")
        s = pd.Series([100.0, 200.0, 100.0, 150.0], index=idx)
        assert max_drawdown(s) == pytest.approx(-0.5)

    def test_vol_of_flat_series_is_zero(self):
        assert annualised_vol(monthly("2022-01-31", 60, 0.0)) == pytest.approx(0.0, abs=1e-9)

    def test_pct_from_high(self):
        assert pct_from_high(80, 100) == pytest.approx(-0.2)
        assert pct_from_high(None, 100) is None
        assert pct_from_high(80, 0) is None


class TestClean:
    def test_nan_and_inf_become_none(self):
        assert clean(float("nan")) is None
        assert clean(float("inf")) is None
        assert clean(np.nan) is None

    def test_valid_numbers_pass_through(self):
        assert clean(1.5) == 1.5
        assert clean(0) == 0.0
        assert clean("USD") == "USD"
        assert clean(None) is None


class TestExchangeMap:
    """Ticker -> Yahoo symbol mapping, including the fiddly regional cases."""

    def test_us_share_class_uses_dash(self):
        from pipeline.exchange_map import candidates
        assert candidates("BRK.B", "USD") == ["BRK-B"]

    def test_hong_kong_zero_pads_to_four(self):
        from pipeline.exchange_map import candidates
        assert candidates("700", "HKD") == ["0700.HK"]

    def test_korean_a_prefix_is_stripped(self):
        from pipeline.exchange_map import candidates
        assert candidates("A000660", "KRW")[0] == "000660.KS"

    def test_euro_is_ambiguous_and_ordered(self):
        from pipeline.exchange_map import candidates, is_ambiguous
        assert is_ambiguous("EUR")
        assert candidates("MC", "EUR")[0] == "MC.PA"

    def test_unknown_currency_yields_nothing(self):
        from pipeline.exchange_map import candidates
        assert candidates("ABC", "XYZ") == []

    def test_european_share_class_separators(self):
        """Holdings files write share classes three different ways; Yahoo wants one."""
        from pipeline.exchange_map import candidates
        assert 'BA.L' in candidates('BA.', 'GBP')          # trailing dot
        assert 'ATCO-B.ST' in candidates('ATCO B', 'SEK')  # space
        assert 'NOVO-B.CO' in candidates('NOVOB', 'DKK')   # no separator at all

    def test_no_malformed_double_separator(self):
        from pipeline.exchange_map import candidates
        for sym in candidates('BA.', 'GBP') + candidates('RR.', 'GBP'):
            assert '-.' not in sym and '..' not in sym


class TestSanitisePrices:
    """Yahoo's adjusted history carries bars that cannot be prices. They have to
    go before any metric reads the series: one negative bar made Acciona's max
    drawdown -747%, and one pence/pounds bar put 3i's volatility at 537%."""

    def _series(self, values, start="2020-01-31"):
        idx = pd.date_range(start=start, periods=len(values), freq="ME")
        return pd.Series([float(v) for v in values], index=idx)

    def test_drops_negative_adjusted_closes(self):
        out = sanitise_prices(self._series([-1.7, -1.6, 50, 52, 54, 56]))
        assert list(out) == [50, 52, 54, 56]

    def test_drawdown_stays_within_minus_one_hundred_percent(self):
        """The bug this exists to stop: a negative bar reported a -747% decline."""
        # The negative bar has to follow a positive peak, as Acciona's does.
        dirty = self._series([100, 90, -1.7, 95, 100])
        assert max_drawdown(dirty) < -1.0
        assert max_drawdown(sanitise_prices(dirty)) == pytest.approx(-0.1)

    def test_drops_an_isolated_hundred_x_bar(self):
        out = sanitise_prices(self._series([2000, 2100, 20.5, 2200, 2300]))
        assert list(out) == [2000, 2100, 2200, 2300]

    def test_keeps_a_genuine_crash_that_does_not_snap_back(self):
        """A real collapse persists; only a bar that reverts is a data error."""
        values = [100, 100, 10, 9, 11, 10]
        assert list(sanitise_prices(self._series(values))) == values

    def test_keeps_ordinary_volatility_untouched(self):
        values = [100, 130, 95, 150, 80, 140]
        assert list(sanitise_prices(self._series(values))) == values

    def test_short_series_survive(self):
        assert list(sanitise_prices(self._series([100, 250]))) == [100, 250]
        assert len(sanitise_prices(pd.Series(dtype=float))) == 0

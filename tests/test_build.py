"""Tests for dataset assembly: currency conversion and stale-value carry-over."""
import json

import pandas as pd
import pytest

from pipeline import build, schema
from pipeline.build import _rate, _sparkline, _to_usd


class TestCurrencyConversion:
    FX = {'USD': 1.0, 'EUR': 1.10, 'GBP': 1.35, 'JPY': 0.0062}

    def test_usd_passes_through(self):
        assert _to_usd(1000, 'USD', self.FX) == 1000

    def test_converts_at_spot(self):
        assert _to_usd(1000, 'EUR', self.FX) == pytest.approx(1100)

    def test_pence_quoted_cap_uses_the_major_unit_rate(self):
        """Yahoo quotes LSE prices in pence but reports market cap in pounds.

        Dividing by 100 here made Shell look like a $2B company.
        """
        assert _to_usd(10_000, 'GBp', self.FX) == pytest.approx(13_500.0)
        assert _to_usd(10_000, 'GBP', self.FX) == pytest.approx(13_500.0)

    def test_other_minor_units_map_to_their_major_currency(self):
        fx = {'ILS': 0.30, 'ZAR': 0.055, 'KWD': 3.25}
        assert _to_usd(100, 'ILA', fx) == pytest.approx(30.0)
        assert _to_usd(100, 'ZAc', fx) == pytest.approx(5.5)
        assert _to_usd(100, 'KWF', fx) == pytest.approx(325.0)

    def test_unknown_currency_returns_none(self):
        assert _to_usd(1000, 'XYZ', self.FX) is None

    def test_missing_inputs(self):
        assert _to_usd(None, 'EUR', self.FX) is None
        assert _to_usd(1000, None, self.FX) is None


class TestSparkline:
    def test_downsamples_to_requested_points(self):
        s = pd.Series(range(500), dtype=float,
                      index=pd.date_range('1990-01-31', periods=500, freq='ME'))
        assert len(_sparkline(s, points=60)) == 60

    def test_short_series_passes_through(self):
        s = pd.Series([1.0, 2.0, 3.0],
                      index=pd.date_range('2026-01-31', periods=3, freq='ME'))
        assert _sparkline(s, points=60) == [1.0, 2.0, 3.0]

    def test_empty(self):
        assert _sparkline(pd.Series(dtype=float)) == []
        assert _sparkline(None) == []


class TestStaleCarryOver:
    """Values persist across a failed fetch, but a real null is respected."""

    def _universe(self):
        return pd.DataFrame([{
            'symbol': 'AAA', 'name': 'Acme', 'local_ticker': 'AAA',
            'sedol': None, 'weight': 1.0, 'currency': 'USD',
        }])

    def _previous(self, tmp_path, monkeypatch, rows):
        path = tmp_path / 'stocks.json'
        path.write_text(json.dumps(rows), encoding='utf-8')
        monkeypatch.setattr(build, 'STOCKS_JSON', path)

    def test_failed_fetch_keeps_the_previous_value(self, tmp_path, monkeypatch):
        self._previous(tmp_path, monkeypatch, [
            {'symbol': 'AAA', 'market_cap_usd': 5e11, 'trailing_pe': 20.0,
             'sector': 'Tech', 'country': 'United States', 'name': 'Acme',
             'return_10y': 0.1, 'return_20y': 0.2},
        ])
        # No quote data at all for AAA -> the fetch failed.
        rows, _, _ = build.build(self._universe(), {}, {}, {}, {}, {'USD': 1.0})
        assert rows[0]['market_cap_usd'] == 5e11
        assert rows[0]['trailing_pe'] == 20.0
        assert rows[0]['stale'] is True

    def test_successful_fetch_with_a_real_null_is_respected(self, tmp_path, monkeypatch):
        """A company that turns lossmaking has no P/E; the old one must not stick."""
        self._previous(tmp_path, monkeypatch, [
            {'symbol': 'AAA', 'market_cap_usd': 5e11, 'trailing_pe': 20.0},
        ])
        quotes = {'AAA': {'marketCap': 6e11, 'currency': 'USD', 'trailingPE': None}}
        rows, _, _ = build.build(self._universe(), quotes, {}, {}, {}, {'USD': 1.0})
        assert rows[0]['market_cap_usd'] == pytest.approx(6e11)
        assert rows[0]['trailing_pe'] is None
        assert rows[0]['stale'] is False

    def test_no_previous_data_is_not_stale(self, tmp_path, monkeypatch):
        self._previous(tmp_path, monkeypatch, [])
        quotes = {'AAA': {'marketCap': 1e9, 'currency': 'USD'}}
        rows, _, _ = build.build(self._universe(), quotes, {}, {}, {}, {'USD': 1.0})
        assert rows[0]['stale'] is False


class TestRanking:
    """The table is advertised as ranked by market capitalisation. Rows arrive in
    index-weight order, which is free-float adjusted and so is not the same
    thing -- it once put a $53B chipmaker at rank 900 beside a $0.5B REIT."""

    def test_rank_follows_market_cap_not_input_order(self):
        rows = [{'symbol': 'SMALL', 'rank': 1, 'market_cap_usd': 1e9},
                {'symbol': 'BIG', 'rank': 2, 'market_cap_usd': 9e9},
                {'symbol': 'MID', 'rank': 3, 'market_cap_usd': 5e9}]
        out = build._rank_by_market_cap(rows)
        assert [r['symbol'] for r in out] == ['BIG', 'MID', 'SMALL']
        assert [r['rank'] for r in out] == [1, 2, 3]

    def test_rows_without_a_market_cap_rank_last(self):
        rows = [{'symbol': 'NONE', 'rank': 1, 'market_cap_usd': None},
                {'symbol': 'SOME', 'rank': 2, 'market_cap_usd': 1e9}]
        out = build._rank_by_market_cap(rows)
        assert [r['symbol'] for r in out] == ['SOME', 'NONE']


class TestMinorUnitGuard:
    """The pence bug is invisible in the output -- every London cap stays
    internally consistent, just uniformly 100x small -- so validation
    reconstructs the USD cap from price x shares independently."""

    FX = {'GBP': 1.35, 'USD': 1.0}

    def _row(self, usd):
        return {'symbol': 'SHEL.L', 'currency': 'GBp', 'price': 3344.5,
                'shares_outstanding': 5_504_900_949.0, 'market_cap_usd': usd}

    def test_correct_conversion_passes(self):
        from pipeline import schema
        good = self._row(3344.5 * 5_504_900_949.0 * 1.35 / 100)
        schema._check_minor_units([self._row(good['market_cap_usd'])] * 10, self.FX)

    def test_hundredfold_error_is_rejected(self):
        from pipeline import schema
        bad = 3344.5 * 5_504_900_949.0 * 1.35 / 10_000
        with pytest.raises(schema.ValidationError, match='pence'):
            schema._check_minor_units([self._row(bad)] * 10, self.FX)

    def test_kuwaiti_fils_divide_by_a_thousand(self):
        assert build.MINOR_UNITS['KWF'] == ('KWD', 1000)
        assert build.major_currency('KWF') == 'KWD'
        assert build.major_currency('USD') == 'USD'


class TestPercentQuotedRates:
    """Yahoo mixes conventions: margins and returns arrive as fractions, but
    dividend yield and debt/equity arrive as percentages. Publishing both as-is
    rendered NVIDIA's 0.44% dividend as 44% and its 0.17x gearing as 17x."""

    def test_dividend_yield_becomes_a_fraction(self):
        assert _rate(0.44) == pytest.approx(0.0044)
        assert _rate(15.81) == pytest.approx(0.1581)

    def test_debt_to_equity_becomes_a_ratio(self):
        assert _rate(55.9) == pytest.approx(0.559)

    def test_missing_stays_missing(self):
        assert _rate(None) is None

    def test_the_conversion_reaches_the_published_row(self):
        universe = pd.DataFrame([{'symbol': 'AAA', 'name': 'A', 'local_ticker': 'A',
                                  'sedol': 'S', 'weight': 1.0, 'currency': 'USD',
                                  'merged_symbols': []}])
        quotes = {'AAA': {'marketCap': 1e9, 'currency': 'USD',
                          'dividendYield': 2.2, 'debtToEquity': 55.9}}
        rows, _, _ = build.build(universe, quotes, {}, {}, {}, {'USD': 1.0})
        assert rows[0]['dividend_yield'] == pytest.approx(0.022)
        assert rows[0]['debt_to_equity'] == pytest.approx(0.559)


class TestPublishGuards:
    """The two mistakes that look completely normal in the published JSON: rows
    left in index-weight order under a heading that says market cap, and a rate
    that silently switches between percent and fraction upstream."""

    def _rows(self, caps):
        return [{'symbol': f'S{i}', 'rank': i + 1, 'market_cap_usd': c}
                for i, c in enumerate(caps)]

    def test_weight_ordered_rows_are_rejected(self):
        rows = self._rows([9e11, 8e11, 5e11, 7e11])
        with pytest.raises(schema.ValidationError, match='descending market-cap'):
            schema._check_ranking(rows)

    def test_market_cap_order_passes(self):
        schema._check_ranking(self._rows([9e11, 8e11, 7e11, 5e11]))

    def test_rank_must_be_a_dense_sequence(self):
        rows = self._rows([9e11, 8e11])
        rows[1]['rank'] = 7
        with pytest.raises(schema.ValidationError, match='dense'):
            schema._check_ranking(rows)

    def test_missing_market_caps_must_sit_at_the_bottom(self):
        rows = self._rows([9e11, None, 7e11])
        with pytest.raises(schema.ValidationError, match='rank last'):
            schema._check_ranking(rows)
        schema._check_ranking(self._rows([9e11, 7e11, None]))

    def test_percent_quoted_dividend_yield_is_caught(self):
        rows = [{'dividend_yield': v} for v in (0.44, 2.2, 5.0, 15.8)]
        with pytest.raises(schema.ValidationError, match='dividend_yield'):
            schema._check_rate_units(rows)

    def test_fractions_pass(self):
        schema._check_rate_units([{'dividend_yield': v, 'debt_to_equity': v * 25}
                                  for v in (0.0044, 0.022, 0.05, 0.158)])

"""Tests for parsing Yahoo's response shapes.

The caching and retry machinery this module leans on is generic and lives
in ``fetcher``; its tests are in test_fetcher.py.
"""
import pandas as pd

from pipeline.yahoo import _extract_close


class TestExtractClose:
    def _multi(self):
        idx = pd.date_range("2026-01-31", periods=3, freq="ME")
        cols = pd.MultiIndex.from_product([["Close", "Volume"], ["AAA", "BBB"]])
        return pd.DataFrame(
            [[1.0, 10.0, 5, 5], [2.0, 20.0, 5, 5], [3.0, float("nan"), 5, 5]],
            index=idx, columns=cols,
        )

    def test_multi_symbol_frame(self):
        out = _extract_close(self._multi(), ["AAA", "BBB"])
        assert set(out) == {"AAA", "BBB"}
        assert len(out["AAA"]) == 3
        assert len(out["BBB"]) == 2          # the NaN row is dropped

    def test_all_nan_symbol_is_omitted(self):
        idx = pd.date_range("2026-01-31", periods=2, freq="ME")
        cols = pd.MultiIndex.from_product([["Close"], ["DEAD"]])
        df = pd.DataFrame([[float("nan")], [float("nan")]], index=idx, columns=cols)
        assert _extract_close(df, ["DEAD"]) == {}

    def test_single_symbol_frame(self):
        idx = pd.date_range("2026-01-31", periods=2, freq="ME")
        df = pd.DataFrame({"Close": [1.0, 2.0], "Volume": [5, 5]}, index=idx)
        out = _extract_close(df, ["SOLO"])
        assert list(out) == ["SOLO"]

    def test_empty_and_none(self):
        assert _extract_close(pd.DataFrame(), ["A"]) == {}
        assert _extract_close(None, ["A"]) == {}

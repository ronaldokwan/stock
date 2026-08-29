"""Tests for the Yahoo layer's resilience machinery."""
import pandas as pd

from pipeline.yahoo import _Breaker, _extract_close, _is_rate_limit


class TestBreaker:
    def test_trips_after_threshold_consecutive_failures(self):
        b = _Breaker(3)
        for _ in range(2):
            b.record(limited=True)
        assert not b.tripped
        b.record(limited=True)
        assert b.tripped

    def test_a_success_resets_the_count(self):
        b = _Breaker(3)
        b.record(limited=True)
        b.record(limited=True)
        b.record(limited=False)
        b.record(limited=True)
        assert not b.tripped

    def test_stays_tripped_once_tripped(self):
        """Skipped tasks report no failure; that must not un-trip the breaker."""
        b = _Breaker(2)
        b.record(limited=True)
        b.record(limited=True)
        assert b.tripped
        for _ in range(10):
            b.record(limited=False)
        assert b.tripped


class TestRateLimitDetection:
    def test_recognises_yfinance_rate_limit_error(self):
        class YFRateLimitError(Exception):
            pass
        assert _is_rate_limit(YFRateLimitError("Too Many Requests. Rate limited."))

    def test_recognises_http_429(self):
        assert _is_rate_limit(Exception("HTTP 429 returned"))

    def test_ignores_unrelated_errors(self):
        assert not _is_rate_limit(ValueError("no timezone found"))
        assert not _is_rate_limit(KeyError("Close"))


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

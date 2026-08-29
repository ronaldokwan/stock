"""Tests for the shared caching and retry machinery.

This logic used to be copied across four fetch functions, where it could only be
exercised by hitting Yahoo. Extracted, it can be tested directly -- including the
paths that matter most and are hardest to provoke against a live source: a
half-written cache file, a chunk that fails for a reason other than throttling,
and a stage that gives up mid-queue.
"""
import time

import pytest

from pipeline import fetcher
from pipeline.fetcher import Breaker


class TestBreaker:
    def test_trips_after_threshold_consecutive_failures(self):
        b = Breaker(3)
        for _ in range(2):
            b.record(limited=True)
        assert not b.tripped
        b.record(limited=True)
        assert b.tripped

    def test_a_success_resets_the_count(self):
        b = Breaker(3)
        b.record(limited=True)
        b.record(limited=True)
        b.record(limited=False)
        b.record(limited=True)
        assert not b.tripped

    def test_stays_tripped_once_tripped(self):
        """Skipped tasks report no failure; that must not un-trip the breaker."""
        b = Breaker(2)
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
        assert fetcher.is_rate_limit(YFRateLimitError("Too Many Requests. Rate limited."))

    def test_recognises_http_429(self):
        assert fetcher.is_rate_limit(Exception("HTTP 429 returned"))

    def test_ignores_unrelated_errors(self):
        assert not fetcher.is_rate_limit(ValueError("no timezone found"))
        assert not fetcher.is_rate_limit(KeyError("Close"))


class TestSafeName:
    def test_class_markers_do_not_escape_the_cache_directory(self):
        assert fetcher.safe_name("BRK/B") == "BRK_B"
        assert "/" not in fetcher.safe_name("A/B")
        assert chr(92) not in fetcher.safe_name("A" + chr(92) + "B")


class TestPartition:
    def test_splits_cached_from_missing(self, tmp_path):
        (tmp_path / "AAA.json").write_text('{"v": 1}', encoding="utf-8")
        cached, missing = fetcher.partition(["AAA", "BBB"], tmp_path, ttl=3600)
        assert cached == {"AAA": {"v": 1}}
        assert missing == ["BBB"]

    def test_a_stale_file_counts_as_missing(self, tmp_path):
        path = tmp_path / "AAA.json"
        path.write_text('{"v": 1}', encoding="utf-8")
        old = time.time() - 7200
        import os
        os.utime(path, (old, old))
        cached, missing = fetcher.partition(["AAA"], tmp_path, ttl=3600)
        assert cached == {} and missing == ["AAA"]

    def test_a_corrupt_cache_file_is_refetched_not_raised(self, tmp_path):
        """An interrupted run leaves truncated JSON. That costs one refetch."""
        (tmp_path / "AAA.json").write_text("{not json", encoding="utf-8")
        cached, missing = fetcher.partition(["AAA"], tmp_path, ttl=3600)
        assert cached == {} and missing == ["AAA"]


class TestStore:
    def test_round_trips_through_partition(self, tmp_path):
        fetcher.store("BRK/B", {"v": 2}, tmp_path)
        cached, missing = fetcher.partition(["BRK/B"], tmp_path, ttl=3600)
        assert cached == {"BRK/B": {"v": 2}} and missing == []

    def test_an_unwritable_cache_is_not_fatal(self, tmp_path):
        fetcher.store("AAA", object(), tmp_path)          # not JSON-serialisable
        assert fetcher.partition(["AAA"], tmp_path, ttl=3600)[1] == ["AAA"]


class TestChunked:
    def _run(self, items, fetch, threshold=3, size=2):
        breaker = Breaker(threshold)
        out = list(fetcher.chunked(items, size, fetch, breaker=breaker, label="test"))
        return out, breaker

    def test_covers_every_item_in_order(self, monkeypatch):
        monkeypatch.setattr(fetcher, "pause", lambda: None)
        seen = []
        out, _ = self._run([1, 2, 3, 4, 5], lambda c: seen.append(c) or c)
        assert seen == [[1, 2], [3, 4], [5]]
        assert out == [[1, 2], [3, 4], [5]]

    def test_retries_a_rate_limited_chunk_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(fetcher, "pause", lambda: None)
        monkeypatch.setattr(fetcher, "backoff", lambda *a, **k: None)
        attempts = []

        def flaky(chunk):
            attempts.append(chunk)
            if len(attempts) == 1:
                raise Exception("HTTP 429 rate limited")
            return chunk

        out, breaker = self._run([1, 2], flaky)
        assert len(attempts) == 2 and out == [[1, 2]]
        assert not breaker.tripped

    def test_a_non_rate_limit_error_does_not_retry(self, monkeypatch):
        """One malformed response must not cost four requests."""
        monkeypatch.setattr(fetcher, "pause", lambda: None)
        attempts = []

        def broken(chunk):
            attempts.append(chunk)
            raise ValueError("no timezone found")

        out, breaker = self._run([1, 2], broken)
        assert len(attempts) == 1 and out == [None]
        assert not breaker.tripped                   # one failed chunk, threshold 3

    def test_stops_early_once_the_breaker_trips(self, monkeypatch):
        monkeypatch.setattr(fetcher, "pause", lambda: None)
        attempts = []

        def dead(chunk):
            attempts.append(chunk)
            return []

        out, breaker = self._run(list(range(20)), dead, threshold=2)
        assert breaker.tripped
        # Two empty chunks trip it; the remaining eight are left for next run.
        assert len(attempts) == 2 and len(out) == 2

    def test_no_items_makes_no_requests(self):
        out, _ = self._run([], lambda c: pytest.fail("should not be called"))
        assert out == []


class TestEach:
    def test_yields_only_symbols_with_data(self):
        breaker = Breaker(5)
        values = {"A": {"v": 1}, "B": {}, "C": {"v": 3}}
        got = dict(fetcher.each(["A", "B", "C"], values.get,
                                breaker=breaker, workers=2, label="test"))
        assert got == {"A": {"v": 1}, "C": {"v": 3}}

    def test_none_feeds_the_breaker_and_stops_the_queue(self):
        breaker = Breaker(2)
        started = []

        def limited(symbol):
            started.append(symbol)
            return None

        got = dict(fetcher.each([f"S{i}" for i in range(50)], limited,
                                breaker=breaker, workers=1, label="test"))
        assert got == {} and breaker.tripped
        # The pool still drains, but skipped symbols never call fetch.
        assert len(started) < 50

    def test_an_empty_result_is_not_a_rate_limit(self):
        breaker = Breaker(2)
        list(fetcher.each(["A", "B", "C"], lambda s: {},
                          breaker=breaker, workers=1, label="test"))
        assert not breaker.tripped

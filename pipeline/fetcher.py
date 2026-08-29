"""Disk caching and polite retry — the plumbing every remote source shares.

Four fetches in this pipeline (price history, income statements, batch quotes,
profile enrichment) and one in ``fundamentals`` all have the same shape: check a
per-symbol cache file, collect what is missing, request it in chunks or across a
small thread pool, retry on a rate limit, stop early once the source is clearly
refusing, and write each success back to disk. Written out per source, that was
~250 lines of near-identical plumbing wrapping ~20 lines of "what to ask for and
how to parse it", and the copies had already drifted apart.

The retry policy lives here so it is one thing rather than four. What stays in
each source module is the part that is genuinely about that source: its URL, its
fields, and how to read its response.

Nothing here knows about Yahoo or the SEC. The one source-specific behaviour a
caller may need during a rate limit -- clearing yfinance's stale session crumb
-- is injected as ``on_rate_limit``.
"""
from __future__ import annotations

import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, Iterator

from . import config as C

log = logging.getLogger(__name__)


class Breaker:
    """Stops a stage once the source starts refusing, instead of burning the queue.

    Latching matters: once tripped, the in-flight tasks that get skipped report
    no failure, and a non-latching counter would reset on them and let the stage
    start hammering the source again.
    """

    def __init__(self, threshold: int):
        self.threshold = threshold
        self._consecutive = 0
        self._tripped = False
        self._lock = Lock()

    @property
    def tripped(self) -> bool:
        return self._tripped

    def record(self, *, limited: bool) -> None:
        with self._lock:
            if self._tripped:
                return
            self._consecutive = self._consecutive + 1 if limited else 0
            if self._consecutive >= self.threshold:
                self._tripped = True


def is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "too many requests" in text or "429" in text


def safe_name(symbol: str) -> str:
    """A symbol as a filename. Class markers like ``BRK/B`` are not path-safe."""
    return symbol.replace("/", "_").replace(chr(92), "_")


def fresh(path: Path, ttl: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


def chunks(seq, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def pause() -> None:
    """Jittered gap between requests. The pipeline is polite by default."""
    time.sleep(C.YF_PAUSE + random.uniform(0, C.YF_PAUSE))


def backoff(attempt: int, on_rate_limit: Callable[[], object] | None = None) -> None:
    if attempt == 0 and on_rate_limit is not None:
        on_rate_limit()
    delay = C.YF_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1.5)
    log.info("    rate limited, backing off %.1fs", delay)
    time.sleep(delay)


# ------------------------------------------------------------------- cache ---
def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def partition(symbols: Iterable[str], cache_dir: Path, ttl: float, *,
              suffix: str = ".json", load: Callable[[Path], object] = _read_json,
              ) -> tuple[dict, list[str]]:
    """Split ``symbols`` into what the cache already has and what must be fetched.

    An unreadable cache file counts as missing rather than raising: a truncated
    write from an interrupted run should cost one refetch, not the whole stage.
    """
    cached: dict[str, object] = {}
    missing: list[str] = []
    for symbol in symbols:
        path = cache_dir / f"{safe_name(symbol)}{suffix}"
        if fresh(path, ttl):
            try:
                cached[symbol] = load(path)
                continue
            except Exception:                        # noqa: BLE001
                pass
        missing.append(symbol)
    return cached, missing


def store(symbol: str, value, cache_dir: Path, *, suffix: str = ".json",
          dump: Callable[[Path, object], None] = _write_json) -> None:
    """Best-effort cache write. A cache that cannot be written is not fatal."""
    try:
        dump(cache_dir / f"{safe_name(symbol)}{suffix}", value)
    except Exception:                                # noqa: BLE001
        pass


# ------------------------------------------------------------------ fetch ----
def chunked(items: list, size: int, fetch: Callable[[list], object], *,
            breaker: Breaker, label: str,
            on_rate_limit: Callable[[], object] | None = None,
            ) -> Iterator[object]:
    """Call ``fetch`` on successive chunks, yielding whatever each returns.

    Retries a rate-limited chunk with exponential backoff, gives up on any other
    error (one bad chunk must not end the stage), and stops entirely once the
    breaker trips -- at which point the remaining symbols are simply left for the
    next run, since everything already fetched is cached.

    A falsy result counts as a rate limit for the breaker's purposes: an empty
    chunk after the retries are exhausted is what being blocked looks like.
    """
    total = len(items)
    for n, chunk in enumerate(chunks(items, size), 1):
        if breaker.tripped:
            log.warning("%s: rate limited repeatedly, stopping early "
                        "(%d symbols left for the next run)",
                        label, total - (n - 1) * size)
            return

        result = None
        for attempt in range(C.YF_RETRIES):
            try:
                result = fetch(chunk)
                break
            except Exception as e:                   # noqa: BLE001
                if is_rate_limit(e) and attempt < C.YF_RETRIES - 1:
                    backoff(attempt, on_rate_limit)
                    continue
                log.warning("%s chunk %d failed: %s", label, n, str(e)[:110])
                break

        breaker.record(limited=not result)
        yield result
        log.info("  %s %d/%d", label, min(n * size, total), total)
        pause()


def each(symbols: list[str], fetch: Callable[[str], object], *,
         breaker: Breaker, workers: int, label: str,
         ) -> Iterator[tuple[str, object]]:
    """Run ``fetch`` per symbol across a small pool, yielding truthy results.

    ``fetch`` returns ``None`` to mean "rate limited" (which feeds the breaker),
    a falsy value to mean "no data for this symbol", or the data. Symbols still
    queued when the breaker trips return immediately without a request.

    The result is recorded on the breaker inside the worker, not in this loop.
    Recording as results are *consumed* means a pool that runs ahead of the
    consumer can finish the entire queue before the breaker has seen enough
    failures to trip -- which is precisely the case the breaker exists to stop.
    ``Breaker.record`` is already lock-guarded, so calling it from the pool is
    safe.
    """
    def guarded(symbol: str):
        if breaker.tripped:
            return symbol, None
        value = fetch(symbol)
        breaker.record(limited=value is None)
        return symbol, value

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(guarded, s) for s in symbols]
        for future in as_completed(futures):
            symbol, value = future.result()
            if value:
                yield symbol, value
            done += 1
            if done % 50 == 0:
                log.info("  %s %d/%d", label, done, len(symbols))

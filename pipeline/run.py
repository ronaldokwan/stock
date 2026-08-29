"""Pipeline entry point.

    python -m pipeline.run                  # full run (resumes from cache)
    python -m pipeline.run --limit 25       # fast smoke test
    python -m pipeline.run --max-new 300    # cap fresh fetches this run
    python -m pipeline.run --skip-sec       # price data only

Yahoo rate-limits hard, so a cold run may not finish in one pass. Everything is
cached per symbol: just run it again and it picks up where it stopped.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import build, config as C, fundamentals, quotes as quotes_mod, schema, universe, yahoo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run")
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the N largest holdings (smoke test)")
    parser.add_argument("--max-new", type=int, default=None,
                        help="cap how many uncached symbols to fetch this run")
    parser.add_argument("--profile-budget", type=int, default=None,
                        help="how many sector/quality profiles to top up this run")
    parser.add_argument("--skip-sec", action="store_true",
                        help="skip SEC fundamentals (faster; loses 10y revenue/profit)")
    parser.add_argument("--refresh-holdings", action="store_true",
                        help="re-download the SSGA holdings file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
    )
    log = logging.getLogger("pipeline")
    started = time.time()

    def step(n: int, label: str) -> None:
        log.info("")
        log.info("[%d/7] %s", n, label)

    # 1 ---------------------------------------------------------------------
    step(1, "Universe")
    path = universe.download_holdings(force=args.refresh_holdings)
    holdings = universe.load_holdings(path)
    candidates = universe.resolve(holdings, yahoo.probe, limit=args.limit)
    symbols = candidates["symbol"].tolist()
    if not symbols:
        log.error("no symbols resolved - aborting")
        return 1

    # 2 ---------------------------------------------------------------------
    step(2, f"Price history ({len(symbols)} candidates)")
    history = yahoo.fetch_history(symbols, max_new=args.max_new)

    # 3 ---------------------------------------------------------------------
    step(3, "Quotes (batched)")
    batch = quotes_mod.fetch_batch(symbols, max_new=args.max_new)

    # 4 ---------------------------------------------------------------------
    step(4, "Profiles (sector, quality) - best effort")
    profiles = quotes_mod.fetch_profiles(symbols, max_new=args.profile_budget)
    quotes = quotes_mod.merge(batch, profiles)

    # A market cap is the ranking key, so a row without one cannot earn a place
    # in a market-cap screener.
    with_data = {s for s, q in quotes.items() if q.get("marketCap")}
    log.info("%d/%d symbols have a market cap", len(with_data), len(symbols))
    target = args.limit or C.TARGET_UNIVERSE
    # Yahoo's long name is the same on every listing of a company, so it is what
    # lets finalise() collapse ADRs, dual listings and preferred lines together.
    long_names = {s: (q.get("longName") or q.get("shortName"))
                  for s, q in quotes.items()}
    uni = universe.finalise(candidates, with_data, target, names=long_names)
    universe.save(uni)
    symbols = uni["symbol"].tolist()

    # 5 ---------------------------------------------------------------------
    step(5, "Fundamentals")
    names = dict(zip(uni["symbol"], uni["name"]))
    sec = {} if args.skip_sec else fundamentals.fetch_sec(symbols, names)
    non_sec = [s for s in symbols if s not in sec]
    income = yahoo.fetch_income_history(non_sec, max_new=args.max_new)

    # 6 ---------------------------------------------------------------------
    step(6, "FX rates")
    currencies = [q.get("currency") for q in quotes.values() if q.get("currency")]
    currencies += uni["currency"].dropna().tolist()
    # Pence/agorot quotes need their major-unit rate (GBp -> GBP), not their own.
    currencies = [build.major_currency(c) for c in currencies]
    fx = yahoo.fetch_fx(currencies)

    # 7 ---------------------------------------------------------------------
    step(7, "Build and validate")
    rows, meta, sparks = build.build(uni, quotes, history, sec, income, fx)

    try:
        schema.validate(rows, fx)
    except schema.ValidationError as e:
        if args.limit:
            log.warning("validation skipped for --limit run: %s", e)
        else:
            log.error("VALIDATION FAILED: %s", e)
            log.error("existing data left untouched - re-run to fill the gaps "
                      "(everything fetched so far is cached)")
            return 2

    build.write(rows, meta, sparks)

    log.info("")
    log.info("done in %.1f min - %d stocks", (time.time() - started) / 60, len(rows))
    fb = meta["fundamentals_breakdown"]
    log.info("fundamentals: %d SEC, %d Yahoo, %d none", fb["sec"], fb["yahoo"], fb["none"])
    cov = meta["coverage"]
    for key in ("market_cap_usd", "trailing_pe", "return_10y", "return_20y",
                "revenue_cagr_10y"):
        log.info("  %-18s %.0f%%", key, cov.get(key, 0) * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

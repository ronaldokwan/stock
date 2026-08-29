"""Tests for SEC fact extraction: tag merging, staleness and name matching.

These guard the two subtle correctness traps found while building the pipeline:
filers switch XBRL tags mid-history, and delisted foreign issuers leave stale
figures on EDGAR that must not be presented as current growth.
"""
from datetime import datetime, timezone

import pytest

from pipeline.fundamentals import (IFRS_PROFIT_TAGS, IFRS_REVENUE_TAGS,
                                   NET_INCOME_TAGS, REVENUE_TAGS, _annual_series,
                                   _is_current, build_name_index,
                                   match_cik_by_name, normalise_name)

YEAR = datetime.now(timezone.utc).year


def facts(taxonomy: str, tag_series: dict[str, dict[str, float]], unit="USD"):
    """Build a minimal companyfacts payload with SEC-style CY frames."""
    return {"facts": {taxonomy: {
        tag: {"units": {unit: [
            {"frame": f"CY{y}", "val": v} for y, v in series.items()
        ]}} for tag, series in tag_series.items()
    }}}


class TestTagMerging:
    def test_merges_across_the_asc606_tag_switch(self):
        """Apple's revenue lives in two tags; the merged series must be continuous."""
        payload = facts("us-gaap", {
            "SalesRevenueNet": {str(y): 100.0 + y for y in range(YEAR - 12, YEAR - 8)},
            "RevenueFromContractWithCustomerExcludingAssessedTax":
                {str(y): 200.0 + y for y in range(YEAR - 9, YEAR + 1)},
        })
        series = _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS)
        assert len(series) == 13   # 2 tags, one overlapping year
        assert min(series) == str(YEAR - 12)
        assert max(series) == str(YEAR)

    def test_modern_tag_wins_overlapping_years(self):
        overlap = str(YEAR - 1)
        payload = facts("us-gaap", {
            "SalesRevenueNet": {str(YEAR - 2): 1.0, overlap: 999.0},
            "RevenueFromContractWithCustomerExcludingAssessedTax":
                {overlap: 500.0, str(YEAR): 600.0},
        })
        series = _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS)
        assert series[overlap] == 500.0

    def test_falls_back_to_ifrs_for_foreign_filers(self):
        payload = facts("ifrs-full", {"Revenue": {str(y): float(y)
                                                 for y in range(YEAR - 10, YEAR + 1)}})
        series = _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS)
        assert len(series) == 11

    def test_us_gaap_preferred_over_ifrs(self):
        payload = {"facts": {
            "us-gaap": facts("us-gaap", {"Revenues": {str(YEAR - 1): 1.0,
                                                      str(YEAR): 2.0}})["facts"]["us-gaap"],
            "ifrs-full": facts("ifrs-full", {"Revenue": {str(YEAR - 1): 90.0,
                                                        str(YEAR): 99.0}})["facts"]["ifrs-full"],
        }}
        assert _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS)[str(YEAR)] == 2.0

    def test_quarterly_frames_are_ignored(self):
        payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"frame": f"CY{YEAR}Q1", "val": 10.0},
            {"frame": f"CY{YEAR}Q2", "val": 11.0},
        ]}}}}}
        assert _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS) == {}

    def test_single_datapoint_is_not_a_series(self):
        payload = facts("us-gaap", {"Revenues": {str(YEAR): 1.0}})
        assert _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS) == {}


class TestStaleness:
    def test_delisted_issuer_series_is_rejected(self):
        """Toyota-style: real data on EDGAR, but it stops years ago."""
        payload = facts("ifrs-full", {"Revenue": {str(y): float(y)
                                                 for y in range(2009, 2020)}})
        assert _annual_series(payload, REVENUE_TAGS, IFRS_REVENUE_TAGS) == {}

    def test_current_series_is_kept(self):
        assert _is_current({str(YEAR - 1): 1.0, str(YEAR): 2.0})

    def test_empty_series_is_not_current(self):
        assert not _is_current({})


class TestNameMatching:
    def test_strips_legal_forms(self):
        assert normalise_name("Apple Inc.") == "APPLE"
        assert normalise_name("Shell plc") == "SHELL"
        assert normalise_name("SAP SE") == "SAP"

    def test_ambiguous_names_are_dropped(self):
        """Two registrants normalising alike must not be matched to either."""
        raw = {"0": {"cik_str": 1, "ticker": "AAA", "title": "Acme Corp"},
               "1": {"cik_str": 2, "ticker": "BBB", "title": "Acme Inc"}}
        assert "ACME" not in build_name_index(raw)

    def test_exact_match_wins(self):
        raw = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        assert match_cik_by_name("APPLE INC", build_name_index(raw)) == 320193

    def test_no_match_returns_none(self):
        raw = {"0": {"cik_str": 1, "ticker": "AAA", "title": "Apple Inc."}}
        assert match_cik_by_name("Totally Different Company", build_name_index(raw)) is None

    def test_short_junk_name_is_rejected(self):
        assert match_cik_by_name("A", {}) is None
        assert match_cik_by_name("", {}) is None

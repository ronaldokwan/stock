"""Curated constituents the holdings file does not contain.

The universe is an ETF's holdings rather than its index's constituent list, and
SPGM samples: Saudi Aramco, the ninth largest company in the world, is not in the
file at any weight. These rows therefore bypass ticker resolution and must
survive a truncation that is ordered by an index weight they do not have.
"""
import logging
import math

import pandas as pd
import pytest

from pipeline import universe


def _resolved(*symbols, weights=None):
    """A frame shaped like resolve()'s output."""
    weights = weights or [1.0 / (i + 1) for i in range(len(symbols))]
    return pd.DataFrame([
        {"name": s, "local_ticker": s, "sedol": None, "weight": w,
         "currency": "USD", "symbol": s, "via_override": False}
        for s, w in zip(symbols, weights)
    ])


class TestLoadSupplemental:
    def test_the_shipped_list_parses(self):
        df = universe.load_supplemental()
        assert not df.empty
        assert {"symbol", "name", "currency", "weight", "supplemental"} <= set(df.columns)

    def test_aramco_is_listed(self):
        """The case the mechanism exists for."""
        assert "2222.SR" in set(universe.load_supplemental()["symbol"])

    def test_entries_carry_no_index_weight(self):
        """The fund holds none of these, so a weight would be fabricated."""
        assert all(math.isnan(w) for w in universe.load_supplemental()["weight"])

    def test_entries_are_flagged_as_override_so_dedup_keeps_them(self):
        assert universe.load_supplemental()["via_override"].all()

    def test_a_missing_file_is_not_fatal(self, monkeypatch, tmp_path):
        monkeypatch.setattr(universe, "SUPPLEMENTAL", tmp_path / "absent.yaml")
        assert universe.load_supplemental().empty


class TestWithSupplemental:
    def test_appends_to_the_candidate_frame(self, monkeypatch):
        monkeypatch.setattr(universe, "load_supplemental",
                            lambda: _extra("2222.SR"))
        out = universe.with_supplemental(_resolved("AAA", "BBB"))
        assert list(out["symbol"]) == ["AAA", "BBB", "2222.SR"]
        assert list(out["supplemental"]) == [False, False, True]

    def test_a_symbol_already_in_the_holdings_is_not_duplicated(self, monkeypatch):
        """If the fund starts holding one, the real holding supersedes the entry."""
        monkeypatch.setattr(universe, "load_supplemental", lambda: _extra("AAA"))
        out = universe.with_supplemental(_resolved("AAA", "BBB"))
        assert list(out["symbol"]) == ["AAA", "BBB"]
        assert not out["supplemental"].any()

    def test_an_empty_list_still_flags_every_row(self, monkeypatch):
        monkeypatch.setattr(universe, "load_supplemental", lambda: pd.DataFrame())
        out = universe.with_supplemental(_resolved("AAA"))
        assert list(out["supplemental"]) == [False]


def _extra(symbol):
    return pd.DataFrame([{
        "name": symbol, "local_ticker": symbol, "sedol": None,
        "weight": float("nan"), "currency": "USD", "symbol": symbol,
        "via_override": True, "supplemental": True,
    }])


class TestFinaliseKeepsSupplemental:
    def _frame(self):
        df = _resolved("BIG", "MID", "SMALL", weights=[3.0, 2.0, 1.0])
        df["supplemental"] = False
        return pd.concat([df, _extra("SUPP")], ignore_index=True)

    def test_survives_a_weight_ordered_truncation(self):
        """A weight-ordered cut would always discard a row with no weight."""
        out = universe.finalise(self._frame(),
                                {"BIG", "MID", "SMALL", "SUPP"}, target=2)
        assert set(out["symbol"]) == {"BIG", "SUPP"}

    def test_takes_its_slot_from_the_bottom_of_the_weighted_set(self):
        out = universe.finalise(self._frame(),
                                {"BIG", "MID", "SMALL", "SUPP"}, target=3)
        assert set(out["symbol"]) == {"BIG", "MID", "SUPP"}
        assert len(out) == 3

    def test_the_target_row_count_is_still_honoured(self):
        out = universe.finalise(self._frame(),
                                {"BIG", "MID", "SMALL", "SUPP"}, target=4)
        assert len(out) == 4

    def test_a_supplemental_symbol_with_no_data_is_reported(self, caplog):
        """A curated entry that stops resolving should not vanish silently."""
        caplog.set_level(logging.WARNING, logger="pipeline.universe")
        out = universe.finalise(self._frame(), {"BIG", "MID", "SMALL"}, target=4)
        assert "SUPP" not in set(out["symbol"])
        assert "SUPP" in caplog.text

    def test_frames_without_the_column_are_unaffected(self):
        """finalise predates this feature and is called without the flag in tests."""
        df = _resolved("BIG", "MID", weights=[2.0, 1.0])
        out = universe.finalise(df, {"BIG", "MID"}, target=1)
        assert list(out["symbol"]) == ["BIG"]


class TestPublishedShape:
    def test_a_supplemental_row_publishes_a_null_index_weight(self):
        """NaN rather than 0.0, so the table shows a dash, not a fabricated zero."""
        from pipeline import build

        # Built directly rather than through with_supplemental(), which would
        # also append the real shipped list.
        indexed = _resolved("AAA")
        indexed["supplemental"] = False
        uni = pd.concat([indexed, _extra("2222.SR")], ignore_index=True)
        quotes = {"AAA": {"marketCap": 1e9, "currency": "USD"},
                  "2222.SR": {"marketCap": 9e11, "currency": "SAR"}}
        rows, _, _ = build.build(uni, quotes, {}, {}, {}, {"USD": 1.0, "SAR": 0.2666})
        by = {r["symbol"]: r for r in rows}
        assert by["2222.SR"]["index_weight"] is None
        assert by["2222.SR"]["rank"] == 1          # ranked on market cap like any row
        assert by["AAA"]["index_weight"] == pytest.approx(1.0)

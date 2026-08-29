"""Ticker resolution has to identify a company, not just find a live symbol.

A local ticker plus a currency is not unique. `SAN` in euros is Banco Santander
in Madrid *and* Sanofi in Paris; both return data, so accepting the first suffix
that responded published Sanofi under Santander's index weight and dropped
Santander from the table. The probe therefore returns names, and resolution
prefers a candidate whose name agrees with the holding.

The name check may only ever *prefer* a candidate, never reject one: companies
rename themselves (General Electric answers to "GE Aerospace"), so a mismatch is
weak evidence and treating it as fatal would drop good rows.
"""
import json

import pandas as pd
import pytest

from pipeline import universe
from pipeline.universe import _same_company


class TestSameCompany:
    def test_the_collision_this_exists_for(self):
        assert _same_company("BANCO SANTANDER SA", "Banco Santander, S.A.")
        assert not _same_company("BANCO SANTANDER SA", "Sanofi")

    def test_linde_is_not_linedata(self):
        assert _same_company("LINDE PLC", "Linde plc")
        assert not _same_company("LINDE PLC", "Linedata Services S.A.")

    def test_kingspan_is_not_kerry(self):
        assert not _same_company("KINGSPAN GROUP PLC", "Kerry Group plc")

    def test_truncated_ssga_names_still_match(self):
        """SSGA truncates: "MANUFAC" has to pair with "Manufacturing"."""
        assert _same_company("TAIWAN SEMICONDUCTOR MANUFAC",
                             "Taiwan Semiconductor Manufacturing Company Limited")

    def test_accents_are_folded(self):
        assert _same_company("NESTLE SA REG", "Nestlé S.A.")
        assert _same_company("L OREAL", "L'Oréal S.A.")  # SSGA drops the apostrophe

    def test_one_shared_word_is_not_enough_when_there_are_others(self):
        """Otherwise China Mobile answers to China Life."""
        assert not _same_company("CHINA MOBILE LTD", "China Life Insurance Company")
        assert not _same_company("SAMSUNG ELECTRONICS", "Samsung Electro-Mechanics Co")

    def test_a_legal_form_can_be_the_distinguishing_word(self):
        """KGaA is boilerplate everywhere except where it separates two Mercks."""
        assert not _same_company("MERCK KGAA", "Merck & Co., Inc.")
        assert _same_company("MERCK KGAA", "Merck KGaA")

    def test_a_single_word_name_matches_on_that_word(self):
        assert _same_company("SAP SE", "SAP SE")
        assert _same_company("LINDE PLC", "Linde plc")

    def test_corporate_boilerplate_alone_never_matches(self):
        assert not _same_company("ALPHA GROUP PLC", "Beta Holdings Limited")

    def test_empty_names_do_not_match(self):
        assert not _same_company("", "Anything Inc")
        assert not _same_company("Anything Inc", "")


def _holdings(*rows):
    return pd.DataFrame([{"name": n, "local_ticker": t, "currency": c,
                          "sedol": None, "weight": w}
                         for n, t, c, w in rows])


class TestResolvePrefersTheRightCompany:
    def test_santander_is_not_resolved_to_sanofi(self, tmp_path, monkeypatch):
        """SAN.PA (Sanofi) is probed first and exists; SAN.MC is the right one."""
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        names = {"SAN.PA": "Sanofi", "SAN.MC": "Banco Santander, S.A."}
        out = universe.resolve(_holdings(("BANCO SANTANDER SA", "SAN", "EUR", 0.18)),
                               lambda syms: {s: names[s] for s in syms if s in names})
        assert list(out["symbol"]) == ["SAN.MC"]

    def test_sanofi_still_resolves_to_sanofi(self, tmp_path, monkeypatch):
        """The same ticker and currency, the other company."""
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        names = {"SAN.PA": "Sanofi", "SAN.MC": "Banco Santander, S.A."}
        out = universe.resolve(_holdings(("SANOFI", "SAN", "EUR", 0.09)),
                               lambda syms: {s: names[s] for s in syms if s in names})
        assert list(out["symbol"]) == ["SAN.PA"]

    def test_a_renamed_company_falls_back_instead_of_being_dropped(
            self, tmp_path, monkeypatch):
        """No candidate matches by name, so the first that exists still wins."""
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        out = universe.resolve(_holdings(("DHL GROUP", "DHL", "EUR", 0.11)),
                               lambda syms: {"DHL.PA": "Deutsche Post AG"}
                               if "DHL.PA" in syms else {})
        assert list(out["symbol"]) == ["DHL.PA"]

    def test_a_fallback_is_recorded_for_review(self, tmp_path, monkeypatch):
        mismatches = tmp_path / "m.json"
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", mismatches)
        universe.resolve(_holdings(("DHL GROUP", "DHL", "EUR", 0.11)),
                         lambda syms: {"DHL.PA": "Deutsche Post AG"}
                         if "DHL.PA" in syms else {})
        recorded = json.loads(mismatches.read_text(encoding="utf-8"))
        assert recorded[0]["symbol"] == "DHL.PA"
        assert recorded[0]["yahoo_name"] == "Deutsche Post AG"

    def test_a_name_match_is_not_recorded_as_a_mismatch(self, tmp_path, monkeypatch):
        mismatches = tmp_path / "m.json"
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", mismatches)
        names = {"SAN.PA": "Sanofi", "SAN.MC": "Banco Santander, S.A."}
        universe.resolve(_holdings(("BANCO SANTANDER SA", "SAN", "EUR", 0.18)),
                         lambda syms: {s: names[s] for s in syms if s in names})
        assert json.loads(mismatches.read_text(encoding="utf-8")) == []

    def test_nothing_exists_leaves_the_holding_unresolved(self, tmp_path, monkeypatch):
        unresolved = tmp_path / "u.json"
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", unresolved)
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        out = universe.resolve(_holdings(("GHOST CO", "GHST", "EUR", 0.01)),
                               lambda syms: {})
        assert out.empty
        assert json.loads(unresolved.read_text(encoding="utf-8"))[0]["name"] == "GHOST CO"

    def test_a_stub_symbol_does_not_win_over_the_real_listing(
            self, tmp_path, monkeypatch):
        """Several exchanges answer with a name but no market cap, price or
        currency. identify() withholds those, so the next candidate wins rather
        than the company being lost at the market-cap filter."""
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        # NOVOB.CO is the stub and is withheld; NOVO-B.CO is the real line.
        out = universe.resolve(
            _holdings(("NOVO NORDISK A/S B", "NOVOB", "DKK", 0.13)),
            lambda syms: {"NOVO-B.CO": "Novo Nordisk A/S"}
            if "NOVO-B.CO" in syms else {})
        assert list(out["symbol"]) == ["NOVO-B.CO"]

    def test_an_unambiguous_ticker_is_never_probed(self, tmp_path, monkeypatch):
        """One candidate means no network call; history validates it for free."""
        monkeypatch.setattr(universe, "UNRESOLVED_JSON", tmp_path / "u.json")
        monkeypatch.setattr(universe, "MISMATCHES_JSON", tmp_path / "m.json")
        out = universe.resolve(
            _holdings(("GENERAL ELECTRIC", "GE", "USD", 0.22)),
            lambda syms: pytest.fail(f"should not probe {syms}"))
        assert list(out["symbol"]) == ["GE"]

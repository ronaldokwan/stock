"""Tests for universe construction: ticker mapping and duplicate-listing merges."""
import pandas as pd
import pytest

from pipeline import universe
from pipeline.exchange_map import candidates


class TestTickerCandidates:
    def test_uae_holdings_map_to_the_ae_suffix(self):
        """Yahoo serves Dubai and Abu Dhabi under .AE; .AD/.DU return nothing,
        so every AED holding in the index went unresolved."""
        assert candidates('AMANAT', 'AED')[0] == 'AMANAT.AE'

    def test_currency_suffixed_ticker_is_trimmed(self):
        """SSGA writes Securitas as "SECU B_SEK"; Yahoo wants SECU-B.ST."""
        assert 'SECU-B.ST' in candidates('SECU B_SEK', 'SEK')

    def test_korean_a_prefix_is_stripped(self):
        assert candidates('A005930', 'KRW')[0] == '005930.KS'


def _holdings(*rows):
    return pd.DataFrame(
        [{'name': n, 'symbol': s, 'weight': w, 'via_override': o}
         for n, s, w, o in rows]
    )


class TestDeduplicate:
    """Yahoo returns the issuer's name on every listing of a company, so an ADR
    and its local line both come back as one name. Left alone, each company then
    occupies two ranks with its market cap counted twice."""

    def test_primary_listing_beats_its_adr(self):
        df = _holdings(
            ('TAIWAN SEMICONDUCTOR SP ADR', 'TSM', 1.4759, False),
            ('TAIWAN SEMICONDUCTOR MANUFAC', '2330.TW', 0.0430, False),
        )
        names = {'TSM': 'Taiwan Semiconductor Manufacturing Company Limited',
                 '2330.TW': 'Taiwan Semiconductor Manufacturing Company Limited'}
        out = universe.deduplicate(df, names)
        assert list(out['symbol']) == ['2330.TW']
        assert out.iloc[0]['weight'] == pytest.approx(1.5189)
        assert out.iloc[0]['merged_symbols'] == ['TSM']

    def test_ordinary_shares_beat_the_preferred_line(self):
        df = _holdings(
            ('SAMSUNG ELECTRONICS PREF', '005935.KS', 0.0894, False),
            ('SAMSUNG ELECTR GDR REG S', '005930.KS', 0.7692, True),
        )
        names = {'005935.KS': 'Samsung Electronics Co., Ltd.',
                 '005930.KS': 'Samsung Electronics Co., Ltd.'}
        out = universe.deduplicate(df, names)
        # The GDR wording is in the SSGA name, but an override already pointed
        # that holding at the Korean ordinary line, so it must not be demoted.
        assert list(out['symbol']) == ['005930.KS']

    def test_share_classes_collapse_to_the_heavier_one(self):
        df = _holdings(('ALPHABET INC CL A', 'GOOGL', 1.6892, False),
                       ('ALPHABET INC CL C', 'GOOG', 1.4086, False))
        out = universe.deduplicate(df, {'GOOGL': 'Alphabet Inc.',
                                        'GOOG': 'Alphabet Inc.'})
        assert list(out['symbol']) == ['GOOGL']

    def test_similar_names_are_not_merged(self):
        """Fuzzy matching would fold Samsung Electro-Mechanics into Samsung
        Electronics. Only an exact name match may merge."""
        df = _holdings(('SAMSUNG ELECTRONICS', '005930.KS', 0.77, False),
                       ('SAMSUNG ELECTRO MECHANICS CO', '009150.KS', 0.04, False))
        out = universe.deduplicate(df, {'005930.KS': 'Samsung Electronics Co., Ltd.',
                                        '009150.KS': 'Samsung Electro-Mechanics Co., Ltd.'})
        assert len(out) == 2

    def test_rows_without_a_yahoo_name_are_never_merged(self):
        df = _holdings(('A CORP', 'AAA', 0.5, False), ('B CORP', 'BBB', 0.4, False))
        out = universe.deduplicate(df, {'AAA': None, 'BBB': None})
        assert len(out) == 2

    def test_merged_weights_change_the_ordering(self):
        df = _holdings(('BIG CO', 'BIG', 0.9, False),
                       ('SPLIT CO', 'SPL', 0.6, False),
                       ('SPLIT CO SP ADR', 'SPLY', 0.5, False))
        out = universe.deduplicate(df, {'BIG': 'Big Co', 'SPL': 'Split Co',
                                        'SPLY': 'Split Co'})
        assert list(out['symbol']) == ['SPL', 'BIG']

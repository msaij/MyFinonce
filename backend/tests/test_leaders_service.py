"""Tests for app/services/leaders.py -- the classification logic ported out
of fetcher/pages/3_Leaders_&_Laggards.py's inline helper functions."""

import pandas as pd
import pytest

from app.services import leaders


class TestQuartileBadge:
    @pytest.mark.parametrize(
        "q,expected",
        [(1, "Q1 (Top 25%)"), (2, "Q2 (25-50%)"), (3, "Q3 (50-75%)"), (4, "Q4 (Bottom 25%)")],
    )
    def test_maps_each_quartile(self, q, expected):
        assert leaders.quartile_badge(q) == expected


class TestClassifyQuadrant:
    """Both axes are relative to the fund's own peers: return minus the peer median (pp) and
    volatility against the peers' median volatility (%)."""

    def test_ahead_and_calmer(self):
        assert leaders.classify_quadrant(2.0, -10.0) == leaders.QUADRANT_AHEAD_CALMER

    def test_ahead_but_bumpier(self):
        assert leaders.classify_quadrant(2.0, 15.0) == leaders.QUADRANT_AHEAD_BUMPIER

    def test_behind_but_calmer(self):
        assert leaders.classify_quadrant(-2.0, -5.0) == leaders.QUADRANT_BEHIND_CALMER

    def test_behind_and_bumpier(self):
        assert leaders.classify_quadrant(-2.0, 5.0) == leaders.QUADRANT_BEHIND_BUMPIER

    def test_level_with_peers_on_both_counts_as_ahead_and_calmer(self):
        # Inclusive on both: a fund exactly at both peer medians lands in one bucket, not none.
        assert leaders.classify_quadrant(0.0, 0.0) == leaders.QUADRANT_AHEAD_CALMER

    def test_a_calm_asset_class_is_not_a_star_for_being_calm(self):
        """What the market-wide version got wrong: a liquid fund trailing other liquid funds
        is behind its peers, however low its volatility is next to equity."""
        df = pd.DataFrame({
            "scheme_code": [1, 2], "scheme_name": ["Liquid A", "Liquid B"], "display_name": ["A", "B"],
            "fund_house": ["X", "Y"], "category": ["Liquid", "Liquid"],
            "period_return_pct": [1.40, 1.55], "cat_median_return": [1.5, 1.5], "cat_alpha_pct": [-0.10, 0.05],
            "annualized_vol_pct": [0.30, 0.25], "vol_vs_peers_pct": [8.0, -4.0], "n_trading_days": [90, 90],
            "dist_from_52w_high_pct": [0.0, 0.0], "quartile": [4, 1], "peer_percentile": [10.0, 90.0],
        })
        rows = leaders.build_leaders_dataset(df)["rows"].set_index("scheme_code")
        assert rows.loc[1, "quadrant"] == leaders.QUADRANT_BEHIND_BUMPIER
        assert rows.loc[2, "quadrant"] == leaders.QUADRANT_AHEAD_CALMER


class TestDiagnoseLaggard:
    """By rank among peers, not by fixed percentage-point cut-offs: -3 pp is a disaster for a
    liquid fund over a month and noise for a small-cap fund over three years."""

    def test_the_bottom_tenth_of_its_peers(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-0.4, peer_percentile=5.0) == \
            leaders.LAGGARD_BOTTOM_DECILE

    def test_the_bottom_quarter(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-0.4, peer_percentile=20.0) == \
            leaders.LAGGARD_BOTTOM_QUARTILE

    def test_a_small_gap_in_a_falling_category_is_the_category(self):
        assert leaders.diagnose_laggard(cat_median_return=-5.0, cat_alpha_pct=-1.0, peer_percentile=40.0) == \
            leaders.LAGGARD_WITH_CATEGORY

    def test_a_small_gap_in_a_rising_category(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-0.5, peer_percentile=40.0) == \
            leaders.LAGGARD_SLIGHTLY_BEHIND

    def test_a_fund_ahead_of_its_peers_is_never_called_a_laggard(self):
        """The old fallback labelled funds BEATING their peers "Mild Underperformer" whenever
        they were 10% off a 52-week high."""
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=0.8, peer_percentile=70.0) == \
            leaders.NOT_A_LAGGARD

    def test_no_peer_rank_or_alpha_is_not_a_crash(self):
        assert leaders.diagnose_laggard(3.0, None, None) == leaders.NOT_A_LAGGARD
        assert leaders.diagnose_laggard(3.0, -0.5, float("nan")) == leaders.LAGGARD_SLIGHTLY_BEHIND


def test_too_few_peers_is_unranked_not_bottom_quartile():
    """quartile_badge used to fall through to "Q4" for a fund with no quartile at all."""
    assert leaders.quartile_badge(None) == leaders.quartile_badge(float("nan")) == "Unranked (under 5 peers)"


class TestMinTradingDaysGuard:
    def test_excludes_thin_history_rows_when_vol_data_present(self):
        df = pd.DataFrame({
            "annualized_vol_pct": [12.0, None, 8.0],
            "n_trading_days": [30, 2, 1],
        })
        filtered, excluded = leaders.apply_min_trading_days_guard(df)
        assert excluded == 2  # the two rows with n_trading_days < 5
        assert len(filtered) == 1

    def test_no_filtering_when_no_vol_data_at_all(self):
        # The no-date-range query path: annualized_vol_pct is uniformly NaN by
        # design, not "insufficient data" -- must not exclude everything.
        df = pd.DataFrame({
            "annualized_vol_pct": [None, None],
            "n_trading_days": [0, 0],
        })
        filtered, excluded = leaders.apply_min_trading_days_guard(df)
        assert excluded == 0
        assert len(filtered) == 2

    def test_empty_input_returns_empty_no_crash(self):
        df = pd.DataFrame(columns=["annualized_vol_pct", "n_trading_days"])
        filtered, excluded = leaders.apply_min_trading_days_guard(df)
        assert excluded == 0
        assert filtered.empty

    def test_all_rows_below_threshold_retained_with_vol_nulled(self):
        df = pd.DataFrame({
            "annualized_vol_pct": [10.5, 14.2],
            "n_trading_days": [4, 4],
            "period_return_pct": [1.5, -0.8],
        })
        filtered, excluded = leaders.apply_min_trading_days_guard(df)
        assert excluded == 0
        assert len(filtered) == 2
        assert filtered["annualized_vol_pct"].isna().all()


class TestApplyKeywordFilter:
    def _sample_df(self):
        return pd.DataFrame({
            "display_name": ["Motilal Oswal Arbitrage Fund (Direct) [153187]", "HDFC Small Cap Fund [118989]"],
            "scheme_name": ["Motilal Oswal Arbitrage Fund", "HDFC Small Cap Fund"],
            "scheme_code": [153187, 118989],
            "fund_house": ["Motilal Oswal", "HDFC"],
            "category": ["Hybrid Scheme - Arbitrage Fund", "Equity Scheme - Small Cap Fund"],
        })

    def test_all_tokens_must_match_regardless_of_order(self):
        df = self._sample_df()
        result = leaders.apply_keyword_filter(df, "arbitrage motilal")
        assert len(result) == 1
        assert result.iloc[0]["scheme_code"] == 153187

    def test_scheme_code_is_searchable(self):
        df = self._sample_df()
        result = leaders.apply_keyword_filter(df, "118989")
        assert len(result) == 1
        assert result.iloc[0]["scheme_code"] == 118989

    def test_empty_query_returns_everything_unfiltered(self):
        df = self._sample_df()
        result = leaders.apply_keyword_filter(df, "")
        assert len(result) == len(df)


class TestBuildLeadersDataset:
    def _sample_df(self):
        return pd.DataFrame({
            "scheme_code": [1, 2, 3],
            "scheme_name": ["Fund A", "Fund B", "Fund C"],
            "display_name": ["Fund A [1]", "Fund B [2]", "Fund C [3]"],
            "fund_house": ["AMC1", "AMC2", "AMC1"],
            "category": ["Cat X", "Cat X", "Cat Y"],
            "period_return_pct": [10.0, -2.0, 5.0],
            "cat_median_return": [4.0, 4.0, 5.0],
            "cat_alpha_pct": [6.0, -6.0, 0.0],
            "annualized_vol_pct": [8.0, 12.0, 6.0],
            "n_trading_days": [60, 60, 60],
            "dist_from_52w_high_pct": [-1.0, -20.0, -2.0],
            "quartile": [1, 4, 2],
            "peer_percentile": [100.0, 0.0, 50.0],
        })

    def test_computes_correct_kpi_summary(self):
        result = leaders.build_leaders_dataset(self._sample_df())
        assert result["total_funds"] == 3
        assert result["advancers"] == 2  # Fund A, Fund C
        assert result["decliners"] == 1  # Fund B
        assert result["market_median_return"] == pytest.approx(5.0)
        assert result["top_alpha"]["name"] == "Fund A [1]"

    def test_quartile_rank_column_added(self):
        result = leaders.build_leaders_dataset(self._sample_df())
        rows = result["rows"]
        assert rows[rows["scheme_code"] == 1]["quartile_rank"].iloc[0] == "Q1 (Top 25%)"
        assert rows[rows["scheme_code"] == 2]["quartile_rank"].iloc[0] == "Q4 (Bottom 25%)"

    def test_diagnostic_classification_computed_for_every_row(self):
        result = leaders.build_leaders_dataset(self._sample_df())
        rows = result["rows"]
        # Fund B: behind its peers and last among them.
        assert rows[rows["scheme_code"] == 2]["diagnostic_classification"].iloc[0] == leaders.LAGGARD_BOTTOM_DECILE
        assert rows[rows["scheme_code"] == 1]["diagnostic_classification"].iloc[0] == leaders.NOT_A_LAGGARD

    def test_leading_category_needs_five_distinct_funds(self):
        """Direct and Regular plans are one fund twice: four funds in eight schemes must not
        be enough to crown a category."""
        rows = []
        for i in range(4):
            for plan in ("Direct", "Regular"):
                rows.append({"scheme_code": len(rows) + 1, "scheme_name": f"Hot Fund {i}", "display_name": f"Hot {i} {plan}",
                             "fund_house": "A", "category": "Hot", "period_return_pct": 20.0, "cat_median_return": 20.0,
                             "cat_alpha_pct": 0.0, "annualized_vol_pct": 9.0, "n_trading_days": 60,
                             "dist_from_52w_high_pct": -1.0, "quartile": 1, "peer_percentile": 50.0})
        for i in range(5):
            rows.append({"scheme_code": 100 + i, "scheme_name": f"Broad Fund {i}", "display_name": f"Broad {i}",
                         "fund_house": "B", "category": "Broad", "period_return_pct": 2.0, "cat_median_return": 2.0,
                         "cat_alpha_pct": 0.0, "annualized_vol_pct": 9.0, "n_trading_days": 60,
                         "dist_from_52w_high_pct": -1.0, "quartile": 1, "peer_percentile": 50.0})
        result = leaders.build_leaders_dataset(pd.DataFrame(rows))
        assert result["leading_category"]["name"] == "Broad" and result["leading_category"]["funds"] == 5

    def test_empty_input_returns_safe_empty_result_not_a_crash(self):
        df = pd.DataFrame(columns=[
            "scheme_code", "scheme_name", "display_name", "fund_house", "category",
            "period_return_pct", "cat_median_return", "cat_alpha_pct",
            "annualized_vol_pct", "n_trading_days", "dist_from_52w_high_pct", "quartile",
        ])
        result = leaders.build_leaders_dataset(df)
        assert result["total_funds"] == 0
        assert result["top_alpha"] is None
        assert result["rows"].empty


def test_a_fund_without_a_peer_position_has_no_quadrant_not_the_string_nan():
    """pandas 3 fills a partially assigned new string column with "nan"; the page counted it
    as a fifth quadrant."""
    df = pd.DataFrame({
        "scheme_code": [1, 2], "scheme_name": ["A", "B"], "display_name": ["A", "B"], "fund_house": ["X", "Y"],
        "category": ["C", "C"], "period_return_pct": [1.0, 2.0], "cat_median_return": [1.5, None],
        "cat_alpha_pct": [-0.5, None], "annualized_vol_pct": [5.0, 6.0], "vol_vs_peers_pct": [2.0, None],
        "n_trading_days": [60, 60], "dist_from_52w_high_pct": [0.0, 0.0], "quartile": [3, None], "peer_percentile": [40.0, None],
    })
    rows = leaders.build_leaders_dataset(df)["rows"].set_index("scheme_code")
    assert rows.loc[1, "quadrant"] == leaders.QUADRANT_BEHIND_BUMPIER
    assert rows.loc[2, "quadrant"] is None

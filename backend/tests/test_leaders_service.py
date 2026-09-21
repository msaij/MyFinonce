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
    def test_high_return_low_risk_is_alpha_star(self):
        assert leaders.classify_quadrant(10.0, 5.0, med_ret=5.0, med_vol=10.0) == "Institutional Alpha Stars (High Return, Low Risk)"

    def test_high_return_high_risk_is_high_beta(self):
        assert leaders.classify_quadrant(10.0, 15.0, med_ret=5.0, med_vol=10.0) == "High-Beta Momentum (High Return, High Risk)"

    def test_low_return_low_risk_is_defensive(self):
        assert leaders.classify_quadrant(2.0, 5.0, med_ret=5.0, med_vol=10.0) == "Defensive Anchors (Low Return, Low Risk)"

    def test_low_return_high_risk_is_value_trap(self):
        assert leaders.classify_quadrant(2.0, 15.0, med_ret=5.0, med_vol=10.0) == "Value Traps / Laggards (Low Return, High Risk)"

    def test_exactly_at_median_counts_as_high_return_low_risk(self):
        # >= med_ret and <= med_vol are both inclusive -- a fund sitting exactly on
        # both medians is an Alpha Star, not excluded from every bucket.
        assert leaders.classify_quadrant(5.0, 10.0, med_ret=5.0, med_vol=10.0) == "Institutional Alpha Stars (High Return, Low Risk)"


class TestDiagnoseLaggard:
    def test_category_wide_correction_is_cyclical_dip(self):
        assert leaders.diagnose_laggard(cat_median_return=-5.0, cat_alpha_pct=-1.0, dist_from_52w_high_pct=-5.0) == \
            "Cyclical Dip (Category-wide correction; moving with peers)"

    def test_severe_alpha_loss_is_structural_drag_even_if_category_up(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-5.0, dist_from_52w_high_pct=-2.0) == \
            "Structural Drag (Chronic underperformance vs peers)"

    def test_deep_drawdown_without_severe_alpha_loss(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-1.0, dist_from_52w_high_pct=-20.0) == \
            "Deep Drawdown (Far from 52W High)"

    def test_mild_underperformer_is_the_fallback(self):
        assert leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-0.5, dist_from_52w_high_pct=-2.0) == "Mild Underperformer"

    def test_none_drawdown_treated_as_zero_not_a_crash(self):
        # A fund can have no 52w-high figure at all (thin history) -- must not raise.
        result = leaders.diagnose_laggard(cat_median_return=3.0, cat_alpha_pct=-0.5, dist_from_52w_high_pct=None)
        assert result == "Mild Underperformer"


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
        # Fund B: cat_alpha_pct -6.0 < -3.0 -> Structural Drag
        assert rows[rows["scheme_code"] == 2]["diagnostic_classification"].iloc[0] == "Structural Drag (Chronic underperformance vs peers)"

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

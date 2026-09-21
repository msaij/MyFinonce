"""Tier 2: Boundary & Corner Cases (Features 8 - 11: TER Engineering, Plan Pairing, Fee Drag & DB Performance).
Verifies missing schemes, empty feeds, extreme TERs, unpaired funds, negative CAGR fee drag, and zero TER spread.
Each feature has >= 5 boundary test cases (Total: 20 tests).
"""

import math
import pytest

from app.db.connection import get_connection, fetchdf
from tests.e2e.e2e_oracles import (
    get_plan_matcher_fn,
    get_fee_drag_fn,
    oracle_reconcile_dual_era_ter,
    oracle_pair_direct_and_regular,
    oracle_compute_fee_drag,
)


# =====================================================================
# Feature 8: Database Boundaries (5 tests)
# =====================================================================

class TestTier2Feature8DatabaseBoundaries:
    """Boundary conditions for database queries and latency."""

    def test_f8_boundary_scheme_with_zero_nav_history(self):
        """Querying nonexistent scheme code returns empty result without SQL exception."""
        con = get_connection()
        try:
            sql = "SELECT * FROM nav_history WHERE scheme_code = -999999 LIMIT 10;"
            df = fetchdf(con.execute(sql))
            assert df.empty
        finally:
            con.close()

    def test_f8_boundary_single_day_nav_history(self):
        """Scheme with only 1 NAV row does not break lateral join or summary table lookups."""
        con = get_connection()
        try:
            sql = """
                SELECT s.scheme_code, lat.latest_nav
                FROM schemes s
                LEFT JOIN LATERAL (
                    SELECT nav as latest_nav FROM nav_history WHERE scheme_code = s.scheme_code ORDER BY nav_date DESC LIMIT 1
                ) lat ON true
                WHERE s.scheme_code = 118482;
            """
            df = fetchdf(con.execute(sql))
            assert not df.empty
        finally:
            con.close()

    def test_f8_boundary_duplicate_nav_dates_handling(self):
        """Primary key or unique constraint prevents duplicate (scheme_code, nav_date)."""
        con = get_connection()
        try:
            sql = """
                SELECT conname, contype
                FROM pg_constraint
                WHERE conrelid = 'nav_history'::regclass;
            """
            df_cons = fetchdf(con.execute(sql))
            types = df_cons["contype"].tolist()
            # Primary key ('p') or unique ('u')
            assert "p" in types or "u" in types
        finally:
            con.close()

    def test_f8_boundary_concurrent_queries_pool_stability(self):
        """Rapid sequential queries do not exhaust or lock the connection pool."""
        for _ in range(10):
            con = get_connection()
            try:
                res = con.execute("SELECT 1;").fetchone()
                assert res[0] == 1
            finally:
                con.close()

    def test_f8_boundary_large_limit_offset_query(self):
        """Large limit query (5,000 rows) executes within memory and timeout constraints."""
        con = get_connection()
        try:
            sql = "SELECT scheme_code, nav_date, nav FROM nav_history LIMIT 5000;"
            df = fetchdf(con.execute(sql))
            assert len(df) == 5000
        finally:
            con.close()


# =====================================================================
# Feature 9: Dual-Era TER Sync Boundaries (5 tests)
# =====================================================================

class TestTier2Feature9DualEraTERBoundaries:
    """Boundary conditions for AMFI TER synchronization."""

    def test_f9_boundary_empty_ter_feed(self):
        """Empty TER feed produces 0 processed count without crashing."""
        res = oracle_reconcile_dual_era_ter([])
        assert res["processed_count"] == 0
        assert res["checkpoint_status"] == "COMMITTED"

    def test_f9_boundary_negative_or_zero_ter(self):
        """Zero TER (0.00%) for promotional or index schemes is captured accurately."""
        rows = [{"scheme_code": 111, "date": "2024-01-01", "official_ter": 0.0}]
        res = oracle_reconcile_dual_era_ter(rows)
        assert res["sample"][0]["official_ter"] == 0.0

    def test_f9_boundary_extreme_ter_outlier(self):
        """Unusually high TER (e.g. 5.50%) parses without numerical truncation."""
        rows = [{"scheme_code": 222, "date": "2024-01-01", "official_ter": 5.50}]
        res = oracle_reconcile_dual_era_ter(rows)
        assert res["sample"][0]["official_ter"] == 5.50

    def test_f9_boundary_missing_non_critical_fields(self):
        """Records with missing scheme_name fall back safely to defaults."""
        rows = [{"scheme_code": 333, "date": "2024-01-01", "ter": 1.25}]
        res = oracle_reconcile_dual_era_ter(rows)
        assert res["sample"][0]["scheme_code"] == 333
        assert res["sample"][0]["scheme_type"] in ["Direct", "Regular"]

    def test_f9_boundary_idempotent_duplicate_reingestion(self):
        """Re-ingesting the exact same payload preserves checkpoint consistency."""
        rows = [{"scheme_code": 444, "date": "2024-01-01", "official_ter": 0.85}]
        res1 = oracle_reconcile_dual_era_ter(rows)
        res2 = oracle_reconcile_dual_era_ter(rows)
        assert res1["processed_count"] == res2["processed_count"] == 1


# =====================================================================
# Feature 10: Plan Matcher Boundaries (5 tests)
# =====================================================================

class TestTier2Feature10PlanMatcherBoundaries:
    """Boundary conditions for Direct vs Regular plan pairing."""

    def test_f10_boundary_empty_scheme_list(self):
        """Empty input list returns an empty dictionary."""
        pairs = get_plan_matcher_fn()([])
        assert pairs == {}

    def test_f10_boundary_all_direct_no_regular(self):
        """Universe containing only Direct plans produces zero paired records."""
        schemes = [
            {"scheme_code": 1, "scheme_name": "Fund A Direct", "fund_house": "HDFC", "category": "Large Cap"},
            {"scheme_code": 2, "scheme_name": "Fund B Direct", "fund_house": "ICICI", "category": "Mid Cap"},
        ]
        pairs = get_plan_matcher_fn()(schemes)
        assert len(pairs) == 0

    def test_f10_boundary_all_regular_no_direct(self):
        """Universe containing only Regular plans produces zero paired records."""
        schemes = [
            {"scheme_code": 10, "scheme_name": "Fund A Regular", "fund_house": "HDFC", "category": "Large Cap"},
            {"scheme_code": 20, "scheme_name": "Fund B Regular", "fund_house": "ICICI", "category": "Mid Cap"},
        ]
        pairs = get_plan_matcher_fn()(schemes)
        assert len(pairs) == 0

    def test_f10_boundary_special_characters_in_scheme_name(self):
        """Scheme names with ampersands, hyphens, and slashes pair reliably."""
        schemes = [
            {"scheme_code": 101, "scheme_name": "L&T / HSBC Mid-Cap Fund - Regular Plan (Growth)", "fund_house": "HSBC", "category": "Mid Cap"},
            {"scheme_code": 102, "scheme_name": "L&T / HSBC Mid-Cap Fund - Direct Plan (Growth)", "fund_house": "HSBC", "category": "Mid Cap"},
        ]
        pairs = get_plan_matcher_fn()(schemes)
        assert pairs.get(101) == 102

    def test_f10_boundary_ambiguous_amc_spacing(self):
        """Leading/trailing whitespace in AMC names does not prevent pairing."""
        schemes = [
            {"scheme_code": 201, "scheme_name": "Tata Digital India Fund Regular Growth", "fund_house": "  Tata Mutual Fund  ", "category": "Sectoral"},
            {"scheme_code": 202, "scheme_name": "Tata Digital India Fund Direct Growth", "fund_house": "Tata Mutual Fund", "category": "Sectoral"},
        ]
        pairs = get_plan_matcher_fn()(schemes)
        assert pairs.get(201) == 202


# =====================================================================
# Feature 11: Fee Drag Boundaries (5 tests)
# =====================================================================

class TestTier2Feature11FeeDragBoundaries:
    """Boundary conditions for fee drag compounding and gross alpha isolation."""

    def test_f11_boundary_zero_ter_differential(self):
        """When Direct and Regular have identical CAGR and TER, fee drag is 0.0."""
        res = get_fee_drag_fn()(0.12, 0.12, 0.01, 0.01)
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            assert res["horizons"][h]["cumulative_drag_pct"] == 0.0
            assert res["horizons"][h]["rupee_wealth_erosion"] == 0.0

    def test_f11_boundary_negative_cagr_bear_market_fee_drag(self):
        """In a bear market with negative CAGR, fee drag is NOT clamped to zero (Direct still retains more capital)."""
        # Direct drops 10%, Regular drops 11.5%
        res = get_fee_drag_fn()(-0.10, -0.115, 0.0075, 0.0185, initial_capital=100000.0)

        for h in ["1Y", "3Y", "5Y"]:
            h_data = res["horizons"][h]
            # Direct wealth should exceed Regular wealth even during a crash
            assert h_data["wealth_direct"] > h_data["wealth_regular"]
            assert h_data["rupee_wealth_erosion"] > 0.0

    def test_f11_boundary_extreme_high_cagr_compounding(self):
        """Extraordinary return (35% p.a.) over 10Y calculates without overflow."""
        res = get_fee_drag_fn()(0.35, 0.335, 0.007, 0.018, initial_capital=100000.0)
        w_10y_d = res["horizons"]["10Y"]["wealth_direct"]
        w_10y_r = res["horizons"]["10Y"]["wealth_regular"]

        assert w_10y_d > 1500000.0  # 1.35^10 ~ 20x capital
        assert w_10y_d > w_10y_r

    def test_f11_boundary_zero_initial_capital(self):
        """Zero initial capital results in 0.0 rupee wealth erosion without division by zero."""
        res = get_fee_drag_fn()(0.15, 0.14, 0.008, 0.018, initial_capital=0.0)
        assert res["horizons"]["1Y"]["rupee_wealth_erosion"] == 0.0
        assert res["horizons"]["10Y"]["rupee_wealth_erosion"] == 0.0

    def test_f11_boundary_regular_higher_return_anomaly(self):
        """In the rare anomaly where Regular CAGR > Direct CAGR, drag spreads negative gracefully."""
        res = get_fee_drag_fn()(0.12, 0.13, 0.01, 0.01)
        assert res["horizons"]["1Y"]["cagr_spread_pct"] < 0.0

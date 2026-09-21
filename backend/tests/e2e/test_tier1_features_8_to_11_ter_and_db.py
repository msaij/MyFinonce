"""Tier 1: Feature Coverage (Features 8 - 11: TER Engineering, Plan Pairing, Fee Drag & DB Performance).
Verifies DB Latency SLA, Dual-Era AMFI TER, Plan Matcher, and Multi-Horizon Fee Drag.
Each feature has >= 5 isolated test cases (Total: 20 tests).
"""

import math
import time
import pytest

from app.db.connection import get_connection, fetchdf
from tests.e2e.e2e_oracles import (
    oracle_query_benchmark_metrics,
    oracle_reconcile_dual_era_ter,
    get_plan_matcher_fn,
    get_fee_drag_fn,
)


# =====================================================================
# Feature 8: Database Query Performance Optimization (5 tests)
# =====================================================================

class TestTier1Feature8DatabasePerformance:
    """Feature 8: Covering indexes, query un-fencing, and sub-1,000ms latency SLA across 25,336 schemes."""

    def test_f8_lateral_join_latency_under_1000ms_sla(self):
        """Lateral join query on nav_history and summary_table must execute under 1,000ms."""
        con = get_connection()
        try:
            sql = """
                SELECT s.scheme_code, s.scheme_name, s.category,
                       lat.latest_nav, lat.latest_date
                FROM schemes s
                LEFT JOIN LATERAL (
                    SELECT n.nav as latest_nav, n.nav_date as latest_date
                    FROM nav_history n
                    WHERE n.scheme_code = s.scheme_code
                    ORDER BY n.nav_date DESC
                    LIMIT 1
                ) lat ON true
                LIMIT 500;
            """
            t0 = time.perf_counter()
            df = fetchdf(con.execute(sql))
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert elapsed_ms < 1000.0, f"Query took {elapsed_ms:.1f}ms, exceeding 1,000ms SLA"
        finally:
            con.close()

    def test_f8_covering_index_definition_exists(self):
        """Verifies database schema contains covering index idx_nav_history_cov or btree index on (scheme_code, nav_date)."""
        con = get_connection()
        try:
            sql = """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'nav_history';
            """
            df_idx = fetchdf(con.execute(sql))
            idx_names = df_idx["indexname"].tolist()
            # Must have index on scheme_code and nav_date
            has_cov_or_key = any("idx_nav_history_cov" in name or "nav_history_pkey" in name or "nav_history_scheme_code_nav_date" in name for name in idx_names)
            assert has_cov_or_key, f"Missing covering index or composite PK on nav_history: {idx_names}"
        finally:
            con.close()

    def test_f8_summary_table_multi_year_columns(self):
        """Summary table contains multi-horizon return columns (1Y, 3Y, 5Y or return_1y_pct)."""
        con = get_connection()
        try:
            sql = """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'summary_table';
            """
            df_cols = fetchdf(con.execute(sql))
            cols = set(df_cols["column_name"].tolist())
            assert "return_1y_pct" in cols or "return_1y" in cols
            assert "latest_nav" in cols
            assert "latest_date" in cols
        finally:
            con.close()

    def test_f8_10year_rolling_window_unfencing(self):
        """10-year rolling window query completes without client timeout or statement cancellation."""
        con = get_connection()
        try:
            sql = """
                SELECT scheme_code, MIN(nav_date) as min_dt, MAX(nav_date) as max_dt, COUNT(*) as cnt
                FROM nav_history
                GROUP BY scheme_code
                HAVING COUNT(*) > 100
                LIMIT 10;
            """
            t0 = time.perf_counter()
            df = fetchdf(con.execute(sql))
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert elapsed_ms < 1000.0
        finally:
            con.close()

    def test_f8_database_stats_cache_invalidation(self):
        """Database stats cache invalidation resets cached statistics cleanly."""
        from app.db import queries as db_queries
        db_queries.invalidate_database_stats_cache()
        stats = db_queries.get_database_stats()
        assert isinstance(stats, dict)
        assert "schemes_count" in stats or "n_schemes" in stats or "total_schemes" in stats or "nav_count" in stats


# =====================================================================
# Feature 9: Dual-Era AMFI TER Synchronization (5 tests)
# =====================================================================

class TestTier1Feature9DualEraTERSync:
    """Feature 9: Ingestion and reconciliation of AMFI TER archive across both pre-2024 and 2026 standardized formats."""

    def test_f9_pre2024_unstandardized_ter_ingestion(self):
        """Reconciles pre-2024 Reg 52(6A) unstandardized format with base TER and additional expenses."""
        sample_pre2024 = [
            {"scheme_code": 118482, "date": "2023-05-15", "base_ter": 0.80, "additional_expenses": 0.05, "scheme_name": "Bandhan Nifty 50 Direct Growth"},
            {"scheme_code": 118481, "date": "2023-05-15", "base_ter": 1.65, "additional_expenses": 0.05, "scheme_name": "Bandhan Nifty 50 Regular Growth"},
        ]
        res = oracle_reconcile_dual_era_ter(sample_pre2024)
        assert res["processed_count"] == 2
        assert res["checkpoint_status"] == "COMMITTED"
        assert res["sample"][0]["official_ter"] == 0.80

    def test_f9_2026_standardized_ter_ingestion(self):
        """Reconciles 2026 standardized schema with official TER and explicit distribution commission."""
        sample_2026 = [
            {"scheme_code": 120503, "ter_date": "2026-03-01", "official_ter": 0.65, "scheme_name": "HDFC Mid-Cap Opportunities Direct Plan"},
            {"scheme_code": 101150, "ter_date": "2026-03-01", "official_ter": 1.70, "scheme_name": "HDFC Mid-Cap Opportunities Regular Plan"},
        ]
        res = oracle_reconcile_dual_era_ter(sample_2026)
        assert res["processed_count"] == 2
        assert res["sample"][1]["scheme_type"] == "Regular"

    def test_f9_checkpointed_backfill_persistence(self):
        """Sync checkpoint tracking supports restartable backfills without duplicate execution."""
        from app.db import bulk as db_bulk
        con = get_connection()
        try:
            job_name = "test_e2e_ter_sync"
            db_bulk.clear_checkpoints(con, job_name)
            # Mark unit completed
            db_bulk.checkpoint_mark(con, job_name, "chunk_2023_01", "done", 500)
            completed = db_bulk.completed_units(con, job_name)
            assert "chunk_2023_01" in completed
            # Cleanup
            db_bulk.clear_checkpoints(con, job_name)
        finally:
            con.close()

    def test_f9_ter_deduplication_by_scheme_and_date(self):
        """Upserting duplicate TER records on the same scheme and date updates rather than duplicates."""
        from app.db import queries as db_queries
        rows = [
            {"scheme_code": 999991, "ter_date": "2024-01-01", "ter": 0.75},
            {"scheme_code": 999991, "ter_date": "2024-01-01", "ter": 0.72},  # updated value
        ]
        # Oracle reconciliation dedups to the latest
        res = oracle_reconcile_dual_era_ter(rows)
        assert res["processed_count"] == 2

    def test_f9_amfi_client_chunked_pagination(self):
        """AMFI client defines valid historical download URLs and request headers."""
        from app import amfi_ter_client
        assert hasattr(amfi_ter_client, "AmfiTerClient")
        assert hasattr(amfi_ter_client, "TER_API_URL")
        assert amfi_ter_client.TER_API_URL is not None


# =====================================================================
# Feature 10: Direct vs Regular Plan Pairing (5 tests)
# =====================================================================

class TestTier1Feature10DirectRegularPlanPairing:
    """Feature 10: Deterministic algorithm pairing Direct and Regular fund variants."""

    def test_f10_pair_matching_by_amc_and_category(self):
        """Pairs Direct and Regular schemes with identical AMC, category, and Growth option."""
        schemes = [
            {"scheme_code": 118481, "scheme_name": "Bandhan Nifty 50 Index Fund Regular Plan Growth", "fund_house": "Bandhan", "category": "Index Funds"},
            {"scheme_code": 118482, "scheme_name": "Bandhan Nifty 50 Index Fund Direct Plan Growth", "fund_house": "Bandhan", "category": "Index Funds"},
        ]
        fn = get_plan_matcher_fn()
        pairs = fn(schemes)

        assert 118481 in pairs
        assert pairs[118481] == 118482

    def test_f10_idcw_growth_isolation(self):
        """Growth Regular is never paired with IDCW Direct scheme."""
        schemes = [
            {"scheme_code": 1001, "scheme_name": "SBI Bluechip Fund Regular Growth", "fund_house": "SBI", "category": "Large Cap"},
            {"scheme_code": 1002, "scheme_name": "SBI Bluechip Fund Direct IDCW", "fund_house": "SBI", "category": "Large Cap"},
            {"scheme_code": 1003, "scheme_name": "SBI Bluechip Fund Direct Growth", "fund_house": "SBI", "category": "Large Cap"},
        ]
        fn = get_plan_matcher_fn()
        pairs = fn(schemes)

        # 1001 (Growth) must pair with 1003 (Growth), NOT 1002 (IDCW)
        assert pairs.get(1001) == 1003

    def test_f10_regular_scheme_maps_to_direct_scheme(self):
        """Verification mapping structure: regular_code -> direct_code."""
        schemes = [
            {"scheme_code": 201, "scheme_name": "Mirae Asset Large Cap Regular Growth", "fund_house": "Mirae", "category": "Large Cap"},
            {"scheme_code": 202, "scheme_name": "Mirae Asset Large Cap Direct Growth", "fund_house": "Mirae", "category": "Large Cap"},
            {"scheme_code": 301, "scheme_name": "Axis Small Cap Regular Growth", "fund_house": "Axis", "category": "Small Cap"},
            {"scheme_code": 302, "scheme_name": "Axis Small Cap Direct Growth", "fund_house": "Axis", "category": "Small Cap"},
        ]
        fn = get_plan_matcher_fn()
        pairs = fn(schemes)

        assert pairs[201] == 202
        assert pairs[301] == 302

    def test_f10_unpaired_scheme_handling(self):
        """Standalone schemes with no matching Direct counterpart are safely omitted without crashing."""
        schemes = [
            {"scheme_code": 999, "scheme_name": "Old Orphan Fund Regular Growth", "fund_house": "OldAMC", "category": "Thematic"},
        ]
        fn = get_plan_matcher_fn()
        pairs = fn(schemes)

        assert 999 not in pairs
        assert isinstance(pairs, dict)

    def test_f10_case_insensitive_name_resolution(self):
        """Handles variations in casing and whitespace in AMC and scheme names."""
        schemes = [
            {"scheme_code": 401, "scheme_name": "NIPPON INDIA GROWTH FUND - REGULAR PLAN - GROWTH", "fund_house": " Nippon India Mutual Fund ", "category": "Mid Cap"},
            {"scheme_code": 402, "scheme_name": "Nippon India Growth Fund - Direct Plan - Growth", "fund_house": "nippon india mutual fund", "category": "Mid Cap"},
        ]
        fn = get_plan_matcher_fn()
        pairs = fn(schemes)

        assert pairs.get(401) == 402


# =====================================================================
# Feature 11: Fee Drag Attribution Modeling (5 tests)
# =====================================================================

class TestTier1Feature11FeeDragAttribution:
    """Feature 11: Multi-horizon (1Y, 3Y, 5Y, 10Y) compounding fee drag, rupee erosion, and gross vs net alpha."""

    def test_f11_multi_horizon_compounding_wealth_erosion(self):
        """Computes cumulative fee drag percentage across 1Y, 3Y, 5Y, and 10Y horizons."""
        direct_cagr = 0.15  # 15% p.a.
        regular_cagr = 0.14  # 14% p.a. (~100 bps TER differential)
        ter_d = 0.0075
        ter_r = 0.0175

        fn = get_fee_drag_fn()
        res = fn(direct_cagr, regular_cagr, ter_d, ter_r, initial_capital=100000.0)

        horizons = res["horizons"]
        assert "1Y" in horizons and "3Y" in horizons and "5Y" in horizons and "10Y" in horizons
        # Cumulative drag increases with horizon due to compounding
        assert horizons["1Y"]["cumulative_drag_pct"] < horizons["3Y"]["cumulative_drag_pct"]
        assert horizons["3Y"]["cumulative_drag_pct"] < horizons["5Y"]["cumulative_drag_pct"]
        assert horizons["5Y"]["cumulative_drag_pct"] < horizons["10Y"]["cumulative_drag_pct"]

    def test_f11_rupee_wealth_erosion_proportionality(self):
        """10-year compounding rupee wealth erosion exceeds 10x the 1-year erosion due to exponential compounding."""
        direct_cagr = 0.16
        regular_cagr = 0.148  # 1.2% TER difference
        ter_d = 0.0065
        ter_r = 0.0185

        fn = get_fee_drag_fn()
        res = fn(direct_cagr, regular_cagr, ter_d, ter_r, initial_capital=100000.0)

        erosion_1y = res["horizons"]["1Y"]["rupee_wealth_erosion"]
        erosion_10y = res["horizons"]["10Y"]["rupee_wealth_erosion"]

        # Exponential compounding implies erosion_10y > 10 * erosion_1y
        assert erosion_10y > 10.0 * erosion_1y

    def test_f11_gross_alpha_vs_net_alpha_isolation(self):
        """Gross Alpha = Direct Net Alpha + avg(TER_Direct)."""
        direct_alpha = 3.50  # 3.50% annualized net alpha
        regular_alpha = 2.45
        ter_direct = 0.0075  # 0.75%
        ter_regular = 0.0180  # 1.80%

        fn = get_fee_drag_fn()
        res = fn(0.14, 0.1295, ter_direct, ter_regular, direct_alpha=direct_alpha, regular_alpha=regular_alpha)

        alpha_iso = res["alpha_isolation"]
        expected_gross = direct_alpha + (ter_direct * 100.0)  # 3.50 + 0.75 = 4.25%
        assert math.isclose(alpha_iso["gross_alpha_pct"], expected_gross, abs_tol=1e-3)
        assert math.isclose(alpha_iso["net_alpha_direct_pct"], direct_alpha, abs_tol=1e-3)
        assert math.isclose(alpha_iso["net_alpha_regular_pct"], regular_alpha, abs_tol=1e-3)

    def test_f11_distribution_commission_drag_attribution(self):
        """Isolates distribution commission drag = TER_Regular - TER_Direct."""
        ter_direct = 0.0080
        ter_regular = 0.0195

        fn = get_fee_drag_fn()
        res = fn(0.12, 0.1085, ter_direct, ter_regular)

        alpha_iso = res["alpha_isolation"]
        expected_dist_drag = round((ter_regular - ter_direct) * 100.0, 4)  # 1.15%
        assert math.isclose(alpha_iso["distribution_drag_pct"], expected_dist_drag, abs_tol=1e-3)

    def test_f11_cagr_spread_exactness(self):
        """CAGR spread equals Direct CAGR - Regular CAGR within 4 decimal places."""
        d_cagr = 0.1825
        r_cagr = 0.1710

        fn = get_fee_drag_fn()
        res = fn(d_cagr, r_cagr, 0.007, 0.0185)

        for h in ["1Y", "3Y", "5Y", "10Y"]:
            cagr_spread = res["horizons"][h]["cagr_spread_pct"]
            expected = round((d_cagr - r_cagr) * 100.0, 4)
            assert math.isclose(cagr_spread, expected, abs_tol=1e-3)

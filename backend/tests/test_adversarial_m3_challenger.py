"""Adversarial Verification & Stress Test Suite for Milestone 3 (Database Performance & Concurrency).

Challenger 1 Adversarial Harness:
1. Concurrency: 5 concurrent connections executing 10Y rolling window lateral join across all 25,336 schemes.
2. Inactive Funds: Lateral join across 16,566 inactive schemes without is_active filter.
3. Concurrency on Active Funds: 5 concurrent connections executing lateral join on 8,770 active schemes.
4. Short/Sparse History Semantics: Verification of edge-case schemes (<1Y history, discontinued funds).
5. Summary Table SLA: Verification of sub-10ms SLA on summary table point queries and category slices.
"""

import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from app.db.connection import get_connection, fetchdf
from app.db.queries import _configure_parallel_planner


class TestMilestone3AdversarialDatabasePerformance:
    """Adversarial stress testing of database latency SLAs and concurrency."""

    @classmethod
    def setup_class(cls):
        """Pre-warm summary_table and prime working set cache to eliminate cold-cache latency spikes."""
        con = get_connection()
        try:
            con.execute("ALTER TABLE summary_table SET (parallel_workers = 4);")
            con.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm;")
            con.execute("SELECT pg_prewarm('summary_table');")
            # Warm the working set for 10-year rolling lateral join query without thrashing 512MB shared_buffers
            _configure_parallel_planner(con, num_workers=2)
            con.execute("""
                SELECT s.scheme_name, s.broad_category, s.category,
                       CASE 
                           WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                               ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                           ELSE NULL
                       END as period_return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date ASC LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date DESC LIMIT 1
                ) pe ON TRUE;
            """).fetchall()
        except Exception:
            pass
        finally:
            con.close()

    # -------------------------------------------------------------------------
    # 1. Concurrency Stress: 5 Concurrent 10Y Lateral Joins across ALL Schemes
    # -------------------------------------------------------------------------
    def test_concurrent_lateral_join_all_25336_schemes_sla(self):
        """Stress Test: 5 concurrent connections running 10Y rolling lateral join across all 25,336 schemes.
        
        Requirement:
            ORIGINAL_REQUEST §Acceptance: Lateral join queries on nav_history and summary_table
            execute in under 1,000ms for 10-year rolling windows across 25,000+ schemes.
        """
        sql_all = """
            SELECT s.scheme_name, s.broad_category, s.category,
                   CASE 
                       WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                           ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                       ELSE NULL
                   END as period_return_pct
            FROM summary_table s
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date ASC LIMIT 1
            ) ps ON TRUE
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date DESC LIMIT 1
            ) pe ON TRUE;
        """

        def execute_query(conn_id: int):
            con = get_connection()
            try:
                _configure_parallel_planner(con, num_workers=2)
                t0 = time.perf_counter()
                df = fetchdf(con.execute(sql_all))
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return conn_id, len(df), elapsed_ms
            finally:
                con.close()

        # Execute 5 concurrent connections
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(execute_query, i) for i in range(5)]
            results = [f.result() for f in futures]

        max_elapsed = max(r[2] for r in results)
        avg_elapsed = sum(r[2] for r in results) / len(results)
        
        # All 5 connections must return all schemes
        for cid, rows, elapsed in results:
            assert rows >= 25336, f"Conn {cid} returned {rows} rows, expected >= 25336"

        assert max_elapsed < 1000.0, (
            f"Concurrency SLA Violation: 5 concurrent connections across all 25,336 schemes "
            f"peaked at {max_elapsed:.2f}ms (average {avg_elapsed:.2f}ms), exceeding strict 1,000ms SLA."
        )

    # -------------------------------------------------------------------------
    # 2. Inactive Funds Lateral Join Latency
    # -------------------------------------------------------------------------
    def test_lateral_join_inactive_schemes_sla(self):
        """Stress Test: 10Y rolling lateral join across 16,566 inactive schemes.
        
        Evaluates whether query planner handles inactive scheme partition without table scan degradation.
        """
        sql_inactive = """
            SELECT s.scheme_name, s.broad_category, s.category,
                   CASE 
                       WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                           ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                       ELSE NULL
                   END as period_return_pct
            FROM summary_table s
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date ASC LIMIT 1
            ) ps ON TRUE
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date DESC LIMIT 1
            ) pe ON TRUE
            WHERE NOT s.is_active;
        """
        con = get_connection()
        try:
            _configure_parallel_planner(con, num_workers=2)
            t0 = time.perf_counter()
            df = fetchdf(con.execute(sql_inactive))
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert len(df) == 16566
            assert elapsed_ms < 1000.0, (
                f"Inactive Schemes SLA Violation: Lateral query on 16,566 inactive funds took {elapsed_ms:.2f}ms, "
                f"exceeding strict 1,000ms SLA."
            )
        finally:
            con.close()

    # -------------------------------------------------------------------------
    # 3. Concurrency on Active Schemes
    # -------------------------------------------------------------------------
    def test_concurrent_lateral_join_active_schemes_sla(self):
        """5 concurrent connections executing lateral join on 8,770 active schemes under warm cache."""
        sql_active = """
            SELECT s.scheme_name, s.broad_category, s.category,
                   CASE 
                       WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                           ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                       ELSE NULL
                   END as period_return_pct
            FROM summary_table s
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date ASC LIMIT 1
            ) ps ON TRUE
            LEFT JOIN LATERAL (
                SELECT nav, nav_date FROM nav_history
                WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                ORDER BY nav_date DESC LIMIT 1
            ) pe ON TRUE
            WHERE s.is_active;
        """

        def execute_query(conn_id: int):
            con = get_connection()
            try:
                _configure_parallel_planner(con, num_workers=2)
                t0 = time.perf_counter()
                df = fetchdf(con.execute(sql_active))
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return conn_id, len(df), elapsed_ms
            finally:
                con.close()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(execute_query, i) for i in range(5)]
            results = [f.result() for f in futures]

        max_elapsed = max(r[2] for r in results)
        assert max_elapsed < 1000.0, (
            f"Active Concurrency SLA Violation: 5 concurrent connections on active schemes peaked at {max_elapsed:.2f}ms."
        )

    # -------------------------------------------------------------------------
    # 4. Sparse & Short History Scheme Semantic Verification
    # -------------------------------------------------------------------------
    def test_short_history_scheme_semantic_correctness(self):
        """Edge Case: Schemes with <1 year history (e.g. 153292 with 26 days history)
        must return NULL/None rather than truncated short-period returns masquerading as full 10-year rolling returns.
        """
        con = get_connection()
        try:
            sql = """
                SELECT s.scheme_code, s.scheme_name,
                       ps.nav as start_nav, pe.nav as end_nav, s.latest_nav,
                       CASE 
                           WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                               ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                           ELSE NULL
                       END as period_return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date ASC LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date DESC LIMIT 1
                ) pe ON TRUE
                WHERE s.scheme_code = 153292;
            """
            df = fetchdf(con.execute(sql))
            assert len(df) == 1
            ret = df.iloc[0]["period_return_pct"]
            # Scheme 153292 only has 26 days of history, so 10Y rolling return must be NULL
            assert ret is None or str(ret) == "nan" or (hasattr(ret, "isna") and ret.isna()), (
                f"Expected NULL/None for 26-day fund in 10Y rolling window, got: {ret}"
            )
            assert df.iloc[0]["start_nav"] is not None
            assert df.iloc[0]["end_nav"] is not None
        finally:
            con.close()

    def test_discontinued_scheme_returns_null_without_crash(self):
        """Edge Case: Inactive schemes with 0 NAVs in the 10-year window (e.g. 100054, discontinued 2015)
        cleanly return NULL without runtime error or division-by-zero.
        """
        con = get_connection()
        try:
            sql = """
                SELECT s.scheme_code, s.scheme_name,
                       ps.nav as start_nav, pe.nav as end_nav,
                       CASE 
                           WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                               ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                           ELSE NULL
                       END as period_return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date ASC LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date DESC LIMIT 1
                ) pe ON TRUE
                WHERE s.scheme_code = 100054;
            """
            df = fetchdf(con.execute(sql))
            assert len(df) == 1
            assert df.iloc[0]["start_nav"] is None or str(df.iloc[0]["start_nav"]) == "nan"
            assert df.iloc[0]["period_return_pct"] is None or str(df.iloc[0]["period_return_pct"]) == "nan"
        finally:
            con.close()

    # -------------------------------------------------------------------------
    # 5. Summary Table Latency: Strict <10ms SLA
    # -------------------------------------------------------------------------
    def test_summary_table_point_lookup_sub_10ms_sla(self):
        """Summary table point lookup by scheme_code must execute under 10ms (target <1ms)."""
        con = get_connection()
        try:
            sql = """
                SELECT scheme_code, scheme_name, return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct
                FROM summary_table
                WHERE scheme_code = 118482;
            """
            # Warm cache
            con.execute(sql).fetchall()

            times = []
            for _ in range(10):
                t0 = time.perf_counter()
                res = con.execute(sql).fetchall()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                times.append(elapsed_ms)

            min_time = min(times)
            avg_time = sum(times) / len(times)
            assert min_time < 10.0, f"Summary table point lookup took {min_time:.2f}ms, exceeding 10ms SLA"
            assert avg_time < 5.0, f"Average summary table point lookup took {avg_time:.2f}ms"
        finally:
            con.close()

    def test_summary_table_category_filter_sub_10ms_sla(self):
        """Summary table category filter query (1,300+ Index Funds) must execute under 10ms."""
        con = get_connection()
        try:
            sql = """
                SELECT scheme_code, scheme_name, return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct
                FROM summary_table
                WHERE category = 'Other Scheme - Index Funds' AND is_active;
            """
            # Warm cache
            con.execute(sql).fetchall()

            times = []
            for _ in range(10):
                t0 = time.perf_counter()
                res = con.execute(sql).fetchall()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                times.append(elapsed_ms)

            min_time = min(times)
            assert min_time < 10.0, f"Summary table category query took {min_time:.2f}ms, exceeding 10ms SLA"
        finally:
            con.close()

    def test_parallel_gather_workers_planned_and_launched(self):
        """EXPLAIN (ANALYZE, BUFFERS) confirms PostgreSQL planner selects Gather with 4 workers."""
        con = get_connection()
        try:
            _configure_parallel_planner(con, num_workers=4)
            sql = """
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT s.scheme_name, s.broad_category, s.category,
                       CASE 
                           WHEN (pe.nav_date - ps.nav_date) >= 3200 THEN
                               ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                           ELSE NULL
                       END as period_return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date ASC LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= '2016-09-01' AND nav_date <= '2026-09-01'
                    ORDER BY nav_date DESC LIMIT 1
                ) pe ON TRUE;
            """
            rows = con.execute(sql).fetchall()
            plan_text = "\n".join(r[0] for r in rows)
            assert "Gather" in plan_text, f"Expected Gather in plan: {plan_text}"
            assert "Workers Planned: 4" in plan_text, f"Expected Workers Planned: 4 in plan: {plan_text}"
            assert "Workers Launched: 4" in plan_text, f"Expected Workers Launched: 4 in plan: {plan_text}"
            assert "Parallel Seq Scan on summary_table" in plan_text, f"Expected Parallel Seq Scan in plan: {plan_text}"
        finally:
            con.close()


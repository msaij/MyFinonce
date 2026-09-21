"""Tier 5: White-Box System, API & Concurrency Hardening Adversarial Test Suite.

Milestone 5 Phase 2 White-Box Adversarial Audit:
1. Concurrency Stress Testing:
   - 10 parallel requests to /api/quant/{scheme_code}/factors ensuring zero connection starvation, zero thread deadlocks, and latency strictly < 1,000ms SLA.
   - 10 parallel requests to /api/quant/{scheme_code}/fee-drag ensuring zero connection starvation, zero thread deadlocks, and latency strictly < 1,000ms SLA.
2. Unusual Scheme Names & Plan Matcher Adversarial Hardening:
   - Complex multi-class names ("Growth - Direct Plan - Bonus Option", "Institutional Dividend Reinvestment Weekly").
   - Special characters, currency tokens (₹), and unicode string normalization.
   - Zero false-positive option cross-matches (Growth must never pair with Dividend/IDCW).
   - "Dividend Yield" fund naming immunity against false-positive IDCW option classification.
3. Explainable AI (XAI) Structured JSON Validation:
   - Verification of generate_fund_diagnostics_report schema containing all required institutional fields:
     'executive_summary', 'market_regime_context', 'factor_exposures', 'tail_risk_verdict', 'stress_scenarios_summary', 'actionable_guidance'.
   - Deterministic qualitative text across multiple factor risk regimes.
4. API Invalid Payload Handling & Error Boundary Defense:
   - Malformed JSON payloads (HTTP 422, zero 500 crash).
   - Out-of-range dates ("1900-01-01", "2099-12-31") returning clean 422 with zero 500 crash.
   - Non-numeric shock values returning clean 422 with zero 500 crash.
   - Empty view vectors in Black-Litterman endpoint (views_matrix_P: [], views_returns_Q: []) returning clean 400/422 validation error rather than unhandled IndexError / 500 crash.
"""

import concurrent.futures
import datetime
import time

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import plan_matcher
from app.services.quant_intelligence import generate_fund_diagnostics_report


# =====================================================================
# 1. Concurrency Stress Testing (10 Parallel Requests & Latency SLA)
# =====================================================================

class TestTier5ConcurrencySLA:
    """Stress tests 10 concurrent requests to factor attribution and fee drag endpoints."""

    TEST_SCHEME_CODE = 100645  # SBI Consumption Opportunities Fund (2,892 NAV points, active)
    PAIRED_SCHEME_CODE = 100644

    def test_concurrency_10_parallel_factors_sla(self):
        """10 parallel requests to /api/quant/{scheme_code}/factors must produce zero deadlocks, zero starvation, and latency < 1,000ms SLA."""
        client = TestClient(app)

        def make_request(idx: int):
            t0 = time.perf_counter()
            resp = client.get(f"/api/quant/{self.TEST_SCHEME_CODE}/factors")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return idx, resp.status_code, elapsed_ms, resp.json() if resp.status_code == 200 else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in futures]

        # 1. Zero starvation / zero deadlocks: All 10 requests must succeed with HTTP 200
        statuses = [status for _, status, _, _ in results]
        assert all(s == 200 for s in statuses), f"Expected all HTTP 200 under concurrency, got statuses: {statuses}"

        # 2. Strict latency SLA < 1,000ms
        latencies = [lat for _, _, lat, _ in results]
        max_lat = max(latencies)
        avg_lat = sum(latencies) / len(latencies)

        assert max_lat < 1000.0, (
            f"Latency SLA violation: Max concurrent latency {max_lat:.1f}ms exceeds strict 1,000ms threshold "
            f"(Average: {avg_lat:.1f}ms, all latencies: {[round(l, 1) for l in latencies]})"
        )

    def test_concurrency_10_parallel_fee_drag_unpaired_sla(self):
        """10 parallel requests to /api/quant/{scheme_code}/fee-drag (unpaired auto-lookup) must produce zero deadlocks and latency < 1,000ms SLA."""
        client = TestClient(app)

        def make_request(idx: int):
            t0 = time.perf_counter()
            resp = client.get(f"/api/quant/{self.TEST_SCHEME_CODE}/fee-drag")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return idx, resp.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in futures]

        statuses = [status for _, status, _ in results]
        assert all(s == 200 for s in statuses), f"Expected all HTTP 200 under concurrency, got statuses: {statuses}"

        latencies = [lat for _, _, lat in results]
        max_lat = max(latencies)
        assert max_lat < 1000.0, (
            f"Latency SLA violation in fee-drag unpaired lookup: Max concurrent latency {max_lat:.1f}ms "
            f"exceeds strict 1,000ms SLA (Latencies: {[round(l, 1) for l in latencies]}). "
            f"Full-table scan in find_paired_scheme across 25,336 schemes must be cached or indexed."
        )

    def test_concurrency_10_parallel_fee_drag_paired_sla(self):
        """10 parallel requests to /api/quant/{scheme_code}/fee-drag with explicit paired_scheme_code must complete < 1,000ms SLA."""
        client = TestClient(app)

        def make_request(idx: int):
            t0 = time.perf_counter()
            resp = client.get(f"/api/quant/{self.TEST_SCHEME_CODE}/fee-drag?paired_scheme_code={self.PAIRED_SCHEME_CODE}")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return idx, resp.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in futures]

        statuses = [status for _, status, _ in results]
        assert all(s == 200 for s in statuses), f"Expected all HTTP 200, got: {statuses}"

        latencies = [lat for _, _, lat in results]
        max_lat = max(latencies)
        assert max_lat < 1000.0, f"Max latency {max_lat:.1f}ms exceeds 1,000ms SLA"


# =====================================================================
# 2. Plan Matcher Adversarial Hardening (Unusual Names & Zero False Positives)
# =====================================================================

class TestTier5PlanMatcherAdversarialNames:
    """Stress tests plan matcher token parsing against complex multi-class names, unicode, and cross-matching."""

    def test_plan_matcher_complex_multiclass_bonus_growth(self):
        """Complex name 'Growth - Direct Plan - Bonus Option' must pair with 'Growth - Regular Plan - Bonus Option'."""
        schemes = [
            {
                "scheme_code": 501,
                "scheme_name": "ABC Bluechip Growth - Direct Plan - Bonus Option",
                "fund_house": "ABC Asset Management Ltd",
                "category": "Equity Scheme - Large Cap",
            },
            {
                "scheme_code": 502,
                "scheme_name": "ABC Bluechip Growth - Regular Plan - Bonus Option",
                "fund_house": "ABC Asset Management Ltd",
                "category": "Equity Scheme - Large Cap",
            },
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert 502 in pairs, "Regular bonus-growth scheme 502 must be matched"
        assert pairs[502] == 501, f"Expected 502 -> 501, got {pairs.get(502)}"

    def test_plan_matcher_institutional_dividend_reinvestment_frequency(self):
        """Weekly dividend reinvestment must pair with Weekly direct, and Monthly with Monthly direct."""
        schemes = [
            {
                "scheme_code": 601,
                "scheme_name": "Axis Treasury Advantage - Institutional Dividend Reinvestment Weekly - Direct Plan",
                "fund_house": "Axis Mutual Fund",
                "category": "Debt Scheme - Low Duration",
            },
            {
                "scheme_code": 602,
                "scheme_name": "Axis Treasury Advantage - Institutional Dividend Reinvestment Weekly",
                "fund_house": "Axis Mutual Fund",
                "category": "Debt Scheme - Low Duration",
            },
            {
                "scheme_code": 603,
                "scheme_name": "Axis Treasury Advantage - Institutional Dividend Reinvestment Monthly - Direct Plan",
                "fund_house": "Axis Mutual Fund",
                "category": "Debt Scheme - Low Duration",
            },
            {
                "scheme_code": 604,
                "scheme_name": "Axis Treasury Advantage - Institutional Dividend Reinvestment Monthly",
                "fund_house": "Axis Mutual Fund",
                "category": "Debt Scheme - Low Duration",
            },
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(602) == 601, f"Weekly regular 602 must pair with Weekly direct 601, got {pairs.get(602)}"
        assert pairs.get(604) == 603, f"Monthly regular 604 must pair with Monthly direct 603, got {pairs.get(604)}"

    def test_plan_matcher_special_characters_and_unicode_normalization(self):
        """Handles unicode em-dash, currency symbols (₹), zero-width characters, and brackets cleanly."""
        schemes = [
            {
                "scheme_code": 701,
                "scheme_name": "Kotak Mahindra Emerging Equity Fund \u2013 Direct Plan \u2013 Growth (\u20b9)",
                "fund_house": "Kotak Mahindra Asset Management Co. Ltd.",
                "category": "Equity Scheme - Mid Cap",
            },
            {
                "scheme_code": 702,
                "scheme_name": "Kotak Mahindra Emerging Equity Fund \u2013 Regular Plan \u2013 Growth (\u20b9)",
                "fund_house": "Kotak Mahindra Mutual Fund",
                "category": "Equity Scheme - Mid Cap",
            },
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(702) == 701, f"Unicode scheme 702 must pair with 701, got {pairs.get(702)}"

    def test_plan_matcher_zero_false_positive_option_cross_match(self):
        """Regular IDCW/Dividend option must NEVER cross-match with Direct Growth option."""
        schemes = [
            {
                "scheme_code": 801,
                "scheme_name": "HDFC Top 100 - Direct Plan - Growth",
                "fund_house": "HDFC Mutual Fund",
                "category": "Equity Scheme - Large Cap",
            },
            {
                "scheme_code": 802,
                "scheme_name": "HDFC Top 100 - Regular Plan - IDCW Payout",
                "fund_house": "HDFC Mutual Fund",
                "category": "Equity Scheme - Large Cap",
            },
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert 802 not in pairs, f"False-positive cross-match detected: Regular IDCW 802 matched to Direct Growth {pairs.get(802)}"

    def test_plan_matcher_dividend_yield_fund_immunity(self):
        """'Dividend Yield' category name must NOT falsely trigger IDCW classification on Growth funds."""
        schemes = [
            {
                "scheme_code": 901,
                "scheme_name": "UTI Dividend Yield Fund - Direct Plan - Growth Option",
                "fund_house": "UTI Mutual Fund",
                "category": "Equity Scheme - Dividend Yield",
            },
            {
                "scheme_code": 902,
                "scheme_name": "UTI Dividend Yield Fund - Regular Plan - Growth Option",
                "fund_house": "UTI Mutual Fund",
                "category": "Equity Scheme - Dividend Yield",
            },
            {
                "scheme_code": 903,
                "scheme_name": "UTI Dividend Yield Fund - Direct Plan - IDCW Option",
                "fund_house": "UTI Mutual Fund",
                "category": "Equity Scheme - Dividend Yield",
            },
            {
                "scheme_code": 904,
                "scheme_name": "UTI Dividend Yield Fund - Regular Plan - IDCW Option",
                "fund_house": "UTI Mutual Fund",
                "category": "Equity Scheme - Dividend Yield",
            },
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(902) == 901, f"Growth fund 902 must pair with Growth direct 901, got {pairs.get(902)}"
        assert pairs.get(904) == 903, f"IDCW fund 904 must pair with IDCW direct 903, got {pairs.get(904)}"
        # Verify no cross-pairing between Growth and IDCW
        assert pairs.get(902) != 903, "Growth Regular must not pair with IDCW Direct"
        assert pairs.get(904) != 901, "IDCW Regular must not pair with Growth Direct"


# =====================================================================
# 3. Explainable AI (XAI) Intelligence Structured JSON Validation
# =====================================================================

class TestTier5XAIIntelligenceStructuredJSON:
    """Validates generate_fund_diagnostics_report output schema and determinism against institutional specification."""

    MANDATED_XAI_FIELDS = [
        "executive_summary",
        "market_regime_context",
        "factor_exposures",
        "tail_risk_verdict",
        "stress_scenarios_summary",
        "actionable_guidance",
    ]

    def test_xai_diagnostics_report_required_institutional_fields(self):
        """generate_fund_diagnostics_report output must contain all 6 required institutional XAI fields."""
        betas = {"mkt_excess": 1.15, "smb": 0.28, "hml": -0.15, "wml": 0.20}
        report = generate_fund_diagnostics_report(
            scheme_name="Mirae Asset Large Cap",
            factor_betas=betas,
            alpha_ann_pct=2.75,
            sharpe=1.45,
            quartile=1,
            is_regular=True,
            ter_diff=0.92,
        )

        missing_fields = [f for f in self.MANDATED_XAI_FIELDS if f not in report]
        assert not missing_fields, (
            f"Institutional XAI contract violation: generate_fund_diagnostics_report output is missing "
            f"required fields: {missing_fields}. Current keys: {list(report.keys())}"
        )

    def test_xai_diagnostics_report_deterministic_regimes_and_non_empty_text(self):
        """XAI report must produce non-empty, deterministic natural language text across extreme risk regimes."""
        regimes = [
            # High beta, high momentum growth
            {"name": "Aggressive Tech", "betas": {"mkt_excess": 1.45, "smb": 0.40, "hml": -0.30, "wml": 0.35}, "alpha": 4.5, "sharpe": 1.8},
            # Defensive low volatility deep value
            {"name": "Defensive Utility", "betas": {"mkt_excess": 0.65, "smb": -0.35, "hml": 0.45, "wml": -0.20}, "alpha": 0.2, "sharpe": 0.7},
            # Distressed negative alpha fund
            {"name": "Distressed Fund", "betas": {"mkt_excess": 1.10, "smb": 0.10, "hml": 0.05, "wml": -0.15}, "alpha": -3.5, "sharpe": -0.4},
        ]

        for regime in regimes:
            rep1 = generate_fund_diagnostics_report(regime["name"], regime["betas"], regime["alpha"], regime["sharpe"])
            rep2 = generate_fund_diagnostics_report(regime["name"], regime["betas"], regime["alpha"], regime["sharpe"])

            # Verify bit-for-bit determinism
            assert rep1 == rep2, f"XAI report generation non-deterministic for regime {regime['name']}"

            # Verify all available text fields are non-empty strings
            for k, v in rep1.items():
                if isinstance(v, str):
                    assert len(v.strip()) > 0, f"Field '{k}' must not be empty in regime {regime['name']}"


# =====================================================================
# 4. API Invalid Payload Handling & Error Boundary Defense
# =====================================================================

class TestTier5APIInvalidPayloadHandling:
    """Stress tests boundary handling on malformed JSON, out-of-range dates, non-numeric shocks, and empty view vectors."""

    def test_api_malformed_json_handling(self):
        """Malformed JSON to POST endpoints must return HTTP 422 Unprocessable Entity with zero 500 crash."""
        client = TestClient(app)
        endpoints = [
            "/api/portfolio-advisor/suggest",
            "/api/portfolio-advisor/score",
            "/api/portfolio-advisor/black-litterman",
        ]
        for ep in endpoints:
            resp = client.post(ep, content="{malformed_json_payload: None,", headers={"Content-Type": "application/json"})
            assert resp.status_code == 422, f"Endpoint {ep} must return HTTP 422 on malformed JSON, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert "detail" in data, f"Endpoint {ep} 422 response must contain 'detail' error message"

    def test_api_out_of_range_historical_dates(self):
        """Out-of-range dates ('1900-01-01', '2099-12-31') must return HTTP 422 with zero 500 crashes."""
        client = TestClient(app)
        scheme_code = 100645

        # 1. Past out-of-range date (1900)
        r_past = client.get(f"/api/quant/{scheme_code}/factors?start_date=1900-01-01&end_date=1900-12-31")
        assert r_past.status_code == 422, f"Expected 422 for 1900 date range, got {r_past.status_code}: {r_past.text}"

        # 2. Future out-of-range date (2099)
        r_fut = client.get(f"/api/quant/{scheme_code}/factors?start_date=2099-01-01&end_date=2099-12-31")
        assert r_fut.status_code == 422, f"Expected 422 for 2099 date range, got {r_fut.status_code}: {r_fut.text}"

        # 3. Inverted dates (start > end) must be auto-corrected or rejected with 422, not crash 500
        r_inv = client.get(f"/api/quant/{scheme_code}/factors?start_date=2026-01-01&end_date=2020-01-01")
        assert r_inv.status_code in [200, 422], f"Inverted dates must return 200 (auto-swapped) or 422, got {r_inv.status_code}"

    def test_api_non_numeric_shock_values(self):
        """Non-numeric shock query parameters must return HTTP 422 Unprocessable Entity with zero 500 crash."""
        client = TestClient(app)
        scheme_code = 100645

        resp = client.get(f"/api/quant/{scheme_code}/stress-test?shock_market=catastrophic_crash&shock_size=NaN_string")
        assert resp.status_code == 422, f"Expected HTTP 422 for non-numeric shock, got {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", [])
        assert any("shock_market" in str(d) for d in detail), f"Expected validation error on shock_market in {detail}"

    def test_api_black_litterman_empty_view_vectors(self):
        """POST JSON [] / [] is K=0: 200 with prior == posterior."""
        client = TestClient(app)
        payload = {
            "cov_matrix": [[0.04, 0.01], [0.01, 0.04]],
            "asset_names": ["Asset_A", "Asset_B"],
            "prior_weights": [0.6, 0.4],
            "views_matrix_P": [],
            "views_returns_Q": [],
        }

        resp = client.post("/api/portfolio-advisor/black-litterman", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prior_returns"] == body["posterior_expected_returns"]
        assert body["optimal_weights"] == pytest.approx([0.6, 0.4], abs=1e-6)

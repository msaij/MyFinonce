"""Unit tests for Fee Drag Attribution Modeling Service.
Verifies multi-horizon compounding, CAGR spread, rupee erosion, 3-tier attribution,
and gross vs net alpha isolation.
"""

import math

from app.services import fee_drag


class TestFeeDragUnit:
    """Comprehensive mathematical and integration tests for fee drag attribution."""

    def test_multi_horizon_compounding_drag_progression(self):
        """Cumulative fee drag increases monotonically across 1Y, 3Y, 5Y, and 10Y horizons."""
        d_cagr = 0.15
        r_cagr = 0.138
        ter_d = 0.0075
        ter_r = 0.0195

        res = fee_drag.compute_fee_drag_attribution(d_cagr, r_cagr, ter_d, ter_r, initial_capital=100000.0)
        h = res["horizons"]

        assert "1Y" in h and "3Y" in h and "5Y" in h and "10Y" in h
        assert h["1Y"]["cumulative_drag_pct"] < h["3Y"]["cumulative_drag_pct"]
        assert h["3Y"]["cumulative_drag_pct"] < h["5Y"]["cumulative_drag_pct"]
        assert h["5Y"]["cumulative_drag_pct"] < h["10Y"]["cumulative_drag_pct"]

    def test_cagr_spread_exactness(self):
        """CAGR spread equals Direct CAGR - Regular CAGR within 4 decimal places."""
        d_cagr = 0.1750
        r_cagr = 0.1625
        res = fee_drag.compute_fee_drag_attribution(d_cagr, r_cagr, 0.006, 0.0185)

        for horiz in ["1Y", "3Y", "5Y", "10Y"]:
            spread = res["horizons"][horiz]["cagr_spread_pct"]
            expected = round((d_cagr - r_cagr) * 100.0, 4)
            assert math.isclose(spread, expected, abs_tol=1e-3)

    def test_rupee_wealth_erosion_exponential_scaling(self):
        """Compounding wealth erosion over 10Y exceeds 10x the 1Y erosion on ₹100,000 capital."""
        d_cagr = 0.16
        r_cagr = 0.145
        ter_d = 0.0070
        ter_r = 0.0190

        res = fee_drag.compute_fee_drag_attribution(d_cagr, r_cagr, ter_d, ter_r, initial_capital=100000.0)
        e_1y = res["horizons"]["1Y"]["rupee_wealth_erosion"]
        e_10y = res["horizons"]["10Y"]["rupee_wealth_erosion"]

        assert e_10y > 10.0 * e_1y

    def test_three_tier_attribution_reconstitution(self):
        """Total drag decomposes into Operating Drag and Distribution Drag."""
        ter_d = 0.0080
        ter_r = 0.0190
        res = fee_drag.compute_fee_drag_attribution(0.14, 0.129, ter_d, ter_r, initial_capital=100000.0)

        alpha_iso = res["alpha_isolation"]
        assert math.isclose(alpha_iso["operating_drag_pct"], round(ter_d * 100.0, 4), abs_tol=1e-3)
        assert math.isclose(alpha_iso["distribution_drag_pct"], round((ter_r - ter_d) * 100.0, 4), abs_tol=1e-3)

    def test_gross_alpha_vs_net_alpha_isolation(self):
        """Gross Alpha = Direct Net Alpha + avg(TER_Direct)."""
        direct_alpha = 4.20
        regular_alpha = 3.10
        ter_d = 0.0070
        ter_r = 0.0180

        res = fee_drag.compute_fee_drag_attribution(
            0.15, 0.139, ter_d, ter_r, direct_alpha=direct_alpha, regular_alpha=regular_alpha
        )
        alpha_iso = res["alpha_isolation"]

        expected_gross = direct_alpha + (ter_d * 100.0)
        assert math.isclose(alpha_iso["gross_alpha_pct"], expected_gross, abs_tol=1e-3)
        assert math.isclose(alpha_iso["net_alpha_direct_pct"], direct_alpha, abs_tol=1e-3)
        assert math.isclose(alpha_iso["net_alpha_regular_pct"], regular_alpha, abs_tol=1e-3)

    def test_boundary_zero_ter_differential(self):
        """When Direct and Regular have identical CAGR and TER, drag is 0.0."""
        res = fee_drag.compute_fee_drag_attribution(0.12, 0.12, 0.01, 0.01)
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            assert res["horizons"][h]["cumulative_drag_pct"] == 0.0
            assert res["horizons"][h]["rupee_wealth_erosion"] == 0.0

    def test_boundary_negative_cagr_bear_market(self):
        """Direct plan preserves more capital in a negative return market."""
        res = fee_drag.compute_fee_drag_attribution(-0.15, -0.165, 0.0075, 0.0185, initial_capital=100000.0)
        h_5y = res["horizons"]["5Y"]
        assert h_5y["wealth_direct"] > h_5y["wealth_regular"]
        assert h_5y["rupee_wealth_erosion"] > 0.0

    def test_boundary_zero_initial_capital(self):
        """Zero initial capital yields zero rupee wealth erosion without division errors."""
        res = fee_drag.compute_fee_drag_attribution(0.15, 0.14, 0.008, 0.018, initial_capital=0.0)
        assert res["horizons"]["1Y"]["rupee_wealth_erosion"] == 0.0
        assert res["horizons"]["10Y"]["rupee_wealth_erosion"] == 0.0

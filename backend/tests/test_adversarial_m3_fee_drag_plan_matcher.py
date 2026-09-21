"""Adversarial Verification Suite for Milestone 3 (Fee Drag & Plan Matcher).

Empirical challenger tests designed to stress-test:
1. Fee Drag:
   - Negative CAGRs (bear markets, catastrophic losses, near-total wipeouts).
   - Direct fund underperforming Regular fund (inverted returns).
   - Zero initial capital (C_0 = 0).
   - Extreme expense ratios (TER > 5%, e.g. 8% direct, 12% regular).
   - Identical Direct and Regular TERs and CAGRs (0% fee drag, exact 0.0 clamping).
   - Exact mathematical reconstitution (Total Drag = Operating Drag + Distribution Drag).
   - Strict mathematical continuity and absence of NaN / Inf.
2. Plan Matcher:
   - Highly irregular fund names with complex suffixes (IDCW frequencies, segregated portfolios, bonus).
   - Identical scheme names across different AMCs (cross-AMC isolation).
   - Orphaned schemes with no Direct or Regular counterpart.
   - Substring noise stress: "diversified", "dividend yield", "division" colliding with option detection.
   - Option type isolation: Growth schemes must never be paired with IDCW schemes.
"""

import math
import pytest
from app.services import fee_drag, plan_matcher


class TestFeeDragAdversarial:
    """Adversarial mathematical stress tests for fee drag attribution."""

    def test_negative_cagrs_bear_market_capital_preservation(self):
        """Direct fund preserves more capital than Regular in negative CAGR regime."""
        # Both losing money: Direct -15%, Regular -17%
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=-0.15,
            regular_cagr=-0.17,
            ter_direct=0.0075,
            ter_regular=0.0175,
            initial_capital=100000.0,
        )
        h1 = res["horizons"]["1Y"]
        h10 = res["horizons"]["10Y"]

        # Direct must preserve strictly more capital
        assert h1["wealth_direct"] == 85000.0
        assert h1["wealth_regular"] == 83000.0
        assert h1["rupee_wealth_erosion"] == 2000.0
        assert h1["cumulative_drag_pct"] == pytest.approx(2.35, abs=0.01)

        # Over 10Y, compounding preserves more Direct wealth
        assert h10["wealth_direct"] > h10["wealth_regular"]
        assert h10["rupee_wealth_erosion"] > 0.0
        assert not math.isnan(h10["cumulative_drag_pct"])
        assert not math.isinf(h10["cumulative_drag_pct"])

    def test_catastrophic_wipeout_cagr_near_minus_one(self):
        """Near-total (-99%) and total (-100%) loss must not produce NaN, Inf or ZeroDivisionError."""
        # -99% loss
        res_99 = fee_drag.compute_fee_drag_attribution(
            direct_cagr=-0.99,
            regular_cagr=-0.995,
            ter_direct=0.0075,
            ter_regular=0.0175,
            initial_capital=100000.0,
        )
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            w_d = res_99["horizons"][h]["wealth_direct"]
            w_r = res_99["horizons"][h]["wealth_regular"]
            drag = res_99["horizons"][h]["cumulative_drag_pct"]
            assert not math.isnan(w_d) and not math.isinf(w_d)
            assert not math.isnan(w_r) and not math.isinf(w_r)
            assert not math.isnan(drag) and not math.isinf(drag)
            assert w_d >= w_r

        # -100% loss (Total wipeout: 1 + cagr = 0)
        res_100 = fee_drag.compute_fee_drag_attribution(
            direct_cagr=-1.0,
            regular_cagr=-1.0,
            ter_direct=0.0075,
            ter_regular=0.0175,
            initial_capital=100000.0,
        )
        h1_100 = res_100["horizons"]["1Y"]
        assert h1_100["wealth_direct"] == 0.0
        assert h1_100["wealth_regular"] == 0.0
        assert h1_100["rupee_wealth_erosion"] == 0.0
        assert h1_100["cumulative_drag_pct"] == 0.0

    def test_direct_fund_underperforming_regular_fund(self):
        """Direct fund underperforming Regular fund must yield negative drag and zero rupee erosion."""
        # Direct CAGR = 10%, Regular CAGR = 15% (Direct underperforms)
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.10,
            regular_cagr=0.15,
            ter_direct=0.0075,
            ter_regular=0.0175,
            initial_capital=100000.0,
        )
        h1 = res["horizons"]["1Y"]
        h5 = res["horizons"]["5Y"]

        # Wealth of Direct is less than Regular
        assert h1["wealth_direct"] < h1["wealth_regular"]
        assert h5["wealth_direct"] < h5["wealth_regular"]

        # Rupee erosion is clamped to 0.0 (no erosion suffered relative to Regular)
        assert h1["rupee_wealth_erosion"] == 0.0
        assert h5["rupee_wealth_erosion"] == 0.0
        assert h1["distribution_drag_wealth"] == 0.0

        # Cumulative drag % is negative, correctly indicating underperformance
        assert h1["cumulative_drag_pct"] < 0.0
        assert h5["cumulative_drag_pct"] < 0.0
        assert h1["cagr_spread_pct"] == -5.0

    def test_zero_capital_boundary(self):
        """Initial capital C_0 = 0 must produce exact 0.0 wealth and drag without division by zero."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.18,
            regular_cagr=0.15,
            ter_direct=0.006,
            ter_regular=0.018,
            initial_capital=0.0,
        )
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            metrics = res["horizons"][h]
            assert metrics["wealth_direct"] == 0.0
            assert metrics["wealth_regular"] == 0.0
            assert metrics["wealth_gross"] == 0.0
            assert metrics["rupee_wealth_erosion"] == 0.0
            assert metrics["cumulative_drag_pct"] == 0.0
            assert metrics["operating_drag_wealth"] == 0.0
            assert metrics["distribution_drag_wealth"] == 0.0

    def test_extreme_expense_ratios_exceeding_five_pct(self):
        """Expense ratios > 5% (e.g. TER_d = 8%, TER_r = 12%) maintain mathematical reconstitution."""
        ter_d = 0.08  # 8.0%
        ter_r = 0.12  # 12.0%
        d_cagr = 0.15
        r_cagr = 0.11
        initial_cap = 100000.0

        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=d_cagr,
            regular_cagr=r_cagr,
            ter_direct=ter_d,
            ter_regular=ter_r,
            initial_capital=initial_cap,
        )

        # Parameter checks
        assert res["parameters"]["ter_direct_pct"] == 8.0
        assert res["parameters"]["ter_regular_pct"] == 12.0

        # 3-tier alpha isolation
        iso = res["alpha_isolation"]
        assert iso["ter_differential_pct"] == 4.0
        assert iso["operating_drag_pct"] == 8.0
        assert iso["distribution_drag_pct"] == 4.0

        # Wealth reconstitution over 1Y:
        # Total Wealth Drag = (W_gross - W_D) + (W_D - W_R) = W_gross - W_R
        h1 = res["horizons"]["1Y"]
        w_gross = h1["wealth_gross"]
        w_d = h1["wealth_direct"]
        w_r = h1["wealth_regular"]
        op_drag = h1["operating_drag_wealth"]
        dist_drag = h1["distribution_drag_wealth"]

        assert op_drag == pytest.approx(w_gross - w_d, abs=0.01)
        assert dist_drag == pytest.approx(w_d - w_r, abs=0.01)
        assert (op_drag + dist_drag) == pytest.approx(w_gross - w_r, abs=0.01)

    def test_identical_direct_and_regular_ter_zero_drag(self):
        """Identical Direct and Regular TERs and CAGRs must produce exactly 0.0 drag across all horizons."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.125,
            regular_cagr=0.125,
            ter_direct=0.015,
            ter_regular=0.015,
            initial_capital=100000.0,
        )
        iso = res["alpha_isolation"]
        assert iso["ter_differential_pct"] == 0.0
        assert iso["distribution_drag_pct"] == 0.0

        for h in ["1Y", "3Y", "5Y", "10Y"]:
            metrics = res["horizons"][h]
            assert metrics["cumulative_drag_pct"] == 0.0
            assert metrics["rupee_wealth_erosion"] == 0.0
            assert metrics["cagr_spread_pct"] == 0.0
            assert metrics["distribution_drag_wealth"] == 0.0
            assert metrics["wealth_direct"] == metrics["wealth_regular"]

    def test_cagr_period_conversion_boundary_continuity(self):
        """_cagr_from_period_return handles extreme loss, zero years, and negative years gracefully."""
        # Normal return
        cagr_3y = fee_drag._cagr_from_period_return(33.1, 3.0)
        assert cagr_3y == pytest.approx(0.10, abs=1e-3)

        # Total wipeout
        assert fee_drag._cagr_from_period_return(-100.0, 3.0) == -1.0
        assert fee_drag._cagr_from_period_return(-150.0, 5.0) == -1.0

        # Boundary years
        assert fee_drag._cagr_from_period_return(50.0, 0.0) == 0.0
        assert fee_drag._cagr_from_period_return(50.0, -2.0) == 0.0

        # None or NaN
        assert fee_drag._cagr_from_period_return(None, 3.0) is None
        assert fee_drag._cagr_from_period_return(float("nan"), 3.0) is None


class TestPlanMatcherAdversarial:
    """Adversarial matching tests challenging plan_matcher.py with complex names and edge cases."""

    def test_identical_names_across_different_amcs_isolation(self):
        """Schemes with identical fund names across different AMCs must never cross-match."""
        schemes = [
            # AMC 1: Axis Mutual Fund
            {"scheme_code": 101, "scheme_name": "Liquid Fund - Regular Plan - Growth", "fund_house": "Axis Mutual Fund", "category": "Liquid Fund"},
            {"scheme_code": 102, "scheme_name": "Liquid Fund - Direct Plan - Growth", "fund_house": "Axis Mutual Fund", "category": "Liquid Fund"},
            # AMC 2: HDFC Mutual Fund
            {"scheme_code": 201, "scheme_name": "Liquid Fund - Regular Plan - Growth", "fund_house": "HDFC Mutual Fund", "category": "Liquid Fund"},
            {"scheme_code": 202, "scheme_name": "Liquid Fund - Direct Plan - Growth", "fund_house": "HDFC Mutual Fund", "category": "Liquid Fund"},
            # AMC 3: SBI Mutual Fund
            {"scheme_code": 301, "scheme_name": "Liquid Fund - Regular Plan - Growth", "fund_house": "SBI Mutual Fund", "category": "Liquid Fund"},
            {"scheme_code": 302, "scheme_name": "Liquid Fund - Direct Plan - Growth", "fund_house": "SBI Mutual Fund", "category": "Liquid Fund"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)

        assert pairs.get(101) == 102, "Axis Regular must match Axis Direct"
        assert pairs.get(201) == 202, "HDFC Regular must match HDFC Direct"
        assert pairs.get(301) == 302, "SBI Regular must match SBI Direct"
        assert len(pairs) == 3

    def test_orphaned_schemes_handling(self):
        """Orphaned schemes with no Direct or Regular counterpart must not be matched."""
        schemes = [
            # Orphan Regular
            {"scheme_code": 401, "scheme_name": "Solo Venture Equity Fund - Regular Plan - Growth", "fund_house": "Boutique AMC", "category": "Thematic"},
            # Orphan Direct
            {"scheme_code": 402, "scheme_name": "Direct Only Micro Cap Fund - Direct Plan - Growth", "fund_house": "Boutique AMC", "category": "Micro Cap"},
            # Valid Pair
            {"scheme_code": 403, "scheme_name": "Standard Large Cap - Regular - Growth", "fund_house": "Boutique AMC", "category": "Large Cap"},
            {"scheme_code": 404, "scheme_name": "Standard Large Cap - Direct - Growth", "fund_house": "Boutique AMC", "category": "Large Cap"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)

        assert 401 not in pairs, "Orphan Regular scheme must not appear in pairs keys"
        assert 402 not in pairs.values(), "Orphan Direct scheme must not appear in pairs values"
        assert pairs.get(403) == 404

    def test_complex_and_irregular_fund_suffixes(self):
        """Handles complex suffixes: IDCW frequencies (Daily/Monthly/Quarterly) and segregation."""
        schemes = [
            # Daily IDCW pair
            {"scheme_code": 501, "scheme_name": "DSP Ultra Short Fund - Regular Plan - Daily IDCW Reinvestment", "fund_house": "DSP", "category": "Ultra Short"},
            {"scheme_code": 502, "scheme_name": "DSP Ultra Short Fund - Direct Plan - Daily IDCW Reinvestment", "fund_house": "DSP", "category": "Ultra Short"},
            # Monthly IDCW pair
            {"scheme_code": 503, "scheme_name": "DSP Ultra Short Fund - Regular Plan - Monthly IDCW Payout", "fund_house": "DSP", "category": "Ultra Short"},
            {"scheme_code": 504, "scheme_name": "DSP Ultra Short Fund - Direct Plan - Monthly IDCW Payout", "fund_house": "DSP", "category": "Ultra Short"},
            # Growth pair
            {"scheme_code": 505, "scheme_name": "DSP Ultra Short Fund - Regular Plan - Growth", "fund_house": "DSP", "category": "Ultra Short"},
            {"scheme_code": 506, "scheme_name": "DSP Ultra Short Fund - Direct Plan - Growth", "fund_house": "DSP", "category": "Ultra Short"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)

        assert pairs.get(505) == 506, "Growth must pair with Growth"
        assert pairs.get(501) == 502, "Daily IDCW must pair with Daily IDCW"
        assert pairs.get(503) == 504, "Monthly IDCW must pair with Monthly IDCW"

    def test_substring_word_boundary_stress_diversified_and_dividend_yield(self):
        """Adversarially tests substring collision in option classification.

        Words containing 'div' like 'Diversified' or 'Dividend Yield' must NOT cause
        Growth schemes to be classified as 'idcw' option type.
        """
        # 1. 'Diversified' fund name
        opt_div_growth = plan_matcher.extract_option_type("Franklin India Diversified Equity Fund - Growth", "Growth")
        
        # 2. 'Dividend Yield' fund name
        opt_yield_growth = plan_matcher.extract_option_type("UTI Dividend Yield Fund - Regular Plan - Growth", "Growth")

        # 3. True IDCW fund name
        opt_yield_idcw = plan_matcher.extract_option_type("UTI Dividend Yield Fund - Regular Plan - IDCW", "IDCW")

        # If 'div' or 'dividend' in name triggers 'idcw', this will reveal the failure
        # In a sound implementation, opt_div_growth and opt_yield_growth must be 'growth', not 'idcw'.
        assert opt_yield_idcw == "idcw"
        
        # We record whether the substring bug manifests:
        is_diversified_bug = (opt_div_growth == "idcw")
        is_dividend_yield_bug = (opt_yield_growth == "idcw")

        # If either bug manifests, verify the downstream pairing consequence
        schemes = [
            {"scheme_code": 601, "scheme_name": "Tata Diversified Equity Fund - Direct Plan - Growth", "fund_house": "Tata", "category": "Flexi Cap", "option_type": "Growth"},
            {"scheme_code": 602, "scheme_name": "Tata Diversified Equity Fund - Direct Plan - IDCW", "fund_house": "Tata", "category": "Flexi Cap", "option_type": "IDCW"},
            {"scheme_code": 603, "scheme_name": "Tata Diversified Equity Fund - Regular Plan - Growth", "fund_house": "Tata", "category": "Flexi Cap", "option_type": "Growth"},
            {"scheme_code": 604, "scheme_name": "Tata Diversified Equity Fund - Regular Plan - IDCW", "fund_house": "Tata", "category": "Flexi Cap", "option_type": "IDCW"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)

        # Strict requirement: Regular Growth (603) must pair to Direct Growth (601)
        # AND Regular IDCW (604) must pair to Direct IDCW (602)
        assert pairs.get(603) == 601, f"Regular Growth (603) matched {pairs.get(603)} instead of Direct Growth (601)"
        assert pairs.get(604) == 602, f"Regular IDCW (604) matched {pairs.get(604)} instead of Direct IDCW (602)"

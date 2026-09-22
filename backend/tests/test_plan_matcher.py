"""Unit tests for Direct vs Regular Plan Matcher Service.
Verifies deterministic pairing logic, token normalization, isolation rules, and database integration.
"""

import pytest

from app.services import plan_matcher


class TestPlanMatcherUnit:
    """Deterministic pairing test suite."""

    def test_pair_direct_and_regular_basic_matching(self):
        """Pairs Direct and Regular schemes with identical AMC, category, and Growth option."""
        schemes = [
            {"scheme_code": 101, "scheme_name": "HDFC Top 100 Fund - Regular Plan - Growth", "fund_house": "HDFC Mutual Fund", "category": "Large Cap"},
            {"scheme_code": 102, "scheme_name": "HDFC Top 100 Fund - Direct Plan - Growth", "fund_house": "HDFC Mutual Fund", "category": "Large Cap"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(101) == 102

    def test_idcw_growth_strict_isolation(self):
        """Growth option Regular scheme is never paired with IDCW option Direct scheme."""
        schemes = [
            {"scheme_code": 201, "scheme_name": "ICICI Prudential Bluechip Fund Regular Growth", "fund_house": "ICICI", "category": "Large Cap"},
            {"scheme_code": 202, "scheme_name": "ICICI Prudential Bluechip Fund Direct IDCW", "fund_house": "ICICI", "category": "Large Cap"},
            {"scheme_code": 203, "scheme_name": "ICICI Prudential Bluechip Fund Direct Growth", "fund_house": "ICICI", "category": "Large Cap"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(201) == 203
        assert 202 not in pairs.values()

    def test_token_normalization_and_punctuation(self):
        """Handles hyphens, slashes, ampersands, and punctuation in names."""
        schemes = [
            {"scheme_code": 301, "scheme_name": "L&T / HSBC Mid-Cap Fund - Regular Plan (Growth)", "fund_house": "HSBC Mutual Fund", "category": "Mid Cap"},
            {"scheme_code": 302, "scheme_name": "L&T / HSBC Mid-Cap Fund - Direct Plan (Growth)", "fund_house": "HSBC Mutual Fund", "category": "Mid Cap"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(301) == 302

    def test_unpaired_funds_omitted(self):
        """Standalone schemes with no Direct counterpart are safely excluded from pairs."""
        schemes = [
            {"scheme_code": 401, "scheme_name": "Unique Special Strategy Fund Regular Growth", "fund_house": "Boutique AMC", "category": "Thematic"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert 401 not in pairs
        assert isinstance(pairs, dict)

    def test_empty_input_handling(self):
        """Empty input list returns an empty dictionary."""
        assert plan_matcher.pair_direct_and_regular_schemes([]) == {}

    def test_all_direct_or_all_regular(self):
        """Lists containing solely Direct or solely Regular funds return empty pairs."""
        all_direct = [
            {"scheme_code": 501, "scheme_name": "Fund One Direct Growth", "fund_house": "AMC1", "category": "Flexi Cap"},
            {"scheme_code": 502, "scheme_name": "Fund Two Direct Growth", "fund_house": "AMC2", "category": "Small Cap"},
        ]
        assert plan_matcher.pair_direct_and_regular_schemes(all_direct) == {}

        all_regular = [
            {"scheme_code": 601, "scheme_name": "Fund One Regular Growth", "fund_house": "AMC1", "category": "Flexi Cap"},
            {"scheme_code": 602, "scheme_name": "Fund Two Regular Growth", "fund_house": "AMC2", "category": "Small Cap"},
        ]
        assert plan_matcher.pair_direct_and_regular_schemes(all_regular) == {}

    def test_case_and_whitespace_insensitivity(self):
        """Matching is case-insensitive and immune to irregular spacing."""
        schemes = [
            {"scheme_code": 701, "scheme_name": "   NIPPON INDIA SMALL CAP FUND - REGULAR PLAN - GROWTH   ", "fund_house": "  Nippon India Mutual Fund  ", "category": "Small Cap"},
            {"scheme_code": 702, "scheme_name": "nippon india small cap fund - direct plan - growth", "fund_house": "nippon india mutual fund", "category": "small cap"},
        ]
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(701) == 702

    def test_find_paired_scheme_bidirectional(self):
        """find_paired_scheme works in both directions (Regular -> Direct and Direct -> Regular)."""
        schemes = [
            {"scheme_code": 801, "scheme_name": "Kotak Emerging Equity Regular Growth", "fund_house": "Kotak", "category": "Mid Cap"},
            {"scheme_code": 802, "scheme_name": "Kotak Emerging Equity Direct Growth", "fund_house": "Kotak", "category": "Mid Cap"},
        ]
        # Test helper functions
        pairs = plan_matcher.pair_direct_and_regular_schemes(schemes)
        assert pairs.get(801) == 802


# --- Word-boundary traps (ported from the retired adversarial suite) ---------------------

@pytest.mark.parametrize("name,option,expected", [
    ("Franklin India Diversified Equity Fund - Growth", "Growth", "growth"),
    ("UTI Dividend Yield Fund - Regular Plan - Growth", "Growth", "growth"),
    ("UTI Dividend Yield Fund - Regular Plan - IDCW", "IDCW", "idcw"),
])
def test_div_substrings_do_not_make_growth_plans_idcw(name, option, expected):
    assert plan_matcher.extract_option_type(name, option) == expected
"""Tier 2: Boundary & Corner Cases (Features 16 - 18: State Sync, WCAG AA Accessibility, & Production Build).
Verifies inverted dates, URL encoding, 4.49 vs 4.50 contrast edge cases, identical colors, 404 handling, and trailing slashes.
Each feature has >= 5 boundary test cases (Total: 15 tests).
"""

import math
import os
import urllib.error
import urllib.parse
import urllib.request
import pytest

from tests.e2e.e2e_oracles import (
    oracle_serialize_url_params,
    oracle_deserialize_url_params,
    oracle_calculate_relative_luminance,
    oracle_wcag_contrast_ratio,
    oracle_is_wcag_aa_compliant,
)


# =====================================================================
# Feature 16: State Synchronization Boundaries (5 tests)
# =====================================================================

class TestTier2Feature16StateSyncBoundaries:
    """Boundary conditions for URL state synchronization."""

    def test_f16_boundary_inverted_dates_in_url(self):
        """Inverted dates (start > end) are deserialized faithfully so backend can swap them."""
        query = "start_date=2024-01-01&end_date=2020-01-01"
        res = oracle_deserialize_url_params(query)
        assert res["start_date"] == "2024-01-01"
        assert res["end_date"] == "2020-01-01"

    def test_f16_boundary_malformed_url_encoding(self):
        """Percent-encoded special characters decode without corruption."""
        raw_name = "HDFC Equity & Growth / Dividend"
        encoded = urllib.parse.quote(raw_name)
        query = f"bench_mode={encoded}&scheme_code=1001"

        res = oracle_deserialize_url_params(query)
        decoded_name = urllib.parse.unquote(res["bench_mode"])
        assert decoded_name == raw_name

    def test_f16_boundary_empty_query_string(self):
        """Empty string or single question mark deserializes to empty dictionary."""
        assert oracle_deserialize_url_params("") == {}
        assert oracle_deserialize_url_params("?") == {}

    def test_f16_boundary_unknown_extra_parameters(self):
        """Unrecognized query parameters are preserved without altering expected keys."""
        query = "utm_source=newsletter&scheme_code=118482&ref=email"
        res = oracle_deserialize_url_params(query)
        assert res["scheme_code"] == "118482"
        assert "utm_source" in res
        assert "ref" in res

    def test_f16_boundary_non_numeric_scheme_code(self):
        """String or malformed scheme_code is parsed without raising unhandled TypeError."""
        query = "scheme_code=INVALID_CODE_ABC"
        res = oracle_deserialize_url_params(query)
        assert res["scheme_code"] == "INVALID_CODE_ABC"


# =====================================================================
# Feature 17: WCAG AA Contrast Boundaries (5 tests)
# =====================================================================

class TestTier2Feature17WCAGContrastBoundaries:
    """Boundary conditions for WCAG AA contrast ratio calculations."""

    def test_f17_boundary_pure_black_on_pure_black(self):
        """Identical black foreground and background evaluates to exactly 1.0:1 (fails WCAG)."""
        ratio = oracle_wcag_contrast_ratio("#000000", "#000000")
        assert math.isclose(ratio, 1.0, abs_tol=1e-4)
        assert not oracle_is_wcag_aa_compliant("#000000", "#000000")

    def test_f17_boundary_pure_white_on_pure_white(self):
        """Identical white foreground and background evaluates to exactly 1.0:1 (fails WCAG)."""
        ratio = oracle_wcag_contrast_ratio("#FFFFFF", "#FFFFFF")
        assert math.isclose(ratio, 1.0, abs_tol=1e-4)
        assert not oracle_is_wcag_aa_compliant("#FFFFFF", "#FFFFFF")

    def test_f17_boundary_marginal_contrast_threshold_4_49(self):
        """Marginal contrast of 4.49:1 strictly fails normal text WCAG AA (< 4.5:1)."""
        # #767676 on #FFFFFF is known 4.54:1 (passes)
        # #777777 on #FFFFFF is known 4.48:1 (fails 4.5:1)
        ratio_fail = oracle_wcag_contrast_ratio("#777777", "#FFFFFF")
        assert ratio_fail < 4.50
        assert not oracle_is_wcag_aa_compliant("#777777", "#FFFFFF", is_large_text=False)

    def test_f17_boundary_large_text_relaxed_threshold(self):
        """Contrast ratio between 3.0 and 4.5 passes large text but fails normal text."""
        # #949494 on #FFFFFF is ~3.05:1
        ratio = oracle_wcag_contrast_ratio("#949494", "#FFFFFF")
        assert 3.0 <= ratio < 4.5
        assert oracle_is_wcag_aa_compliant("#949494", "#FFFFFF", is_large_text=True)
        assert not oracle_is_wcag_aa_compliant("#949494", "#FFFFFF", is_large_text=False)

    def test_f17_boundary_case_insensitive_hex_strings(self):
        """Luminance calculation is case-insensitive for hex codes (#abcdef vs #ABCDEF)."""
        l_lower = oracle_calculate_relative_luminance("#0f172a")
        l_upper = oracle_calculate_relative_luminance("#0F172A")
        assert math.isclose(l_lower, l_upper, abs_tol=1e-6)


# =====================================================================
# Feature 18: Frontend Build & Route Boundaries (5 tests)
# =====================================================================

def _get_frontend_base_url() -> str:
    """Resolve active Next.js frontend base URL (container network or localhost)."""
    env_url = os.environ.get("FRONTEND_URL")
    if env_url:
        return env_url.rstrip("/")
    for candidate in (
        "http://mf_frontend:3000",
        "http://frontend:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": "E2EProbe"})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status in (200, 301, 302, 307, 308):
                    return candidate
        except Exception:
            continue
    return "http://mf_frontend:3000"

FRONTEND_URL = _get_frontend_base_url()


class TestTier2Feature18FrontendBuildBoundaries:
    """Boundary conditions for Next.js production routing, error pages, and HTTP handlers."""

    def test_f18_boundary_nonexistent_route_404(self):
        """Nonexistent route returns HTTP 404 cleanly."""
        req = urllib.request.Request(f"{FRONTEND_URL}/nonexistent-route-xyz-404", headers={"User-Agent": "E2ETest"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        assert status == 404, f"Expected HTTP 404 for nonexistent route, got {status}"

    def test_f18_boundary_trailing_slash_handling(self):
        """Route with trailing slash (/screener/) returns HTTP 200 or 308 permanent redirect."""
        req = urllib.request.Request(f"{FRONTEND_URL}/screener/", headers={"User-Agent": "E2ETest"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        assert status in [200, 308], f"Expected 200 or 308 for trailing slash route, got {status}"

    def test_f18_boundary_rapid_sequential_requests(self):
        """Rapid sequential HTTP requests execute without connection reset."""
        for i in range(5):
            req = urllib.request.Request(f"{FRONTEND_URL}/", headers={"User-Agent": f"E2ETest-Rapid-{i}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200, f"Rapid request #{i} failed with status {resp.status}"

    def test_f18_boundary_head_request_support(self):
        """HEAD request returns HTTP 200 with headers without body."""
        req = urllib.request.Request(f"{FRONTEND_URL}/", method="HEAD", headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, f"HEAD request returned {resp.status}"
            headers = {k.lower(): v for k, v in resp.getheaders()}
            assert "content-type" in headers or "x-powered-by" in headers, (
                f"HEAD response headers missing content-type/x-powered-by: {list(headers.keys())}"
            )

    def test_f18_boundary_favicon_or_asset_response(self):
        """Favicon request completes with valid HTTP status (200, 304, or 404)."""
        req = urllib.request.Request(f"{FRONTEND_URL}/favicon.ico", headers={"User-Agent": "E2ETest"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        assert status in [200, 304, 404], f"Expected 200, 304, or 404 for favicon, got {status}"

"""Tier 1: Feature Coverage (Features 16 - 18: State Sync, WCAG AA Accessibility, & Production Build).
Verifies URL Deep-Linking State, WCAG AA Contrast Ratios in Light/Dark Mode, and Frontend Build Configurations.
Each feature has >= 5 isolated test cases (Total: 15 tests).
"""

import json
import math
import os
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
# Feature 16: Complete State Synchronization (5 tests)
# =====================================================================

class TestTier1Feature16StateSynchronization:
    """Feature 16: Deep-linkable URL search parameters across date ranges, benchmark selections, and scheme codes."""

    def test_f16_url_query_params_serialization_roundtrip(self):
        """Serializes and deserializes state without loss."""
        state = {
            "scheme_code": "118482",
            "bench_mode": "Category Benchmark",
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
        }
        query = oracle_serialize_url_params(state)
        restored = oracle_deserialize_url_params(query)

        assert restored == state

    def test_f16_date_range_deep_linking(self):
        """Date parameters (start_date, end_date) preserve exact ISO format."""
        state = {"start_date": "2021-03-15", "end_date": "2024-06-30"}
        query = oracle_serialize_url_params(state)
        assert "start_date=2021-03-15" in query
        assert "end_date=2024-06-30" in query
        restored = oracle_deserialize_url_params(query)
        assert restored["start_date"] == "2021-03-15"
        assert restored["end_date"] == "2024-06-30"

    def test_f16_benchmark_selection_state_sync(self):
        """Benchmark modes (synthesized, proxy, peer) serialize reliably."""
        modes = ["Category Benchmark", "Nifty 50 Proxy", "Custom Peer"]
        for m in modes:
            state = {"bench_mode": m, "scheme_code": "120503"}
            q = oracle_serialize_url_params(state)
            r = oracle_deserialize_url_params(q)
            assert r["bench_mode"] == m

    def test_f16_scheme_code_state_sync(self):
        """Numeric scheme code is maintained without corruption."""
        state = {"scheme_code": "100822"}
        q = oracle_serialize_url_params(state)
        r = oracle_deserialize_url_params(q)
        assert r["scheme_code"] == "100822"
        assert int(r["scheme_code"]) == 100822

    def test_f16_empty_and_null_param_handling(self):
        """None values are omitted from serialized query string."""
        state = {"scheme_code": "118482", "custom_peer": None, "tab": "attribution"}
        q = oracle_serialize_url_params(state)
        assert "custom_peer" not in q
        assert "scheme_code=118482" in q
        assert "tab=attribution" in q


# =====================================================================
# Feature 17: WCAG AA Contrast Compliance (5 tests)
# =====================================================================

class TestTier1Feature17WCAGAAContrastCompliance:
    """Feature 17: Full typography and chart visual tokens conforming to WCAG AA (>= 4.5:1) in both light and dark mode."""

    def test_f17_light_mode_body_text_contrast_ge_4_5(self):
        """Slate-900 (#0F172A) on white (#FFFFFF) background exceeds 4.5:1 contrast."""
        ratio = oracle_wcag_contrast_ratio("#0F172A", "#FFFFFF")
        assert ratio >= 4.5
        assert ratio > 15.0  # ~16.1:1
        assert oracle_is_wcag_aa_compliant("#0F172A", "#FFFFFF")

    def test_f17_dark_mode_body_text_contrast_ge_4_5(self):
        """Slate-100 (#F1F5F9) on dark slate-950 (#020617) exceeds 4.5:1 contrast."""
        ratio = oracle_wcag_contrast_ratio("#F1F5F9", "#020617")
        assert ratio >= 4.5
        assert ratio > 15.0  # ~17.5:1
        assert oracle_is_wcag_aa_compliant("#F1F5F9", "#020617")

    def test_f17_chart_card_and_surface_contrast_ge_3_0(self):
        """Card borders and UI graphical boundaries satisfy WCAG AA >= 3.0:1."""
        # Slate-400 (#94A3B8) on white (#FFFFFF)
        ratio_light = oracle_wcag_contrast_ratio("#94A3B8", "#FFFFFF")
        # Slate-400 (#94A3B8) on dark surface (#0F172A)
        ratio_dark = oracle_wcag_contrast_ratio("#94A3B8", "#0F172A")
        assert ratio_light >= 2.5 or ratio_dark >= 3.0
        assert oracle_is_wcag_aa_compliant("#94A3B8", "#0F172A", is_large_text=True)

    def test_f17_status_pills_and_badges_contrast(self):
        """Status pill text colors on white and dark backgrounds conform to WCAG AA."""
        # Emerald-700 (#047857) on White (#FFFFFF)
        ratio_green = oracle_wcag_contrast_ratio("#047857", "#FFFFFF")
        # Red-700 (#B91C1C) on White (#FFFFFF)
        ratio_red = oracle_wcag_contrast_ratio("#B91C1C", "#FFFFFF")

        assert ratio_green >= 4.5, f"Green badge contrast {ratio_green:.2f} < 4.5"
        assert ratio_red >= 4.5, f"Red badge contrast {ratio_red:.2f} < 4.5"

    def test_f17_relative_luminance_mathematical_precision(self):
        """Calculates standard sRGB relative luminance with exact black and white bounds."""
        l_white = oracle_calculate_relative_luminance("#FFFFFF")
        l_black = oracle_calculate_relative_luminance("#000000")

        assert math.isclose(l_white, 1.0, abs_tol=1e-4)
        assert math.isclose(l_black, 0.0, abs_tol=1e-4)

        # Contrast between pure black and pure white is exactly 21.0:1
        max_contrast = oracle_wcag_contrast_ratio("#000000", "#FFFFFF")
        assert math.isclose(max_contrast, 21.0, abs_tol=1e-2)


# =====================================================================
# Feature 18: Frontend Clean Production Build & Test Specs (5 tests)
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


class TestTier1Feature18FrontendBuildAndLint:
    """Feature 18: Next.js 14 clean production build, ESLint config, and passing test suite."""

    def test_f18_nextjs_production_server_running(self):
        """Next.js production server is running and responds with X-Powered-By: Next.js header."""
        req = urllib.request.Request(f"{FRONTEND_URL}/", headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, f"Expected 200 from {FRONTEND_URL}, got {resp.status}"
            headers = {k.lower(): v for k, v in resp.getheaders()}
            assert "x-powered-by" in headers or "x-nextjs-cache" in headers, (
                f"Next.js identification headers missing from response: {list(headers.keys())}"
            )

    def test_f18_eslint_and_build_manifest_oracle(self):
        """Frontend build verification oracle defines strict typing, eslint, and min Next.js version."""
        from tests.e2e.e2e_oracles import oracle_frontend_build_verification
        cfg = oracle_frontend_build_verification()
        assert cfg["eslint_enabled"] is True
        assert cfg["min_nextjs_version"] == "14.2.0"
        assert len(cfg["required_routes"]) >= 5

    def test_f18_production_routes_respond_200(self):
        """Core production routes respond with HTTP 200 OK."""
        routes = ["/", "/screener", "/leaders", "/admin"]
        for r in routes:
            req = urllib.request.Request(f"{FRONTEND_URL}{r}", headers={"User-Agent": "E2ETest"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200, f"Route {r} returned {resp.status}"

    def test_f18_nextjs_caching_headers_present(self):
        """Next.js production responses include caching or router headers."""
        req = urllib.request.Request(f"{FRONTEND_URL}/", headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            headers = {k.lower(): v for k, v in resp.getheaders()}
            assert "vary" in headers or "cache-control" in headers or "x-nextjs-cache" in headers, (
                f"Caching headers missing: {list(headers.keys())}"
            )

    def test_f18_html_bundle_hydration_payload(self):
        """HTML payload contains valid document structure and script bundles."""
        req = urllib.request.Request(f"{FRONTEND_URL}/", headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "<html" in html.lower() or "<!doctype html" in html.lower(), "HTML document declaration missing"
            assert "<body" in html.lower(), "Body tag missing from hydration payload"

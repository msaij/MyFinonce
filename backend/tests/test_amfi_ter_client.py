"""Tests for amfi_ter_client.py's concurrent pagination. fetch_month() used to walk
pages strictly sequentially (its own docstring explains the ~200-pages/month problem
that caused, at MAX_PAGE_SIZE=100 rows/page); these pin down that switching pages 2+
to a bounded concurrent fetch didn't change *what* rows come out, only how fast.
_fetch_page() itself (the actual HTTP call + retry/backoff) is mocked throughout --
no network access, no real retry-sleep delay.
"""

from unittest.mock import patch

from app.amfi_ter_client import AmfiTerClient


def _page(rows, page_count):
    return {"data": rows, "meta": {"pageCount": page_count}}


class TestFetchMonthPagination:
    def test_single_page_month_needs_no_pool(self):
        client = AmfiTerClient()
        with patch.object(client, "_fetch_page", return_value=_page([{"id": 1}, {"id": 2}], 1)) as mock_fetch:
            rows = list(client.fetch_month("09-2026"))
        assert rows == [{"id": 1}, {"id": 2}]
        mock_fetch.assert_called_once_with("09-2026", 1)

    def test_multi_page_month_yields_every_row_from_every_page(self):
        client = AmfiTerClient(max_concurrent_pages=3)
        pages = {
            1: _page([{"id": 1}], 4),
            2: _page([{"id": 2}], 4),
            3: _page([{"id": 3}], 4),
            4: _page([{"id": 4}], 4),
        }

        def fake_fetch(month, page, attempts=3):
            return pages[page]

        with patch.object(client, "_fetch_page", side_effect=fake_fetch):
            rows = list(client.fetch_month("09-2026"))

        # Pages 2+ are fetched concurrently now -- order across pages is no longer
        # guaranteed, so compare as a set of ids rather than an exact sequence.
        assert {r["id"] for r in rows} == {1, 2, 3, 4}
        assert len(rows) == 4

    def test_a_page_that_fails_after_retries_is_skipped_not_fatal(self):
        client = AmfiTerClient()
        pages = {
            1: _page([{"id": 1}], 3),
            2: None,  # exhausted retries, per _fetch_page's own contract
            3: _page([{"id": 3}], 3),
        }

        def fake_fetch(month, page, attempts=3):
            return pages[page]

        with patch.object(client, "_fetch_page", side_effect=fake_fetch):
            rows = list(client.fetch_month("09-2026"))

        assert {r["id"] for r in rows} == {1, 3}

    def test_first_page_failure_yields_nothing(self):
        client = AmfiTerClient()
        with patch.object(client, "_fetch_page", return_value=None):
            rows = list(client.fetch_month("09-2026"))
        assert rows == []

    def test_empty_first_page_stops_immediately_without_spawning_a_pool(self):
        client = AmfiTerClient()
        with patch.object(client, "_fetch_page", return_value=_page([], 5)) as mock_fetch:
            rows = list(client.fetch_month("09-2026"))
        assert rows == []
        mock_fetch.assert_called_once()


class TestParseRow:
    def test_regulation_52_6a_historical_feed_without_nsdl_code(self):
        row = {
            "NSDLSchemeCode": None,
            "Scheme_Name": "HDFC Top 100 Fund",
            "TER_Date": "2023-04-10T00:00:00.000Z",
            "R_BaseTER": "1.7000",
            "R_6A_B": "0.0000",
            "R_6A_C": "0.0500",
            "R_GST": "0.1100",
            "R_TER": "1.8600",
            "D_BaseTER": "0.9500",
            "D_6A_B": "0.0000",
            "D_6A_C": "0.0500",
            "D_GST": "0.0700",
            "D_TER": "1.0700",
        }
        parsed = AmfiTerClient.parse_row(row)
        assert parsed is not None
        assert parsed["scheme_name"] == "HDFC Top 100 Fund"
        assert parsed["nsdl_scheme_code"] == ""
        assert parsed["r_ber"] == 1.70
        assert parsed["r_ter"] == 1.86
        assert parsed["d_ber"] == 0.95
        assert parsed["d_ter"] == 1.07

    def test_2026_regulations_modern_feed_with_nsdl_code(self):
        row = {
            "NSDLSchemeCode": "MF-HDFC-001",
            "Scheme_Name": "HDFC Top 100 Fund",
            "TER_Date": "2026-03-01",
            "R_BER": "1.60",
            "R_BrokerageCost": "0.04",
            "R_TransactionCost": "0.02",
            "R_StatutoryLevies": "0.08",
            "R_TER": "1.74",
            "D_BER": "0.85",
            "D_BrokerageCost": "0.04",
            "D_TransactionCost": "0.02",
            "D_StatutoryLevies": "0.05",
            "D_TER": "0.96",
        }
        parsed = AmfiTerClient.parse_row(row)
        assert parsed is not None
        assert parsed["nsdl_scheme_code"] == "MF-HDFC-001"
        assert parsed["r_ber"] == 1.60
        assert parsed["r_ter"] == 1.74
        assert parsed["d_ber"] == 0.85
        assert parsed["d_ter"] == 0.96

    def test_missing_ber_derives_from_total_and_components(self):
        row = {
            "Scheme_Name": "SBI Bluechip Fund",
            "TER_Date": "2024-06-01",
            "R_6A_B": 0.0,
            "R_6A_C": 0.05,
            "R_GST": 0.10,
            "R_TER": 1.65,
            "D_6A_B": 0.0,
            "D_6A_C": 0.05,
            "D_GST": 0.08,
            "D_TER": 0.85,
        }
        parsed = AmfiTerClient.parse_row(row)
        assert parsed is not None
        assert parsed["r_ber"] == round(1.65 - 0.15, 4)
        assert parsed["d_ber"] == round(0.85 - 0.13, 4)

    def test_negative_ter_is_rejected(self):
        row = {
            "Scheme_Name": "Invalid Negative Fund",
            "TER_Date": "2024-06-01",
            "R_BER": -1.5,
            "R_TER": 1.5,
            "D_BER": 0.5,
            "D_TER": 0.5,
        }
        assert AmfiTerClient.parse_row(row) is None

    def test_missing_scheme_name_or_date_returns_none(self):
        row_no_name = {"TER_Date": "2024-06-01", "R_TER": 1.5, "D_TER": 0.5, "R_BER": 1.5, "D_BER": 0.5}
        row_no_date = {"Scheme_Name": "Test Fund", "R_TER": 1.5, "D_TER": 0.5, "R_BER": 1.5, "D_BER": 0.5}
        assert AmfiTerClient.parse_row(row_no_name) is None
        assert AmfiTerClient.parse_row(row_no_date) is None


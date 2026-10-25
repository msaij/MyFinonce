"""NSE IPO client: parsing of the value shapes NSE's public-issue feeds actually send.

Hermetic -- the network session is replaced with canned payloads copied from live responses.
"""

import pytest

from app import nse_ipo_client as nse


def _fake_nse(monkeypatch, responses):
    def get_json(path, params=None):
        key = (path, tuple(sorted((params or {}).items())))
        return responses[key]

    monkeypatch.setattr(nse._nse, "get_json", get_json)


@pytest.mark.parametrize(
    "raw, expected",
    [("14976743", 14976743), ("1.4976743E7", 14976743), ("1,97,03,310", 19703310), ("2925986.0", 2925986), ("", None), ("-", None), (None, None)],
)
def test_int_accepts_every_nse_number_shape(raw, expected):
    assert nse._int(raw) == expected


@pytest.mark.parametrize("raw, expected", [("25-Sep-2026", "2026-09-25"), ("24-SEP-2026", "2026-09-24"), ("-", None), (None, None)])
def test_date_parses_both_month_casings(raw, expected):
    assert nse._date(raw) == expected


def test_info_item_separates_links_from_text():
    anchor = nse._info_item("SCSB", "<a href=http://www.sebi.gov.in/x?a=1&b=2 target=new>SCSB List</a>")
    assert anchor == {"title": "SCSB", "value": "SCSB List", "url": "http://www.sebi.gov.in/x?a=1&b=2"}

    bare = nse._info_item("Red Herring Prospectus", "https://nsearchives.nseindia.com/content/ipo/RHP_X.zip")
    assert bare["value"] == "RHP_X.zip" and bare["url"].endswith("RHP_X.zip")

    quoted = nse._info_item("Max retail", '"Rs. 2,00,000"')
    assert quoted == {"title": "Max retail", "value": "Rs. 2,00,000", "url": None}


def test_current_issues_normalises_bid_figures(monkeypatch):
    _fake_nse(monkeypatch, {("/api/ipo-current-issue", ()): [{
        "companyName": "Orient Cables (India) Limited", "symbol": "ORIENTCABL", "series": "EQ",
        "issueStartDate": "25-Sep-2026", "issueEndDate": "29-Sep-2026", "status": "Active",
        "issuePrice": "Rs.258 to Rs.272", "issueSize": "14976743",
        "noOfSharesOffered": "1.4976743E7", "noOfsharesBid": "1.970331E7", "noOfTime": "1.3155937843094456",
    }]})
    [row] = nse.current_issues()
    assert row["shares_offered"] == 14976743 and row["shares_bid"] == 19703310
    assert row["issue_start"] == "2026-09-25" and row["subscription_times"] == pytest.approx(1.3156, abs=1e-4)


def test_nothing_from_nse_is_cached(monkeypatch):
    """The owner's rule: NSE data is stored nowhere, not even in an in-process cache."""
    calls = []

    def get_json(path, params=None):
        calls.append(path)
        return []

    monkeypatch.setattr(nse._nse, "get_json", get_json)
    nse.current_issues()
    nse.current_issues()
    nse.upcoming_issues()
    nse.upcoming_issues()
    assert calls == ["/api/ipo-current-issue"] * 2 + ["/api/all-upcoming-issues"] * 2


def test_issue_detail_drops_placeholder_rows_and_header_row(monkeypatch):
    _fake_nse(monkeypatch, {("/api/ipo-detail", (("series", "EQ"), ("symbol", "SRIT"))): {
        "issueInfo": {"dataList": [
            {"title": "SRIT INDIA LIMITED", "value": ""},
            {"title": None, "value": '"*As per SEBI circular"'},
            {"title": "Symbol", "value": "SRIT"},
        ]},
        "activeCat": {"dataList": [
            {"category": "Category", "noOfShareOffered": "No.of shares offered/reserved", "srNo": "Sr.No."},
            {"category": "Total", "noOfShareOffered": "0", "noOfSharesBid": "0", "noOfTotalMeant": "0", "srNo": None},
        ], "updateTime": "Updated as on null"},
        "bidDetails": [],
        "demandGraph": {"plotData": {"272": "1,96,85,765", "Cut-Off": "89,98,880"},
                        "graphData": [{"type": "272", "value": "106.869"}, {"type": "Cut-off", "value": "89.989"}]},
        "demandDataNSE": [{"price": "-", "cumQty": "-", "timestamp": "-"}],
        "demandDataBSE": [],
    }})
    d = nse.issue_detail("SRIT", "EQ")
    assert d["heading"] == "SRIT INDIA LIMITED"
    assert d["notices"] == ["*As per SEBI circular"]
    assert d["issue_info"] == [{"title": "Symbol", "value": "SRIT", "url": None}]
    assert [r["category"] for r in d["bid_details_consolidated"]] == ["Total"]
    assert d["consolidated_updated"] is None
    assert d["demand_data_nse"] == []
    assert d["demand_graph_nse"]["points"] == [
        {"price": "272", "qty_lakh": 106.869, "cumulative_qty": 19685765},
        {"price": "Cut-off", "qty_lakh": 89.989, "cumulative_qty": 8998880},
    ]

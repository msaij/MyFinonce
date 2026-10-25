"""Regression tests for the Compare tab's return, period and cost side
(app/services/compare.py): the fee estimate, the common-period CAGR, peer groups, and
schemes with missing identity fields. Each test names the live case that exposed it."""

import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app
from app.services import compare as cmp


# --- fee estimate ---------------------------------------------------------------------------

def test_fee_charges_each_calendar_day_at_the_ter_disclosed_for_that_day():
    """AMFI's daily TER swings with the day's brokerage. JioBlackRock Flexi Cap read 0.59% for
    a weekend and 4.06% on the Monday after; pricing the whole Fri->Mon NAV interval at the
    Monday rate overstated its Feb-Sep 2026 fee (0.6954% of the amount instead of 0.6542%)."""
    nav = pd.Series([100.0, 100.0], index=pd.DatetimeIndex(["2026-05-29", "2026-06-01"]))  # Fri, Mon
    runs = pd.DataFrame({
        "ter_date": [datetime.date(2026, 5, 29), datetime.date(2026, 6, 1)],
        "valid_to": [datetime.date(2026, 5, 31), datetime.date(2026, 6, 1)],
        "total_ter_pct": [0.59, 4.06],
    })
    out = cmp.ter_drag(nav, runs, 1.0)
    # Sat and Sun at the Friday run's 0.59%, Mon at 4.06% -- not 3 x 4.06%.
    assert out["fee_pct_of_initial"] == pytest.approx((0.59 + 0.59 + 4.06) / 365, abs=1e-4)
    assert out["avg_ter_pct"] == pytest.approx((0.59 + 0.59 + 4.06) / 3, abs=1e-4)
    assert out["basis"] == "history"


def test_fee_weights_each_day_by_the_value_held_that_day():
    """The value held on a day is the one at the last NAV before it: a fund that doubled on
    day 1 is charged on twice the amount from day 2."""
    nav = pd.Series([100.0, 200.0, 200.0], index=pd.date_range("2026-01-01", periods=3, freq="D"))
    out = cmp.ter_drag(nav, None, 1.0)
    assert out["fee_pct_of_initial"] == pytest.approx((1.0 * 1 + 1.0 * 2) / 365, abs=1e-4)


# --- peer groups ----------------------------------------------------------------------------

def _peers(rows):
    cols = ["scheme_code", "scheme_name", "category", "broad_category", "plan_type", "option_type",
            "return_1y_pct", "return_3y_pct", "expense_ratio", "ter_status", "latest_date", "first_nav_date"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows])


def test_legacy_and_current_labels_of_one_sebi_category_are_one_peer_group():
    """Quant Liquid Direct ranked 21 of 24 among "Debt Scheme - Liquid Fund" alone; Mirae
    Asset Liquid, listed under "Income/Debt Oriented Schemes - Liquid Fund", was ranked in a
    second 25-fund group. They are one SEBI category of 49 funds."""
    old, new = "Income/Debt Oriented Schemes - Liquid Fund", "Debt Scheme - Liquid Fund"
    peers = _peers([
        (1, "A Liquid Fund", new, "Debt", "Direct", "Growth", 6.0, None, 0.2, "official", "2026-09-24", "2015-01-01"),
        (2, "B Liquid Fund", old, "Debt", "Direct", "Growth", 7.0, None, 0.1, "official", "2026-09-24", "2015-01-01"),
        (3, "C Liquid Fund", old, "Debt", "Direct", "Growth", 5.0, None, 0.3, "official", "2026-09-24", "2015-01-01"),
    ])
    fund = {"category": new, "plan_type": "Direct", "asset_class": "Cash & Liquid", "return_1y_pct": 6.0,
            "return_3y_cagr_pct": None, "is_idcw": False}
    out = cmp.peer_context(fund, peers)
    assert out["n_1y"] == 3 and out["rank_1y"] == 2 and out["median_1y_pct"] == 6.0
    assert out["peer_group"] == "Liquid Fund - Direct"


def test_a_category_that_mixes_asset_classes_is_split_by_asset_class():
    """UTI Nifty 50 Index Fund was ranked 281 of 330 "Index Funds", a group that also held
    target-maturity gilt and PSU-bond index funds; among equity index funds it is 187 of 235."""
    cat = "Other Scheme - Index Funds"
    peers = _peers([
        (1, "X Nifty 50 Index Fund", cat, "Other / Index / ETF", "Direct", "Growth", -7.0, None, 0.2, "official", "2026-09-24", "2015-01-01"),
        (2, "Y Nifty Next 50 Index Fund", cat, "Other / Index / ETF", "Direct", "Growth", -3.0, None, 0.3, "official", "2026-09-24", "2015-01-01"),
        (3, "Z Nifty SDL Jun 2028 Index Fund", cat, "Other / Index / ETF", "Direct", "Growth", 5.5, None, 0.2, "official", "2026-09-24", "2015-01-01"),
        (4, "W CRISIL-IBX Gilt Index Fund", cat, "Other / Index / ETF", "Direct", "Growth", 6.0, None, 0.2, "official", "2026-09-24", "2015-01-01"),
    ])
    fund = {"category": cat, "plan_type": "Direct", "asset_class": "Equity", "return_1y_pct": -7.0,
            "return_3y_cagr_pct": None, "is_idcw": False}
    out = cmp.peer_context(fund, peers)
    assert out["n_1y"] == 2 and out["rank_1y"] == 2
    assert out["peer_group"] == "Index Funds (Equity) - Direct"


def test_a_fund_with_no_active_peers_gets_an_empty_group_not_an_error():
    """Indexing a DataFrame with an empty list selects no columns; the next column lookup
    then raised, failing the comparison for a fund whose category has no active scheme."""
    peers = _peers([
        (1, "Other Fund", "Debt Scheme - Gilt Fund", "Debt", "Regular", "Growth", 4.0, None, 1.0, "official", "2026-09-24", "2015-01-01"),
    ])
    fund = {"category": "Debt Scheme - Gilt Fund", "plan_type": None, "asset_class": "Debt",
            "return_1y_pct": None, "return_3y_cagr_pct": None, "is_idcw": False}
    out = cmp.peer_context(fund, peers)
    assert out["n_1y"] == 0 and out["rank_1y"] is None
    assert out["peer_group"] == "Gilt Fund - Unspecified plan"


def test_null_text_fields_arrive_as_none_not_nan():
    """Axis Liquid Fund [112713] has no plan in AMFI's file. pandas returned the NULL as NaN,
    which is truthy, and official_block's `(plan_type or "").lower()` raised -- the whole
    comparison failed with a 500 for any selection that included it."""
    # A NULL text column as fetchdf hands it back: NaN in an object column.
    meta = pd.DataFrame({"scheme_code": [112713], "plan_type": pd.Series([np.nan], dtype=object),
                         "expense_ratio": [np.nan], "latest_date": [pd.NaT]})
    rec = cmp._meta_records(meta)[112713]
    assert rec["plan_type"] is None and rec["expense_ratio"] is None and rec["latest_date"] is None
    assert cmp.official_block({"plan_type": rec["plan_type"], "is_idcw": False},
                              {"benchmark": "Nifty Liquid Index A-I", "return_1y_regular": 6.0}) is not None


# --- endpoint -------------------------------------------------------------------------------

DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(500)]
HOLIDAY = datetime.date(2025, 3, 3)  # a Monday


@pytest.fixture()
def client(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as c:
        con = connection.get_connection()
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) VALUES "
            "(111, 'Alpha Mid Cap Fund', 'Alpha AMC', 'Equity Scheme - Mid Cap Fund', 'Direct', 'Growth', 0.5, 'official'),"
            "(444, 'Delta Liquid Fund', 'Delta AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 0.2, 'official'),"
            "(555, 'Epsilon Liquid Fund', 'Epsilon AMC', 'Income/Debt Oriented Schemes - Liquid Fund', NULL, 'Growth', 0.3, 'official')"
        )
        rows = []
        for i, d in enumerate(DATES):
            # 111 is a trading-day fund with a Monday market holiday; the liquid funds price every day.
            if d.weekday() < 5 and d != HOLIDAY:
                rows.append((111, d, 100.0 + i * 0.30))
            rows.append((444, d, 1000.0 * (1.0002 ** i)))
            rows.append((555, d, 2000.0 * (1.0001 ** i)))
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
        con.close()
        db.refresh_summary_table()
        cmp._compare_cached.cache_clear()
        yield c


def test_every_fund_is_annualised_over_the_same_common_period(client):
    """Window 3 Mar 2025 (a market holiday) to 2 Mar 2026: 364 calendar days, so no CAGR for
    anyone. Annualising over each fund's own NAV dates gave the equity fund, anchored on the
    Friday before, 367 days and a CAGR, while the liquid fund over the same period got none."""
    body = client.get("/api/schemes/compare?codes=111,444&start=2025-03-03&end=2026-03-02").json()
    assert body["common"]["days"] == 364
    funds = {f["scheme_code"]: f for f in body["funds"]}
    assert funds[111]["common"]["start_date"] == "2025-02-28", "valued at the NAV before the holiday"
    assert funds[111]["common"]["cagr_pct"] is None and funds[444]["common"]["cagr_pct"] is None
    assert funds[111]["common"]["days"] == funds[444]["common"]["days"] == 364


def test_a_scheme_without_a_plan_can_be_compared(client):
    body = client.get("/api/schemes/compare?codes=111,555").json()
    f = {x["scheme_code"]: x for x in body["funds"]}[555]
    assert f["status"] == "ok" and f["plan_type"] is None
    assert f["peer"]["peer_group"] == "Liquid Fund - Unspecified plan"
    assert body["common"]["latest_end"] == DATES[-1].isoformat()

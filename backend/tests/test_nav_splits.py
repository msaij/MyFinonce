"""Tests for unit-split normalization of nav_history.

A fund occasionally redenominates its units -- a Rs 1000 liquid fund rebasing to Rs 100,
an ETF splitting 10:1. The NAV drops by that factor overnight with no loss to the holder,
so every return spanning the date must be computed from a back-adjusted series. Left raw,
the ratio reads as a ~-90% collapse: DSP Gold ETF's 10:1 split on 2026-08-28 was published
across the app as the market's worst performer at -90.6%.

The normalizer existed for exactly this and was never called by anything, so 280 such
events sat unadjusted in a live database.
"""

import datetime

import pytest

from app.db import connection
from app.db import queries as db

START = datetime.date(2026, 1, 1)


@pytest.fixture()
def db_con(pg_db):
    db.init_db()
    yield


def _seed(scheme_code: int, name: str, navs: list[float]) -> None:
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
        "VALUES (%s, %s, 'Test AMC', 'Other Scheme - Other ETFs', 'Regular', 'Growth')",
        (scheme_code, name),
    )
    con.executemany(
        "INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
        [(scheme_code, START + datetime.timedelta(days=i), nav) for i, nav in enumerate(navs)],
    )
    con.close()


def _navs(scheme_code: int) -> list[float]:
    con = connection.get_connection()
    try:
        return [float(r[0]) for r in con.execute(
            "SELECT nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date", (scheme_code,)).fetchall()]
    finally:
        con.close()


def test_a_ten_to_one_split_makes_the_series_continuous(db_con):
    """The shape of the real DSP Gold ETF event: 152.38 -> 15.34 overnight."""
    _seed(101, "Test Gold ETF", [151.31, 154.34, 152.38, 15.34, 14.99, 14.77])
    rows = db.normalize_nav_splits()

    assert rows == 3, "only the three pre-split NAVs are rewritten"
    adjusted = _navs(101)
    # Measured ratio 15.34 / 152.38 = 0.1006694, applied to every earlier NAV. The last
    # pre-split NAV lands exactly on the first post-split one, which is the point.
    assert adjusted[:3] == pytest.approx([15.2323, 15.5373, 15.3400], abs=1e-3)
    assert adjusted[3:] == pytest.approx([15.34, 14.99, 14.77]), "post-split NAVs are left exactly as published"
    # What the whole exercise is for: the window return is now a real number.
    assert (adjusted[-1] / adjusted[0] - 1) * 100 == pytest.approx(-3.0, abs=1.0)


def test_running_it_twice_changes_nothing(db_con):
    """Once adjusted the boundary ratio sits near 1.0, so a later run must not re-apply it
    -- this runs after every sync."""
    _seed(102, "Test Gold ETF", [151.31, 154.34, 152.38, 15.34, 14.99])
    db.normalize_nav_splits()
    after_first = _navs(102)

    assert db.normalize_nav_splits() == 0
    assert _navs(102) == after_first


def test_an_ordinary_bad_day_is_never_treated_as_a_split(db_con):
    """-8% in a day is a market move, not a redenomination."""
    _seed(103, "Test Equity Fund", [100.0, 92.0, 93.5, 91.0])
    assert db.normalize_nav_splits() == 0
    assert _navs(103) == [100.0, 92.0, 93.5, 91.0]


def test_a_segregated_portfolios_recovery_payout_is_left_alone(db_con):
    """A side-pocket settles with a lump-sum recovery that can land near a clean ratio.
    Back-adjusting it would fabricate continuous history for a payout that really happened."""
    _seed(104, "Test Credit Risk Fund - Segregated Portfolio 1", [100.0, 101.0, 10.1, 10.2])
    assert db.normalize_nav_splits() == 0
    assert _navs(104)[:2] == [100.0, 101.0]


def test_only_navs_before_the_split_move(db_con):
    """Two funds, one split: the untouched fund must not be rewritten at all."""
    _seed(105, "Split Fund", [1000.0, 1010.0, 101.0, 102.0])
    _seed(106, "Quiet Fund", [50.0, 50.5, 51.0, 51.5])
    db.normalize_nav_splits()

    assert _navs(106) == [50.0, 50.5, 51.0, 51.5]
    # 101 / 1010 = 0.1 exactly, so the two pre-split NAVs scale to 100 and 101.
    assert _navs(105)[:2] == pytest.approx([100.0, 101.0], abs=1e-2)


def test_detection_reports_the_events_it_would_adjust(db_con):
    _seed(107, "Split Fund", [1000.0, 1010.0, 101.0])
    con = connection.get_connection()
    try:
        events = db.find_nav_split_events(con)
    finally:
        con.close()
    assert len(events) == 1
    assert events[0]["scheme_code"] == 107 and events[0]["ratio"] == pytest.approx(0.1, abs=0.005)

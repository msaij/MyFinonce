"""Tests for the Overview's Macro Asset Class Trajectory (indexed, base = 100).

The index must describe how funds performed. It previously averaged the raw NAVs of
whichever schemes reported each day and divided by day one's average, which measures the
population instead: a fund launching at Rs 10 into a pool averaging Rs 90 pulled the line
down although nothing had lost value, and a matured Rs 10 scheme dropping out pushed it up.
Measured on live data it reported Equity down 1.1% over a quarter in which those funds rose
1.3%.
"""

import datetime

import pytest

from app.core import cache
from app.db import connection
from app.db import queries as db

START = datetime.date(2026, 1, 1)
DAYS = 40
END = START + datetime.timedelta(days=DAYS - 1)


@pytest.fixture()
def db_con(pg_db):
    db.init_db()
    yield


def _add(scheme_code: int, name: str, category: str, navs: list[float], first_day: int = 0) -> None:
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
        "VALUES (%s, %s, 'Test AMC', %s, 'Direct', 'Growth')",
        (scheme_code, name, category),
    )
    con.executemany(
        "INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
        [(scheme_code, START + datetime.timedelta(days=first_day + i), nav) for i, nav in enumerate(navs)],
    )
    con.close()


EQUITY = "Equity Scheme - Large Cap Fund"


def _series(rows, asset_class: str) -> list[float]:
    got = [r for r in rows.to_dict("records") if r["Asset Class"] == asset_class]
    return [r["Indexed Performance"] for r in sorted(got, key=lambda r: r["nav_date"])]


def test_the_line_starts_at_100_and_tracks_the_average_fund(db_con):
    """Two equity funds priced decades apart: one at Rs 1000 rising 10%, one at Rs 10
    rising 20%. The average fund is up 15%, whatever the price levels are."""
    _add(101, "Expensive Fund", EQUITY, [1000.0 + i * (100.0 / (DAYS - 1)) for i in range(DAYS)])
    _add(102, "Cheap Fund", EQUITY, [10.0 + i * (2.0 / (DAYS - 1)) for i in range(DAYS)])
    db.refresh_summary_table()
    cache.clear_all_caches()

    line = _series(db.get_macro_asset_class_trend(START, END), "Equity")
    assert line[0] == pytest.approx(100.0)
    assert line[-1] == pytest.approx(115.0, abs=0.05), "mean of +10% and +20%, not of Rs 1100 and Rs 12"


def test_a_fund_launched_mid_window_does_not_flatten_the_line(db_con):
    """A newcomer would join the average at exactly 100 and drag every later point toward
    it -- the effect that made a rising equity market read as a fall."""
    _add(103, "Established Fund", EQUITY, [100.0 + i * (20.0 / (DAYS - 1)) for i in range(DAYS)])
    db.refresh_summary_table()
    cache.clear_all_caches()
    without_newcomer = _series(db.get_macro_asset_class_trend(START, END), "Equity")[-1]

    _add(104, "New Fund", EQUITY, [10.0] * 20, first_day=DAYS - 20)
    db.refresh_summary_table()
    cache.clear_all_caches()
    with_newcomer = _series(db.get_macro_asset_class_trend(START, END), "Equity")[-1]

    assert with_newcomer == pytest.approx(without_newcomer, abs=0.01)
    assert with_newcomer == pytest.approx(120.0, abs=0.05)


def test_a_scheme_that_stopped_publishing_is_left_out(db_con):
    """Its last NAV would otherwise leave the basket mid-window and move the line."""
    _add(105, "Live Fund", EQUITY, [100.0 + i * (20.0 / (DAYS - 1)) for i in range(DAYS)])
    _add(106, "Wound Up Fund", EQUITY, [500.0] * 5)  # stops on day 5 -> inactive
    db.refresh_summary_table()
    cache.clear_all_caches()

    line = _series(db.get_macro_asset_class_trend(START, END), "Equity")
    assert line[-1] == pytest.approx(120.0, abs=0.05)


def test_the_global_plan_filter_applies(db_con):
    _add(107, "Direct Fund", EQUITY, [100.0 + i * (20.0 / (DAYS - 1)) for i in range(DAYS)])
    db.refresh_summary_table()
    cache.clear_all_caches()

    assert _series(db.get_macro_asset_class_trend(START, END, plan_type="Direct"), "Equity")[-1] == pytest.approx(120.0, abs=0.05)
    assert db.get_macro_asset_class_trend(START, END, plan_type="Regular").empty

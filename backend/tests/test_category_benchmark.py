"""The synthetic category benchmark -- the "peers" every relative figure in the app is
measured against (Holdings tiles and Performance tab, the Risk tab's beta/alpha/capture,
and the Quant page's category comparison).

It used to average "up to 50 schemes, Direct and Growth sorted first", which let IDCW
schemes into the sample whenever a category had fewer than 50 Direct-Growth funds. A
daily-IDCW liquid fund distributes its accrual every day, so its NAV never moves and it
adds an exact zero to the average: on live data that understated the liquid-fund category
by 23-28%. These tests seed exactly that situation.
"""

import datetime

import pytest

from app import quant_analytics
from app.db import queries as db
from tests.holdings_support import business_days, seed

d = datetime.date
LIQUID = "Debt Scheme - Liquid Fund"
LEGACY = "Income/Debt Oriented Schemes - Legacy Fund"


@pytest.fixture()
def days(pg_db):
    db.init_db()
    days = list(business_days(d(2025, 1, 1), d(2025, 3, 31)))
    seed(
        [(1, "Alpha Liquid Fund - Direct Plan - Growth", "Alpha MF", LIQUID, "Direct", "Growth"),
         (2, "Beta Liquid Fund - Direct Plan - Growth", "Beta MF", LIQUID, "Direct", "Growth"),
         (3, "Alpha Liquid Fund - Direct Plan - Daily IDCW", "Alpha MF", LIQUID, "Direct", "IDCW"),
         # Labelled Growth by AMFI, but the name says otherwise -- the case the reinvestment
         # ISIN exists to catch, guarded here by name as well.
         (4, "Gamma Liquid Fund - Direct Plan - Dividend", "Gamma MF", LIQUID, "Direct", "Growth"),
         (5, "Alpha Liquid Fund - Regular Plan - Growth", "Alpha MF", LIQUID, "Regular", "Growth"),
         (6, "Delta Legacy Fund - Growth", "Delta MF", LEGACY, "Regular", "Growth")],
        {1: [(x, 1000.0 * 1.0002 ** i) for i, x in enumerate(days)],
         2: [(x, 2000.0 * 1.0003 ** i) for i, x in enumerate(days)],
         3: [(x, 1000.0) for x in days],          # pays out daily: the NAV never moves
         4: [(x, 10.0) for x in days],
         5: [(x, 1000.0 * 1.0001 ** i) for i, x in enumerate(days)],
         6: [(x, 50.0 * 1.0004 ** i) for i, x in enumerate(days)]},
    )
    return days


def _returns(category, days, exclude=None):
    df = quant_analytics.get_synthetic_category_benchmark(category, days[1], days[-1], exclude_scheme_code=exclude)
    return df["daily_return"].dropna()


def test_the_peer_average_is_direct_growth_only(days):
    """Alpha (+0.02%/day) and Beta (+0.03%/day) are the category's Direct-Growth funds, so
    the peer return is +0.025%/day. The old sample also averaged in the flat IDCW scheme,
    the mislabelled dividend scheme and the Regular plan, dragging it down to ~0.012%."""
    r = _returns(LIQUID, days)
    assert len(r) > 30
    assert r.mean() == pytest.approx(0.00025, rel=2e-3)


def test_a_flat_idcw_nav_never_enters_the_sample(days):
    r = _returns(LIQUID, days)
    assert (r.abs() > 1e-6).all(), "no day may be averaged down by a zero-return distribution"


def test_the_analysed_scheme_is_left_out_of_its_own_peer_group(days):
    assert _returns(LIQUID, days, exclude=2).mean() == pytest.approx(0.0002, rel=2e-3)


def test_a_category_without_direct_plans_falls_back_to_its_growth_schemes(days):
    """Pre-2013 legacy categories have no Direct plans at all; returning nothing would
    silently drop the benchmark rather than use the closest honest sample."""
    assert _returns(LEGACY, days).mean() == pytest.approx(0.0004, rel=2e-3)

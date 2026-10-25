"""The category peer series (quant_analytics.get_synthetic_category_benchmark) against the
defects found on live data on 2026-09-25, while auditing the Backtest tab's "Category peers"
benchmark:

- segregated portfolios (credit side pockets) counted as funds: +154% for the Credit Risk
  peers over five years against +76% for the funds themselves;
- daily-payout options labelled "Growth" (NAV fixed at 1000.0000 all year) averaged in zeros;
- a closed plan's final "rebased" NAV (Quant Liquid 148511: 13.5225 -> 10.0000, then gone)
  booked as a -26% day for the liquid category;
- each peer's own NAV-to-NAV return averaged on the dates it priced, so a weekend-pricing
  peer next to weekday-only peers double-counted the weekend (FoF Overseas +103% vs +93%);
- the two AMFI labels of one SEBI category treated as two peer groups;
- ETFs (single plan) benchmarked only against the 2 schemes AMFI happened to mark Direct;
- no way to narrow a mixed category (Index Funds: Nifty + gilt) to one asset class.
"""

import datetime

import pandas as pd
import pytest

from app import quant_analytics
from app.db import queries as db
from tests.holdings_support import seed

d = datetime.date
DAYS = [d(2025, 1, 1) + datetime.timedelta(days=i) for i in range(90)]
WEEKDAYS = [x for x in DAYS if x.weekday() < 5]
G = 0.0002  # growth per calendar day of every well-behaved peer below


def _level(x: datetime.date, base: float = 1000.0) -> float:
    return base * (1.0 + G) ** (x - DAYS[0]).days


@pytest.fixture()
def peers(pg_db):
    db.init_db()
    quant_analytics.get_synthetic_category_benchmark.cache_clear()
    cr, liq, legacy_liq, etf, idx = ("Debt Scheme - Credit Risk Fund", "Debt Scheme - Liquid Fund",
                                     "Income/Debt Oriented Schemes - Liquid Fund", "Other Scheme - Other ETFs",
                                     "Other Scheme - Index Funds")
    spike_day, dead_day = DAYS[40], DAYS[30]
    seed(
        [(1, "Alpha Credit Risk Fund", "A", cr, "Direct", "Growth"),
         (2, "Alpha Credit Risk Fund - Segregated Portfolio 1", "A", cr, "Direct", "Growth"),
         (11, "Beta Liquid Fund", "B", liq, "Direct", "Growth"),                 # prices every day
         (12, "Gamma Liquid Fund", "C", liq, "Direct", "Growth"),                # weekdays only
         (13, "Delta Liquid Fund", "D", legacy_liq, "Direct", "Growth"),         # legacy label, same category
         (14, "Epsilon Liquid Fund", "E", liq, "Direct", "Growth"),              # daily payout labelled Growth
         (15, "Zeta Liquid Fund", "F", liq, "Direct", "Growth"),                 # closes with a rebased NAV
         (16, "Eta Liquid Fund", "G", liq, "Direct", "Growth"),                  # one mis-keyed NAV
         (21, "Theta Nifty 50 ETF", "H", etf, "Direct", "Growth"),
         (22, "Iota Nifty 50 ETF", "I", etf, "Regular", "Growth"),
         (31, "Kappa Nifty 50 Index Fund", "K", idx, "Direct", "Growth"),
         (32, "Lambda Gilt Index Fund", "L", idx, "Direct", "Growth")],
        {1: [(x, _level(x)) for x in WEEKDAYS],
         2: [(x, 0.1353 if x < DAYS[20] else 0.5342) for x in WEEKDAYS],
         11: [(x, _level(x)) for x in DAYS],
         12: [(x, _level(x)) for x in WEEKDAYS],
         13: [(x, _level(x)) for x in DAYS],
         14: [(x, 1000.0) for x in DAYS],
         15: [(x, _level(x, 1350.0) if x < dead_day else 1000.0) for x in DAYS if x <= dead_day],
         16: [(x, _level(x) * (0.6 if x == spike_day else 1.0)) for x in DAYS],
         21: [(x, _level(x, 200.0) * (1.001 ** i)) for i, x in enumerate(WEEKDAYS)],
         22: [(x, _level(x, 20.0) * (1.002 ** i)) for i, x in enumerate(WEEKDAYS)],
         31: [(x, 100.0 * 1.002 ** i) for i, x in enumerate(WEEKDAYS)],
         32: [(x, 100.0 * 1.0001 ** i) for i, x in enumerate(WEEKDAYS)]},
    )
    return DAYS


def _total(category, lo, hi, **kw):
    df = quant_analytics.get_synthetic_category_benchmark(category, lo, hi, **kw)
    s = df.set_index("nav_date")["nav"]
    return s.iloc[-1] / 100.0 - 1.0, df


def test_segregated_portfolio_is_not_a_peer(peers):
    total, df = _total("Debt Scheme - Credit Risk Fund", DAYS[10], DAYS[-1])
    expected = _level(WEEKDAYS[-1]) / _level(max(x for x in WEEKDAYS if x < DAYS[10])) - 1.0
    assert total == pytest.approx(expected, rel=1e-3)
    assert df["daily_return"].max() < 0.01  # the side pocket's +295% day is gone


def test_liquid_peer_index_grows_at_the_peers_calendar_rate(peers):
    """Every live liquid peer grows exactly G per calendar day, whatever days it prices on.
    The old per-fund-date average booked ~one extra day of accrual every weekend (the
    weekday-only peer's Monday return is 3 days, averaged with the daily peers' 1 day), took
    the payout option's zeros, the closed plan's -26% "rebase" and the mis-keyed NAV."""
    lo, hi = DAYS[6], DAYS[-1]  # a Tuesday: every peer's first in-window return spans one day
    total, df = _total("Debt Scheme - Liquid Fund", lo, hi)
    # The index's first level already carries lo's own one-day return.
    expected = (1.0 + G) ** ((hi - lo).days + 1) - 1.0
    assert total == pytest.approx(expected, rel=2e-3)
    assert df["daily_return"].min() > 0  # no zero, no -26%, no -40% spike


def test_both_amfi_labels_of_one_sebi_category_are_one_peer_group(peers):
    a, _ = _total("Debt Scheme - Liquid Fund", DAYS[5], DAYS[-1])
    b, _ = _total("Income/Debt Oriented Schemes - Liquid Fund", DAYS[5], DAYS[-1])
    assert a == pytest.approx(b, rel=1e-12)


def test_every_etf_is_a_peer_whatever_its_plan_label(peers):
    """The "Direct" ETF rises 0.1% a trading day (on top of G), the "Regular" one 0.2%. An
    ETF has one plan; the old rule kept only the one AMFI marked Direct."""
    lo = DAYS[6]
    total, df = _total("Other Scheme - Other ETFs", lo, DAYS[-1])
    daily = df.set_index("nav_date")["daily_return"]
    tuesday_after = daily[daily.index > pd.Timestamp(lo)].index[0]
    assert daily[tuesday_after] == pytest.approx(((1 + G) * 1.001 - 1 + (1 + G) * 1.002 - 1) / 2, rel=1e-2)


def test_asset_class_narrows_a_mixed_category(peers):
    eq, _ = _total("Other Scheme - Index Funds", DAYS[5], DAYS[-1], asset_class="Equity")
    mixed, _ = _total("Other Scheme - Index Funds", DAYS[5], DAYS[-1])
    debt, _ = _total("Other Scheme - Index Funds", DAYS[5], DAYS[-1], asset_class="Debt")
    n = sum(1 for x in WEEKDAYS if DAYS[5] <= x)
    assert eq == pytest.approx(1.002 ** n - 1.0, rel=2e-3)
    assert debt == pytest.approx(1.0001 ** n - 1.0, rel=2e-2)
    assert debt < mixed < eq

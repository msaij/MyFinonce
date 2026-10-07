""""If sold now": what selling a holding at its latest NAV would give -- STT only on
equity-oriented funds, no estimate for funds sold on the exchange."""

import datetime
import functools

import pytest
from fastapi.testclient import TestClient

from app.classification import is_equity_oriented, is_exchange_traded
from app.main import app
from tests.holdings_support import add_ok, business_days, new_portfolio, seed

d = datetime.date


@pytest.mark.parametrize("category,broad,name,expected", [
    ("Equity Scheme - Flexi Cap Fund", "Equity", "Alpha Flexi Cap Fund", True),
    ("Equity Scheme - ELSS", "Equity", "Alpha Tax Saver Fund", True),
    ("Hybrid Scheme - Arbitrage Fund", "Hybrid", "Alpha Arbitrage Fund", True),
    ("Hybrid Scheme - Aggressive Hybrid Fund", "Hybrid", "Alpha Hybrid Equity Fund", True),
    ("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", "Hybrid", "Alpha BAF", True),
    ("Hybrid Scheme - Equity Savings", "Hybrid", "Alpha Equity Savings Fund", True),
    ("Hybrid Scheme - Balanced Hybrid Fund", "Hybrid", "Alpha Balanced Fund", False),
    ("Hybrid Scheme - Conservative Hybrid Fund", "Hybrid", "Alpha Conservative Fund", False),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "UTI Nifty 50 Index Fund", True),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "Motilal Oswal S&P 500 Index Fund", False),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "Bharat Bond Index Fund April 2030", False),
    ("Debt Scheme - Liquid Fund", "Debt", "Alpha Liquid Fund", False),
    ("Other Scheme - FoF Domestic", "Other / Index / ETF", "Nippon India ETF Gold FoF", False),
])
def test_stt_applies_to_equity_oriented_funds_only(category, broad, name, expected):
    assert is_equity_oriented(category, broad, name) is expected


@pytest.mark.parametrize("category,name,expected", [
    ("Other Scheme - Other ETFs", "Nippon India ETF Nifty BeES", True),
    ("Other Scheme - Gold ETF", "SBI Gold ETF", True),
    ("Income/Debt Oriented Schemes - Fixed Term Plan", "Alpha FTP Series 9", True),
    ("Income", "HDFC FMP 1124D March 2023", True),
    ("Debt Scheme - Credit Risk Fund", "Alpha Interval Fund Quarterly Plan", True),
    # An ETF fund of fund is an ordinary fund redeemed at its NAV.
    ("Other Scheme - FoF Domestic", "Nippon India ETF Gold FoF", False),
    ("Debt Scheme - Liquid Fund", "Alpha Liquid Fund", False),
])
def test_exchange_traded_schemes_are_recognised(category, name, expected):
    assert is_exchange_traded(category, name) is expected


@pytest.fixture()
def client(pg_db):
    days = list(business_days(d(2024, 1, 1), d(2024, 12, 31)))
    seed([(5001, "Alpha Liquid Fund", "Alpha MF", "Debt Scheme - Liquid Fund", "Direct", "Growth"),
          (5002, "Alpha Arbitrage Fund", "Alpha MF", "Hybrid Scheme - Arbitrage Fund", "Direct", "Growth"),
          (5003, "Alpha Nifty ETF", "Alpha MF", "Other Scheme - Other ETFs", "Direct", "Growth")],
         {5001: [(day, 1000.0 + 0.2 * i) for i, day in enumerate(days)],
          5002: [(day, 10.0 + 0.001 * i) for i, day in enumerate(days)],
          5003: [(day, 200.0 + 0.1 * i) for i, day in enumerate(days)]})
    return TestClient(app)


def test_selling_now_deducts_stt_only_where_it_applies_and_counts_stamp_duty(client):
    pid = new_portfolio(client)
    buy = functools.partial(add_ok, client, portfolio_id=pid, txn_type="BUY", trade_date="2024-06-03")
    buy(scheme_code=5001, amount=100000)
    buy(scheme_code=5002, amount=50000)
    buy(scheme_code=5003, amount=20000)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    pos = {p["scheme_code"]: p for p in body["positions"]}

    liquid, arb, etf = pos[5001], pos[5002], pos[5003]
    assert liquid["sell_now_stt"] == 0 and liquid["sell_now_value"] == pytest.approx(liquid["current_value"])
    assert arb["sell_now_stt"] == pytest.approx(arb["current_value"] * 0.00001)
    assert arb["sell_now_value"] == pytest.approx(arb["current_value"] - arb["sell_now_stt"])
    # Profit is against the cash paid, stamp duty included -- not against the allotted value.
    assert liquid["stamp_duty"] > 0
    assert liquid["sell_now_profit"] == pytest.approx(liquid["current_value"] - 100000)
    assert arb["sell_now_profit"] == pytest.approx(arb["sell_now_value"] - 50000)
    # The ETF is sold on the exchange: no NAV-based estimate, and left out of the totals.
    assert etf["sell_now_value"] is None and etf["sell_now_note"] == "exchange_traded"

    k = body["kpis"]
    assert k["sell_now_excluded"] == 1
    assert k["sell_now_value"] == pytest.approx(liquid["sell_now_value"] + arb["sell_now_value"])
    assert k["sell_now_stt"] == pytest.approx(arb["sell_now_stt"])
    assert k["sell_now_profit"] == pytest.approx(liquid["sell_now_profit"] + arb["sell_now_profit"])

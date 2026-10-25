"""Tests for resolving a scheme's plan from AMFI's published Direct/Regular NAV pair,
and for the single refresh that chains NAV -> resolution -> TER -> summary rebuild.

Why this exists: AMFI's NAV file leaves Plan blank for ~40% of rows. The fund-performance
feed publishes each fund's two plan NAVs on a stated date, so a scheme whose NAV equals the
Direct figure IS the Direct plan. Everything here guards the edges of that identification.
"""

import datetime

import pytest

from app import amfi_sync
from app.db import connection
from app.db import queries as db

NAV_DATE = datetime.date(2026, 9, 21)
FEED_DATE = "21-Sep-2026"


@pytest.fixture()
def seeded(pg_db):
    db.init_db()
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES "
        # The real shape of the bug: both plans of one fund, both stored as 'Regular'.
        "(145834, 'Motilal Oswal Liquid Fund', 'Motilal Oswal Mutual Fund', 'Debt Scheme - Liquid Fund', 'Regular', NULL),"
        "(145946, 'Motilal Oswal Liquid Fund', 'Motilal Oswal Mutual Fund', 'Debt Scheme - Liquid Fund', 'Regular', NULL),"
        # A fund whose Growth and IDCW schemes still share a NAV (no payout yet).
        "(200001, 'Twin Option Fund', 'Test AMC', 'Debt Scheme - Liquid Fund', NULL, NULL),"
        "(200002, 'Twin Option Fund', 'Test AMC', 'Debt Scheme - Liquid Fund', NULL, 'IDCW')"
    )
    con.executemany(
        "INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
        [(145834, NAV_DATE, 14.9732), (145946, NAV_DATE, 14.7909),
         (200001, NAV_DATE, 10.5), (200002, NAV_DATE, 10.5)],
    )
    con.close()
    yield


def _feed(monkeypatch, rows):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def fetch_all(self, report_date=None):
            return iter(rows)

        def fetch_latest_full(self, *a, **k):
            return FEED_DATE, list(rows)

    monkeypatch.setattr("app.amfi_perf_client.AmfiPerfClient", FakeClient)


MOTILAL = {"schemeName": "Motilal Oswal Liquid Fund", "navDate": FEED_DATE,
           "navDirect": "14.9732", "navRegular": "14.7909"}
TWINS = {"schemeName": "Twin Option Fund", "navDate": FEED_DATE,
         "navDirect": "10.5", "navRegular": "10.1"}


def _plans():
    con = connection.get_connection()
    try:
        return {c: (p, o) for c, p, o in con.execute(
            "SELECT scheme_code, plan_type, option_type FROM schemes ORDER BY scheme_code").fetchall()}
    finally:
        con.close()


class TestPlanFromNavs:
    def test_a_nav_equal_to_the_direct_figure_identifies_the_direct_plan(self):
        assert amfi_sync._plan_from_navs(14.9732, 14.9732, 14.7909) == "Direct"
        assert amfi_sync._plan_from_navs(14.7909, 14.9732, 14.7909) == "Regular"

    def test_a_nav_matching_neither_identifies_nothing(self):
        assert amfi_sync._plan_from_navs(11.11, 14.9732, 14.7909) is None

    def test_two_plans_priced_the_same_prove_nothing(self):
        """Right after launch both plans can report the same NAV; picking either would be
        a coin toss dressed as a fact."""
        assert amfi_sync._plan_from_navs(10.0, 10.0, 10.0) is None

    def test_a_fund_offering_only_one_plan_is_still_identifiable(self):
        assert amfi_sync._plan_from_navs(10.0, 10.0, None) == "Direct"
        assert amfi_sync._plan_from_navs(10.0, None, 10.0) == "Regular"


def test_the_direct_plan_stops_being_labelled_regular(seeded, monkeypatch):
    _feed(monkeypatch, [MOTILAL])
    result = amfi_sync.resolve_plan_options()

    assert result["plan_changed"] == 1
    assert _plans()[145834] == ("Direct", "Growth"), "its NAV is the fund's published Direct NAV"
    assert _plans()[145946][0] == "Regular", "already correct, left alone"


def test_resolution_stamps_nav_match_on_what_it_writes_and_nothing_else(seeded, monkeypatch):
    _feed(monkeypatch, [MOTILAL])
    con = connection.get_connection()
    try:
        con.execute("UPDATE schemes SET plan_source = 'amfi' WHERE scheme_code = 145946")
    finally:
        con.close()

    amfi_sync.resolve_plan_options()

    con = connection.get_connection()
    try:
        src = {c: (p, o) for c, p, o in con.execute(
            "SELECT scheme_code, plan_source, option_source FROM schemes").fetchall()}
    finally:
        con.close()
    assert src[145834] == ("nav_match", "nav_match"), "both fields were written by the match"
    # Its plan was already right and is not rewritten, so that source stays; its blank
    # option IS filled by the match, so that one is stamped.
    assert src[145946] == ("amfi", "nav_match")


def _riskometers():
    con = connection.get_connection()
    try:
        return {c: (lvl, d) for c, lvl, d in con.execute(
            "SELECT scheme_code, riskometer, riskometer_as_of FROM schemes").fetchall()}
    finally:
        con.close()


def test_the_riskometer_is_stamped_on_every_scheme_of_the_fund(seeded, monkeypatch):
    """SEBI classifies the portfolio, which every plan and option of a fund shares -- so
    the label lands on both Motilal schemes, not just the one whose plan the NAVs settle."""
    _feed(monkeypatch, [{**MOTILAL, "riskometerScheme": "Low to Moderate"}])
    result = amfi_sync.resolve_plan_options()

    got = _riskometers()
    assert got[145834] == ("Low to Moderate", NAV_DATE)
    assert got[145946] == ("Low to Moderate", NAV_DATE)
    assert got[200001] == (None, None), "not in this feed, so untouched"
    assert result["riskometer_found"] == 2 and result["riskometer_changed"] == 2


def test_an_unchanged_riskometer_is_not_rewritten(seeded, monkeypatch):
    _feed(monkeypatch, [{**MOTILAL, "riskometerScheme": "Moderate"}])
    amfi_sync.resolve_plan_options()
    assert amfi_sync.resolve_plan_options()["riskometer_changed"] == 0


def test_a_dry_run_writes_no_riskometer(seeded, monkeypatch):
    _feed(monkeypatch, [{**MOTILAL, "riskometerScheme": "Moderate"}])
    assert amfi_sync.resolve_plan_options(dry_run=True)["riskometer_found"] == 2
    assert _riskometers()[145834] == (None, None)


def test_a_dry_run_reports_the_same_changes_and_writes_none(seeded, monkeypatch):
    _feed(monkeypatch, [MOTILAL])
    before = _plans()
    result = amfi_sync.resolve_plan_options(dry_run=True)

    assert result["plan_changed"] == 1 and result["dry_run"] is True
    assert _plans() == before


def test_an_option_is_filled_only_when_one_scheme_carries_that_nav(seeded, monkeypatch):
    """Both twins sit on the Direct NAV, so both are Direct -- but which of them is the
    Growth option cannot be told apart, and the stored IDCW must not be overwritten."""
    _feed(monkeypatch, [TWINS])
    amfi_sync.resolve_plan_options()

    plans = _plans()
    assert plans[200001][0] == "Direct" and plans[200002][0] == "Direct"
    assert plans[200001][1] is None, "ambiguous: not claimed as Growth"
    assert plans[200002][1] == "IDCW", "AMFI said IDCW; a NAV coincidence must not overrule it"


def test_an_empty_feed_changes_nothing_and_says_so(seeded, monkeypatch):
    _feed(monkeypatch, [])
    before = _plans()
    result = amfi_sync.resolve_plan_options()

    assert result["ok"] is False and result["plan_changed"] == 0
    assert _plans() == before


def test_full_refresh_runs_nav_then_resolution_then_ter_then_one_rebuild(seeded, monkeypatch):
    """The order is a dependency chain, and the rebuild happens once at the end -- the TER
    sync never rebuilt the summary table itself, so a new expense ratio used to stay
    invisible to every page until some later NAV sync happened to do it."""
    calls = []
    monkeypatch.setattr(amfi_sync, "sync_daily_nav",
                        lambda _trigger="manual", refresh_summary=True: (calls.append(f"nav(refresh={refresh_summary})"), (True, "5 NAVs"))[1])
    monkeypatch.setattr(amfi_sync, "restate_recent_navs",
                        lambda **kw: (calls.append("restate"), {"ok": True, "checked": 7, "restated": 1})[1])
    monkeypatch.setattr(db, "normalize_nav_splits", lambda: (calls.append("splits"), 0)[1])
    monkeypatch.setattr(amfi_sync, "resolve_plan_options",
                        lambda: (calls.append("resolve"), {"ok": True, "resolved": 3, "plan_changed": 1, "option_changed": 0})[1])
    monkeypatch.setattr(amfi_sync, "sync_official_ter",
                        lambda _trigger="manual": (calls.append("ter"), (True, "TER ok"))[1])
    monkeypatch.setattr(db, "refresh_summary_table", lambda: calls.append("rebuild"))

    result = amfi_sync.sync_all(_trigger="test")

    # The restatement must sit between the NAV write and the split normaliser: after it
    # so today's row is there to compare, before it so filled-in days are normalised too.
    assert calls == ["nav(refresh=False)", "restate", "splits", "resolve", "ter", "rebuild"]
    assert result["ok"] is True and result["ter"]["ok"] is True
    assert result["restatement"]["checked"] == 7 and result["restatement"]["restated"] == 1


def test_a_restatement_failure_does_not_fail_a_good_nav_sync(seeded, monkeypatch):
    monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual", refresh_summary=True: (True, "5 NAVs"))

    def boom(**kw):
        raise RuntimeError("AMFI history endpoint down")

    monkeypatch.setattr(amfi_sync, "restate_recent_navs", boom)
    monkeypatch.setattr(amfi_sync, "resolve_plan_options", lambda: {"ok": True, "resolved": 0, "plan_changed": 0, "option_changed": 0})
    monkeypatch.setattr(amfi_sync, "sync_official_ter", lambda _trigger="manual": (True, "TER ok"))
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    result = amfi_sync.sync_all(_trigger="test")

    assert result["ok"] is True and result["nav"]["ok"] is True
    assert result["restatement"]["ok"] is False and "down" in result["restatement"]["reason"]
    assert result["splits"]["ok"] is True, "the chain carries on past a failed restatement"


def test_a_ter_outage_does_not_fail_a_good_nav_sync(seeded, monkeypatch):
    """AMFI's TER portal went down for hours on 2026-09-23 while NAVs kept flowing. One
    merged verdict would have reported the whole refresh as failed."""
    monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual", refresh_summary=True: (True, "5 NAVs"))
    monkeypatch.setattr(amfi_sync, "restate_recent_navs", lambda **kw: {"ok": True})
    monkeypatch.setattr(amfi_sync, "resolve_plan_options", lambda: {"ok": True, "resolved": 0, "plan_changed": 0, "option_changed": 0})
    monkeypatch.setattr(amfi_sync, "sync_official_ter", lambda _trigger="manual": (False, "portal returned no usable data"))
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    result = amfi_sync.sync_all(_trigger="test")

    assert result["ok"] is True and result["nav"]["ok"] is True
    assert result["ter"]["ok"] is False and "portal" in result["ter"]["message"]


def test_resolution_is_skipped_when_the_nav_sync_failed(seeded, monkeypatch):
    monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual", refresh_summary=True: (False, "AMFI unreachable"))
    monkeypatch.setattr(amfi_sync, "resolve_plan_options", lambda: pytest.fail("must not run without fresh NAVs"))
    monkeypatch.setattr(amfi_sync, "restate_recent_navs", lambda **kw: pytest.fail("must not run without fresh NAVs"))
    monkeypatch.setattr(amfi_sync, "sync_official_ter", lambda _trigger="manual": (True, "TER ok"))
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    result = amfi_sync.sync_all(_trigger="test")
    assert result["ok"] is False and result["resolve"]["ok"] is False


def test_a_later_nav_sync_does_not_blank_a_resolved_plan(seeded, monkeypatch):
    """AMFI keeps sending those rows with the Plan column empty. If an empty value
    overwrote what resolution worked out, every NAV sync would undo the previous one."""
    _feed(monkeypatch, [MOTILAL])
    amfi_sync.resolve_plan_options()
    assert _plans()[145834] == ("Direct", "Growth")

    # Exactly what the parser now yields for an unlabelled row.
    amfi_sync._merge_amfi_payload(
        {145834: {"scheme_code": 145834, "scheme_name": "Motilal Oswal Liquid Fund",
                  "fund_house": "Motilal Oswal Mutual Fund", "category": "Debt Scheme - Liquid Fund",
                  "plan_type": None, "option_type": None, "isin": None}},
        [(145834, datetime.date(2026, 9, 22), 14.9763)],
        label="test",
    )
    assert _plans()[145834] == ("Direct", "Growth")


def test_a_plan_amfi_does_state_still_wins(seeded, monkeypatch):
    amfi_sync._merge_amfi_payload(
        {145834: {"scheme_code": 145834, "scheme_name": "Motilal Oswal Liquid Fund",
                  "fund_house": "Motilal Oswal Mutual Fund", "category": "Debt Scheme - Liquid Fund",
                  "plan_type": "Regular", "option_type": "IDCW", "isin": None}},
        [(145834, datetime.date(2026, 9, 22), 14.9763)],
        label="test",
    )
    assert _plans()[145834] == ("Regular", "IDCW")


def test_verify_reports_what_is_actually_held(seeded, monkeypatch):
    db.refresh_summary_table()
    report = amfi_sync.verify_sync()

    assert report["nav"]["rows"] == 4 and report["nav"]["schemes"] == 4
    assert report["schemes"]["total"] == 4
    assert isinstance(report["findings"], list)


def _snapshot():
    con = connection.get_connection()
    try:
        cols = [d for d in ("fund_name", "fund_house", "scheme_codes", "aum_cr", "benchmark",
                            "return_1y_direct", "return_1y_benchmark", "as_of")]
        return {r[0]: dict(zip(cols, r)) for r in con.execute(
            f"SELECT {', '.join(cols)} FROM amfi_fund_snapshot ORDER BY fund_name").fetchall()}
    finally:
        con.close()


def test_the_feeds_assets_and_benchmark_are_kept_once_per_fund(seeded, monkeypatch):
    """AUM is reported per fund, so it is stored once -- summing it over a fund's Direct and
    Regular schemes would count the same money twice."""
    _feed(monkeypatch, [{**MOTILAL, "_sub_category": "Liquid", "_category_id": 2, "dailyAUM": 1234.5,
                         "benchmark": "NIFTY Liquid Index A-I", "return1YearDirect": 6.91,
                         "return1YearBenchmark": "6.72"}])
    result = amfi_sync.resolve_plan_options()
    assert result["snapshot_written"] == 1 and result["snapshot_with_house"] == 1

    row = _snapshot()["Motilal Oswal Liquid Fund"]
    assert row["fund_house"] == "Motilal Oswal Mutual Fund"
    assert sorted(row["scheme_codes"]) == [145834, 145946]
    assert row["aum_cr"] == pytest.approx(1234.5) and row["benchmark"] == "NIFTY Liquid Index A-I"
    assert (row["return_1y_direct"], row["return_1y_benchmark"]) == pytest.approx((6.91, 6.72))
    assert row["as_of"] == NAV_DATE


def test_each_fetch_replaces_the_snapshot_and_a_dry_run_writes_none(seeded, monkeypatch):
    _feed(monkeypatch, [{**MOTILAL, "_sub_category": "Liquid", "dailyAUM": 100}, {**TWINS, "_sub_category": "Liquid"}])
    amfi_sync.resolve_plan_options()
    assert set(_snapshot()) == {"Motilal Oswal Liquid Fund", "Twin Option Fund"}

    _feed(monkeypatch, [{**MOTILAL, "_sub_category": "Liquid", "dailyAUM": 250}])
    assert amfi_sync.resolve_plan_options(dry_run=True)["snapshot_written"] == 0
    assert set(_snapshot()) == {"Motilal Oswal Liquid Fund", "Twin Option Fund"}, "dry run leaves it alone"

    amfi_sync.resolve_plan_options()
    snap = _snapshot()
    assert set(snap) == {"Motilal Oswal Liquid Fund"}, "a fund gone from the feed is gone from the snapshot"
    assert snap["Motilal Oswal Liquid Fund"]["aum_cr"] == pytest.approx(250)


def test_a_fund_the_feed_names_differently_is_matched_by_its_published_navs(seeded, monkeypatch):
    """103 feed funds matched no scheme by name ("ICICI Prudential Large Cap Fund" vs our
    "... Bluechip Fund"), so their AUM, riskometer and benchmark were lost. The feed's own
    Direct and Regular NAVs identify the fund within its fund house."""
    renamed = {**MOTILAL, "schemeName": "Motilal Oswal Liquid Plan (erstwhile Liquid Fund)",
               "_sub_category": "Liquid", "dailyAUM": 500, "riskometerScheme": "Low to Moderate"}
    _feed(monkeypatch, [renamed])
    result = amfi_sync.resolve_plan_options()
    assert result["funds_not_matched"] == 0
    row = _snapshot()["Motilal Oswal Liquid Plan (erstwhile Liquid Fund)"]
    assert sorted(row["scheme_codes"]) == [145834, 145946]
    assert _plans()[145834][0] == "Direct"


def test_an_nfo_price_or_a_shared_nav_identifies_nothing():
    d = NAV_DATE
    idx = {(d, "A MF", 10.0): {"fund one"}, (d, "A MF", 12.3456): {"fund two", "fund three"}, (d, "A MF", 15.5): {"fund four"}}
    assert amfi_sync._fund_name_by_nav(idx, d, "A MF", 10.0, None) is None, "new funds all start at Rs 10"
    assert amfi_sync._fund_name_by_nav(idx, d, "A MF", 12.3456, None) is None, "two of our funds share this NAV"
    assert amfi_sync._fund_name_by_nav(idx, d, "A MF", 15.5, None) == "fund four"
    assert amfi_sync._fund_name_by_nav(idx, d, "B MF", 15.5, None) is None, "only within the brand's own house"


def test_a_renamed_fund_is_placed_with_its_amc_by_brand():
    """The feed calls it "ICICI Prudential Large Cap Fund"; ours still says "Bluechip".
    The brand prefix every Indian scheme name carries still places it, longest brand first."""
    brands = amfi_sync._brand_prefixes(["ICICI Prudential Mutual Fund", "Aditya Birla Sun Life Mutual Fund",
                                        "Aditya Birla Mutual Fund", "Quant Mutual Fund"])
    assert amfi_sync._house_by_brand("ICICI Prudential Large Cap Fund", brands) == "ICICI Prudential Mutual Fund"
    assert amfi_sync._house_by_brand("Aditya Birla Sun Life Liquid Fund", brands) == "Aditya Birla Sun Life Mutual Fund"
    assert amfi_sync._house_by_brand("Quantum Liquid Fund", brands) is None, "a brand must end at a word boundary"
    # A brand's first word places a fund only when no other AMC shares it.
    brands = amfi_sync._brand_prefixes(["Kotak Mahindra Mutual Fund", "Aditya Birla Sun Life Mutual Fund",
                                        "Aditya Capital Mutual Fund"])
    assert amfi_sync._house_by_brand("Kotak Gold ETF Fund", brands) == "Kotak Mahindra Mutual Fund"
    assert amfi_sync._house_by_brand("Aditya Something Fund", brands) is None

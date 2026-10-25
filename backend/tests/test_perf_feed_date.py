"""The fund-performance feed is read for the newest FULL trading day, not its own latest
report date: over a weekend or holiday that date lists ~100 funds, and the fund snapshot
built from it is replaced whole."""

from app.amfi_perf_client import AmfiPerfClient


class FakeFeed(AmfiPerfClient):
    def __init__(self, report_date, sizes):
        self._report_date = report_date
        self.sizes = sizes  # report date label -> number of funds it lists
        self.fetched = []

    def report_date(self):
        return self._report_date

    def fetch_all(self, report_date=None):
        self.fetched.append(report_date)
        return iter([{"schemeName": f"Fund {i}"} for i in range(self.sizes.get(report_date, 0))])


def test_a_full_trading_day_is_used_as_it_is():
    feed = FakeFeed("25-Sep-2026", {"25-Sep-2026": 2025})
    label, rows = feed.fetch_latest_full()
    assert (label, len(rows), feed.fetched) == ("25-Sep-2026", 2025, ["25-Sep-2026"])


def test_a_weekend_report_date_steps_back_to_friday_without_fetching_the_weekend():
    # The real case of 28 Sep 2026: the feed reported Sunday the 27th.
    feed = FakeFeed("27-Sep-2026", {"27-Sep-2026": 98, "26-Sep-2026": 82, "25-Sep-2026": 2025})
    label, rows = feed.fetch_latest_full()
    assert (label, len(rows)) == ("25-Sep-2026", 2025)
    assert feed.fetched == ["25-Sep-2026"]


def test_a_weekday_holiday_is_recognised_by_its_row_count():
    # Friday 2 Oct 2026, Gandhi Jayanti: a weekday that lists only the daily-NAV funds.
    feed = FakeFeed("02-Oct-2026", {"02-Oct-2026": 90, "01-Oct-2026": 2070})
    label, rows = feed.fetch_latest_full()
    assert (label, len(rows)) == ("01-Oct-2026", 2070)
    assert feed.fetched == ["02-Oct-2026", "01-Oct-2026"]


def test_no_full_day_in_the_window_returns_nothing_rather_than_a_sample():
    feed = FakeFeed("25-Sep-2026", {"25-Sep-2026": 90})
    assert feed.fetch_latest_full(max_days_back=3) == (None, [])


def test_an_unreadable_report_date_returns_nothing():
    assert FakeFeed(None, {}).fetch_latest_full() == (None, [])

"""Tests for AmfiClient.parse_amfi_nav_lines' header/column handling.

Focused on the two-ISIN-column layout AMFI actually publishes, which the parser
mishandled until 2026-09-12. The feed carries both "ISIN Div Payout/ISIN Growth"
and "ISIN Div Reinvestment", and a given row populates exactly one of them
depending on its option type. The parser recorded only the first ISIN column it
saw, so every IDCW-Reinvestment scheme in every file silently lost its ISIN --
silently because the row still parsed, still produced a NAV, and still upserted
fine, just with isin = NULL.
"""

import datetime

import pytest

from app.amfi_client import AmfiClient


# The real NAVAll.txt column order: the two ISIN columns sit between the scheme
# code and the scheme name. Row 1 is a Growth plan (first ISIN populated, second
# "-"); row 2 is an IDCW Reinvestment plan (first "-", second populated); row 3
# has neither.
TWO_ISIN_COLUMNS = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Repurchase Price;Sale Price;Date
111111;INF000001;-;Test Fund - Direct Plan - Growth;125.4321;0;0;01-Feb-2025
222222;-;INF000002;Test Fund - Direct Plan - IDCW Reinvestment;120.1234;0;0;01-Feb-2025
333333;-;-;Test Fund - Regular Plan - Growth;118.0000;0;0;01-Feb-2025
"""


def _parse(text, **kwargs):
    return list(AmfiClient().parse_amfi_nav_lines(text, **kwargs))


def _isin_by_code(text):
    return {meta["scheme_code"]: meta["isin"] for meta, _ in _parse(text)}


def test_isin_is_read_from_the_first_column_when_populated():
    assert _isin_by_code(TWO_ISIN_COLUMNS)[111111] == "INF000001"


def test_isin_is_read_from_the_second_column_for_reinvestment_rows():
    """The actual bug: this row's ISIN lives in the *second* ISIN column, and the
    parser only ever looked at the first."""
    assert _isin_by_code(TWO_ISIN_COLUMNS)[222222] == "INF000002"


def test_isin_is_none_when_no_column_has_one():
    assert _isin_by_code(TWO_ISIN_COLUMNS)[333333] is None


@pytest.mark.parametrize("placeholder", ["-", "", "None", "null"])
def test_isin_placeholders_are_normalized_to_none(placeholder):
    text = TWO_ISIN_COLUMNS.replace("111111;INF000001;-;", f"111111;{placeholder};{placeholder};")
    assert _isin_by_code(text)[111111] is None


def test_placeholder_in_the_first_column_does_not_mask_a_real_second_isin():
    """Guards the specific ordering mistake of taking the first column's value
    unconditionally: a "-" there must not win over a real ISIN beside it."""
    text = TWO_ISIN_COLUMNS.replace("222222;-;INF000002;", "222222;None;INF000002;")
    assert _isin_by_code(text)[222222] == "INF000002"


def test_the_rest_of_the_row_still_parses_correctly():
    """The ISIN change moves column indices around, so pin the neighbouring fields
    against an off-by-one."""
    records = _parse(TWO_ISIN_COLUMNS)
    assert len(records) == 3
    meta, nav = records[0]
    assert meta["scheme_code"] == 111111
    assert meta["scheme_name"] == "Test Fund - Direct Plan - Growth"
    assert meta["fund_house"] == "Test Mutual Fund"
    assert meta["category"] == "Equity Scheme - Large Cap Fund"
    assert meta["plan_type"] == "Direct"
    assert nav["nav"] == pytest.approx(125.4321)
    assert nav["nav_date"] == datetime.date(2025, 2, 1)


def test_an_amc_line_without_the_word_fund_leaves_fund_house_blank():
    """Documents where the blank fund_house the merge has to defend against
    actually comes from: AMC lines are recognized by containing "fund", so an AMC
    styled "... Asset Management Company Ltd" is not recognized and every scheme
    under it parses with fund_house "". amfi_sync.SCHEME_UPDATE must therefore
    never let that blank overwrite a stored value -- see
    test_amfi_sync_merge.test_merge_does_not_erase_fund_house_or_category_with_a_blank."""
    text = TWO_ISIN_COLUMNS.replace("Test Mutual Fund", "Test Asset Management Company Ltd")
    metas = {meta["scheme_code"]: meta for meta, _ in _parse(text)}
    assert metas[111111]["fund_house"] == ""


def test_default_amc_fills_in_when_the_feed_names_no_amc():
    """The single-AMC 90-day report has no AMC banner at all, so the caller passes
    the name it already knows."""
    text = TWO_ISIN_COLUMNS.replace("Test Mutual Fund", "Test Asset Management Company Ltd")
    metas = {meta["scheme_code"]: meta
             for meta, _ in _parse(text, default_amc="Known AMC")}
    assert metas[111111]["fund_house"] == "Known AMC"


def test_a_single_isin_column_layout_still_works():
    """Some historical reports publish only one ISIN column; the list-of-indices
    handling must not require two."""
    text = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

Scheme Code;Scheme Name;ISIN;Net Asset Value;Date
111111;Test Fund - Direct Plan - Growth;INF000009;125.4321;01-Feb-2025
"""
    assert _isin_by_code(text)[111111] == "INF000009"

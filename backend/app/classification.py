"""What a scheme IS: its SEBI category without AMFI's labelling noise, and the asset class
its money is actually in.

One module so the Holdings page and the market-wide Overview classify a fund the same way.
Before this lived in the Holdings services, the Overview grouped on the raw AMFI columns
instead, and the two pages disagreed about the very same fund.
"""

from __future__ import annotations

import re
from typing import Optional

# --- SEBI category ------------------------------------------------------------------
#
# AMFI's NAV file still lists some live schemes under the pre-2018 section headers, so one
# SEBI category arrives under two labels: Mirae Asset Liquid Fund is "Income/Debt Oriented
# Schemes - Liquid Fund" while Axis Liquid Fund is "Debt Scheme - Liquid Fund". Grouping on
# the raw label split the same category into two unrelated-looking buckets, and gave each
# half its own "peer median". Only the section prefix is dropped, plus the few legacy labels
# that name the very same category; a legacy label that SEBI's 2017 recategorisation
# genuinely redefined ("Short Term Fund") is kept as it is rather than guessed onto a
# modern category.
_CATEGORY_PREFIX = re.compile(
    r"^(?:equity scheme|debt scheme|hybrid scheme|other scheme|solution oriented scheme|"
    r"income/debt oriented schemes)\s*-\s*", re.I)
_CATEGORY_ALIASES = {
    "banking and psu debt fund": "Banking and PSU Fund",
    "liquid": "Liquid Fund",
    "money market": "Money Market Fund",
    "gilt": "Gilt Fund",
    "gold etfs": "Gold ETF",
    "fund of funds - overseas": "FoF Overseas",
    "fund of funds - domestic": "FoF Domestic",
}


def sebi_category(category: Optional[str]) -> Optional[str]:
    """The scheme's SEBI category without AMFI's section prefix, legacy labels folded in."""
    if not isinstance(category, str) or not category.strip():
        return None
    short = _CATEGORY_PREFIX.sub("", category.strip())
    return _CATEGORY_ALIASES.get(short.lower(), short)


# --- Asset classes -------------------------------------------------------------------
#
# A *total* classification: every scheme lands in exactly one class.
# summary_table.broad_category alone is not enough: its "Other / Index / ETF" bucket mixes
# Nifty index funds, gilt ETFs, gold ETFs and Nasdaq FoFs. Hybrid funds stay "Hybrid" --
# splitting them into their equity and debt legs needs monthly portfolio disclosures this
# app does not ingest.

ASSET_CLASSES = ["Equity", "International Equity", "Hybrid", "Debt", "Cash & Liquid", "Gold & Commodities", "Other"]


def broad_category_of(category: Optional[str]) -> str:
    """summary_table.broad_category, derived from the AMFI category label with the same rules
    as the summary build (db/queries.py), for callers that only have `schemes.category`."""
    cat = (category or "").strip()
    low = cat.lower()
    if low.startswith("equity scheme"):
        return "Equity"
    if low.startswith("debt scheme") or low.startswith("income/debt oriented schemes") or cat == "Income":
        return "Debt"
    if low.startswith("hybrid scheme"):
        return "Hybrid"
    if low.startswith("solution oriented"):
        return "Solution Oriented"
    return "Other / Index / ETF"

_INTL = re.compile(r"nasdaq|s&p ?500|sp ?500|nyse|fang|global|international|overseas|world|hang seng|"
                   r"us (equity|bluechip|opportunities|total|specific)|u\.s\.|japan|china|taiwan|europe|greater china|"
                   r"emerging market", re.I)
_GOLD = re.compile(r"\bgold\b|\bsilver\b|commodit", re.I)
_CASH = re.compile(r"liquid fund|overnight fund|money market", re.I)
_DEBT_NAME = re.compile(r"gilt|g-?sec|\bsdl\b|bond|debt|crisil ibx|nifty .*(psu|aaa|sdl|g-sec)|target maturity|"
                        r"treasury|t-?bill|corporate|psu|bharat bond|liquid|money market|overnight|income", re.I)


def classify(category: Optional[str], broad_category: Optional[str], scheme_name: Optional[str]) -> str:
    cat = category or ""
    broad = broad_category or ""
    name = scheme_name or ""
    # Not for an equity scheme: "ICICI Prudential Commodities Fund" is a thematic EQUITY fund
    # that owns metal and cement companies, not the metals themselves.
    if (_GOLD.search(name) and broad != "Equity") or "Gold ETF" in cat:
        return "Gold & Commodities"
    if "FoF Overseas" in cat or _INTL.search(name):
        return "International Equity"
    # On the normalised category too, so the bare legacy labels ("Liquid", "Money Market")
    # land where their modern names do rather than in "Other".
    if _CASH.search(cat) or _CASH.search(sebi_category(cat) or "") or "Arbitrage Fund" in cat:
        # Arbitrage funds are "Hybrid" in SEBI's taxonomy but hedged, market-neutral
        # carry trades: they behave like cash, so they're treated as cash equivalents.
        return "Cash & Liquid"
    if broad == "Debt":
        return "Debt"
    if broad == "Hybrid":
        return "Hybrid"
    if broad == "Equity":
        return "Equity"
    if "Index Funds" in cat or "ETF" in cat:
        return "Debt" if _DEBT_NAME.search(name) else "Equity"
    if broad == "Solution Oriented":
        # Retirement/children's funds: their SEBI category leaves the equity/debt mix to
        # the scheme, so the scheme name is the only honest signal we have here.
        return "Debt" if _DEBT_NAME.search(name) else "Hybrid"
    if "FoF Domestic" in cat:
        return "Debt" if _DEBT_NAME.search(name) else "Hybrid"
    return "Other"


# --- IDCW ---------------------------------------------------------------------------

_IDCW_NAME = re.compile(r"idcw|dividend(?!\s*yield)", re.I)


def is_idcw(option_type: Optional[str], scheme_name: Optional[str]) -> bool:
    """An IDCW scheme's NAV drops by exactly what it pays out, so any return measured on its
    NAV counts the payout as a loss. Matching the name as well as the column catches the live
    schemes AMFI labels "Growth" while naming them IDCW -- but not a Growth "Dividend Yield"
    fund, whose name is about the stocks it picks."""
    if isinstance(option_type, str) and option_type.strip().upper() == "IDCW":
        return True
    return bool(_IDCW_NAME.search(scheme_name or ""))

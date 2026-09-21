"""Direct vs Regular Plan Matcher Service.

Deterministically pairs Direct mutual fund schemes with their Regular counterpart
based on fund house (AMC), category, and option type (Growth vs IDCW), normalizing
name tokens and stripping distribution plan identifiers.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.cache import cached
from app.db.connection import get_connection, fetchdf


# Common token cleaning patterns
_PLAN_NOISE_RE = re.compile(
    r"\b(direct|regular|plan|option|growth|idcw|dividend|payout|reinvestment|transfer|sweep|bonus|unclaimed|segregated|portfolio)\b",
    re.IGNORECASE,
)
_SPECIAL_CHARS_RE = re.compile(r"[-_/\\(),:;&+\u2013\u2014\u2212₹$]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_IDCW_WORD_RE = re.compile(r"\b(idcw|dividend|payout|reinvestment|income\s+distribution|div)\b", re.IGNORECASE)
_GROWTH_WORD_RE = re.compile(r"\b(growth)\b", re.IGNORECASE)
_DIVIDEND_YIELD_RE = re.compile(r"\bdividend\s+yield\b", re.IGNORECASE)
_AMC_SUFFIX_RE = re.compile(
    r"\b(mutual fund|mf|amc|asset management company|asset management co|asset management|co|corp|corporation|ltd|limited|trustee)\b",
    re.IGNORECASE,
)

_cached_pairs_dict: Optional[Dict[int, int]] = None
_cached_reverse_pairs_dict: Optional[Dict[int, int]] = None
_pairs_cache_time: float = 0.0
_pairs_lock = threading.Lock()


def normalize_fund_house(fund_house: Optional[str]) -> str:
    """Normalizes AMC/fund house names for robust matching."""
    if not fund_house:
        return ""
    name = fund_house.strip().lower()
    # Normalize common AMC and corporate suffixes
    name = _AMC_SUFFIX_RE.sub("", name)
    name = _SPECIAL_CHARS_RE.sub(" ", name)
    name = re.sub(r"\.+", " ", name)
    name = _MULTI_SPACE_RE.sub(" ", name).strip().strip(".")
    return name


def normalize_category(category: Optional[str]) -> str:
    """Normalizes scheme category strings."""
    if not category:
        return ""
    cat = category.strip().lower()
    cat = re.sub(r"^(equity scheme|debt scheme|hybrid scheme|solution oriented scheme|other scheme)\s*-\s*", "", cat, flags=re.IGNORECASE)
    cat = _SPECIAL_CHARS_RE.sub(" ", cat)
    return _MULTI_SPACE_RE.sub(" ", cat).strip()


def extract_option_type(scheme_name: str, option_type: Optional[str] = None) -> str:
    """Deterministically identifies the option type: 'growth' vs 'idcw' vs 'other'.

    Prioritizes explicit option_type when provided and valid ('growth' or 'idcw').
    Uses word-boundary regexes, safely distinguishing 'diversified' and 'dividend yield'.
    """
    if option_type:
        opt_lower = option_type.strip().lower()
        if opt_lower == "growth" or _GROWTH_WORD_RE.search(opt_lower):
            return "growth"
        if opt_lower == "idcw" or _IDCW_WORD_RE.search(opt_lower):
            return "idcw"

    name_lower = (scheme_name or "").lower()
    # Mask out "dividend yield" to avoid false positive IDCW classification on growth funds
    clean_name = _DIVIDEND_YIELD_RE.sub(" ", name_lower)

    has_growth = bool(_GROWTH_WORD_RE.search(clean_name))
    has_idcw = bool(_IDCW_WORD_RE.search(clean_name))

    if has_growth and not has_idcw:
        return "growth"
    if has_idcw and not has_growth:
        return "idcw"
    if has_growth and has_idcw:
        growth_matches = list(_GROWTH_WORD_RE.finditer(clean_name))
        idcw_matches = list(_IDCW_WORD_RE.finditer(clean_name))
        if growth_matches and idcw_matches:
            if growth_matches[-1].start() > idcw_matches[-1].start():
                return "growth"
            return "idcw"

    if option_type:
        ot = option_type.strip().lower()
        if ot:
            return ot
    return "growth"


def extract_plan_type(scheme_name: str, plan_type: Optional[str] = None) -> str:
    """Classifies a scheme as 'Direct' or 'Regular'."""
    if plan_type:
        pt = plan_type.strip().lower()
        if pt == "direct":
            return "direct"
        if pt == "regular":
            return "regular"

    name_lower = (scheme_name or "").lower()
    if re.search(r"\bdirect\b", name_lower):
        return "direct"
    return "regular"


def clean_base_fund_name(scheme_name: str) -> str:
    """Strips plan type, option type, and filler noise to isolate the core fund name."""
    clean = _SPECIAL_CHARS_RE.sub(" ", scheme_name.lower())
    clean = _PLAN_NOISE_RE.sub(" ", clean)
    clean = _MULTI_SPACE_RE.sub(" ", clean).strip()
    return clean


def _compute_pairs_for_schemes(schemes: List[Dict[str, Any]]) -> Dict[int, int]:
    if not schemes:
        return {}

    # Separate into Direct and Regular candidates by normalized grouping
    # Key: (normalized_amc, normalized_category, option_type, clean_base_name)
    direct_exact: Dict[Tuple[str, str, str, str], int] = {}
    direct_by_group: Dict[Tuple[str, str, str], List[Tuple[str, int]]] = {}

    regular_candidates: List[Tuple[int, str, str, str, str]] = []

    for s in schemes:
        try:
            code = int(s["scheme_code"])
        except (KeyError, TypeError, ValueError):
            continue

        raw_name = str(s.get("scheme_name", "") or "").strip()
        if not raw_name:
            continue

        amc = normalize_fund_house(str(s.get("fund_house", s.get("amc", "")) or ""))
        cat = normalize_category(str(s.get("category", "") or ""))
        opt = extract_option_type(raw_name, s.get("option_type"))
        plan = extract_plan_type(raw_name, s.get("plan_type"))
        base_name = clean_base_fund_name(raw_name)

        if plan == "direct":
            exact_key = (amc, cat, opt, base_name)
            if exact_key not in direct_exact:
                direct_exact[exact_key] = code
            group_key = (amc, cat, opt)
            direct_by_group.setdefault(group_key, []).append((base_name, code))
        else:
            regular_candidates.append((code, amc, cat, opt, base_name))

    pairs: Dict[int, int] = {}

    for reg_code, amc, cat, opt, base_name in regular_candidates:
        # 1. Try exact match on (amc, cat, opt, base_name)
        exact_key = (amc, cat, opt, base_name)
        if exact_key in direct_exact:
            pairs[reg_code] = direct_exact[exact_key]
            continue

        # 2. Try best token overlap within (amc, cat, opt)
        group_key = (amc, cat, opt)
        group_directs = direct_by_group.get(group_key)
        if not group_directs:
            continue

        if len(group_directs) == 1:
            # Single Direct candidate in the same AMC, Category, and Option
            pairs[reg_code] = group_directs[0][1]
            continue

        # Find candidate with highest word/token intersection
        reg_tokens = set(base_name.split())
        best_match_code = None
        best_overlap = 0

        for d_base, d_code in group_directs:
            d_tokens = set(d_base.split())
            overlap = len(reg_tokens & d_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match_code = d_code

        if best_match_code is not None and best_overlap > 0:
            pairs[reg_code] = best_match_code

    return pairs


@cached(ttl=3600)
def pair_direct_and_regular_schemes(
    schemes: Optional[List[Dict[str, Any]]] = None,
    conn: Any = None,
) -> Dict[int, int]:
    """Deterministically matches Regular mutual fund schemes to their Direct counterpart.

    Args:
        schemes: Optional list of scheme dictionaries. If None, queries the database.
                 Expected keys per scheme: 'scheme_code', 'scheme_name', and optionally
                 'fund_house' / 'amc', 'category', 'plan_type', 'option_type'.
        conn: Optional database connection if querying DB.

    Returns:
        Dict[int, int]: Mapping from regular_scheme_code -> direct_scheme_code.
    """
    global _cached_pairs_dict, _cached_reverse_pairs_dict, _pairs_cache_time

    if schemes is not None:
        return _compute_pairs_for_schemes(schemes)

    now = time.time()
    if _cached_pairs_dict is not None and (now - _pairs_cache_time < 3600):
        return _cached_pairs_dict

    with _pairs_lock:
        now = time.time()
        if _cached_pairs_dict is not None and (now - _pairs_cache_time < 3600):
            return _cached_pairs_dict

        close_con = False
        con = conn
        if con is None:
            con = get_connection()
            close_con = True
        try:
            sql = """
                SELECT scheme_code, scheme_name, fund_house, category, plan_type, option_type
                FROM schemes
                WHERE isin IS NOT NULL OR scheme_name IS NOT NULL;
            """
            df = fetchdf(con.execute(sql))
            schemes_records = df.to_dict(orient="records")
        finally:
            if close_con:
                con.close()

        pairs = _compute_pairs_for_schemes(schemes_records)
        _cached_pairs_dict = pairs
        _cached_reverse_pairs_dict = {d: r for r, d in pairs.items()}
        _pairs_cache_time = now
        return pairs


@cached(ttl=3600)
def find_paired_scheme(scheme_code: int, conn: Any = None) -> Optional[int]:
    """Finds the paired Direct counterpart for a Regular scheme, or Regular counterpart for a Direct scheme.

    Returns:
        paired_scheme_code if found, else None.
    """
    pairs = pair_direct_and_regular_schemes(schemes=None, conn=conn)
    # Check if scheme_code is Regular
    if scheme_code in pairs:
        return pairs[scheme_code]
    # Check if scheme_code is Direct (reverse lookup)
    if _cached_reverse_pairs_dict is not None and scheme_code in _cached_reverse_pairs_dict:
        return _cached_reverse_pairs_dict[scheme_code]
    reverse_map = {d: r for r, d in pairs.items()}
    return reverse_map.get(scheme_code)


def _warmup_plan_matcher() -> None:
    try:
        pair_direct_and_regular_schemes(schemes=None)
    except Exception:
        pass


threading.Thread(target=_warmup_plan_matcher, daemon=True, name="plan_matcher_warmup").start()

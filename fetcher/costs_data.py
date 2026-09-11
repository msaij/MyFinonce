"""Source-aware TER and redemption-cost utilities.

NAV data identifies a scheme, but it does not contain its current TER or its
scheme-specific exit-load terms. This module deliberately keeps unavailable
data unavailable: it never invents a TER or an exit-load rule from a category
or a scheme name.
"""

import csv
import datetime as dt
import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional


COST_DATA_VERSION = "source_aware_v1"
STATUS_OFFICIAL = "official"
STATUS_LEGACY = "legacy_unverified"
STATUS_UNKNOWN = "unknown"
EXIT_RULE_UNAVAILABLE = "Exit-load rule unavailable. Verify the current SID/KIM and addendum."

_TER_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None


def _get_legacy_ter_csv_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "data", "amfi_ter_data.csv")


def normalize_scheme_name(name: str) -> str:
    """Normalize only formatting differences; do not perform fuzzy matching.

    Shared by every source that identifies a scheme by name rather than by a
    common code (the bundled legacy TER CSV, and the official AMFI TER-portal
    sync) so all of them apply the exact same unambiguous-match discipline.
    """
    text = (name or "").lower()
    text = re.sub(r"\(formerly.*?\)", "", text)
    text = re.sub(r"[-–]\s*(direct|regular).*", "", text)
    text = re.sub(r"\b(direct|regular)\b.*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(growth|idcw|dividend|payout|reinvestment|plan|option)\b", " ", text)
    return " ".join(text.split())


def _parse_optional_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _load_legacy_ter_data() -> Dict[str, List[Dict[str, Any]]]:
    """Load the bundled legacy CSV without representing it as current official data."""
    global _TER_CACHE
    if _TER_CACHE is not None:
        return _TER_CACHE

    records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    csv_path = _get_legacy_ter_csv_path()
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                name = (row.get("Scheme Name") or "").strip()
                key = normalize_scheme_name(name)
                if key:
                    records[key].append(
                        {
                            "scheme_name": name,
                            "regular_ter": _parse_optional_float(row.get("Regular Plan - Total TER (%)")),
                            "direct_ter": _parse_optional_float(row.get("Direct Plan - Total TER (%)")),
                        }
                    )
    _TER_CACHE = dict(records)
    return _TER_CACHE


def _unique_legacy_ter(scheme_name: str, plan_type: str) -> Optional[float]:
    """Return a TER only for an unambiguous normalized-name match."""
    matches = _load_legacy_ter_data().get(normalize_scheme_name(scheme_name), [])
    is_direct = (plan_type or "").lower() == "direct" or "direct" in (scheme_name or "").lower()
    values = {
        row["direct_ter"] if is_direct else row["regular_ter"]
        for row in matches
        if (row["direct_ter"] if is_direct else row["regular_ter"]) is not None
    }
    return round(values.pop(), 4) if len(values) == 1 else None


def get_scheme_cost_specs(scheme_code: int, scheme_name: str, category: str, plan_type: str) -> Dict[str, Any]:
    """Return only traceable cost fields; legacy name matches remain explicitly unverified."""
    del scheme_code, category
    ter = _unique_legacy_ter(scheme_name, plan_type)
    if ter is None:
        ter_status = STATUS_UNKNOWN
        ter_source = "No TER record matched by scheme code"
    else:
        ter_status = STATUS_LEGACY
        ter_source = "Bundled TER CSV; source URL and effective date not recorded"
    return {
        "expense_ratio": ter,
        "ter_status": ter_status,
        "ter_source": ter_source,
        "ter_source_url": None,
        "ter_as_of_date": None,
        "exit_load_pct": None,
        "exit_load_days": None,
        "exit_load_description": EXIT_RULE_UNAVAILABLE,
        "exit_rule_json": None,
        "exit_rule_status": STATUS_UNKNOWN,
        "exit_rule_source": "No scheme-specific exit-load record",
        "exit_rule_source_url": None,
        "exit_rule_as_of_date": None,
        "exit_rule_effective_from": None,
        "lock_in_years": None,
    }


def parse_exit_rule(rule_json: Optional[str]) -> List[Dict[str, Any]]:
    """Validate a source-supplied, date-independent rule schedule.

    A rule has ``start_day``, optional ``end_day``, and ``rate_pct``. Optional
    ``free_units_pct`` applies a per-lot allowance before the load. This is a
    data format, not a substitute for the original SID/KIM terms.
    """
    if not rule_json:
        return []
    try:
        payload = json.loads(rule_json)
        rules = payload["rules"] if isinstance(payload, dict) else payload
    except (TypeError, ValueError, KeyError):
        return []
    if not isinstance(rules, list):
        return []

    parsed = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        start_day = rule.get("start_day", 0)
        end_day = rule.get("end_day")
        rate_pct = rule.get("rate_pct")
        free_units_pct = rule.get("free_units_pct", 0)
        if not isinstance(start_day, int) or start_day < 0:
            continue
        if end_day is not None and (not isinstance(end_day, int) or end_day < start_day):
            continue
        if not isinstance(rate_pct, (int, float)) or rate_pct < 0:
            continue
        if not isinstance(free_units_pct, (int, float)) or not 0 <= free_units_pct <= 100:
            continue
        parsed.append(
            {
                "start_day": start_day,
                "end_day": end_day,
                "rate_pct": float(rate_pct),
                "free_units_pct": float(free_units_pct),
            }
        )
    return parsed


def _parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def calculate_redemption_from_lots(
    lots: Iterable[Dict[str, Any]],
    redemption_date: Any,
    redemption_units: float,
    redemption_nav: float,
    exit_rule_json: Optional[str],
    exit_rule_effective_from: Any = None,
) -> Dict[str, Any]:
    """Calculate a FIFO redemption only when a verified rule schedule is supplied.

    Each lot needs ``purchase_date`` and ``units``. The caller must retain the
    source and effective-date checks before passing its rule to this function.
    """
    rules = parse_exit_rule(exit_rule_json)
    if not rules:
        return {"calculable": False, "reason": EXIT_RULE_UNAVAILABLE}
    if exit_rule_effective_from is None:
        return {"calculable": False, "reason": "The exit rule needs an effective-from date for lot-level calculation."}
    if redemption_units <= 0 or redemption_nav < 0:
        return {"calculable": False, "reason": "Redemption units and NAV must be positive."}

    try:
        exit_date = _parse_date(redemption_date)
        effective_from = _parse_date(exit_rule_effective_from)
        ordered_lots = sorted(
            ({"purchase_date": _parse_date(lot["purchase_date"]), "units": float(lot["units"])} for lot in lots),
            key=lambda lot: lot["purchase_date"],
        )
    except (KeyError, TypeError, ValueError):
        return {"calculable": False, "reason": "Each lot requires a valid purchase date and unit count."}

    remaining = redemption_units
    penalty = 0.0
    details = []
    for lot in ordered_lots:
        if lot["units"] <= 0 or remaining <= 0:
            continue
        units = min(remaining, lot["units"])
        holding_days = (exit_date - lot["purchase_date"]).days
        if holding_days < 0:
            return {"calculable": False, "reason": "A purchase date cannot be after redemption."}
        if lot["purchase_date"] < effective_from:
            return {"calculable": False, "reason": "A historical exit-rule record is required for a lot bought before this rule took effect."}
        matching = next(
            (rule for rule in rules if holding_days >= rule["start_day"] and (rule["end_day"] is None or holding_days <= rule["end_day"])),
            None,
        )
        if matching is None:
            return {"calculable": False, "reason": "The supplied rule has no band for every redeemed lot."}
        chargeable_units = max(0.0, units * (1 - matching["free_units_pct"] / 100.0))
        lot_penalty = chargeable_units * redemption_nav * matching["rate_pct"] / 100.0
        penalty += lot_penalty
        details.append({"purchase_date": lot["purchase_date"], "units": units, "holding_days": holding_days, "rate_pct": matching["rate_pct"], "penalty": lot_penalty})
        remaining -= units

    if remaining > 1e-9:
        return {"calculable": False, "reason": "Redemption units exceed the supplied transaction lots."}
    gross_value = redemption_units * redemption_nav
    return {"calculable": True, "gross_redemption_value": gross_value, "exit_load_penalty_amount": penalty, "net_redemption_value": gross_value - penalty, "lots": details}


def estimate_current_ter_drag(investment_amount: float, final_value: float, holding_days: int, ter_pct: Optional[float]) -> Optional[float]:
    """Return an illustrative current-TER estimate, never an historical actual fee."""
    if ter_pct is None or investment_amount < 0 or final_value < 0 or holding_days < 0:
        return None
    years = holding_days / 365.25
    return ((investment_amount + final_value) / 2.0) * (ter_pct / 100.0) * years

"""Source-aware TER utilities.

Ported verbatim from fetcher/costs_data.py (pure stdlib, no framework
coupling) with exactly one deliberate change: `_get_legacy_ter_csv_path()`.
The original derived the bundled CSV's path relative to its OWN file
location (fetcher/costs_data.py -> fetcher/data/amfi_ter_data.csv), which
broke once this module moved into the `app/` subpackage (its __file__ is
now a directory below the mounted data dir, not a sibling of it). Instead,
this derives the CSV's path from the same settings.db_path the rest of
the backend already uses -- both files live in the same data directory (see
docker-compose.yml's `./backend/data:/app/data` mount), so this is the
correct fix, not a workaround.

NAV data identifies a scheme, but it does not contain its current TER.
This module deliberately keeps unavailable data unavailable: it never
invents a TER from a category or a scheme name.

Exit-load tracking (a scheme-specific redemption penalty schedule) was
removed entirely: no AMFI feed ever disclosed it, the only path to a value
was a manual, rarely-used CSV import, and nothing elsewhere in the app
(portfolio backtest included) ever applied it to a real calculation --
every screen that showed it either read "Unavailable"/"unknown" or, in the
backtest, priced redemptions frictionlessly regardless. Kept as dead,
never-populated columns and a never-exercised import path was worse than
removing the concept outright.
"""

import csv
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.core.config import settings

COST_DATA_VERSION = "source_aware_v2"
STATUS_OFFICIAL = "official"
STATUS_LEGACY = "legacy_unverified"
STATUS_UNKNOWN = "unknown"

_TER_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None


def _get_legacy_ter_csv_path() -> str:
    return os.path.join(settings.data_dir, "amfi_ter_data.csv")


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
    }


def estimate_current_ter_drag(investment_amount: float, final_value: float, holding_days: int, ter_pct: Optional[float]) -> Optional[float]:
    """Return an illustrative current-TER estimate, never an historical actual fee."""
    if ter_pct is None or investment_amount < 0 or final_value < 0 or holding_days < 0:
        return None
    years = holding_days / 365.25
    return ((investment_amount + final_value) / 2.0) * (ter_pct / 100.0) * years

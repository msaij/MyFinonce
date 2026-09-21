"""Source-aware TER constants, scheme-name normalization, and NAV-merge fallback.

NAV identifies a scheme but does not contain TER. This module never invents a
TER from a category or a scheme name. Official TERs come from the AMFI TER
portal. Schemes with no official record stay unknown.
"""

import re
from typing import Any, Dict, Optional

COST_DATA_VERSION = "source_aware_v2"
STATUS_OFFICIAL = "official"
STATUS_LEGACY = "legacy_unverified"
STATUS_UNKNOWN = "unknown"


def normalize_scheme_name(name: str) -> str:
    """Normalize only formatting differences; do not perform fuzzy matching.

    Shared by the official AMFI TER-portal sync so name matches stay unambiguous.
    """
    text = (name or "").lower()
    text = re.sub(r"\(formerly.*?\)", "", text)
    text = re.sub(r"[-–]\s*(direct|regular).*", "", text)
    text = re.sub(r"\b(direct|regular)\b.*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(growth|idcw|dividend|payout|reinvestment|plan|option)\b", " ", text)
    return " ".join(text.split())


def get_scheme_cost_specs(scheme_code: int, scheme_name: str, category: str, plan_type: str) -> Dict[str, Any]:
    """NAV-merge fallback: no TER besides official AMFI portal records."""
    del scheme_code, scheme_name, category, plan_type
    return {
        "expense_ratio": None,
        "ter_status": STATUS_UNKNOWN,
        "ter_source": "No TER record matched by scheme code",
        "ter_source_url": None,
        "ter_as_of_date": None,
    }


def estimate_current_ter_drag(investment_amount: float, final_value: float, holding_days: int, ter_pct: Optional[float]) -> Optional[float]:
    """Return an illustrative current-TER estimate, never an historical actual fee."""
    if ter_pct is None or investment_amount < 0 or final_value < 0 or holding_days < 0:
        return None
    years = holding_days / 365.25
    return ((investment_amount + final_value) / 2.0) * (ter_pct / 100.0) * years

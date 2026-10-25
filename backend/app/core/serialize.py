"""The one DataFrame/numpy -> JSON boundary utility for the whole API.

Rule (see migration plan): services may return DataFrames/numpy freely;
ROUTE HANDLERS convert to plain Python via these two functions before
building a Pydantic response. Don't scatter bespoke per-endpoint handling --
every DataFrame-bearing or numpy-bearing return value in the ported
db.py/quant_analytics.py/portfolio_sim.py functions goes through one of
these two.
"""

import math
from typing import Any

import numpy as np
import pandas as pd


def df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of JSON-safe dicts. Handles NaT/NaN/numpy dtypes,
    which `df.to_dict(orient="records")` alone does not make JSON-safe."""
    if df is None or df.empty:
        return []
    records = df.to_dict(orient="records")
    return [sanitize_floats(r) for r in records]


def sanitize_floats(obj: Any) -> Any:
    """Recursively walks dicts/lists/tuples, casting numpy scalar types to
    plain Python and replacing NaN/Inf/NaT with None (JSON null) -- these
    would otherwise either raise in FastAPI's JSON encoder or, for numpy
    NaN specifically, silently serialize as invalid JSON (`NaN` is not
    valid JSON, only a common Python/JS extension some parsers accept)."""
    if obj is None:
        return None
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, np.ndarray):
        return [sanitize_floats(v) for v in obj.tolist()]
    if isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_floats(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return df_to_records(obj)
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj

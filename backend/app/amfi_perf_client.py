"""AMFI's fund-performance feed (the data behind amfiindia.com/otherdata/fund-performance,
served by CRISIL through AMFI's own gateway).

Why this exists: AMFI's daily NAV file leaves the Plan and Option columns blank for about
40% of its rows, so a scheme's plan cannot be read off it. This feed publishes, per fund,
the Direct-plan and Regular-plan NAV side by side on a stated date -- which identifies our
scheme codes by arithmetic instead of by guesswork (see amfi_sync.resolve_plan_options).

It also carries each scheme's SEBI riskometer level and AUM, which AMFI publishes nowhere
else in machine-readable form; neither is stored yet.

The endpoints are undocumented -- they are what the page itself calls. Requests are POSTs
with a JSON body and the whole open-ended universe is ~46 sub-category calls, so this is
run at most daily, never per page view.
"""

from __future__ import annotations

import datetime
import json
import logging
import ssl
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("amfi_perf_client")

PERF_PAGE_URL = "https://www.amfiindia.com/otherdata/fund-performance"
PERF_API_BASE = "https://www.amfiindia.com/gateway/pollingsebi/api/amfi"

#: The feed's own top-level asset classes. Their ids are stable and come back from
#: /fundperformancefilters; hard-coding the range only avoids one extra call.
CATEGORY_IDS = (1, 2, 3, 4, 5, 6)

#: Open Ended. Close Ended is maturityType 2 and is not synced: those schemes cannot be
#: bought and their NAVs are not what a holder of an open-ended plan needs matched.
MATURITY_OPEN_ENDED = 1


class AmfiPerfClient:
    def __init__(self, user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", timeout: int = 60):
        from app.core.config import amfi_ssl_context

        self.headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The gateway is the site's own; identifying the page keeps the request
            # indistinguishable from what a browser on that page sends.
            "Referer": PERF_PAGE_URL,
        }
        self.timeout = timeout
        self.ssl_ctx: ssl.SSLContext = amfi_ssl_context()

    def _post(self, path: str, payload: Dict[str, Any], attempts: int = 3) -> Optional[Dict[str, Any]]:
        body = json.dumps(payload).encode()
        last_err: Optional[Exception] = None
        for attempt in range(attempts):
            req = urllib.request.Request(f"{PERF_API_BASE}/{path}", data=body, headers=self.headers, method="POST")
            try:
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        last_err = RuntimeError(f"HTTP {resp.status}")
                    else:
                        return json.loads(resp.read().decode("utf-8", errors="ignore"))
            except Exception as e:  # noqa: BLE001 -- retried, then reported
                last_err = e
        logger.warning(f"AMFI fund-performance {path} failed after {attempts} attempts: {last_err}")
        return None

    def report_date(self) -> Optional[str]:
        """The latest date the feed itself offers, as 'DD-Mon-YYYY'. Its own value is used
        verbatim rather than today's date: the feed lags NAVs by a day or two."""
        body = self._post("fundperformancefilters", {})
        return ((body or {}).get("data") or {}).get("reportDate")

    def sub_categories(self, category_id: int) -> List[Dict[str, Any]]:
        body = self._post("getsubcategory", {"category": category_id})
        return ((body or {}).get("data") or []) if body else []

    def fetch_all(self, report_date: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """Every open-ended fund the feed covers, one row per fund (not per plan)."""
        report_date = report_date or self.report_date()
        if not report_date:
            return
        for category_id in CATEGORY_IDS:
            for sub in self.sub_categories(category_id):
                body = self._post("fundperformance", {
                    "maturityType": MATURITY_OPEN_ENDED,
                    "category": category_id,
                    "subCategory": sub["id"],
                    "mfid": 0,
                    "reportDate": report_date,
                })
                for row in ((body or {}).get("data") or []):
                    yield {**row, "_category_id": category_id, "_sub_category": sub.get("name")}


def parse_nav_date(raw: Optional[str]) -> Optional[datetime.date]:
    """The feed states the NAV date per row ('21-Sep-2026'); it can trail the report date."""
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def to_nav(raw: Any) -> Optional[float]:
    """NAVs arrive as strings, and a plan a fund does not offer comes back blank or '-'."""
    if raw in (None, "", "-", "N.A.", "NA"):
        return None
    try:
        value = float(str(raw).replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None

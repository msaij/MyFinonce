import datetime
import logging
import re
import ssl
from typing import Dict, Generator, List, Optional, Tuple
import urllib.request

logger = logging.getLogger("amfi_client")

# Complete official catalog of all 76 AMFI registered Mutual Fund houses and their portal IDs
AMFI_AMC_CATALOG = [
    {"mf_id": 62, "name": "360 ONE Mutual Fund"},
    {"mf_id": 85, "name": "Abakkus Mutual Fund"},
    {"mf_id": 39, "name": "ABN AMRO Mutual Fund"},
    {"mf_id": 3, "name": "Aditya Birla Sun Life Mutual Fund"},
    {"mf_id": 50, "name": "AEGON Mutual Fund"},
    {"mf_id": 1, "name": "Alliance Capital Mutual Fund"},
    {"mf_id": 80, "name": "Angel One Mutual Fund"},
    {"mf_id": 53, "name": "Axis Mutual Fund"},
    {"mf_id": 75, "name": "Bajaj Finserv Mutual Fund"},
    {"mf_id": 48, "name": "Bandhan Mutual Fund"},
    {"mf_id": 46, "name": "Bank of India Mutual Fund"},
    {"mf_id": 4, "name": "Baroda BNP Paribas Mutual Fund"},
    {"mf_id": 36, "name": "Benchmark Mutual Fund"},
    {"mf_id": 59, "name": "BNP Paribas Mutual Fund"},
    {"mf_id": 32, "name": "Canara Robeco Mutual Fund"},
    {"mf_id": 81, "name": "Capitalmind Mutual Fund"},
    {"mf_id": 84, "name": "Choice Mutual Fund"},
    {"mf_id": 60, "name": "Daiwa Mutual Fund"},
    {"mf_id": 31, "name": "DBS Chola Mutual Fund"},
    {"mf_id": 38, "name": "Deutsche Mutual Fund"},
    {"mf_id": 6, "name": "DSP Mutual Fund"},
    {"mf_id": 47, "name": "Edelweiss Mutual Fund"},
    {"mf_id": 40, "name": "Fidelity Mutual Fund"},
    {"mf_id": 51, "name": "FirstRand Mutual Fund"},
    {"mf_id": 7, "name": "Franklin Templeton Mutual Fund"},
    {"mf_id": 8, "name": "GIC Mutual Fund"},
    {"mf_id": 73, "name": "Groww Mutual Fund"},
    {"mf_id": 5, "name": "Hathway Mutual Fund"},
    {"mf_id": 9, "name": "HDFC Mutual Fund"},
    {"mf_id": 76, "name": "Helios Mutual Fund"},
    {"mf_id": 37, "name": "HSBC Mutual Fund"},
    {"mf_id": 20, "name": "ICICI Prudential Mutual Fund"},
    {"mf_id": 57, "name": "IDBI Mutual Fund"},
    {"mf_id": 11, "name": "IL&FS Mutual Fund"},
    {"mf_id": 14, "name": "ING Mutual Fund"},
    {"mf_id": 42, "name": "Invesco Mutual Fund"},
    {"mf_id": 70, "name": "ITI Mutual Fund"},
    {"mf_id": 82, "name": "Jio BlackRock Mutual Fund"},
    {"mf_id": 16, "name": "JM Financial Mutual Fund"},
    {"mf_id": 43, "name": "JPMorgan Mutual Fund"},
    {"mf_id": 17, "name": "Kotak Mahindra Mutual Fund"},
    {"mf_id": 56, "name": "L&T Mutual Fund"},
    {"mf_id": 18, "name": "LIC Mutual Fund"},
    {"mf_id": 69, "name": "Mahindra Manulife Mutual Fund"},
    {"mf_id": 45, "name": "Mirae Asset Mutual Fund"},
    {"mf_id": 19, "name": "Morgan Stanley Mutual Fund"},
    {"mf_id": 55, "name": "Motilal Oswal Mutual Fund"},
    {"mf_id": 54, "name": "Navi Mutual Fund"},
    {"mf_id": 21, "name": "Nippon India Mutual Fund"},
    {"mf_id": 68, "name": "NJ Mutual Fund"},
    {"mf_id": 78, "name": "Old Bridge Mutual Fund"},
    {"mf_id": 58, "name": "PGIM India Mutual Fund"},
    {"mf_id": 64, "name": "PPFAS Mutual Fund"},
    {"mf_id": 10, "name": "Principal Mutual Fund"},
    {"mf_id": 13, "name": "quant Mutual Fund"},
    {"mf_id": 41, "name": "Quantum Mutual Fund"},
    {"mf_id": 74, "name": "Samco Mutual Fund"},
    {"mf_id": 22, "name": "SBI Mutual Fund"},
    {"mf_id": 67, "name": "Shriram Mutual Fund"},
    {"mf_id": 2, "name": "Standard Chartered Mutual Fund"},
    {"mf_id": 33, "name": "Sundaram Mutual Fund"},
    {"mf_id": 25, "name": "Tata Mutual Fund"},
    {"mf_id": 26, "name": "Taurus Mutual Fund"},
    {"mf_id": 72, "name": "Trust Mutual Fund"},
    {"mf_id": 79, "name": "Unifi Mutual Fund"},
    {"mf_id": 61, "name": "Union Mutual Fund"},
    {"mf_id": 28, "name": "UTI Mutual Fund"},
    {"mf_id": 71, "name": "WhiteOak Capital Mutual Fund"},
    {"mf_id": 77, "name": "Zerodha Mutual Fund"}
]


class AmfiClient:
    """Official AMFI portal download client and robust header-aware text parser."""

    NAV_DOWNLOAD_PAGE = "https://www.amfiindia.com/net-asset-value/nav-download"
    NAV_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
    NAV_DAILY_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

    def __init__(self, user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"):
        from app.core.config import amfi_ssl_context
        self.headers = {"User-Agent": user_agent}
        self.ssl_ctx = amfi_ssl_context()

    def get_amc_directory(self) -> List[Dict]:
        """
        Discovers all mutual fund houses from AMFI live page,
        falling back to the comprehensive built-in catalog if offline.
        """
        try:
            req = urllib.request.Request(self.NAV_DOWNLOAD_PAGE, headers=self.headers)
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            f_matches = re.findall(r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)', html)
            all_text = "".join(f_matches)
            mfs = re.findall(r'mfId\\*":\\*"(\d+)\\*",\\*"mfName\\*":\\*"([^\\"]+)', all_text)

            if mfs:
                seen = set()
                live_amcs = []
                for mf_id_str, name_str in mfs:
                    mf_id = int(mf_id_str)
                    clean_name = name_str.strip().replace("\\u0026", "&")
                    if mf_id not in seen:
                        seen.add(mf_id)
                        live_amcs.append({"mf_id": mf_id, "name": clean_name})
                logger.info(f"Discovered {len(live_amcs)} Mutual Fund houses dynamically from AMFI portal.")
                try:
                    import json
                    from app.db import queries as db
                    db.set_sync_meta_value("amc_directory_json", json.dumps(live_amcs))
                except Exception:
                    pass
                return live_amcs
        except Exception as e:
            logger.warning(f"Could not scrape live AMC list from AMFI ({e}). Using built-in catalog.")

        return AMFI_AMC_CATALOG

    def download_amc_90d_report(self, mf_id: int, from_date: datetime.date, to_date: datetime.date) -> Optional[str]:
        """
        Downloads past 90-day NAV report for a specific mutual fund house.
        Dates are formatted as DD-Mon-YYYY (e.g. 05-Jun-2026).
        """
        frmdt_str = from_date.strftime("%d-%b-%Y")
        todt_str = to_date.strftime("%d-%b-%Y")

        url = f"{self.NAV_HISTORY_URL}?mf={mf_id}&frmdt={frmdt_str}&todt={todt_str}"
        logger.debug(f"Requesting AMFI report: {url}")

        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=60) as resp:
                if resp.status == 200:
                    data = resp.read().decode("utf-8", errors="ignore")
                    return data
                else:
                    logger.warning(f"HTTP {resp.status} for mf_id={mf_id}")
        except Exception as e:
            logger.error(f"Error downloading 90-day report for mf_id={mf_id}: {e}")
            return None

    #: AMFI's scheme-type codes for the NAV-history report: open-ended, close-ended, interval.
    #: Since ~2026-09-23 the all-AMC report is served only per type -- a request without `tp`
    #: gets the portal's HTML form page with HTTP 200 (the failure ChunkDownloadError was
    #: added for), and tp=4 does not exist.
    NAV_HISTORY_SCHEME_TYPES = (1, 2, 3)

    def download_bulk_historical_report(self, from_date: datetime.date, to_date: datetime.date) -> Optional[str]:
        """
        Downloads bulk historical NAV report for ALL funds across India for up to 90 days.
        Dates are formatted as DD-Mon-YYYY.

        One request per scheme type, joined into one text: each part carries its own
        "Scheme Code" header, which the parser simply re-reads. All or nothing -- a part
        that fails or comes back as anything but a report fails the whole download, because
        callers checkpoint what they receive and a silently missing scheme type would be
        recorded as done.
        """
        frmdt_str = from_date.strftime("%d-%b-%Y")
        todt_str = to_date.strftime("%d-%b-%Y")
        parts = []
        for tp in self.NAV_HISTORY_SCHEME_TYPES:
            url = f"{self.NAV_HISTORY_URL}?tp={tp}&frmdt={frmdt_str}&todt={todt_str}"
            logger.info(f"Requesting bulk AMFI historical report: {url}")
            req = urllib.request.Request(url, headers=self.headers)
            try:
                # 60s, not the old 120s: this is the same NAV-history endpoint
                # download_amc_90d_report already calls with a 60s timeout below, just
                # unfiltered by AMC -- there's no reason a wider report should need double the
                # patience, and a slow/hung request here is also what the historical backfill's
                # should_stop check waits behind (see amfi_sync._historical_backfill_worker) --
                # failing faster halves that worst-case "Stop doesn't seem to do anything" wait.
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=60) as resp:
                    if resp.status != 200:
                        logger.warning(f"HTTP {resp.status} for bulk report tp={tp} {frmdt_str} to {todt_str}")
                        return None
                    body = resp.read().decode("utf-8", errors="ignore")
            except Exception as e:
                logger.error(f"Error downloading bulk report tp={tp} {frmdt_str} to {todt_str}: {e}")
                return None
            if "scheme code" not in body[:4000].lower():
                logger.warning(f"Bulk report tp={tp} {frmdt_str} to {todt_str} is not a NAV report "
                               f"({len(body):,} bytes) -- the portal is likely serving its form page.")
                return None
            parts.append(body)
        return "\n".join(parts)

    def download_daily_report(self) -> Optional[str]:
        """Downloads the single bulk daily closing NAV file for all funds across India."""
        logger.info(f"Downloading daily NAV master feed from {self.NAV_DAILY_URL}...")
        req = urllib.request.Request(self.NAV_DAILY_URL, headers=self.headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=30) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="ignore")
                else:
                    logger.warning(f"HTTP {resp.status} downloading NAVAll.txt")
                    return None
        except Exception as e:
            logger.error(f"Error downloading NAVAll.txt: {e}")
            return None

    @staticmethod
    def parse_date(date_str: str) -> Optional[datetime.date]:
        """Parses dates in DD-Mon-YYYY (05-Jun-2026) or DD-MM-YYYY (05-06-2026)."""
        date_str = date_str.strip()
        if not date_str:
            return None
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    def parse_amfi_nav_lines(self, raw_text: str, default_amc: str = "") -> Generator[Tuple[Dict, Dict], None, None]:
        """
        Parses AMFI text reports line-by-line using dynamic header detection.
        Works seamlessly for both historical reports and daily NAVAll.txt.
        """
        current_category = ""
        current_amc = default_amc

        # Default fallback column index mapping
        # "isin" is a LIST of indices, not one index: AMFI's header carries two ISIN
        # columns -- "ISIN Div Payout/ISIN Growth" and "ISIN Div Reinvestment" -- and
        # which one a given row populates depends on that row's option type. A
        # Growth/IDCW-Payout row fills the first and leaves the second blank or "-";
        # an IDCW-Reinvestment row does the opposite. Recording only the first column
        # (as this did until 2026-09-12) silently dropped the ISIN of every single
        # reinvestment-option scheme in the file.
        col_map = {
            "code": 0,
            "name": 1,
            "plan": 2,
            "option": 3,
            "isin": [4, 5],
            "isin_reinvest": 5,
            "nav": 6,
            "date": 7
        }

        # Diagnostic logging added 2026-09-12 after a historical-backfill chunk hung for 60+
        # minutes with zero log output: this is the only genuinely large, pure-Python O(n)
        # loop in the whole backfill path, and it previously logged nothing between "download
        # requested" and "chunk ingested" -- a truly stuck chunk and a chunk just slowly
        # working through an unusually large response were indistinguishable from the log
        # alone. The size line below shows immediately whether a report is abnormally large;
        # the periodic line shows whether the loop is still actually advancing.
        total_lines = raw_text.count("\n") + 1
        logger.info(f"parse_amfi_nav_lines: {len(raw_text):,} chars, ~{total_lines:,} lines to process.")
        line_count = 0
        # Whether a header line has been seen yet. Rows before any header are read through
        # the fallback indices above, which say where a column *usually* is -- not that this
        # file carries it -- so they can never count as AMFI having reported the column.
        header_seen = False

        for line in raw_text.splitlines():
            line_count += 1
            if line_count % 200_000 == 0:
                logger.info(f"parse_amfi_nav_lines: still working -- {line_count:,}/{total_lines:,} lines processed so far.")
            line = line.strip()
            if not line:
                continue

            # Check if line is a category header
            # E.g. "Open Ended Schemes ( Equity Scheme - Large Cap Fund )"
            if "schemes (" in line.lower() or "schemes(" in line.lower():
                cat_match = re.search(r"\((.*?)\)", line)
                raw_cat = cat_match.group(1).strip() if cat_match else line.strip()
                # Normalize category names to prevent duplicates
                if raw_cat.startswith("Equity Schemes - "):
                    raw_cat = "Equity Scheme - " + raw_cat[len("Equity Schemes - "):]
                elif raw_cat.startswith("Hybrid Schemes - "):
                    raw_cat = "Hybrid Scheme - " + raw_cat[len("Hybrid Schemes - "):]
                elif raw_cat.startswith("Solution Oriented Schemes ** - "):
                    raw_cat = "Solution Oriented Scheme - " + raw_cat[len("Solution Oriented Schemes ** - "):]
                if raw_cat in ("Equity Scheme - ELSS- Tax Saver Fund", "ELSS"):
                    raw_cat = "Equity Scheme - ELSS"
                elif raw_cat in ("Equity Scheme - Sectoral Fund", "Equity Scheme - Thematic Fund"):
                    raw_cat = "Equity Scheme - Sectoral/ Thematic"
                elif raw_cat == "Hybrid Scheme - Balanced Advantage Fund/ Dynamic Asset Allocation":
                    raw_cat = "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage"
                elif raw_cat == "Hybrid Scheme - Equity Savings Fund":
                    raw_cat = "Hybrid Scheme - Equity Savings"
                elif raw_cat == "Hybrid Scheme - Multi Asset Allocation Fund":
                    raw_cat = "Hybrid Scheme - Multi Asset Allocation"
                elif "children" in raw_cat.lower() and "fund" in raw_cat.lower():
                    raw_cat = "Solution Oriented Scheme - Children's Fund"
                elif raw_cat in ("Exchange Traded Funds (ETFs", "Other Scheme - Other  ETFs"):
                    raw_cat = "Other Scheme - Other ETFs"
                elif raw_cat == "Fund of Funds Scheme (Domestic":
                    raw_cat = "Other Scheme - FoF Domestic"
                elif raw_cat == "Overseas Fund of Funds - Fund of Funds investing overseas":
                    raw_cat = "Other Scheme - FoF Overseas"
                elif raw_cat in ("Index Funds - Equity Funds", "Index Funds - Debt Funds", "Index Funds - Hybrid Fund"):
                    raw_cat = "Other Scheme - Index Funds"
                current_category = raw_cat
                continue

            # Check if line is an AMC name
            if ";" not in line:
                if "mutual fund" in line.lower() or "fund" in line.lower():
                    current_amc = line.strip()
                continue

            # Header detection line
            if "scheme code" in line.lower():
                parts_lower = [p.strip().lower() for p in line.split(";")]
                new_map = {}
                for i, col in enumerate(parts_lower):
                    if "scheme code" in col:
                        new_map["code"] = i
                    elif "scheme name" in col or "nav name" in col:
                        new_map["name"] = i
                    elif col == "plan":
                        new_map["plan"] = i
                    elif col == "option":
                        new_map["option"] = i
                    elif "isin" in col:
                        new_map.setdefault("isin", []).append(i)
                        # Which of the two ISIN columns this is matters, not just that it
                        # holds an ISIN: only a scheme with a dividend option is issued a
                        # reinvestment ISIN, so that column identifies IDCW schemes whose
                        # Option field AMFI filled in wrongly or left blank.
                        if "reinvest" in col:
                            new_map["isin_reinvest"] = i
                    elif "net asset value" in col:
                        new_map["nav"] = i
                    elif "date" in col:
                        new_map["date"] = i
                col_map.update(new_map)
                # A header that does not declare Plan, Option or the reinvestment ISIN must
                # not inherit the fallback index for it: that index then points at whatever
                # column this layout puts there (a scheme name, a NAV), which would be
                # recorded as something AMFI "stated". It matters most for the reinvestment
                # column, whose placeholder now CLEARS the stored ISIN -- a column that is not
                # in the file has to read as "not reported", never as "reported blank".
                for optional_col in ("plan", "option", "isin_reinvest"):
                    if optional_col not in new_map:
                        col_map.pop(optional_col, None)
                header_seen = True
                continue

            # Data row
            parts = [p.strip() for p in line.split(";")]
            if not parts or not parts[0].isdigit():
                continue

            try:
                code_idx = col_map.get("code", 0)
                name_idx = col_map.get("name", 1)
                plan_idx = col_map.get("plan")
                option_idx = col_map.get("option")
                isin_indices = col_map.get("isin") or []
                nav_idx = col_map.get("nav", len(parts) - 2)
                date_idx = col_map.get("date", len(parts) - 1)

                scheme_code = int(parts[code_idx])
                scheme_name = parts[name_idx] if name_idx < len(parts) else ""
                nav_str = parts[nav_idx] if nav_idx < len(parts) else ""
                date_str = parts[date_idx] if date_idx < len(parts) else ""

                plan_raw = parts[plan_idx] if plan_idx is not None and plan_idx < len(parts) else ""
                option_raw = parts[option_idx] if option_idx is not None and option_idx < len(parts) else ""
                # First non-placeholder value across every ISIN column -- see col_map's
                # comment: exactly one of them is populated per row, and which one
                # depends on the row's option type.
                isin = None
                for idx in isin_indices:
                    if idx < len(parts):
                        candidate = parts[idx]
                        if candidate and candidate not in ("-", "None", "null"):
                            isin = candidate
                            break

                reinvest_idx = col_map.get("isin_reinvest")
                reinvest_isin = parts[reinvest_idx] if reinvest_idx is not None and reinvest_idx < len(parts) else ""
                has_reinvest_isin = bool(reinvest_isin) and reinvest_isin not in ("-", "None", "null")
                # Two different kinds of "no reinvestment ISIN", and the merge treats them
                # oppositely: a header that carries the column and a row that leaves it blank
                # is AMFI saying the scheme has none (clear any stored one); a file without the
                # column says nothing at all (keep what is stored). A row too short to reach
                # the column is the second kind -- a truncated line is not a statement.
                reinvest_reported = (header_seen and reinvest_idx is not None
                                     and reinvest_idx < len(parts))

                nav_val = float(nav_str)
                nav_date = self.parse_date(date_str)
                if not nav_date:
                    continue
            except (ValueError, IndexError, TypeError):
                continue

            # Normalize Plan. AMFI leaves the Plan column blank for ~40% of rows, and for
            # those the name usually carries no hint either. This used to answer "Regular"
            # for all of them, which is a guess stated as fact: it mislabels every Direct
            # plan in that set, and because the TER matcher picks the Direct or Regular
            # column *from this field*, those funds were then given the Regular expense
            # ratio (Motilal Oswal Liquid Direct, code 145834, carried Regular's 0.41%).
            # None means "AMFI did not say" -- amfi_sync.resolve_plan_options() settles it
            # afterwards from the fund's own published Direct/Regular NAVs.
            #
            # Each value is emitted with its source ('amfi' = AMFI's Plan/Option column,
            # 'isin' = the reinvestment ISIN, 'name' = the scheme name), so the database can
            # tell a stated value from an inferred one. The branches below are the original
            # precedence verbatim; only the source is new. Where the column and the name both
            # carry the winning word, the column is credited -- it is the stronger evidence.
            name_lower = scheme_name.lower()
            plan_lower = plan_raw.lower()
            if "direct" in plan_lower or "direct" in name_lower:
                plan_type = "Direct"
                plan_source = "amfi" if "direct" in plan_lower else "name"
            elif "regular" in plan_lower or "regular" in name_lower:
                plan_type = "Regular"
                plan_source = "amfi" if "regular" in plan_lower else "name"
            else:
                plan_type = None
                plan_source = None

            # Same rule for the option: state it only where AMFI's data says it.
            #
            # The reinvestment ISIN is checked FIRST and outranks the Option column, because
            # it is structural rather than typed: a registrar only issues one to a scheme
            # that has a dividend option, whereas the Option field is free text AMFI's
            # members fill in by hand (this file carries 300+ distinct spellings of it).
            # Where the two disagree the ISIN is right -- Motilal Oswal Digital India Fund
            # 152965 is published as "Direct Plan / Growth" yet carries reinvestment ISIN
            # INF247L01DP7, which is why it and its Growth twin 152964 were indistinguishable
            # in the app. 11 schemes are mislabelled Growth this way and a further 410 carry
            # a reinvestment ISIN with no Option stated at all.
            option_lower = option_raw.lower()
            if has_reinvest_isin:
                option_type = "IDCW"
                option_source = "isin"
            elif "growth" in option_lower or "growth" in name_lower:
                option_type = "Growth"
                option_source = "amfi" if "growth" in option_lower else "name"
            elif "idcw" in option_lower or "dividend" in name_lower or "idcw" in name_lower:
                option_type = "IDCW"
                option_source = "amfi" if "idcw" in option_lower else "name"
            else:
                option_type = None
                option_source = None

            scheme_meta = {
                "scheme_code": scheme_code,
                "scheme_name": scheme_name[:500],
                "fund_house": current_amc or default_amc,
                "category": current_category,
                "scheme_type": "Open Ended" if "open" in current_category.lower() else "Close Ended",
                "plan_type": plan_type,
                "plan_source": plan_source,
                "option_type": option_type,
                "option_source": option_source,
                "isin": isin,
                # Kept rather than consumed and thrown away: it is the evidence for the
                # option above. Without it a scheme reads as IDCW with nothing in the
                # database saying why, when AMFI's own Option column says "Growth".
                "isin_reinvestment": reinvest_isin if has_reinvest_isin else None,
                # Whether None above means "AMFI says none" (True) or "not in this file"
                # (False). A separate flag rather than a sentinel string in the value, so no
                # sentinel can ever reach a stored row -- see amfi_sync._merge_amfi_payload.
                "isin_reinvestment_reported": reinvest_reported,
            }

            nav_record = {
                "scheme_code": scheme_code,
                "nav_date": nav_date,
                "nav": nav_val,
            }

            yield scheme_meta, nav_record

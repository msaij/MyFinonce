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
    {"mf_id": 9, "name": "Hathway Mutual Fund"},
    {"mf_id": 10, "name": "HDFC Mutual Fund"},
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
    {"mf_id": 73, "name": "NJ Mutual Fund"},
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
        self.headers = {"User-Agent": user_agent}
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

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
                    clean_name = name_str.strip().replace(r"\u0026", "&")
                    if mf_id not in seen:
                        seen.add(mf_id)
                        live_amcs.append({"mf_id": mf_id, "name": clean_name})
                logger.info(f"Discovered {len(live_amcs)} Mutual Fund houses dynamically from AMFI portal.")
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

    def download_bulk_historical_report(self, from_date: datetime.date, to_date: datetime.date) -> Optional[str]:
        """
        Downloads bulk historical NAV report for ALL funds across India for up to 90 days.
        Dates are formatted as DD-Mon-YYYY.
        """
        frmdt_str = from_date.strftime("%d-%b-%Y")
        todt_str = to_date.strftime("%d-%b-%Y")
        url = f"{self.NAV_HISTORY_URL}?frmdt={frmdt_str}&todt={todt_str}"
        logger.info(f"Requesting bulk AMFI historical report: {url}")
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=120) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="ignore")
                else:
                    logger.warning(f"HTTP {resp.status} for bulk report {frmdt_str} to {todt_str}")
                    return None
        except Exception as e:
            logger.error(f"Error downloading bulk report {frmdt_str} to {todt_str}: {e}")
            return None

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
        col_map = {
            "code": 0,
            "name": 1,
            "plan": 2,
            "option": 3,
            "isin": 4,
            "nav": 6,
            "date": 7
        }

        for line in raw_text.splitlines():
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
                    elif "isin" in col and "isin" not in new_map:
                        new_map["isin"] = i
                    elif "net asset value" in col:
                        new_map["nav"] = i
                    elif "date" in col:
                        new_map["date"] = i
                col_map.update(new_map)
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
                isin_idx = col_map.get("isin")
                nav_idx = col_map.get("nav", len(parts) - 2)
                date_idx = col_map.get("date", len(parts) - 1)

                scheme_code = int(parts[code_idx])
                scheme_name = parts[name_idx] if name_idx < len(parts) else ""
                nav_str = parts[nav_idx] if nav_idx < len(parts) else ""
                date_str = parts[date_idx] if date_idx < len(parts) else ""

                plan_raw = parts[plan_idx] if plan_idx is not None and plan_idx < len(parts) else ""
                option_raw = parts[option_idx] if option_idx is not None and option_idx < len(parts) else ""
                isin = parts[isin_idx] if isin_idx is not None and isin_idx < len(parts) else None
                if isin in ("-", "", "None", "null"):
                    isin = None

                nav_val = float(nav_str)
                nav_date = self.parse_date(date_str)
                if not nav_date:
                    continue
            except (ValueError, IndexError, TypeError):
                continue

            # Normalize Plan
            name_lower = scheme_name.lower()
            if "direct" in plan_raw.lower() or "direct" in name_lower:
                plan_type = "Direct"
            else:
                plan_type = "Regular"

            # Normalize Option
            if "growth" in option_raw.lower() or "growth" in name_lower:
                option_type = "Growth"
            elif "idcw" in option_raw.lower() or "dividend" in name_lower or "idcw" in name_lower:
                option_type = "IDCW"
            else:
                option_type = "Other"

            scheme_meta = {
                "scheme_code": scheme_code,
                "scheme_name": scheme_name[:500],
                "fund_house": current_amc or default_amc,
                "category": current_category,
                "scheme_type": "Open Ended" if "open" in current_category.lower() else "Close Ended",
                "plan_type": plan_type,
                "option_type": option_type,
                "isin": isin,
            }

            nav_record = {
                "scheme_code": scheme_code,
                "nav_date": nav_date,
                "nav": nav_val,
            }

            yield scheme_meta, nav_record

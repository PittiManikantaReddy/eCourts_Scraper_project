from __future__ import annotations
import argparse
import dataclasses
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Third-party
try:
    import requests
    from bs4 import BeautifulSoup
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
except Exception as e:
    print("Missing dependencies. Please install:\n"
          "  pip install selenium beautifulsoup4 requests lxml\n"
          f"Error: {e}")
    sys.exit(1)


# === Constants (official entry points) ===
ECOURTS_HOME = "https://services.ecourts.gov.in/ecourtindia_v6/"
ECOURTS_CAUSELIST = "https://services.ecourts.gov.in/ecourtindia_v6/?p=cause_list/index"

# Output directories
DEFAULT_OUT_DIR = os.path.join(os.getcwd(), "outputs")
DEFAULT_DL_DIR = os.path.join(os.getcwd(), "downloads")


# === Utilities ===

def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def today_ist() -> datetime:
    # IST = UTC+5:30
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def date_str_ist(days_from_today: int = 0) -> str:
    d = today_ist().date() + timedelta(days=days_from_today)
    # dd-mm-yyyy (common for cause list date pickers)
    return d.strftime("%d-%m-%Y")

def save_json(obj: Any, path: str) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def save_text(text: str, path: str) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-\.]+", "_", name).strip("_")

def log(msg: str, verbose: bool) -> None:
    if verbose:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# === Parsing helpers ===

def parse_case_status_html(html: str) -> Dict[str, Any]:
    """
    Heuristic parser for the CNR 'Case Status' result page.
    Tries to extract key details including court name, case number, and hearing dates.
    """
    soup = BeautifulSoup(html, "lxml")

    text = soup.get_text(" ", strip=True)

    # Very heuristic extraction attempts:
    case_no = None
    m = re.search(r"(Case\s*No\.?|Case\s*Number)\s*[:\-]?\s*([A-Za-z./\-\s]*\d+\/\d{4})", text, re.I)
    if m:
        case_no = m.group(2).strip()

    cnr_found = None
    m = re.search(r"(CNR\s*No\.?)\s*[:\-]?\s*([A-Z0-9]{16})", text, re.I)
    if m:
        cnr_found = m.group(2).strip()

    court_name = None
    m = re.search(r"(Court\s*Name|Court)\s*[:\-]?\s*([^\n\r]+)", text, re.I)
    if m:
        court_name = m.group(2).strip()

    # Try to extract a table of hearings (date, stage/purpose).
    hearings: List[Dict[str, str]] = []
    # Look for date patterns dd-mm-yyyy
    for dt_match in re.finditer(r"(\d{2}-\d{2}-\d{4})", text):
        dt = dt_match.group(1)
        # Look around the date for words 'Purpose', 'Stage', 'Hearing'
        span_start = max(dt_match.start() - 60, 0)
        span_end = min(dt_match.end() + 80, len(text))
        window = text[span_start:span_end]
        purpose = None
        m2 = re.search(r"(Purpose|Stage)\s*[:\-]?\s*([A-Za-z0-9 ,./()_-]{3,60})", window, re.I)
        if m2:
            purpose = m2.group(2).strip()
        hearings.append({"date": dt, "purpose": purpose})

    return {
        "cnr": cnr_found,
        "case_number": case_no,
        "court_name": court_name,
        "hearings": hearings
    }


def parse_cause_list_html(html: str) -> List[Dict[str, Any]]:
    """
    Parses a cause-list result page (after you select state/district/court/date and click Civil/Criminal).
    Returns rows with serial, case_number_text, parties/counsel/purpose if visible.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: List[Dict[str, Any]] = []

    # Generic approach: find all tables; parse rows where there's a serial number.
    tables = soup.find_all("table")
    for table in tables:
        for tr in table.find_all("tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not tds or len(tds) < 2:
                continue

            # Try to detect a serial number in the first cell.
            serial = None
            if re.fullmatch(r"\d{1,4}", tds[0]):
                serial = tds[0]
            elif re.match(r"(?i)(sr\.?\s*no\.?|serial)", tds[0]):
                # likely a header row
                continue

            if serial:
                # Heuristic mapping:
                case_text = None
                court_text = None
                purpose = None
                # Commonly case number appears in 2nd or 3rd columns.
                for cell in tds[1:4]:
                    if re.search(r"\b\d{1,6}\/\d{4}\b", cell) or re.search(r"[A-Za-z]{1,10}\s*\d{1,6}\/\d{4}", cell):
                        case_text = cell
                        break
                # Look for purpose/stage
                for cell in tds:
                    if re.search(r"(?i)(purpose|stage|for hearing|listing)", cell):
                        purpose = cell
                        break
                # 'Court' sometimes shown at the top or near each block; as a fallback, scan any td
                for cell in tds:
                    if re.search(r"(?i)\bCourt\b", cell):
                        court_text = cell
                        break

                rows.append({
                    "serial": serial,
                    "case_text": case_text,
                    "purpose": purpose,
                    "court_info": court_text,
                    "raw_cells": tds
                })
    return rows


def find_case_in_cause_list(entries: List[Dict[str, Any]], case_key: str) -> Optional[Dict[str, Any]]:
    """
    Searches parsed cause-list entries for a case by a flexible key, e.g. "OS 123/2024".
    Returns the matched entry with serial & court.
    """
    # normalize key
    key_norm = re.sub(r"\s+", " ", case_key).strip().lower()

    for e in entries:
        big_text = " ".join(e.get("raw_cells") or [])
        big_text_norm = re.sub(r"\s+", " ", big_text).strip().lower()
        if key_norm in big_text_norm:
            return e

    return None


# === Scraper class ===

@dataclasses.dataclass
class RunResult:
    inputs: Dict[str, Any]
    case_overview: Optional[Dict[str, Any]]
    is_listed_today: Optional[bool]
    is_listed_tomorrow: Optional[bool]
    listing_details: Optional[Dict[str, Any]]
    cause_list: Optional[Dict[str, Any]]
    downloaded_files: List[str]
    task_run_at: str = dataclasses.field(default_factory=now_iso)


class ECourtsScraper:
    def __init__(
        self,
        headless: bool = False,
        browser: str = "chrome",
        out_dir: str = DEFAULT_OUT_DIR,
        download_dir: str = DEFAULT_DL_DIR,
        verbose: bool = False
    ) -> None:
        self.verbose = verbose
        self.out_dir = ensure_dir(out_dir)
        self.download_dir = ensure_dir(download_dir)

        self.driver = self._create_driver(browser=browser, headless=headless)
        self.wait = WebDriverWait(self.driver, 30)

        log(f"Initialized webdriver with browser={browser}, headless={headless}", self.verbose)

    def _create_driver(self, browser: str, headless: bool):
        browser = (browser or "chrome").lower()
        if browser == "firefox":
            options = FirefoxOptions()
            if headless:
                options.add_argument("-headless")
            driver = webdriver.Firefox(options=options)
        else:
            options = ChromeOptions()
            if headless:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1400,1000")
            driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        return driver

    # ---- Generic navigation helpers ----

    def open_home(self):
        log(f"Opening home: {ECOURTS_HOME}", self.verbose)
        self.driver.get(ECOURTS_HOME)
        self.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

    def open_causelist(self):
        log(f"Opening cause list page: {ECOURTS_CAUSELIST}", self.verbose)
        self.driver.get(ECOURTS_CAUSELIST)
        self.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

    def get_page_html(self) -> str:
        # allow dynamic page to settle a bit
        time.sleep(1.0)
        return self.driver.page_source

    def ask_user_to_continue(self, message: str = "Complete the form & CAPTCHA in the opened browser, then press ENTER here to continue..."):
        input(f"\n{message}\n")

    # ---- Data extraction flows ----

    def cnr_flow(self, cnr: str, download_pdf: bool = False) -> Tuple[Dict[str, Any], List[str]]:
        """
        Opens the home page, asks the user to submit a CNR search manually (solve CAPTCHA),
        then parses the resulting case status page. Optionally tries to download PDFs linked on the page.
        """
        self.open_home()
        print("\n[Browser] Please enter the CNR, solve CAPTCHA and click 'Search'.")
        print("[Terminal] When the results page is visible, press ENTER here to continue.")
        self.ask_user_to_continue()

        html = self.get_page_html()
        overview = parse_case_status_html(html)
        log(f"Parsed case overview: {overview}", self.verbose)

        downloaded: List[str] = []
        if download_pdf:
            # Try to find PDF links on the page and download with cookies.
            downloaded = self._download_pdfs_from_current_page()

        return overview, downloaded

    def causelist_flow(
        self,
        section: str = "Civil",
        download_pdf: bool = False
    ) -> Tuple[List[Dict[str, Any]], Optional[str], List[str]]:
        """
        Opens the cause list page, asks user to:
          - Select State/District/Court Complex/Court
          - Pick date
          - Solve CAPTCHA
          - Click Civil/Criminal
        Then parses the visible table into entries. Optionally downloads the cause list PDF if link is present.
        Returns: (entries, pdf_path_if_downloaded, downloaded_files)
        """
        self.open_causelist()
        print("\n[Browser] On the cause list page, please:")
        print("  1) Select State → District → Court Complex → Court Name")
        print("  2) Select the desired 'Cause List Date'")
        print(f"  3) Solve CAPTCHA and click '{section}' (Civil/Criminal)")
        print("[Terminal] When the cause list is visible, press ENTER here to continue.")
        self.ask_user_to_continue()

        html = self.get_page_html()
        entries = parse_cause_list_html(html)
        log(f"Parsed {len(entries)} cause list entries", self.verbose)

        pdf_path = None
        downloaded_files: List[str] = []
        if download_pdf:
            # Try to find a PDF link and download it.
            pdf_path = self._download_first_pdf_from_current_page()
            if pdf_path:
                downloaded_files.append(pdf_path)

        return entries, pdf_path, downloaded_files

    # ---- Downloads ----

    def _download_pdfs_from_current_page(self) -> List[str]:
        """
        Collect all <a> elements with 'pdf' in href from the current page and download them using 'requests'
        with the Selenium session cookies attached.
        """
        anchors = self.driver.find_elements(By.TAG_NAME, "a")
        hrefs = []
        for a in anchors:
            try:
                href = a.get_attribute("href")
                if href and "pdf" in href.lower():
                    hrefs.append(href)
            except Exception:
                continue

        unique_hrefs = sorted(set(hrefs))
        log(f"Found {len(unique_hrefs)} pdf-like links", self.verbose)

        downloaded_paths: List[str] = []
        if not unique_hrefs:
            return downloaded_paths

        # Build a cookie jar from Selenium
        s = requests.Session()
        for c in self.driver.get_cookies():
            s.cookies.set(c["name"], c["value"], domain=c.get("domain"))

        for url in unique_hrefs:
            try:
                r = s.get(url, timeout=60)
                if r.status_code == 200 and r.headers.get("content-type", "").lower().startswith("application/pdf"):
                    fname = sanitize_filename(os.path.basename(url.split("?")[0]) or f"case_{int(time.time())}.pdf")
                    path = os.path.join(self.download_dir, fname)
                    ensure_dir(os.path.dirname(path))
                    with open(path, "wb") as f:
                        f.write(r.content)
                    downloaded_paths.append(path)
                    log(f"Downloaded PDF: {path}", self.verbose)
            except Exception as ex:
                log(f"Failed to download {url}: {ex}", self.verbose)

        return downloaded_paths

    def _download_first_pdf_from_current_page(self) -> Optional[str]:
        files = self._download_pdfs_from_current_page()
        return files[0] if files else None

    # ---- Clean up ----

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass


# === Decision helpers (listed today/tomorrow) ===

def is_date_in_hearings(hearings: List[Dict[str, str]], target_ddmmyyyy: str) -> bool:
    t = target_ddmmyyyy
    return any(h.get("date") == t for h in hearings or [])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ecourts_scraper",
        description="Fetch case details and cause lists from eCourts (manual CAPTCHA)."
    )
    group_inp = parser.add_mutually_exclusive_group(required=True)
    group_inp.add_argument("--cnr", type=str, help="16-char CNR number (e.g., MHAU019999992015)")
    group_inp.add_argument("--case-type", type=str, help="Case type code/text, e.g., OS (use with --case-number and --year)")

    parser.add_argument("--case-number", type=str, help="Case number (required with --case-type)")
    parser.add_argument("--year", type=str, help="Case year (required with --case-type)")
    parser.add_argument("--today", action="store_true", help="Check today's listing")
    parser.add_argument("--tomorrow", action="store_true", help="Check tomorrow's listing")

    parser.add_argument("--causelist", action="store_true", help="Open cause list flow and parse entries")
    parser.add_argument("--section", choices=["Civil", "Criminal"], default="Civil", help="Cause list section to click")
    parser.add_argument("--download-pdf", action="store_true", help="Download PDF(s) linked on the result page")
    parser.add_argument("--download-causelist", action="store_true", help="Download cause list PDF if present")

    parser.add_argument("--browser", choices=["chrome", "firefox"], default="chrome")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="Output directory for JSON/TXT")
    parser.add_argument("--downloads", default=DEFAULT_DL_DIR, help="Directory for downloaded PDFs")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.case_type and (not args.case_number or not args.year):
        parser.error("--case-type requires --case-number and --year")

    inputs = {
        "cnr": args.cnr,
        "case_type": args.case_type,
        "case_number": args.case_number,
        "year": args.year,
        "today": args.today,
        "tomorrow": args.tomorrow,
        "causelist": args.causelist,
        "section": args.section,
        "download_pdf": args.download_pdf,
        "download_causelist": args.download_causelist,
        "browser": args.browser,
        "headless": args.headless
    }

    scraper = ECourtsScraper(
        headless=args.headless,
        browser=args.browser,
        out_dir=args.out,
        download_dir=args.downloads,
        verbose=args.verbose
    )

    result = RunResult(
        inputs=inputs,
        case_overview=None,
        is_listed_today=None,
        is_listed_tomorrow=None,
        listing_details=None,
        cause_list=None,
        downloaded_files=[]
    )

    try:
        # Step 1: If CNR flow requested, parse the case status page
        if args.cnr:
            overview, dl = scraper.cnr_flow(cnr=args.cnr, download_pdf=args.download_pdf)
            result.case_overview = overview
            result.downloaded_files.extend(dl)

        # Step 2: Cause list flow (recommended for serial + court name)
        entries: List[Dict[str, Any]] = []
        cause_pdf = None
        cl_downloads: List[str] = []
        if args.causelist or args.today or args.tomorrow:
            entries, cause_pdf, cl_downloads = scraper.causelist_flow(
                section=args.section,
                download_pdf=args.download_causelist
            )
            result.downloaded_files.extend(cl_downloads)

        # Step 3: Determine listing today/tomorrow
        # Strategy:
        #   - If we have cause-list entries and case details (type/number/year),
        #     search the entries for the case to extract serial & court.
        #   - If only CNR overview is available (no cause-list), use hearings to
        #     infer date match (serial cannot be determined without cause list).
        case_key = None
        if args.case_type and args.case_number and args.year:
            case_key = f"{args.case_type} {args.case_number}/{args.year}"
        elif result.case_overview and result.case_overview.get("case_number"):
            case_key = result.case_overview["case_number"]

        listing_details = None
        listed_today = None
        listed_tomorrow = None

        if entries and case_key:
            # Check today's/tomorrow's entries by relying on you having selected that date in the browser.
            matched = find_case_in_cause_list(entries, case_key)
            if matched:
                listing_details = {
                    "serial": matched.get("serial"),
                    "court": matched.get("court_info"),
                    "case_text": matched.get("case_text"),
                    "purpose": matched.get("purpose")
                }
                # If you clicked 'today' in the browser, that implies listed_today=True.
                listed_today = True if args.today else None
                listed_tomorrow = True if args.tomorrow else None
            else:
                if args.today:
                    listed_today = False
                if args.tomorrow:
                    listed_tomorrow = False

        # As a fallback, if we have hearings from the case overview, try date match.
        if result.case_overview and (args.today or args.tomorrow):
            hearings = result.case_overview.get("hearings", [])
            if args.today and listed_today is None:
                listed_today = is_date_in_hearings(hearings, date_str_ist(0))
            if args.tomorrow and listed_tomorrow is None:
                listed_tomorrow = is_date_in_hearings(hearings, date_str_ist(1))

        # Save cause-list snapshot (parsed table)
        if entries:
            result.cause_list = {
                "count": len(entries),
                "entries": entries,
                "pdf_path": cause_pdf
            }

        result.is_listed_today = listed_today
        result.is_listed_tomorrow = listed_tomorrow
        result.listing_details = listing_details

        # ---- Persist results ----
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"result_{stamp}"
        json_path = os.path.join(args.out, f"{base}.json")
        txt_path = os.path.join(args.out, f"{base}.txt")

        save_json(dataclasses.asdict(result), json_path)

        # Human-readable text summary
        lines = []
        lines.append(f"Run at (UTC): {result.task_run_at}")
        lines.append(f"Inputs: {json.dumps(inputs)}")
        if result.case_overview:
            lines.append(f"Case Overview: {json.dumps(result.case_overview, ensure_ascii=False)}")
        if result.is_listed_today is not None:
            lines.append(f"Listed Today: {result.is_listed_today}")
        if result.is_listed_tomorrow is not None:
            lines.append(f"Listed Tomorrow: {result.is_listed_tomorrow}")
        if result.listing_details:
            lines.append(f"Listing Details: {json.dumps(result.listing_details, ensure_ascii=False)}")
        if result.cause_list:
            lines.append(f"Cause List: count={result.cause_list.get('count')} pdf={result.cause_list.get('pdf_path')}")
        if result.downloaded_files:
            lines.append(f"Downloaded Files: {result.downloaded_files}")

        save_text("\n".join(lines) + "\n", txt_path)

        # ---- Console output ----
        print("\n=== eCourts Scraper Result ===")
        print(f"Saved JSON: {json_path}")
        print(f"Saved Text: {txt_path}")
        if result.listing_details:
            print(f"Serial: {result.listing_details.get('serial')}")
            print(f"Court : {result.listing_details.get('court')}")
        elif result.is_listed_today or result.is_listed_tomorrow:
            print("Case appears to be listed by date (based on hearings), but serial/court requires cause list.")
        else:
            print("Case not found in the selected cause list / date, or details unavailable.")

        if result.downloaded_files:
            print(f"Downloaded: {result.downloaded_files}")

        return 0

    except KeyboardInterrupt:
        print("\nAborted by user.")
        return 130
    except Exception as ex:
        print(f"\nError: {ex}")
        return 1
    finally:
        scraper.close()


if __name__ == "__main__":
    raise SystemExit(main())
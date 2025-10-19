
# eCourts Web Scraping Project

A lightweight, **manual-CAPTCHA–friendly** scraper for India's eCourts portal. It helps you:

- Open the official eCourts pages in a real browser (Chrome/Firefox via Selenium)
- Manually complete the CAPTCHA and any form selections
- Parse the **Case Status** page (by CNR) for core details and hearing dates
- Parse **Cause List** tables (Civil/Criminal) to check if a case appears on a selected date
- **Optionally download PDF(s)** linked from result pages (case documents or cause lists)
- Save a structured JSON and a human-readable TXT summary for each run

> ⚠️ **Important**: This script requires *manual* interaction for CAPTCHA and form inputs. It **does not** bypass any protections.

---

## Table of Contents
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command-Line Usage](#command-line-usage)
- [Examples](#examples)
- [Outputs](#outputs)
- [How It Works](#how-it-works)
- [Troubleshooting & Tips](#troubleshooting--tips)
- [Ethical & Legal Notes](#ethical--legal-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Prerequisites
- **Python**: 3.9 or newer recommended
- **Browsers**: Google Chrome or Mozilla Firefox
- **Drivers**: Selenium can auto-manage drivers in recent versions. If it fails, install the appropriate driver (ChromeDriver/GeckoDriver) and ensure it is on your PATH.

### Python dependencies
Install required packages:

```bash
pip install selenium beautifulsoup4 requests lxml
```

---

## Installation
Clone your repository and place the script inside it (already done if you see this README). The main file is:

```
Mani Web Scraping Project.py
```

> Because the filename contains spaces, use quotes when running it from the terminal.

---

## Quick Start
Run by **CNR** (Case Status) and check **today's** cause list:

```bash
python "Mani Web Scraping Project.py" --cnr MHAU019999992015 --today --browser chrome --headless --verbose
```

You will see a browser window (headless mode still opens a session) for manual CAPTCHA. After you complete the form in the browser, return to the terminal and press **ENTER** when prompted. The script then parses the page and writes JSON/TXT outputs to `./outputs/`.

---

## Command-Line Usage

```text
usage: ecourts_scraper [-h] (--cnr CNR | --case-type CASE_TYPE) [--case-number CASE_NUMBER]
                       [--year YEAR] [--today] [--tomorrow] [--causelist]
                       [--section {Civil,Criminal}] [--download-pdf]
                       [--download-causelist] [--browser {chrome,firefox}] [--headless]
                       [--out OUT] [--downloads DOWNLOADS] [--verbose]

Fetch case details and cause lists from eCourts (manual CAPTCHA).

options:
  -h, --help            show this help message and exit
  --cnr CNR             16-char CNR number (e.g., MHAU019999992015)
  --case-type CASE_TYPE Case type code/text, e.g., OS (use with --case-number and --year)
  --case-number CASE_NUMBER
                        Case number (required with --case-type)
  --year YEAR           Case year (required with --case-type)
  --today               Check today's listing
  --tomorrow            Check tomorrow's listing
  --causelist           Open cause list flow and parse entries
  --section {Civil,Criminal}
                        Cause list section to click (default: Civil)
  --download-pdf        Download PDF(s) linked on the result page
  --download-causelist  Download cause list PDF if present
  --browser {chrome,firefox}
  --headless            Run browser in headless mode
  --out OUT             Output directory for JSON/TXT (default: ./outputs)
  --downloads DOWNLOADS Directory for downloaded PDFs (default: ./downloads)
  --verbose             Verbose console logging
```

> **Notes**
> - `--case-type/--case-number/--year` identify the case text used to match rows inside a parsed cause list.
> - `--today`/`--tomorrow` assume you selected that exact date on the eCourts page before pressing ENTER.

---

## Examples
1) **Case Status by CNR only** (parse overview; optionally download PDFs linked on the page):
```bash
python "Mani Web Scraping Project.py" --cnr MHAU019999992015 --download-pdf --browser chrome
```

2) **Cause List parsing** for a chosen court/date (after you select filters in the browser):
```bash
python "Mani Web Scraping Project.py" --causelist --section Civil --download-causelist --browser firefox --headless
```

3) **Find a specific case** (OS 123/2024) in the cause list you opened in the browser, and mark whether it is listed **today**:
```bash
python "Mani Web Scraping Project.py" --case-type OS --case-number 123 --year 2024 --today --causelist --browser chrome
```

---

## Outputs
Each run writes timestamped files to the `--out` directory (default `./outputs`). Example filenames:

```
outputs/result_20250101_101500.json
outputs/result_20250101_101500.txt
```

**JSON** contains:
```json
{
  "inputs": { ... },
  "case_overview": {
    "cnr": "MHAU019999992015",
    "case_number": "OS 123/2024",
    "court_name": "...",
    "hearings": [ { "date": "20-10-2025", "purpose": "..." } ]
  },
  "is_listed_today": true,
  "is_listed_tomorrow": null,
  "listing_details": {
    "serial": "17",
    "court": "Court: ...",
    "case_text": "OS 123/2024",
    "purpose": "..."
  },
  "cause_list": {
    "count": 42,
    "entries": [ { "serial": "1", "case_text": "...", "purpose": "...", "court_info": "...", "raw_cells": ["..."] } ],
    "pdf_path": "downloads/causelist_2025-10-20.pdf"
  },
  "downloaded_files": [ "downloads/....pdf" ],
  "task_run_at": "2025-10-20T00:00:00Z"
}
```

**TXT** is a human-readable summary with the most important fields.

Downloaded PDFs (if any) are stored under `--downloads` (default `./downloads`).

---

## How It Works
1. **Launch browser & navigate:** The script opens the official eCourts entry points:
   - Home: `https://services.ecourts.gov.in/ecourtindia_v6/`
   - Cause List: `https://services.ecourts.gov.in/ecourtindia_v6/?p=cause_list/index`
2. **Manual step:** You complete the form selection and the CAPTCHA in the real browser.
3. **Parsing:** After you press ENTER in the terminal, the script captures the current page HTML and uses **BeautifulSoup** and heuristics to extract key fields (case details, hearings; cause list rows).
4. **Downloads (optional):** The script looks for links to PDFs and downloads them with session cookies via **requests**.
5. **Save results:** It writes a structured JSON and a TXT summary to `./outputs/` with a timestamped name.

---

## Troubleshooting & Tips
- **Driver errors**: If Selenium can't find a driver, install ChromeDriver/GeckoDriver manually and ensure it is on PATH. Alternatively, upgrade Selenium to benefit from auto driver management.
- **Headless issues** (Linux/CI): Add headless mode (`--headless`). The script also configures Chrome with `--no-sandbox`, `--disable-dev-shm-usage`, and a fixed window size.
- **Heuristic parsing**: Page layouts may vary. The parser aims to be resilient, but some fields can be `null` if the structure differs.
- **Date assumptions**: `--today` and `--tomorrow` checks rely on you having selected that date on the page before pressing ENTER, or on hearing dates parsed from Case Status.
- **Filename with spaces**: Keep the quotes when invoking the script.

---

## Ethical & Legal Notes
- Use responsibly and respect the website's **Terms of Use** and **robots/policies**.
- Do **not** automate CAPTCHA solving or attempt to bypass security.
- Download only documents you are permitted to access.
- Rate-limit your usage and avoid burdening public services.

---

## Contributing
Issues and pull requests are welcome. For substantial changes, please open an issue first to discuss what you would like to change.

---

## License
This project is licensed under the **MIT License** by default. If you prefer a different license, update the `LICENSE` file and this README section accordingly.

"""
Fetch the US Monetary Policy Event-Study Database (USMPD) workbook from the
San Francisco Fed. Source: Acosta, Ajello, Bauer, Loria, and Miranda-Agrippino
(2025), "Financial Market Effects of FOMC Communication," FRBSF WP 2025-30.
https://www.frbsf.org/research-and-insights/data-and-indicators/us-monetary-policy-event-study-database/

Usage:
    python3 01_fetch_usmpd.py [--date YYYYMMDD] [--force]
"""
import argparse
from datetime import date
from pathlib import Path

from lib_fetch import fetch_if_missing

URL = "https://www.frbsf.org/wp-content/uploads/USMPD.xlsx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.data_dir) / f"{args.date}_usmpd.xlsx"
    fetch_if_missing(URL, out_path, args.force)


if __name__ == "__main__":
    main()

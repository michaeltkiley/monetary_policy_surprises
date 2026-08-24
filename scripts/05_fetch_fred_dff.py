"""
Fetch the daily effective federal funds rate (DFF) from FRED, for the Rate
Expectations tab -- the trailing history that connects into the ZQ-implied
forward curve.

Usage:
    python3 05_fetch_fred_dff.py [--date YYYYMMDD] [--force]
"""
import argparse
from datetime import date
from pathlib import Path

from lib_fetch import fetch_if_missing

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.data_dir) / f"{args.date}_fred_dff.csv"
    fetch_if_missing(URL, out_path, args.force)


if __name__ == "__main__":
    main()

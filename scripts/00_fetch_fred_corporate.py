"""
Fetch daily 20-yr Treasury and Moody's Aaa/Baa corporate bond yields from
FRED, for the FRL (2016) replication.

Usage:
    python3 07_fetch_fred_corporate.py [--date YYYYMMDD] [--force]
"""
import argparse
from datetime import date
from pathlib import Path

from lib_fetch import fetch_if_missing

SERIES = ["DGS10", "DGS20", "DAAA", "DBAA"]
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(SERIES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.data_dir) / f"{args.date}_fred_corporate.csv"
    fetch_if_missing(URL, out_path, args.force)


if __name__ == "__main__":
    main()

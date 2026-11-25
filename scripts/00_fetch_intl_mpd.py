"""
Fetch the euro-area and UK counterparts of USMPD.
  EA-MPD: Altavilla, Brugnolini, Gurkaynak, Motto, Ragusa (2019), ECB.
  UKMPD:  Braun, Miranda-Agrippino, Saha (2024), Bank of England.

Usage: python3 00_fetch_intl_mpd.py [--date YYYYMMDD] [--force]
"""
import argparse
from datetime import date
from pathlib import Path

from lib_fetch import fetch_if_missing

URLS = {
    "eampd": "https://www.ecb.europa.eu/pub/pdf/annex/Dataset_EA-MPD.xlsx",
    "ukmpd": "https://www.bankofengland.co.uk/-/media/boe/files/working-paper/2023/"
             "measuring-monetary-policy-in-the-uk-the-ukmpesd.xlsx",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    for key, url in URLS.items():
        out = Path(args.data_dir) / f"{args.date}_{key}.xlsx"
        try:
            fetch_if_missing(url, out, args.force)
        except Exception as e:  # bankofengland.co.uk can refuse scripted downloads
            print(f"Could not fetch {key}: {e}\n  Download it in a browser from {url}\n  and save it as {out}")


if __name__ == "__main__":
    main()

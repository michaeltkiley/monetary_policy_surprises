"""
Fetch + cache USD SOFR and fed funds OIS trades from DTCC's free public swap
tape (CFTC Public Price Dissemination, rates asset class).

Source: daily cumulative files published by DTCC's Swap Data Repository,
    https://<bucket>.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_YYYY_MM_DD.zip
where <bucket> comes from https://pddata.dtcc.com/ppd/api/general/bucketname.
Each file holds every rates swap reported that UTC day, all currencies.

This script only fetches and caches. It keeps every cleared USD SOFR /
fed-funds OIS trade (any start date, packages included, spreads kept) and
writes one small CSV per day. Curves are derived from the cache by
07_build_dtcc_ois_curves.py, so filtering rules can change without refetching.

Minimal cleaning at fetch time:
  * USD; underlier in the SOFR / fed funds OIS sets; new trades (NEWT) or
    corrections (CORR); event type TRAD or blank
  * Cleared in I, Y, C; fixed rate present
  * drop same-file originals that a CORR/EROR/REVI row points to

RETENTION: DTCC keeps only a rolling ~2 years of these files (as of Nov 2026
the oldest was 2024-11-22; older keys return 403). Run this regularly so each
day is cached before the source file expires -- the cache in dtcc/trades/ is
the archive.

Usage:
    python3 06_fetch_dtcc_ois.py --start 2024-11-22 [--end YYYY-MM-DD]
        [--data-dir ../data] [--force] [--sleep 0.2]

Idempotent: days already cached are skipped unless --force. Weekends are
skipped; days with no file (holidays; recent days not yet published) are
skipped, and only days older than 4 days are recorded as permanently absent.
"""
import argparse
import io
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

API = "https://pddata.dtcc.com/ppd/api"

PRODUCTS = {
    "USD-SOFR-COMPOUND": "sofr",
    "USD-SOFR-OIS Compound": "sofr",
    "USD-Federal Funds-OIS Compound": "ffr",
    "USD-Federal Funds-H.15-OIS-COMPOUND": "ffr",
}
CLEARED_OK = {"I", "Y", "C"}
USECOLS = [
    "Dissemination Identifier", "Original Dissemination Identifier",
    "Action type", "Event type", "Execution Timestamp", "Effective Date",
    "Expiration Date", "Cleared", "Package indicator",
    "Notional amount-Leg 1", "Notional currency-Leg 1", "Fixed rate-Leg 1",
    "Spread-Leg 1", "Spread-Leg 2", "UPI Underlier Name", "Platform identifier",
]
OUT_COLS = ["dissem_id", "exec_utc", "eff", "exp", "product", "package",
            "notional", "rate_pct", "spread1", "spread2", "cleared", "platform"]


def bucket_name() -> str:
    r = requests.get(f"{API}/general/bucketname", timeout=30)
    r.raise_for_status()
    name = r.text.strip()
    if not name or "<" in name:
        raise RuntimeError(f"unexpected bucket name response: {name[:80]!r}")
    return name


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(",", "").str.replace("+", ""), errors="coerce")


def filter_trades(df: pd.DataFrame) -> pd.DataFrame:
    corr = df["Action type"].isin(["CORR", "EROR", "REVI"])
    dead = set(df.loc[corr, "Original Dissemination Identifier"]) - {""}
    m = (
        (df["Notional currency-Leg 1"] == "USD")
        & df["UPI Underlier Name"].isin(PRODUCTS)
        & df["Action type"].isin(["NEWT", "CORR"])
        & df["Event type"].isin(["TRAD", ""])
        & df["Cleared"].isin(CLEARED_OK)
        & (df["Fixed rate-Leg 1"] != "")
        & ~df["Dissemination Identifier"].isin(dead)
    )
    d = df.loc[m]
    out = pd.DataFrame({
        "dissem_id": d["Dissemination Identifier"],
        "exec_utc": pd.to_datetime(d["Execution Timestamp"], utc=True, errors="coerce"),
        "eff": pd.to_datetime(d["Effective Date"], errors="coerce").dt.date,
        "exp": pd.to_datetime(d["Expiration Date"], errors="coerce").dt.date,
        "product": d["UPI Underlier Name"].map(PRODUCTS),
        "package": d["Package indicator"].str.upper() == "TRUE",
        "notional": _num(d["Notional amount-Leg 1"]),
        "rate_pct": pd.to_numeric(d["Fixed rate-Leg 1"], errors="coerce") * 100,
        "spread1": pd.to_numeric(d["Spread-Leg 1"], errors="coerce"),
        "spread2": pd.to_numeric(d["Spread-Leg 2"], errors="coerce"),
        "cleared": d["Cleared"],
        "platform": d["Platform identifier"],
    })
    return out.dropna(subset=["exec_utc", "eff", "exp", "rate_pct"])[OUT_COLS]


def fetch_day(session, bucket, d: date):
    """Filtered trades for day d, or None if DTCC has no file for it."""
    name = f"CFTC_CUMULATIVE_RATES_{d:%Y_%m_%d}"
    url = f"https://{bucket}.s3.amazonaws.com/cftc/eod/{name}.zip"
    for attempt in range(4):
        try:
            r = session.get(url, timeout=120)
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), dtype=str, encoding="utf-8-sig",
                         usecols=lambda c: c in USECOLS, keep_default_na=False)
    return filter_trades(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2, help="seconds between downloads")
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    trade_dir = Path(args.data_dir) / "dtcc" / "trades"
    trade_dir.mkdir(parents=True, exist_ok=True)
    missing_log = trade_dir / "_no_file.txt"
    known_missing = set(missing_log.read_text().split()) if missing_log.exists() else set()

    bucket = bucket_name()
    session = requests.Session()
    fetched = skipped = absent = 0
    d = start
    while d <= end:
        ds = d.isoformat()
        if d.weekday() < 5:
            if ((trade_dir / f"{ds}.csv").exists() or ds in known_missing) and not args.force:
                skipped += 1
            else:
                trades = fetch_day(session, bucket, d)
                if trades is None:
                    if (date.today() - d).days > 4:
                        known_missing.add(ds)
                    absent += 1
                    print(f"{ds}: no file (holiday or not yet published)")
                else:
                    trades.to_csv(trade_dir / f"{ds}.csv", index=False)
                    fetched += 1
                    print(f"{ds}: {len(trades)} OIS trades cached")
                time.sleep(args.sleep)
        d += timedelta(days=1)
    missing_log.write_text("\n".join(sorted(known_missing)) + "\n")
    print(f"fetched {fetched}, already cached {skipped}, no file {absent}")


if __name__ == "__main__":
    main()

"""
Build a daily USD OIS "close" by tenor from DTCC's public swap-trade tape
(CFTC Public Price Dissemination, rates asset class) -- a free, end-of-day
complement to the futures/options-based expectations.

Source: daily cumulative files published by DTCC's Swap Data Repository,
    https://<bucket>.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_YYYY_MM_DD.zip
where <bucket> comes from https://pddata.dtcc.com/ppd/api/general/bucketname.
Each file holds every rates swap reported that UTC day, all currencies.

These are TRADES, not dealer quotes. Per day and tenor we keep clean,
spot-starting, par, outright SOFR (or fed funds) OIS trades and report the
median fixed rate over (a) the whole day and (b) a closing window
(default 15:00-17:00 America/New_York). `close` is the closing-window median
when it has at least --min-trades trades, else NaN (no silent fallback).

Cleaning rules (conservative; see README for caveats):
  * USD, new trades (NEWT) or corrections (CORR); drop any same-file original
    that a CORR/EROR/REVI row points to
  * cleared / intend-to-clear (Cleared in I, Y, C), not part of a package
  * fixed rate present, no spread on either leg (par swap)
  * effective date 0-5 days after execution (spot-starting)
  * maturity within a small tolerance of a standard tenor

Usage:
    python3 06_fetch_dtcc_ois.py --start 2024-01-01 [--end 2024-12-31]
        [--product sofr|ffr] [--window-start 15:00 --window-end 17:00]
        [--min-trades 3] [--data-dir ../data] [--force]

Outputs (under --data-dir):
    dtcc/trades_<product>/YYYY-MM-DD.csv   filtered trades per day (cache)
    dtcc_<product>_ois_daily.csv           daily tenor table (rebuilt each run)

RETENTION: DTCC keeps only a rolling ~2 years of these files (as of Nov 2026
the oldest was 2024-11-22; older keys return 403). Run this regularly (the
daily workflow is fine) so each day's filtered trades are cached before the
source file expires -- the cache in dtcc/trades_<product>/ is the archive.

Idempotent: days already cached are skipped unless --force. Weekends are
skipped; days DTCC has no file for (holidays) are recorded and skipped.
"""
import argparse
import io
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

API = "https://pddata.dtcc.com/ppd/api"
ET = ZoneInfo("America/New_York")

PRODUCTS = {
    "sofr": {"USD-SOFR-COMPOUND", "USD-SOFR-OIS Compound"},
    "ffr": {"USD-Federal Funds-OIS Compound", "USD-Federal Funds-H.15-OIS-COMPOUND"},
}

# label -> maturity in years
TENORS = {
    "1M": 1 / 12, "3M": 0.25, "6M": 0.5, "9M": 0.75, "1Y": 1, "18M": 1.5,
    "2Y": 2, "3Y": 3, "4Y": 4, "5Y": 5, "7Y": 7, "10Y": 10, "12Y": 12,
    "15Y": 15, "20Y": 20, "30Y": 30,
}
CLEARED_OK = {"I", "Y", "C"}
USECOLS = [
    "Dissemination Identifier", "Original Dissemination Identifier",
    "Action type", "Event type", "Execution Timestamp", "Effective Date",
    "Expiration Date", "Cleared", "Package indicator",
    "Notional amount-Leg 1", "Notional currency-Leg 1", "Fixed rate-Leg 1",
    "Fixed rate-Leg 2", "Spread-Leg 1", "Spread-Leg 2", "UPI Underlier Name",
]


def bucket_name() -> str:
    r = requests.get(f"{API}/general/bucketname", timeout=30)
    r.raise_for_status()
    name = r.text.strip()
    if not name or "<" in name:
        raise RuntimeError(f"unexpected bucket name response: {name[:80]!r}")
    return name


def tenor_label(years: float):
    best, best_err = None, None
    for lab, t in TENORS.items():
        err = abs(years - t)
        if err <= 0.02 + 0.02 * t and (best_err is None or err < best_err):
            best, best_err = lab, err
    return best


def fetch_day(session, bucket, d: date, underliers):
    """Return filtered-trade DataFrame for day d, or None if no file exists."""
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
    return filter_trades(df, underliers)


def filter_trades(df: pd.DataFrame, underliers) -> pd.DataFrame:
    # originals superseded by a correction/error/revision in the same file
    corr = df["Action type"].isin(["CORR", "EROR", "REVI"])
    dead = set(df.loc[corr, "Original Dissemination Identifier"]) - {""}
    m = (
        (df["Notional currency-Leg 1"] == "USD")
        & df["UPI Underlier Name"].isin(underliers)
        & df["Action type"].isin(["NEWT", "CORR"])
        & df["Event type"].isin(["TRAD", ""])
        & df["Cleared"].isin(CLEARED_OK)
        & (df["Package indicator"].str.upper() == "FALSE")
        & (df["Fixed rate-Leg 1"] != "")
        & (df["Spread-Leg 1"] == "")
        & (df["Spread-Leg 2"] == "")
        & ~df["Dissemination Identifier"].isin(dead)
    )
    df = df.loc[m].copy()
    if df.empty:
        return df.assign(tenor=[], rate_pct=[], notional=[], exec_utc=[])
    ex = pd.to_datetime(df["Execution Timestamp"], utc=True, errors="coerce")
    eff = pd.to_datetime(df["Effective Date"], errors="coerce")
    mat = pd.to_datetime(df["Expiration Date"], errors="coerce")
    lag = (eff - ex.dt.tz_localize(None).dt.normalize()).dt.days
    yrs = (mat - eff).dt.days / 365.25
    df["exec_utc"] = ex
    df["rate_pct"] = pd.to_numeric(df["Fixed rate-Leg 1"], errors="coerce") * 100
    df["notional"] = pd.to_numeric(
        df["Notional amount-Leg 1"].str.replace(",", "").str.replace("+", ""),
        errors="coerce")
    df["tenor"] = [tenor_label(y) if pd.notna(y) else None for y in yrs]
    keep = lag.between(0, 5) & df["tenor"].notna() & df["rate_pct"].between(-1, 15)
    keep &= df["exec_utc"].notna()
    out = df.loc[keep, ["Dissemination Identifier", "exec_utc", "tenor",
                        "rate_pct", "notional", "UPI Underlier Name"]]
    return out.rename(columns={"Dissemination Identifier": "dissem_id",
                               "UPI Underlier Name": "underlier"})


def daily_table(trade_dir: Path, w0: str, w1: str, min_trades: int) -> pd.DataFrame:
    h0, m0 = map(int, w0.split(":"))
    h1, m1 = map(int, w1.split(":"))
    rows = []
    for f in sorted(trade_dir.glob("*.csv")):
        d = date.fromisoformat(f.stem)
        t = pd.read_csv(f, parse_dates=["exec_utc"])
        if t.empty:
            continue
        local = t["exec_utc"].dt.tz_convert(ET)
        mins = local.dt.hour * 60 + local.dt.minute
        t["in_win"] = (mins >= h0 * 60 + m0) & (mins < h1 * 60 + m1)
        for tenor, g in t.groupby("tenor"):
            w = g[g["in_win"]]
            wn = w["notional"].fillna(0)
            rows.append({
                "date": d, "tenor": tenor,
                "n_all": len(g), "median_all": g["rate_pct"].median(),
                "n_win": len(w),
                "median_win": w["rate_pct"].median() if len(w) else np.nan,
                "wavg_win": (np.average(w["rate_pct"], weights=wn)
                             if len(w) and wn.sum() > 0 else np.nan),
                "notional_win": wn.sum(),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["close"] = out["median_win"].where(out["n_win"] >= min_trades)
    order = {k: i for i, k in enumerate(TENORS)}
    return out.sort_values(["date", "tenor"], key=lambda s: s.map(order)
                           if s.name == "tenor" else s).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--product", choices=PRODUCTS, default="sofr")
    ap.add_argument("--window-start", default="15:00", help="ET, HH:MM")
    ap.add_argument("--window-end", default="17:00", help="ET, HH:MM")
    ap.add_argument("--min-trades", type=int, default=3)
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2, help="seconds between downloads")
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    trade_dir = Path(args.data_dir) / "dtcc" / f"trades_{args.product}"
    trade_dir.mkdir(parents=True, exist_ok=True)
    missing_log = trade_dir / "_no_file.txt"
    known_missing = set(missing_log.read_text().split()) if missing_log.exists() else set()

    bucket = bucket_name()
    session = requests.Session()
    underliers = PRODUCTS[args.product]
    fetched = skipped = absent = 0
    d = start
    while d <= end:
        ds = d.isoformat()
        if d.weekday() < 5:
            cached = (trade_dir / f"{ds}.csv").exists()
            if (cached or ds in known_missing) and not args.force:
                skipped += 1
            else:
                trades = fetch_day(session, bucket, d, underliers)
                if trades is None:
                    # recent days may simply not be published yet: don't cache
                    if (date.today() - d).days > 4:
                        known_missing.add(ds)
                    absent += 1
                    print(f"{ds}: no file (holiday or not yet published)")
                else:
                    trades.to_csv(trade_dir / f"{ds}.csv", index=False)
                    fetched += 1
                    print(f"{ds}: {len(trades)} {args.product} OIS trades kept")
                time.sleep(args.sleep)
        d += timedelta(days=1)
    missing_log.write_text("\n".join(sorted(known_missing)) + "\n")

    table = daily_table(trade_dir, args.window_start, args.window_end, args.min_trades)
    out_path = Path(args.data_dir) / f"dtcc_{args.product}_ois_daily.csv"
    table.to_csv(out_path, index=False)
    print(f"fetched {fetched}, cached {skipped}, no-file {absent}; "
          f"wrote {out_path} ({len(table)} date-tenor rows)")


if __name__ == "__main__":
    main()

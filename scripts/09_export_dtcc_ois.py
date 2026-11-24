"""
Export the OIS-implied expected funds rate curve history for the dashboard.

Runs the 'knots' spline fit from 08_fit_dtcc_ois_spline.py on every cached day
(06 -> 07 -> 08 inputs) and writes ../ois_derived/history.json:

    {
      "unit": 0.01,                       # values are integer hundredths of a percent
      "horizon_months": 60,
      "model": "knots",
      "dates": ["2024-11-21", ...],       # fitted dates (ascending)
      "curves": {"YYYY-MM-DD": [v0, v1, ..., v60]},
      "fomc_dates": ["2025-01-29", ...]   # meeting calendar inferred from the tape
    }

For a date d, v0 is the expected rate (EFFR-equivalent, 1-month forward,
percent*100) starting on d itself; v_k (k >= 1) is the same starting on the
first day of the k-th month after d's month -- the same calendar grid the
CME fed funds path uses (monthly average, one point per month), so the two
can be compared point for point.

Usage:
    python3 09_export_dtcc_ois.py [--data-dir ../data] [--out-dir ../ois_derived]
                                  [--out-file PATH]        # overrides --out-dir/history.json

In production this runs in the private cme-rate-data repo's workflow (which also
stores the raw per-day trades durably) and writes export/ois_history.json; the
public dashboard build copies that to ois_derived/history.json.
"""
import argparse
import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HORIZON_MONTHS = 60


def load_fit_module():
    spec = importlib.util.spec_from_file_location(
        "fit08", Path(__file__).with_name("08_fit_dtcc_ois_spline.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def first_of_month_after(d: pd.Timestamp, k: int) -> pd.Timestamp:
    m = d.month - 1 + k
    return pd.Timestamp(year=d.year + m // 12, month=m % 12 + 1, day=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--out-dir", default="../ois_derived")
    ap.add_argument("--out-file", default=None, help="exact output path (default <out-dir>/history.json)")
    ap.add_argument("--lam", type=float, default=0.3)
    ap.add_argument("--lam-near", type=float, default=0.003)
    args = ap.parse_args()
    dd = Path(args.data_dir)

    f = load_fit_module()
    par, fwd, path, basis, meet, effr = f.load_inputs(dd)
    fits, diag = f.fit_all_knots(par, fwd, basis, effr, meet, args.lam, args.lam_near)
    if not fits:
        raise SystemExit("no OIS curves could be fitted")

    curves = {}
    for d in sorted(fits):
        entry = fits[d]
        vals = []
        for k in range(HORIZON_MONTHS + 1):
            start = d if k == 0 else first_of_month_after(d, k)
            a = (start - d).days
            if a + 30 >= f.HDAYS:
                break
            vals.append(int(round(f.h_interval(entry, a, a + 30) * 100)))
        curves[d.strftime("%Y-%m-%d")] = vals

    out = {
        "unit": 0.01,
        "horizon_months": HORIZON_MONTHS,
        "model": "knots",
        "dates": list(curves.keys()),
        "curves": curves,
        "fomc_dates": [pd.Timestamp(m).strftime("%Y-%m-%d") for m in meet],
    }
    p = Path(args.out_file) if args.out_file else Path(args.out_dir) / "history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {p} ({p.stat().st_size / 1024:.0f} KB): {len(curves)} dates "
          f"{out['dates'][0]}..{out['dates'][-1]}, median fit RMSE "
          f"par {diag.rmse_par_bp.median():.2f}bp / meeting-dated {diag.rmse_int_bp.median():.2f}bp")


if __name__ == "__main__":
    main()

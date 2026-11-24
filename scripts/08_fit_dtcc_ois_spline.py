"""
Fit a smooth SOFR OIS forward curve (penalized cubic B-spline) to the daily
spot-starting SOFR OIS par rates built by 07_build_dtcc_ois_curves.py.

Why: the par swaps are deep from 6M to 10Y, but the forward rates implied by
bootstrapping them are noisy (differencing amplifies tenor-to-tenor noise).
Here the instantaneous forward curve f(t) is the unknown; each day we choose
the spline coefficients so the curve REPRICES the observed par rates, subject
to a curvature penalty (integral of f''^2). Smoothness is a modelling
assumption, not a market observation: it deliberately ignores meeting-by-
meeting steps, so this curve is meant for the 1-5Y horizon, with the near-term
step path taken from fed funds futures / meeting-dated OIS.

Model: D(t) = exp(-int_0^t f); an annual-pay, act/360 par swap has
    S(T) = (1 - D(T)) / sum_i alpha_i D(t_i)       (single payment if T <= 1Y).
f is a cubic B-spline on [0, 10Y]; fit is nonlinear least squares in basis
coefficients, residuals in bp, weighted by trade count (and widened when the
close came from the all-day fallback window), plus the curvature penalty.

Outputs (--data-dir):
  dtcc_fitted_curve_daily.csv   per date x horizon (0-5Y, quarterly): instantaneous
                                forward, 3M simple forward (act/360) and the
                                EFFR-equivalent 3M forward (SOFR basis from 07)
  dtcc_fit_diagnostics_daily.csv   per date: pillars used, fit RMSE / max error (bp)

Usage:
    python3 08_fit_dtcc_ois_spline.py [--data-dir ../data] [--lam 0.3]
        [--max-horizon 5] [--report]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline
from scipy.optimize import least_squares

TENOR_YRS = {"6M": 0.5, "9M": 0.75, "1Y": 1, "18M": 1.5, "2Y": 2, "3Y": 3, "4Y": 4,
             "5Y": 5, "7Y": 7, "10Y": 10}
DELTA = 365 / 360
TMAX = 10.0
GRID_STEP = 1 / 24          # integration grid (months/2): hits every pillar date
DEGREE = 3
INTERIOR = [0.5, 1, 1.5, 2, 3, 4, 5, 6, 8]


class Basis:
    """B-spline basis for f(t) with cumulative-integral and curvature matrices."""

    def __init__(self):
        t = np.concatenate([[0] * (DEGREE + 1), INTERIOR, [TMAX] * (DEGREE + 1)])
        self.t = t
        self.n = len(t) - DEGREE - 1
        self.grid = np.arange(0, TMAX + 1e-9, GRID_STEP)
        eye = np.eye(self.n)
        self.B = np.column_stack([BSpline(t, eye[j], DEGREE, extrapolate=False)(self.grid)
                                  for j in range(self.n)])
        self.B = np.nan_to_num(self.B)
        self.B[-1, -1] = 1.0  # right-endpoint convention
        # cumulative integral of each basis function on the grid (trapezoid)
        inc = (self.B[1:] + self.B[:-1]) / 2 * GRID_STEP
        self.I = np.vstack([np.zeros(self.n), np.cumsum(inc, axis=0)])
        # second derivative of f on the grid (penalty)
        self.B2 = np.nan_to_num(np.column_stack([
            BSpline(t, eye[j], DEGREE, extrapolate=False).derivative(2)(self.grid)
            for j in range(self.n)]))

    def idx(self, years):
        return int(round(years / GRID_STEP))


BASIS = Basis()


def pay_times(T):
    if T <= 1:
        return [T]
    ts = list(np.arange(1, int(np.floor(T + 1e-9)) + 1, dtype=float))
    if abs(ts[-1] - T) > 1e-9:
        ts.append(T)
    return ts


SCHED = {k: pay_times(T) for k, T in TENOR_YRS.items()}


def discount(c):
    return np.exp(-(BASIS.I @ c))


def par_rate(D, T, sched):
    prev, ann = 0.0, 0.0
    for ti in sched:
        ann += DELTA * (ti - prev) * D[BASIS.idx(ti)]
        prev = ti
    return (1 - D[BASIS.idx(T)]) / ann


def fit_day(obs, lam, c0):
    """obs: DataFrame(tenor, rate_pct, sigma_bp). Returns coeffs, residuals_bp."""
    tenors = list(obs["tenor"])
    y = obs["rate_pct"].to_numpy() / 100
    sig = obs["sigma_bp"].to_numpy()
    pen_w = np.sqrt(lam * GRID_STEP) * 1e4 * 1e-1   # f'' in bp/yr^2, scaled

    def resid(c):
        D = discount(c)
        r = np.array([(par_rate(D, TENOR_YRS[k], SCHED[k]) - yy) * 1e4 / s
                      for k, yy, s in zip(tenors, y, sig)])
        pen = pen_w * (BASIS.B2 @ c)
        return np.concatenate([r, pen])

    sol = least_squares(resid, c0, method="trf", x_scale=0.01)
    D = discount(sol.x)
    res_bp = np.array([(par_rate(D, TENOR_YRS[k], SCHED[k]) - yy) * 1e4
                       for k, yy in zip(tenors, y)])
    return sol.x, res_bp


def sigma_bp(n, window):
    n_eff = np.minimum(n, 25)
    var = 1.0 + (8.0 / np.sqrt(n_eff)) ** 2
    var = var + np.where(window == "00:00-24:00", 9.0, 0.0)
    return np.sqrt(var)


def fit_all(par, lam, only_dates=None):
    par = par[par["tenor"].isin(TENOR_YRS)].copy()
    par["sigma_bp"] = sigma_bp(par["n"].to_numpy(), par["window"].to_numpy())
    par = par.rename(columns={"median": "rate_pct"})
    out, diag = {}, []
    c_prev = None
    for d, g in par.groupby("date"):
        if only_dates is not None and d not in only_dates:
            continue
        have = set(g["tenor"])
        if len(g) < 5 or not ({"6M", "9M", "1Y", "18M"} & have) or not ({"5Y", "7Y"} & have) \
                or "10Y" not in have:
            continue
        c0 = c_prev if c_prev is not None else np.full(BASIS.n, g["rate_pct"].mean() / 100)
        c, res = fit_day(g, lam, c0)
        c_prev = c
        out[d] = c
        diag.append({"date": d, "n_pillars": len(g), "rmse_bp": float(np.sqrt(np.mean(res**2))),
                     "max_abs_bp": float(np.abs(res).max()),
                     **{f"res_{k}": r for k, r in zip(g["tenor"], res)}})
    return out, pd.DataFrame(diag)


def curve_table(coefs, basis_by_date, max_h):
    rows = []
    for d, c in coefs.items():
        D = discount(c)
        f = BASIS.B @ c
        b = basis_by_date.get(d, np.nan)
        for h in np.arange(0, max_h + 1e-9, 0.25):
            i, j = BASIS.idx(h), BASIS.idx(h + 0.25)
            f3 = (D[i] / D[j] - 1) / (DELTA * 0.25) * 100
            rows.append({"date": d, "horizon_yrs": round(h, 2), "inst_fwd_pct": f[i] * 100,
                         "fwd3m_sofr_pct": f3, "fwd3m_effr_equiv_pct": f3 - b})
    return pd.DataFrame(rows)


def interval_rate(c, a_yrs, b_yrs):
    """Simple act/360 average forward over [a, b] (years), interpolating
    log-discount factors at the exact dates (no grid snapping)."""
    logD = -(BASIS.I @ c)
    da = np.exp(np.interp(a_yrs, BASIS.grid, logD))
    db = np.exp(np.interp(b_yrs, BASIS.grid, logD))
    return (da / db - 1) / (DELTA * (b_yrs - a_yrs)) * 100


def report(par, coefs, diag, data_dir, lam):
    dd = Path(data_dir)
    print(f"\n=== fit quality (lambda={lam}) ===")
    print("days fitted: %d | RMSE bp: median %.2f, p90 %.2f | max |err|: median %.2f, p90 %.2f" % (
        len(diag), diag.rmse_bp.median(), diag.rmse_bp.quantile(.9),
        diag.max_abs_bp.median(), diag.max_abs_bp.quantile(.9)))
    rc = [c for c in diag.columns if c.startswith("res_")]
    print("residual RMSE by tenor (bp):", {c[4:]: round(float(np.sqrt((diag[c] ** 2).mean())), 2)
                                           for c in rc})

    # smoothness: daily change in fitted 3M forwards vs bootstrapped annual forwards
    tab = curve_table(coefs, {}, 5)
    pv = tab.pivot(index="date", columns="horizon_yrs", values="fwd3m_sofr_pct")
    print("\nfitted 3M forward, daily change sd (bp):",
          {h: round(float(pv[h].diff().std() * 100), 1) for h in [0.5, 1.0, 2.0, 3.0, 4.0, 5.0] if h in pv})
    path = pd.read_csv(dd / "dtcc_expected_path_daily.csv", parse_dates=["date", "node_start", "node_end"])
    sd = {}
    for n in ["fwd 1Y-2Y", "fwd 2Y-3Y", "fwd 4Y-5Y"]:
        s = path[path["node"] == n].set_index("date")["sofr"]
        sd[n] = round(float(s.diff().std() * 100), 1)
    print("bootstrapped annual forward, daily change sd (bp), for comparison:", sd)

    # out-of-sample overlap with meeting-dated fed funds OIS (not used in the fit)
    basis = path.dropna(subset=["basis"]).groupby("date")["basis"].first()
    m = path[(path["meeting_no"] >= 1) & (path["source"] == "ffr_ois")].copy()
    rows = []
    for _, r in m.iterrows():
        c = coefs.get(r["date"])
        if c is None:
            continue
        a = (r["node_start"] - r["date"]).days / 365.25
        b = (r["node_end"] - r["date"]).days / 365.25
        if a < 0.02 or b > TMAX:
            continue
        fit = interval_rate(c, a, b) - basis.get(r["date"], np.nan)
        rows.append({"meeting_no": r["meeting_no"], "h": a, "diff_bp": (fit - r["ffr"]) * 100})
    o = pd.DataFrame(rows)
    if len(o):
        o["bucket"] = pd.cut(o["h"], [0, .1, .25, .5, 1.1], labels=["<1.2M", "1.2-3M", "3-6M", "6-13M"])
        print("\nfitted curve minus meeting-dated fed funds OIS (bp), same interval, EFFR-equivalent:")
        print(o.groupby("bucket", observed=True)["diff_bp"].agg(
            n="size", mean="mean", mae=lambda s: s.abs().mean(), sd="std").round(1).to_string())

    # realized: fitted 1y average vs next-365d realized EFFR
    effr = pd.read_csv(dd / "fred_dff.csv")
    effr.columns = ["date", "v"]
    effr["date"] = pd.to_datetime(effr["date"])
    effr = pd.to_numeric(effr["v"], errors="coerce").set_axis(effr["date"]).dropna()
    errs = []
    for d, c in coefs.items():
        end = d + pd.Timedelta(days=365)
        if end > effr.index.max():
            continue
        fit = interval_rate(c, 0.0, 1.0) - basis.get(d, np.nan)
        errs.append((fit - effr[(effr.index >= d) & (effr.index < end)].mean()) * 100)
    e = pd.Series(errs).dropna()
    print("\nfitted 0-1Y average (EFFR-equiv) vs realized next-365d EFFR: n=%d, mean %.1fbp, MAE %.1fbp"
          " (bootstrapped annual forward: MAE 10.1bp)" % (len(e), e.mean(), e.abs().mean()))

    # lambda sensitivity on a subsample of days
    dates = sorted(coefs)[::10]
    print("\nlambda sensitivity (every 10th day): RMSE bp | mean |fwd3m diff| vs chosen lambda at 3Y, 5Y (bp)")
    ref = curve_table({d: coefs[d] for d in dates}, {}, 5)
    for lam2 in [lam / 10, lam, lam * 10, lam * 100]:
        co, dg = fit_all(par, lam2, only_dates=set(dates))
        t2 = curve_table(co, {}, 5)
        mg = t2.merge(ref, on=["date", "horizon_yrs"], suffixes=("", "_ref"))
        dif = {h: round(float(((mg[mg.horizon_yrs == h].fwd3m_sofr_pct - mg[mg.horizon_yrs == h].fwd3m_sofr_pct_ref).abs().mean()) * 100), 1)
               for h in [3.0, 5.0]}
        print(f"  lam={lam2:g}: RMSE {dg.rmse_bp.mean():.2f} | diff vs chosen {dif}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--lam", type=float, default=0.3, help="curvature penalty weight")
    ap.add_argument("--max-horizon", type=float, default=5.0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    dd = Path(args.data_dir)

    par = pd.read_csv(dd / "dtcc_sofr_par_daily.csv", parse_dates=["date"])
    coefs, diag = fit_all(par, args.lam)
    if not coefs:
        raise SystemExit("no days could be fitted")
    basis = {}
    pth = dd / "dtcc_expected_path_daily.csv"
    if pth.exists():
        pb = pd.read_csv(pth, parse_dates=["date"]).dropna(subset=["basis"])
        basis = pb.groupby("date")["basis"].first().to_dict()
    curve_table(coefs, basis, args.max_horizon).to_csv(dd / "dtcc_fitted_curve_daily.csv", index=False)
    diag.to_csv(dd / "dtcc_fit_diagnostics_daily.csv", index=False)
    print(f"fitted {len(coefs)} days (lambda={args.lam}); median RMSE {diag.rmse_bp.median():.2f}bp")
    if args.report:
        report(par, coefs, diag, dd, args.lam)


if __name__ == "__main__":
    main()

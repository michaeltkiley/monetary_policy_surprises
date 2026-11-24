"""
Fit a forward curve (meeting steps near term + penalized cubic B-spline beyond) to the daily
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

TENOR_YRS = {"1M": 1 / 12, "3M": 0.25, "6M": 0.5, "9M": 0.75, "1Y": 1, "18M": 1.5, "2Y": 2, "3Y": 3, "4Y": 4,
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
            rows.append({"date": d, "horizon_yrs": round(h, 2), "inst_fwd_pct": float(360 * np.expm1(f[i] / 365) * 100),
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


# --------------------------------------------------------------------------
# Hybrid model: step function (one level per FOMC meeting) for the first
# ~13 months, joined to the smooth spline beyond. Fits meeting-dated swaps,
# spot-starting par swaps (1M-10Y) and the published EFFR together.
# --------------------------------------------------------------------------
HDAYS = 3650               # daily grid out to 10Y
TCUT = 400                 # days: steps before, spline from here on
SPL_INTERIOR = [1.5, 2, 3, 4, 5, 6, 8]
SPL_LO = 1.0


class HBasis:
    def __init__(self):
        t = np.concatenate([[SPL_LO] * (DEGREE + 1), SPL_INTERIOR, [TMAX] * (DEGREE + 1)])
        self.n = len(t) - DEGREE - 1
        eye = np.eye(self.n)
        yrs = (np.arange(HDAYS) + 0.5) / 365
        self.B = np.nan_to_num(np.column_stack([
            BSpline(t, eye[j], DEGREE, extrapolate=False)(yrs) for j in range(self.n)]))
        pen_days = np.arange(TCUT, HDAYS, 10)
        pyrs = (pen_days + 0.5) / 365
        self.pen_days = pen_days
        self.B2 = np.nan_to_num(np.column_stack([
            BSpline(t, eye[j], DEGREE, extrapolate=False).derivative(2)(pyrs)
            for j in range(self.n)]))
        self.Bcut = self.B[TCUT - 1]   # spline value at the join


HB = HBasis()


def design(meet_off):
    """Cumulative-integral design matrix CM (HDAYS+1 x p): F(day) = CM @ theta.
    Columns: one step level per piece [edges[k], edges[k+1]) then spline coefs."""
    edges = [0] + list(meet_off) + [TCUT]
    K = len(edges) - 1
    M = np.zeros((HDAYS, K + HB.n))
    for k in range(K):
        M[edges[k]:edges[k + 1], k] = 1.0
    M[TCUT:, K:] = HB.B[TCUT:]
    CM = np.vstack([np.zeros((1, M.shape[1])), np.cumsum(M, axis=0) / 365])
    return CM, K


def pay_days(T_days):
    if T_days <= 365:
        return [T_days]
    ts = list(range(365, T_days - 1, 365))
    if ts[-1] != T_days:
        ts.append(T_days)
    return ts


def h_sigma(n, window):
    n_eff = min(n, 25)
    var = 1.0 + (6.0 / np.sqrt(n_eff)) ** 2 + (9.0 if window == "00:00-24:00" else 0.0)
    return float(np.sqrt(var))


def build_obs(date, par_g, fwd_g, basis, effr_val):
    """Observation list in SOFR space. kinds: ('par', T_days, rate, sigma),
    ('int', a_days, b_days, rate, sigma)."""
    obs = []
    for _, r in par_g.iterrows():
        T = int(round(TENOR_YRS[r["tenor"]] * 365))
        obs.append(("par", T, r["median"] / 100, h_sigma(r["n"], r["window"])))
    for _, r in fwd_g.iterrows():
        a = (r["start"] - date).days
        b = (r["end"] - date).days
        if a < 0 or b > HDAYS or b <= a:
            continue
        rate = r["median"] + (basis if r["product"] == "ffr" else 0.0)
        obs.append(("int", a, b, rate / 100, h_sigma(r["n"], r["window"])))
    if effr_val is not None and np.isfinite(effr_val) and np.isfinite(basis):
        obs.append(("int", 0, 3, (effr_val + basis) / 100, 2.0))
    return obs


def model_obs(D, o):
    if o[0] == "par":
        T = o[1]
        prev, ann = 0, 0.0
        for t in pay_days(T):
            ann += DELTA * (t - prev) / 365 * D[t]
            prev = t
        return (1 - D[T]) / ann
    a, b = o[1], o[2]
    return (D[a] / D[b] - 1) / (DELTA * (b - a) / 365)


def fit_day_hybrid(date, obs, meet_off, lam, step_sigma_bp, theta0=None):
    CM, K = design(meet_off)
    sig = np.array([o[-1] for o in obs])
    y = np.array([o[-2] for o in obs])
    pen_w = np.sqrt(lam * 10 / 365) * 1e4 * 1e-1

    def predict(th):
        D = np.exp(-(CM @ th))
        return np.array([model_obs(D, o) for o in obs])

    def resid(th):
        r = (predict(th) - y) * 1e4 / sig
        steps = th[:K]
        dstep = np.diff(steps) * 1e4 / step_sigma_bp
        cont = np.array([(steps[-1] - HB.Bcut @ th[K:]) * 1e4 / 1.0])
        pen = pen_w * (HB.B2 @ th[K:])
        return np.concatenate([r, dstep, cont, pen])

    th0 = np.full(K + HB.n, float(np.mean(y))) if theta0 is None else theta0
    sol = least_squares(resid, th0, method="trf", x_scale=0.01)
    res_bp = (predict(sol.x) - y) * 1e4
    return sol.x, res_bp, CM, K


def fit_all_hybrid(par, fwd, basis_by_date, effr, meetings, lam, step_sigma_bp,
                   only_dates=None):
    fwd = fwd[fwd["kind"] == "meeting"]
    out, diag = {}, []
    pg = {d: g for d, g in par[par["tenor"].isin(TENOR_YRS)].groupby("date")}
    fg = {d: g for d, g in fwd.groupby("date")}
    prev = None
    for d in sorted(pg):
        if only_dates is not None and d not in only_dates:
            continue
        g = pg[d]
        have = set(g["tenor"])
        if len(g) < 5 or not ({"5Y", "7Y"} & have) or "10Y" not in have:
            continue
        b = basis_by_date.get(d, np.nan)
        if not np.isfinite(b):
            continue
        meet_off = [(m - d).days for m in meetings if 0 < (m - d).days < TCUT]
        ev = effr.reindex(effr.index.union([d])).ffill().get(d, np.nan) if effr is not None else None
        obs = build_obs(d, g, fg.get(d, fg_empty(fwd)), b, ev)
        th0 = prev if prev is not None and len(prev) == len(meet_off) + 1 + HB.n else None
        th, res, CM, K = fit_day_hybrid(d, obs, meet_off, lam, step_sigma_bp, th0)
        prev = th
        out[d] = (th, CM, K, meet_off, b)
        kinds = [o[0] for o in obs]
        pr = [r for r, k in zip(res, kinds) if k == "par"]
        ir = [r for r, k in zip(res, kinds) if k == "int"]
        diag.append({"date": d, "n_par": len(pr), "n_int": len(ir),
                     "rmse_par_bp": float(np.sqrt(np.mean(np.square(pr)))) if pr else np.nan,
                     "rmse_int_bp": float(np.sqrt(np.mean(np.square(ir)))) if ir else np.nan})
    return out, pd.DataFrame(diag)


def fg_empty(fwd):
    return fwd.iloc[0:0]


def h_interval(entry, a_days, b_days):
    th, CM, K, _, basis = entry
    D = np.exp(-(CM @ th))
    return ((D[a_days] / D[b_days] - 1) / (DELTA * (b_days - a_days) / 365)) * 100 - basis


def h_curve_table(fits, max_h):
    rows = []
    for d, entry in fits.items():
        for h in np.arange(0, max_h + 1e-9, 0.25):
            a, b = int(round(h * 365)), int(round((h + 0.25) * 365))
            if b > HDAYS:
                continue
            rows.append({"date": d, "horizon_yrs": round(h, 2),
                         "fwd3m_effr_equiv_pct": h_interval(entry, a, b)})
    return pd.DataFrame(rows)


def daily_act360(f):
    """Continuously compounded rate (365-day year) -> one-day act/360 rate,
    the quoting convention of EFFR/SOFR (decimal in, decimal out)."""
    return 360 * np.expm1(np.asarray(f) / 365)


def h_steps_table(fits):
    rows = []
    for d, (th, CM, K, meet_off, basis) in fits.items():
        edges = [0] + list(meet_off)
        for k in range(K):
            rows.append({"date": d, "step": k, "from": d + pd.Timedelta(days=edges[k]),
                         "level_effr_equiv_pct": daily_act360(th[k]) * 100 - basis})
    return pd.DataFrame(rows)


def load_inputs(dd):
    par = pd.read_csv(dd / "dtcc_sofr_par_daily.csv", parse_dates=["date"])
    fwd = pd.read_csv(dd / "dtcc_meeting_fwd_daily.csv", parse_dates=["date", "start", "end"])
    path = pd.read_csv(dd / "dtcc_expected_path_daily.csv",
                       parse_dates=["date", "node_start", "node_end"])
    basis = path.dropna(subset=["basis"]).groupby("date")["basis"].first().to_dict()
    meet = list(pd.read_csv(dd / "dtcc_meeting_calendar.csv",
                            parse_dates=["meeting_date"])["meeting_date"])
    e = pd.read_csv(dd / "fred_dff.csv")
    e.columns = ["d", "v"]
    e["d"] = pd.to_datetime(e["d"])
    effr = pd.to_numeric(e["v"], errors="coerce").set_axis(e["d"]).dropna()
    return par, fwd, path, basis, meet, effr


def run_hybrid(args, dd):
    par, fwd, path, basis, meet, effr = load_inputs(dd)
    fits, diag = fit_all_hybrid(par, fwd, basis, effr, meet, args.lam, args.step_sigma)
    if not fits:
        raise SystemExit("no days could be fitted")
    h_curve_table(fits, args.max_horizon).to_csv(dd / "dtcc_fitted_curve_daily.csv", index=False)
    h_steps_table(fits).to_csv(dd / "dtcc_fitted_steps_daily.csv", index=False)
    diag.to_csv(dd / "dtcc_fit_diagnostics_daily.csv", index=False)
    print(f"hybrid fit: {len(fits)} days; median RMSE par {diag.rmse_par_bp.median():.2f}bp, "
          f"meeting-dated {diag.rmse_int_bp.median():.2f}bp")
    if args.report:
        report_hybrid(par, fwd, path, basis, meet, effr, fits, diag, args)


def report_hybrid(par, fwd, path, basis, meet, effr, fits, diag, args):
    print("\n=== hybrid fit quality ===")
    print("pricing error RMSE (bp): par median %.2f p90 %.2f | meeting-dated median %.2f p90 %.2f" % (
        diag.rmse_par_bp.median(), diag.rmse_par_bp.quantile(.9),
        diag.rmse_int_bp.median(), diag.rmse_int_bp.quantile(.9)))
    # realized-EFFR test by horizon window, hybrid vs smooth-only vs raw fed funds OIS nodes
    smooth, _ = fit_all(par, args.lam)
    rows = []
    wins = [(0, 91), (91, 182), (182, 273), (273, 365)]
    for d, entry in fits.items():
        for a, b in wins:
            end = d + pd.Timedelta(days=b)
            if end > effr.index.max():
                continue
            real = effr[(effr.index >= d + pd.Timedelta(days=a)) & (effr.index < end)].mean()
            row = {"date": d, "win": f"{a//30}-{b//30}M",
                   "hybrid": h_interval(entry, a, b) - real}
            if d in smooth:
                row["smooth"] = interval_rate(smooth[d], a / 365, b / 365) - basis.get(d, np.nan) - real
            rows.append(row)
    r = pd.DataFrame(rows)
    # raw FF OIS meeting nodes: nearest-interval comparison not window aligned; skip
    print("\nerror vs realized mean EFFR over the window (implied - realized, pct pts -> bp)")
    g = r.groupby("win")[["hybrid", "smooth"]]
    tab = pd.concat({"bias_bp": g.mean() * 100, "mae_bp": g.agg(lambda s: s.abs().mean()) * 100,
                     "n": g.count()}, axis=1)
    print(tab.round(1).to_string())
    # in-sample agreement with raw meeting-dated FF OIS
    m = path[(path["meeting_no"] >= 1) & (path["source"] == "ffr_ois")]
    rows = []
    for _, x in m.iterrows():
        en = fits.get(x["date"])
        if en is None:
            continue
        a, b = (x["node_start"] - x["date"]).days, (x["node_end"] - x["date"]).days
        if a < 1 or b > HDAYS:
            continue
        rows.append({"h": a, "diff_bp": (h_interval(en, a, b) - x["ffr"]) * 100})
    o = pd.DataFrame(rows)
    o["bucket"] = pd.cut(o["h"], [0, 30, 91, 182, 400], labels=["<1M", "1-3M", "3-6M", "6-13M"])
    print("\nhybrid minus raw meeting-dated fed funds OIS (bp, in sample):")
    print(o.groupby("bucket", observed=True)["diff_bp"].agg(
        n="size", mean="mean", mae=lambda s: s.abs().mean()).round(1).to_string())
    # 12-month-ahead average and long-end stability
    tab5 = h_curve_table(fits, 5).pivot(index="date", columns="horizon_yrs",
                                        values="fwd3m_effr_equiv_pct")
    print("\ndaily change sd of fitted 3M forward (bp):",
          {h: round(float(tab5[h].diff().std() * 100), 1) for h in [0.25, 0.5, 1.0, 2.0, 3.0, 5.0] if h in tab5})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--lam", type=float, default=0.3, help="curvature penalty weight")
    ap.add_argument("--max-horizon", type=float, default=5.0)
    ap.add_argument("--model", choices=["hybrid", "smooth"], default="hybrid",
                    help="hybrid = meeting steps for ~13M then spline (default); "
                         "smooth = spline only, no near-term meeting data")
    ap.add_argument("--step-sigma", type=float, default=30.0,
                    help="bp; prior sd of jumps between adjacent meeting steps")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    dd = Path(args.data_dir)
    if args.model == "hybrid":
        return run_hybrid(args, dd)

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

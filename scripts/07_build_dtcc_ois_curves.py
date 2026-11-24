"""
Derive OIS-based expected-policy-rate paths and a SOFR OIS par curve from the
trades cached by 06_fetch_dtcc_ois.py (DTCC public swap tape).

Outputs (in --data-dir):
  dtcc_meeting_calendar.csv       FOMC decision dates inferred from the tape
  dtcc_meeting_fwd_daily.csv      per day / product / interval: median swap rate
                                  over meeting-to-meeting (and spot-to-next-
                                  meeting) OIS trades, trade counts, window used
  dtcc_expected_path_daily.csv    expected policy-rate path per day: one node
                                  per FOMC interval + longer-dated pillar
                                  forwards, fed funds OIS preferred, SOFR OIS
                                  (basis-adjusted to EFFR) where fed funds is thin
  dtcc_sofr_par_daily.csv         SOFR OIS par curve (spot-starting, by tenor)
  dtcc_expected_curve_daily.csv   TEST combined curve: EFFR now, meeting nodes to
                                  ~1Y (fed funds OIS), annual SOFR OIS forwards
                                  from 1Y out to --max-years

Method
  * Trade date = execution date in America/New_York; "close" = median over the
    15:00-17:00 ET window, widened to 13:00-17:00 ET and then to the whole day
    when fewer than --min-trades fall in the narrower window; the window used is
    flagged in the `window` column (all-day values can be stale by the close).
    Fewer than --min-trades even all day -> no value (never silently filled).
  * Meeting-dated swaps: cleared, no spread, start/end exactly on consecutive
    inferred meeting dates (packages included -- see --exclude-packages and the
    --report diagnostics comparing package vs outright rates).
  * Node 0 ("now") is the published EFFR (FRED DFF), because swaps from spot to
    the next meeting date rarely trade exactly (3% of days). Those "stub"
    swaps are still written to dtcc_meeting_fwd_daily.csv (kind = stub).
  * Fed funds OIS references EFFR; SOFR OIS trades a few bp away. SOFR-based
    intervals are converted to an EFFR-equivalent by subtracting a trailing
    median SOFR-minus-fed-funds basis estimated from intervals where both trade.
  * Beyond the meeting nodes, annual forwards (0-1Y, 1Y-2Y, ... up to --max-years)
    are bootstrapped from the SOFR OIS par curve (annual fixed leg, act/360,
    par rates interpolated linearly across tenors), then basis-adjusted. SOFR
    OIS is far deeper than fed funds OIS beyond ~6 months (hundreds of swaps a
    day at 2-10Y vs a handful), so this is the long-horizon source.

Usage:
    python3 07_build_dtcc_ois_curves.py [--data-dir ../data] [--min-trades 3]
        [--exclude-packages] [--report]
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

WINDOWS = [("15:00", "17:00"), ("13:00", "17:00"), ("00:00", "24:00")]
OFF_MARKET_BP_PAR = 15   # drop par-curve trades >15bp from the day/tenor median
OFF_MARKET_BP_FWD = 15   # same for meeting-interval trades
TENORS = {"1Y": 1, "18M": 1.5, "2Y": 2, "3Y": 3, "4Y": 4, "5Y": 5, "7Y": 7,
          "10Y": 10, "15Y": 15, "20Y": 20, "30Y": 30}


def load_cache(data_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(data_dir / "dtcc" / "trades" / "2*.csv")))
    if not files:
        raise SystemExit(f"no cached trades in {data_dir}/dtcc/trades -- run 06 first")
    t = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    t["exec_utc"] = pd.to_datetime(t["exec_utc"], utc=True, format="ISO8601")
    t["eff"] = pd.to_datetime(t["eff"])
    t["exp"] = pd.to_datetime(t["exp"])
    et = t["exec_utc"].dt.tz_convert("America/New_York")
    t["date"] = et.dt.tz_localize(None).dt.normalize()
    t["mins"] = et.dt.hour * 60 + et.dt.minute
    t["lag"] = (t["eff"] - t["date"]).dt.days
    t["len"] = (t["exp"] - t["eff"]).dt.days
    flat = (t["spread1"].fillna(0).abs() < 1e-9) & (t["spread2"].fillna(0).abs() < 1e-9)
    t = t[flat & (t["date"].dt.weekday < 5)].copy()
    # late-reported old trades appear in early files; keep days with real volume
    n = t.groupby("date").size()
    t = t[t["date"].isin(n[n >= 200].index)]
    return t


def infer_meetings(t: pd.DataFrame) -> list:
    """FOMC decision dates: frequent start AND end dates of forward-starting
    28-100d fed funds OIS trades, dropping anything within 5 weeks of a
    stronger candidate (IMM-style dates)."""
    # 28-100d excludes the recurring 21-day package strips that otherwise look
    # like extra "meetings"; real FOMC intervals run 5-10 weeks.
    f = t[(t["product"] == "ffr") & (t["lag"] > 5) & t["len"].between(28, 100)]
    c = pd.concat([f.groupby("eff").size().rename("ne"),
                   f.groupby("exp").size().rename("nx"),
                   f.groupby("eff")["date"].nunique().rename("de"),
                   f.groupby("exp")["date"].nunique().rename("dx")],
                  axis=1, sort=True).fillna(0)
    c["score"] = c["ne"] + c["nx"]
    # a real meeting is traded on many different days; one-day strips are not
    cand = c[(c["ne"] >= 5) & (c["nx"] >= 5) & (c["de"] >= 5) & (c["dx"] >= 5)
             & (c["score"] >= 30)].sort_values("score", ascending=False)
    keep = []
    for d, _ in cand["score"].items():
        if all(abs((d - k).days) >= 35 for k in keep):
            keep.append(d)
    return sorted(keep)


def time_mask(t, w):
    h0, m0 = map(int, w[0].split(":"))
    h1, m1 = map(int, w[1].split(":"))
    if (h1, m1) == (24, 0):
        h1, m1 = 23, 60
    return (t["mins"] >= h0 * 60 + m0) & (t["mins"] < h1 * 60 + m1)


def drop_off_market(t, keys, thr_bp, min_group=5):
    """Drop trades far from their own (day, bucket) all-day median.

    The tape contains off-market-coupon swaps (ladders at a fixed rate well away
    from par, 8-15% coupons, etc.) that would otherwise contaminate medians on
    thin buckets. Groups with fewer than min_group trades are left untouched
    (no reference to judge against)."""
    ref = t.groupby(keys)["rate_pct"].transform("median")
    cnt = t.groupby(keys)["rate_pct"].transform("size")
    bad = (cnt >= min_group) & ((t["rate_pct"] - ref).abs() * 100 > thr_bp)
    return t[~bad]


def window_median(t, keys, min_trades):
    """Per-key median/count over the narrow window, widening if too thin."""
    out = []
    done = None
    for i, w in enumerate(WINDOWS):
        g = t[time_mask(t, w)].groupby(keys).agg(
            n=("rate_pct", "size"), median=("rate_pct", "median"),
            notional=("notional", "sum"),
            pkg_share=("package", "mean"))
        g = g[g["n"] >= min_trades]
        g["window"] = f"{w[0]}-{w[1]}"
        if done is not None:
            g = g[~g.index.isin(done)]
        out.append(g)
        done = pd.concat(out).index
    res = pd.concat(out).reset_index()
    return res


def meeting_forwards(t, meetings, min_trades, exclude_packages):
    m = pd.DatetimeIndex(meetings)
    pairs = pd.DataFrame({"start": m[:-1], "end": m[1:]})
    pairs["node"] = np.arange(1, len(pairs) + 1)
    # nodes numbered per day later; here we only need pair membership
    pairset = set(zip(pairs["start"], pairs["end"]))
    meet_set = set(m)
    base = t[t["product"].isin(["ffr", "sofr"])]
    if exclude_packages:
        base = base[~base["package"]]
    # meeting-to-meeting: lag measured to the start meeting must be >= 0
    k = list(zip(base["eff"], base["exp"]))
    mm = base[[p in pairset for p in k] & (base["lag"] >= 0).to_numpy()].copy()
    mm["start"], mm["end"], mm["kind"] = mm["eff"], mm["exp"], "meeting"
    # stub: spot-starting swaps ending on a meeting date
    st = base[base["lag"].between(0, 5) & base["exp"].isin(meet_set)
              & (base["exp"] > base["date"])].copy()
    st["start"], st["end"], st["kind"] = st["date"], st["exp"], "stub"
    # stub trades should not already be meeting-to-meeting
    st = st[~st["eff"].isin(meet_set)]
    allt = pd.concat([mm, st], ignore_index=True)
    allt["end_key"] = allt["end"]
    # for stub, key on end meeting only (start varies by trade); for meeting use both
    allt["start_key"] = np.where(allt["kind"] == "stub", pd.NaT, allt["start"])
    allt["start_key"] = pd.to_datetime(allt["start_key"])
    keys = ["date", "product", "kind", "end_key", "start_key"]
    allt = drop_off_market(allt.assign(start_key=allt["start_key"].fillna(pd.Timestamp(0))),
                           keys, OFF_MARKET_BP_FWD)
    allt["start_key"] = allt["start_key"].replace(pd.Timestamp(0), pd.NaT)
    res = window_median(allt, keys, min_trades)
    res = res.rename(columns={"end_key": "end", "start_key": "start"})
    return res


def par_curve(t, min_trades):
    s = t[(t["product"] == "sofr") & t["lag"].between(0, 5) & ~t["package"]].copy()
    yrs = (s["exp"] - s["eff"]).dt.days / 365.25
    lab = pd.Series(index=s.index, dtype=object)
    best = pd.Series(np.inf, index=s.index)
    for name, y in TENORS.items():
        err = (yrs - y).abs()
        ok = (err <= 0.02 + 0.02 * y) & (err < best)
        lab[ok] = name
        best[ok] = err[ok]
    s["tenor"] = lab
    s = s.dropna(subset=["tenor"])
    s = s[s["rate_pct"].between(-1, 15)]
    s = drop_off_market(s, ["date", "tenor"], OFF_MARKET_BP_PAR)
    res = window_median(s, ["date", "tenor"], min_trades)
    order = {k: i for i, k in enumerate(TENORS)}
    return res.sort_values(["date", "tenor"], key=lambda c: c.map(order) if c.name == "tenor" else c)


def load_effr(data_dir):
    """Published EFFR (FRED DFF), fetched once; None if unavailable."""
    path = Path(data_dir) / "fred_dff.csv"
    try:
        if not path.exists():
            import requests
            r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF", timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
        d = pd.read_csv(path)
        d.columns = ["date", "dff"]
        d["date"] = pd.to_datetime(d["date"])
        return pd.to_numeric(d["dff"], errors="coerce").set_axis(d["date"]).dropna()
    except Exception as e:  # network or parse failure: skip node 0
        print(f"warning: could not load EFFR ({e}); node 0 omitted")
        return None


DELTA = 365 / 360  # act/360 accrual on an annual fixed leg


def bootstrap_forwards(par_by_years, max_years):
    """Annual SOFR OIS forwards from par rates by maturity (years -> percent).

    Par rates are linearly interpolated to a 1Y grid; discount factors are
    bootstrapped from  S_T * DELTA * sum_{i<=T} D_i = 1 - D_T  (annual fixed
    leg, act/360); the forward for year k is (D_{k-1}/D_k - 1)/DELTA.
    Returns (years_end, forward_pct) or None if the curve is too sparse
    (needs >=4 pillars, the shortest at <=18M, out to at least 2Y)."""
    pts = sorted(par_by_years.items())
    if len(pts) < 4 or pts[0][0] > 1.5 or pts[-1][0] < 2:
        return None
    ys = [a for a, _ in pts]
    rs = [b for _, b in pts]
    n = int(min(max_years, ys[-1]))
    grid = np.arange(1, n + 1)
    par = np.interp(grid, ys, rs) / 100
    D = [1.0]
    fw = []
    for k, S in enumerate(par, 1):
        Dk = (1 - S * DELTA * sum(D[1:])) / (1 + S * DELTA)
        fw.append((D[k - 1] / Dk - 1) / DELTA * 100)
        D.append(Dk)
    return list(grid), fw


def build_path(fwd, par, meetings, effr=None, max_years=10):
    """One row per date x node with the expected policy rate (EFFR-equivalent)."""
    f = fwd[fwd["kind"] == "meeting"].copy()
    # meeting node index = position of the interval's start meeting among
    # the meetings still ahead of that date
    m = pd.DatetimeIndex(meetings)
    # basis: SOFR - FFR on identical intervals, trailing 20-day median
    idx = ["date", "kind", "end", "start"]
    piv = f.pivot_table(index=idx, columns="product", values="median",
                        dropna=False).reset_index()
    win = f.pivot_table(index=idx, columns="product", values="window", aggfunc="first")
    nn = f.pivot_table(index=idx, columns="product", values="n", aggfunc="first")
    piv = piv.merge(win.add_prefix("win_").reset_index(), on=idx, how="left")
    piv = piv.merge(nn.add_prefix("n_").reset_index(), on=idx, how="left")
    for c in ["win_ffr", "win_sofr", "n_ffr", "n_sofr"]:
        if c not in piv:
            piv[c] = np.nan
    both = piv.dropna(subset=["ffr", "sofr"]).copy()
    both["basis"] = both["sofr"] - both["ffr"]
    daily_basis = both.groupby("date")["basis"].median()
    allday = pd.Index(sorted(set(f["date"]) | set(par["date"])))
    daily_basis = daily_basis.reindex(allday)
    basis = daily_basis.rolling(20, min_periods=3).median().ffill().bfill()
    basis.name = "basis_sofr_minus_ffr"

    piv["basis"] = piv["date"].map(basis)
    piv["sofr_adj"] = piv["sofr"] - piv["basis"]
    piv["exp_rate"] = piv["ffr"].where(piv["ffr"].notna(), piv["sofr_adj"])
    piv["source"] = np.where(piv["ffr"].notna(), "ffr_ois",
                             np.where(piv["sofr"].notna(), "sofr_ois_adj", None))
    piv = piv.dropna(subset=["exp_rate"])
    piv["window"] = np.where(piv["ffr"].notna(), piv["win_ffr"], piv["win_sofr"])
    piv["n_trades"] = np.where(piv["ffr"].notna(), piv["n_ffr"], piv["n_sofr"])
    piv["node_start"] = piv["start"].where(piv["kind"] == "meeting", piv["date"])
    piv["node_end"] = piv["end"]
    piv["node"] = "post-meeting " + piv["node_start"].dt.strftime("%Y-%m-%d")
    # attach meeting number: 0 = stub, k = k-th meeting ahead of the date
    def mnum(row):
        return int((m > row["date"]).sum() - (m >= row["node_start"]).sum() + 1)
    piv["meeting_no"] = piv.apply(mnum, axis=1)
    meet_nodes = piv[["date", "meeting_no", "node", "node_start", "node_end", "ffr",
                      "sofr", "sofr_adj", "exp_rate", "source", "window", "n_trades", "basis"]]

    # annual forwards bootstrapped from the SOFR OIS par curve
    p = par.pivot_table(index="date", columns="tenor", values="median")
    rows = []
    for d, r in p.iterrows():
        b = basis.get(d, np.nan)
        boot = bootstrap_forwards({TENORS[k]: v for k, v in r.items()
                                   if k in TENORS and pd.notna(v)}, max_years)
        if boot is None:
            continue
        for k, fr in zip(*boot):
            rows.append({"date": d, "meeting_no": np.nan, "node": f"fwd {k-1}Y-{k}Y",
                         "fwd_start_yrs": k - 1, "fwd_end_yrs": k,
                         "node_start": pd.NaT, "node_end": pd.NaT, "ffr": np.nan,
                         "sofr": fr, "sofr_adj": fr - b, "exp_rate": fr - b,
                         "source": "sofr_par_boot_adj", "window": np.nan,
                         "n_trades": np.nan, "basis": b})
    pil = pd.DataFrame(rows)
    parts = [meet_nodes, pil]
    if effr is not None:
        dts = pd.DatetimeIndex(sorted(set(meet_nodes["date"]) | set(par["date"])))
        cur = effr.reindex(effr.index.union(dts)).ffill().reindex(dts)
        parts.append(pd.DataFrame({"date": dts, "meeting_no": 0, "node": "now (EFFR)",
                                   "exp_rate": cur.to_numpy(), "source": "effr_published"}))
    return pd.concat(parts, ignore_index=True).sort_values(
        ["date", "meeting_no", "fwd_start_yrs", "node"]), basis


def report(t, fwd, path, basis, data_dir, min_trades):
    print("\n=== coverage: share of trading days with a value, by product x node ===")
    f = fwd.copy()
    days = t["date"].nunique()
    f["lab"] = np.where(f["kind"] == "stub", "stub", "meeting")
    print((f.groupby(["product", "lab"])["date"].nunique() / days).round(2).to_string())
    # coverage by meeting number (fed funds / combined)
    pm = path[path["meeting_no"].notna() & (path["meeting_no"] >= 1)]
    cov = (pm.groupby("meeting_no")["date"].nunique() / pm["date"].nunique()).round(2)
    print("\nexpected-path node coverage (share of days), by meeting_no:\n", cov.to_string())
    print("\nSOFR-minus-FF basis (bp): median %.1f, p10 %.1f, p90 %.1f" % (
        basis.median() * 100, basis.quantile(.1) * 100, basis.quantile(.9) * 100))
    # package vs outright on identical interval/day (fed funds)
    mt = t[(t["product"] == "ffr") & (t["lag"] >= 0)]
    k = mt[mt["len"].between(14, 120)].copy()
    k = k[k.groupby(["date", "eff", "exp"])["package"].transform("nunique") == 2]
    if len(k):
        g = k.groupby(["date", "eff", "exp", "package"])["rate_pct"].median().unstack()
        dd = (g[True] - g[False]).dropna() * 100
        print("\npackage minus outright, same day/interval, FF OIS: n=%d, mean %.2fbp, "
              "median %.2fbp, p10 %.1f, p90 %.1f" % (len(dd), dd.mean(), dd.median(),
                                                    dd.quantile(.1), dd.quantile(.9)))

    # forecast-error validation vs realized DFF
    dff_path = Path(data_dir) / "fred_dff.csv"
    if not dff_path.exists():
        import requests
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF", timeout=60)
        r.raise_for_status()
        dff_path.write_bytes(r.content)
    dff = pd.read_csv(dff_path)
    dff.columns = ["date", "dff"]
    dff["date"] = pd.to_datetime(dff["date"])
    dff = dff.set_index("date")["dff"].astype(float)
    rows = []
    for _, r in path[path["meeting_no"] >= 1].iterrows():
        s, e = r["node_start"], r["node_end"]
        if pd.isna(s) or e > dff.index.max():
            continue
        real = dff[(dff.index >= s) & (dff.index < e)].mean()
        rows.append({"date": r["date"], "h": (s - r["date"]).days, "src": r["source"],
                     "err": (r["exp_rate"] - real) * 100})
    v = pd.DataFrame(rows)
    if len(v):
        v["bucket"] = pd.cut(v["h"], [-1, 10, 30, 60, 120, 250],
                             labels=["0-10d", "11-30d", "31-60d", "61-120d", "121-250d"])
        print("\nforecast error (implied - realized mean EFFR over the interval), bp,"
              " by days from observation to interval start:")
        print(v.groupby(["src", "bucket"], observed=True)["err"].agg(
            n="size", mean="mean", mae=lambda s: s.abs().mean(), rmse=lambda s: np.sqrt((s**2).mean())
        ).round(1).to_string())
    if len(v):
        w = path[path["meeting_no"] >= 1][["date", "node_start", "window"]]
        v2 = v.merge(path[path["meeting_no"] >= 1][["date", "node_start", "window"]]
                     .assign(h=lambda x: (x["node_start"] - x["date"]).dt.days)
                     [["date", "h", "window"]], on=["date", "h"], how="left")
        print("\nforecast error by window used (ffr_ois only), bp:")
        print(v2[v2["src"] == "ffr_ois"].groupby("window")["err"].agg(
            n="size", mean="mean", mae=lambda s: s.abs().mean()).round(1).to_string())


def build_curve(path, horizon_days=365):
    """Combined expected-funds-rate test curve, one row per date x segment:
    node 0 published EFFR; meeting-by-meeting nodes starting within
    `horizon_days` (fed funds OIS preferred, SOFR OIS basis-adjusted where fed
    funds is thin); then annual SOFR OIS forwards from year 1 onward
    (EFFR-equivalent via a short-end basis -- an assumption for long horizons)."""
    now = path[path["meeting_no"] == 0].copy()
    now["horizon_start"], now["horizon_end"] = now["date"], now["date"]
    m = path[(path["meeting_no"] >= 1)
             & ((path["node_start"] - path["date"]).dt.days <= horizon_days)].copy()
    m["horizon_start"], m["horizon_end"] = m["node_start"], m["node_end"]
    a = path[path["fwd_start_yrs"].notna() & (path["fwd_start_yrs"] >= 1)].copy()
    a["horizon_start"] = a["date"] + pd.to_timedelta(a["fwd_start_yrs"] * 365.25, unit="D")
    a["horizon_end"] = a["date"] + pd.to_timedelta(a["fwd_end_yrs"] * 365.25, unit="D")
    c = pd.concat([now, m, a], ignore_index=True)
    c["years_ahead"] = ((c["horizon_start"] - c["date"]).dt.days / 365.25).round(2)
    c = c.rename(columns={"exp_rate": "expected_rate_pct", "basis": "basis_used_pct"})
    cols = ["date", "node", "horizon_start", "horizon_end", "years_ahead",
            "expected_rate_pct", "source", "window", "n_trades", "basis_used_pct"]
    return c[cols].sort_values(["date", "horizon_start"]).reset_index(drop=True)


def forward_report(path, effr):
    f1 = path[path["node"] == "fwd 0Y-1Y"].set_index("date")["exp_rate"]
    rows = []
    for d, v in f1.items():
        end = d + pd.Timedelta(days=365)
        if end > effr.index.max():
            continue
        rows.append({"date": d, "err": (v - effr[(effr.index >= d) & (effr.index < end)].mean()) * 100})
    e = pd.DataFrame(rows)
    print("\n=== bootstrapped 0-1Y SOFR OIS forward (EFFR-equiv) vs realized mean EFFR over next 365d ===")
    if len(e):
        print("n=%d days (overlapping), mean err %.1fbp, MAE %.1fbp, range %.0f..%.0f bp" % (
            len(e), e["err"].mean(), e["err"].abs().mean(), e["err"].min(), e["err"].max()))
    last = path[(path["date"] == path["date"].max()) & path["fwd_start_yrs"].notna()]
    print("latest-day annual forwards (EFFR-equiv %):",
          ", ".join(f"{int(a)}-{int(b)}Y {v:.2f}" for a, b, v in
                    zip(last["fwd_start_yrs"], last["fwd_end_yrs"], last["exp_rate"])))
    cov = (path[path["fwd_start_yrs"].notna()].groupby("fwd_end_yrs")["date"].nunique()
           / path["date"].nunique()).round(2)
    print("share of days with each annual forward:", cov.to_dict())


def treasury_report(par, data_dir):
    import requests
    out = {}
    for sid, lab in [("DGS2", "2Y"), ("DGS5", "5Y"), ("DGS10", "10Y"), ("DGS30", "30Y")]:
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=60)
        r.raise_for_status()
        d = pd.read_csv(pd.io.common.StringIO(r.text))
        d.columns = ["date", "y"]
        d["date"] = pd.to_datetime(d["date"])
        out[lab] = pd.to_numeric(d["y"], errors="coerce").set_axis(d["date"])
    ust = pd.DataFrame(out)
    ust.to_csv(Path(data_dir) / "fred_treasury_cmt.csv")
    p = par.pivot_table(index="date", columns="tenor", values="median")
    print("\n=== SOFR OIS par minus Treasury CMT (bp), 2024-11 onward ===")
    rows = []
    for lab in ["2Y", "5Y", "10Y", "30Y"]:
        sp = ((p[lab] - ust[lab].reindex(p.index)) * 100).dropna()
        cov = par[par["tenor"] == lab].groupby("window")["date"].nunique()
        rows.append({"tenor": lab, "days": len(sp), "median_bp": sp.median(),
                     "p10": sp.quantile(.1), "p90": sp.quantile(.9),
                     "daily_chg_sd_bp": sp.diff().std()})
        print(lab, "windows used (days):", cov.to_dict())
    print(pd.DataFrame(rows).round(1).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--min-trades", type=int, default=3)
    ap.add_argument("--exclude-packages", action="store_true")
    ap.add_argument("--max-years", type=int, default=10,
                    help="longest annual SOFR OIS forward to build (default 10)")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    dd = Path(args.data_dir)

    t = load_cache(dd)
    meetings = infer_meetings(t)
    pd.DataFrame({"meeting_date": meetings}).to_csv(dd / "dtcc_meeting_calendar.csv", index=False)
    print("inferred meetings:", ", ".join(d.strftime("%Y-%m-%d") for d in meetings))

    fwd = meeting_forwards(t, meetings, args.min_trades, args.exclude_packages)
    fwd.to_csv(dd / "dtcc_meeting_fwd_daily.csv", index=False)
    par = par_curve(t, args.min_trades)
    par.to_csv(dd / "dtcc_sofr_par_daily.csv", index=False)
    path, basis = build_path(fwd, par, meetings, load_effr(dd), args.max_years)
    path.to_csv(dd / "dtcc_expected_path_daily.csv", index=False)
    build_curve(path).to_csv(dd / "dtcc_expected_curve_daily.csv", index=False)
    print(f"wrote {len(fwd)} forward rows, {len(par)} par rows, {len(path)} path nodes")
    if args.report:
        report(t, fwd, path, basis, dd, args.min_trades)
        treasury_report(par, dd)
        eff = load_effr(dd)
        if eff is not None:
            forward_report(path, eff)


if __name__ == "__main__":
    main()

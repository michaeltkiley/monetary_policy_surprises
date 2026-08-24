"""
Production, stage 3: assemble the JSON payload for the 3-tab dashboard
(monetary policy surprises / equity pass-through / corporate pass-through).
Reads the full panel (01) and the production estimates (02).

Tab 1 content: a bar chart of ED1 vs UST10Y surprises over the full history,
with BOTH the strict-date and activity-based ZLB windows shaded (derived
directly from the 'era'/'era_ratio' columns, not hand-picked -- see
build_zlb_bands); and two scatter plots (UST2Y vs UST10Y surprise, and
UST2Y vs the 5-yr breakeven-inflation surprise, i.e. UST5Y_chg - TIPS5Y_chg)
with points grouped by the activity-based ZLB classification and the most
recent 8 events called out separately.

Tabs 2-3 mostly repackage 02's output, plus series not persisted elsewhere:
the rolling-window equity IV-GMM coefficient (Tab 2) and the rolling-window
Baa/Aaa-on-20yr coefficients (Tab 3), each needing its own PC instrument
refit at every window -- and the 'current'-regime corporate reliability
numbers.

Tab 4 (Rate Expectations) is different in kind: it reads a separate local
project, ../../cme_tests (CME STLINT parsing + Carlson-Craig-Melick
risk-neutral probabilities for SOFR/Fed-funds futures options), via
lib_cme.py, plus a trailing history of the actual effective fed funds rate
from FRED. See lib_cme.py's docstring and build_tab_expectations() below
for the current/previous snapshot logic and contract selection.

Usage:
    python3 03_build_dashboard_data.py [--run-date YYYYMMDD]
"""
import argparse
import glob
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import lib_cme
from lib_gmm import iv_gmm

PC_COLS = ["ED1", "UST2Y", "UST5Y", "UST30Y"]
COVID_DATES = pd.to_datetime([
    "2020-03-03", "2020-03-15", "2020-03-19", "2020-03-23", "2020-03-31", "2020-04-29",
])
ROLL_WINDOW = 64  # ~8 years at 8 meetings/yr; 40 was too noisy in practice, 30 rejected earlier for sign flips
EFFR_HISTORY_MONTHS = 1  # how far back the actual-rate line runs; the forward curve is the point


def latest(out_dir: Path, pattern: str) -> Path:
    matches = sorted(glob.glob(str(out_dir / pattern)))
    if not matches:
        raise FileNotFoundError(f"No {pattern} in {out_dir}")
    return Path(matches[-1])


def fit_pc(sub: pd.DataFrame, cols) -> np.ndarray:
    X = sub[cols].to_numpy()
    mean, std = X.mean(axis=0), X.std(axis=0, ddof=1)
    Xs = (X - mean) / std
    corr = np.corrcoef(Xs, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(corr)
    pc1 = eigvecs[:, np.argmax(eigvals)]
    pc1 = pc1 / np.sign(pc1[0])
    return ((sub[cols].to_numpy() - mean) / std) @ pc1


def build_zlb_bands(df: pd.DataFrame, col: str) -> list:
    """Contiguous [start, end] date ranges where `col` == 'zlb_era',
    derived directly from the actual classification -- not hand-picked --
    so gaps (like the 2009-2010 false dawn, or the genuine Dec-2014
    reactivation, both only present when col='era_ratio') show up
    automatically as unshaded stretches rather than needing their own
    explained-away color. col='era' gives the strict, nominal-date bands
    (two solid blocks, no gaps) for comparison."""
    sub = df[df[col] == "zlb_era"].sort_values("Date")
    dates = sub["Date"].tolist()
    if not dates:
        return []
    bands = []
    start = dates[0]
    prev = dates[0]
    for d in dates[1:]:
        if (d - prev).days > 200:  # gap larger than one FOMC cycle -> new band
            bands.append((start, prev))
            start = d
        prev = d
    bands.append((start, prev))
    return [{"start": s.strftime("%Y-%m-%d"), "end": e.strftime("%Y-%m-%d")} for s, e in bands]


def build_rolling_equity(df: pd.DataFrame) -> pd.DataFrame:
    """Tab 2: rolling-window (64-event, ex-COVID) equity IV-GMM coefficient,
    own PC instrument refit at each window."""
    needed = ["SP500_logret", "UST10Y"] + PC_COLS
    d = df[~df["Date"].isin(COVID_DATES)].dropna(subset=needed).sort_values("Date").reset_index(drop=True)
    out_dates, out_coef, out_se = [], [], []
    for i in range(ROLL_WINDOW, len(d) + 1):
        sub = d.iloc[i - ROLL_WINDOW:i].copy()
        sub["PC"] = fit_pc(sub, PC_COLS)
        fit = iv_gmm(sub, "SP500_logret", ["UST10Y"], ["PC"], label="roll")
        out_dates.append(sub["Date"].iloc[-1])
        out_coef.append(fit.params["UST10Y"])
        out_se.append(fit.se["UST10Y"])
    return pd.DataFrame({"Date": out_dates, "coef": out_coef, "se": out_se})


def build_rolling_corporate(df: pd.DataFrame) -> pd.DataFrame:
    """Tab 3: rolling-window (64-event, ex-COVID) Baa/Aaa IV-GMM coefficients
    on the 20yr Treasury daily change -- mirrors build_rolling_equity, but
    fits both dependent variables on the same rolling window (and same
    refit PC instrument) so the two lines share one date axis. Focused on
    the 20yr regressor only (not both maturities) to match the equity tab's
    single-regressor rolling chart, per FRL (2016)'s own choice of 20yr and
    the finding (see README) that 20yr shows cleaner, more significant
    regime breaks than 10yr throughout."""
    endog = "DGS20_chg"
    needed = ["DBAA_chg", "DAAA_chg", endog] + PC_COLS
    d = df[~df["Date"].isin(COVID_DATES)].dropna(subset=needed).sort_values("Date").reset_index(drop=True)
    out_dates, baa_coef, baa_se, aaa_coef, aaa_se = [], [], [], [], []
    for i in range(ROLL_WINDOW, len(d) + 1):
        sub = d.iloc[i - ROLL_WINDOW:i].copy()
        sub["PC"] = fit_pc(sub, PC_COLS)
        fit_baa = iv_gmm(sub, "DBAA_chg", [endog], ["PC"], label="roll")
        fit_aaa = iv_gmm(sub, "DAAA_chg", [endog], ["PC"], label="roll")
        out_dates.append(sub["Date"].iloc[-1])
        baa_coef.append(fit_baa.params[endog]); baa_se.append(fit_baa.se[endog])
        aaa_coef.append(fit_aaa.params[endog]); aaa_se.append(fit_aaa.se[endog])
    return pd.DataFrame({
        "Date": out_dates, "baa_coef": baa_coef, "baa_se": baa_se,
        "aaa_coef": aaa_coef, "aaa_se": aaa_se,
    })


def build_tab_expectations(data_dir: Path) -> dict:
    """Tab 4: the market's expected path for policy rates. The CME-derived
    pieces (forward curve + SR3 probability distributions for 'current' and
    'previous' settlement dates) come pre-computed from lib_cme.load() --
    see that module's docstring for where they come from. This function
    just adds the trailing actual-rate history from FRED (DFF), fetched
    fresh on every run like the rest of this repo's public data."""
    derived = lib_cme.load()
    current = derived["current_date"]

    dff_path = latest(data_dir, "*_fred_dff.csv")
    dff = pd.read_csv(dff_path, na_values=".")
    dff.columns = ["date", "rate"]
    dff["date"] = pd.to_datetime(dff["date"])
    dff = dff.dropna(subset=["rate"])
    cutoff = pd.Timestamp(current) - pd.DateOffset(months=EFFR_HISTORY_MONTHS)
    dff = dff[(dff["date"] >= cutoff) & (dff["date"] <= pd.Timestamp(current))]

    return {
        **derived,
        "effr_dates": dff["date"].dt.strftime("%Y-%m-%d").tolist(),
        "effr_rate": [round(float(x), 3) for x in dff["rate"]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--out-dir", default="../output")
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)

    panel_path = latest(out_dir, "*_full_panel.parquet")
    est_path = latest(out_dir, "*_production_estimates.csv")
    print(f"Reading {panel_path}\nReading {est_path}")
    df = pd.read_parquet(panel_path)
    est = pd.read_csv(est_path)

    # ---- Tab 1, chart 1: ED1 vs UST10Y surprise bars, both era definitions shaded ----
    keep = df[df["regime"] != "excluded_non_policy"].sort_values("Date")
    bars = keep.dropna(subset=["ED1", "UST10Y"])
    tab1 = {
        "dates": bars["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "ed1": [round(float(x), 4) for x in bars["ED1"]],
        "ust10y": [round(float(x), 4) for x in bars["UST10Y"]],
        "era_bands": build_zlb_bands(df, "era"),
        "era_ratio_bands": build_zlb_bands(df, "era_ratio"),
    }

    # ---- Tab 1, chart 2: scatters -- UST2Y vs UST10Y, and UST2Y vs 5yr breakeven ----
    # breakeven = nominal - real (TIPS) yield, so its surprise = UST5Y_chg - TIPS5Y_chg;
    # TIPS5Y only available from 2005 on, so this scatter has a shorter sample.
    keep = keep.copy()
    keep["BEI5Y"] = keep["UST5Y"] - keep["TIPS5Y"]
    n_recent = 8

    def scatter_payload(xcol, ycol):
        sub = keep.dropna(subset=[xcol, ycol, "era_ratio"]).sort_values("Date").reset_index(drop=True)
        is_recent = sub.index >= (len(sub) - n_recent)
        return {
            "dates": sub["Date"].dt.strftime("%Y-%m-%d").tolist(),
            "x": [round(float(v), 4) for v in sub[xcol]],
            "y": [round(float(v), 4) for v in sub[ycol]],
            "group": sub["era_ratio"].tolist(),
            "is_recent": is_recent.tolist(),
        }

    tab1["scatter_2y_10y"] = scatter_payload("UST2Y", "UST10Y")
    tab1["scatter_2y_bei5y"] = scatter_payload("UST2Y", "BEI5Y")

    # ---- Tab 2: equity -- static comparison + rolling window ----
    eq = est[est["model"] == "equity"]
    tab2_static = []
    for _, row in eq.iterrows():
        tab2_static.append({
            "grouping": row["grouping"], "group": row["group"], "n": int(row["n"]),
            "coef": round(float(row["coef"]), 3), "se": round(float(row["se"]), 3),
            "cd_f": round(float(row["cragg_donald_f"]), 1),
            "af_p": None if pd.isna(row["andrews_fair_p"]) else round(float(row["andrews_fair_p"]), 3),
        })
    rolling_eq = build_rolling_equity(df)
    tab2 = {
        "static": tab2_static,
        "roll_dates": rolling_eq["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "roll_coef": [round(float(x), 3) for x in rolling_eq["coef"]],
        "roll_se": [round(float(x), 3) for x in rolling_eq["se"]],
    }

    # ---- Tab 3: corporate -- static comparison, 20yr regressor only ----
    # (10yr is still computed in 02_production_estimates.py and reported in
    # ../replication_scripts/ and the README, just not surfaced on this tab --
    # the dashboard leads with 20yr, per Kiley (2016)'s own choice.)
    tab3_static = []
    for model in ["baa_20yr", "aaa_20yr"]:
        sub = est[est["model"] == model]
        for _, row in sub.iterrows():
            tab3_static.append({
                "model": model, "grouping": row["grouping"], "group": row["group"],
                "n": int(row["n"]), "coef": round(float(row["coef"]), 3), "se": round(float(row["se"]), 3),
                "cd_f": round(float(row["cragg_donald_f"]), 1),
                "af_p": None if pd.isna(row["andrews_fair_p"]) else round(float(row["andrews_fair_p"]), 3),
            })

    rolling_corp = build_rolling_corporate(df)
    tab3 = {
        "static": tab3_static,
        "roll_dates": rolling_corp["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "roll_baa_coef": [round(float(x), 3) for x in rolling_corp["baa_coef"]],
        "roll_baa_se": [round(float(x), 3) for x in rolling_corp["baa_se"]],
        "roll_aaa_coef": [round(float(x), 3) for x in rolling_corp["aaa_coef"]],
        "roll_aaa_se": [round(float(x), 3) for x in rolling_corp["aaa_se"]],
    }

    tab_expectations = build_tab_expectations(Path(args.data_dir))

    payload = {
        "run_date": args.run_date,
        "as_of": df["Date"].max().strftime("%Y-%m-%d"),
        "tab1": tab1,
        "tab2": tab2,
        "tab3": tab3,
        "tab_expectations": tab_expectations,
    }

    out_path = out_dir / f"{args.run_date}_dashboard_data.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {out_path} ({len(json.dumps(payload))} bytes)")
    print(f"  tab1: {len(tab1['dates'])} bar events, "
          f"{len(tab1['scatter_2y_10y']['x'])} 2y/10y scatter points, "
          f"{len(tab1['scatter_2y_bei5y']['x'])} 2y/BEI5y scatter points")
    print(f"  tab2: {len(tab2_static)} static rows, {len(tab2['roll_dates'])} rolling points")
    print(f"  tab3: {len(tab3_static)} static rows, {len(tab3['roll_dates'])} rolling points")
    print(f"  tab_expectations: current={tab_expectations['current_date']} "
          f"previous={tab_expectations['previous_date']} "
          f"contracts={tab_expectations['contract_3mo']}/{tab_expectations['contract_dec_ny']}")


if __name__ == "__main__":
    main()

"""
Production, stage 2: first-pass look at how the equity-price and
corporate-yield pass-through results look once the sample is extended past
2012 -- NOT a full replication, just an extension of the
measurement-error-corrected IV approach validated in ../replication_scripts/
to the current sample, using one consistent specification throughout:

  - Instrument: first PC of {ED1, UST2Y, UST5Y, UST30Y}, fit with SEPARATE
    loadings within each group (own eigenvector each time) -- this was the
    best-identified, least-attenuated instrument found during replication
    (06_jmcb_instrument_variants.py spec 'h'), and resolves the internal-
    consistency problems a UST10Y-containing instrument set ran into once
    UST10Y/DGS10 is the endogenous regressor (11_frl_instrument_variants.py).
  - Regressor: UST10Y (30-min surprise) for the equity model; both DGS10_chg
    and DGS20_chg (daily changes) for the corporate-yield models, reported
    side by side rather than picking one.
  - Two grouping schemes, both reported: 'era' (strict, nominal-rate-based
    zlb1/zlb2 windows) and 'era_ratio' (activity-based reclassification --
    see 01_load_full_sample.py for both).
  - COVID exclusion is now a default part of the baseline, not an ad hoc
    check: the six March-April 2020 events (2020-03-03, -03-15, -03-19,
    -03-23, -03-31, -04-29) are excluded unless --include-covid is passed.
    Found via sensitivity analysis to be highly influential in several
    specs -- e.g. Aaa's zlb_era coefficient under strict dates moves from
    0.663 (se=0.195) to 0.476 (se=0.053) once excluded, almost certainly
    driven by 2020-03-23 (the "unlimited QE"/corporate-credit-facility
    announcement day, DAAA_chg=-0.55, an extreme single-day move). The
    activity-based scheme already excludes most of these dates from
    zlb_era on its own (they get reclassified as non_zlb_era), so this
    mainly matters for the strict-date scheme and for non_zlb_era under
    either scheme (which still contains 2020-03-03/-03-15 either way).

Usage:
    python3 02_production_estimates.py
    python3 02_production_estimates.py --include-covid
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from lib_gmm import andrews_fair_test, iv_gmm, ols_hc, print_fit

PC_COLS = ["ED1", "UST2Y", "UST5Y", "UST30Y"]
ERA_ORDER = ["non_zlb_era", "zlb_era"]
# 'era': strict, nominal-rate-based zlb1/zlb2 windows (cuts stay with the
#   outgoing non-ZLB era, liftoffs move to the incoming one).
# 'era_ratio': activity-based -- within those same nominal windows, a
#   >=2-meeting stretch where the {ED4,OIS1Y,UST2Y}/UST5Y activity ratio is
#   >=60% of its pre-2009 average is reclassified as non_zlb_era, even
#   though the funds rate hadn't moved yet (see 01_load_full_sample.py).
GROUPING_COLS = ["era", "era_ratio"]

COVID_DATES = pd.to_datetime([
    "2020-03-03", "2020-03-15", "2020-03-19", "2020-03-23", "2020-03-31", "2020-04-29",
])

MODEL_SPECS = [
    ("equity", "SP500_logret", "UST10Y"),
    ("baa_10yr", "DBAA_chg", "DGS10_chg"),
    ("aaa_10yr", "DAAA_chg", "DGS10_chg"),
    ("baa_20yr", "DBAA_chg", "DGS20_chg"),
    ("aaa_20yr", "DAAA_chg", "DGS20_chg"),
]


def latest(out_dir: Path) -> Path:
    matches = sorted(glob.glob(str(out_dir / "*_full_panel.parquet")))
    if not matches:
        raise FileNotFoundError(f"No *_full_panel.parquet in {out_dir}; run 01_load_full_sample.py first")
    return Path(matches[-1])


def fit_pc(sub: pd.DataFrame) -> np.ndarray:
    X = sub[PC_COLS].to_numpy()
    mean, std = X.mean(axis=0), X.std(axis=0, ddof=1)
    Xs = (X - mean) / std
    corr = np.corrcoef(Xs, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(corr)
    pc1 = eigvecs[:, np.argmax(eigvals)]
    pc1 = pc1 / np.sign(pc1[0])  # sign normalization only, for readability
    return ((sub[PC_COLS].to_numpy() - mean) / std) @ pc1


def run_group(label, sub, dep, endog):
    needed = [dep, endog] + PC_COLS
    sub = sub.dropna(subset=needed).copy()
    if len(sub) < len(PC_COLS) + 3:
        return None
    sub["PC"] = fit_pc(sub)
    return iv_gmm(sub, dep, [endog], ["PC"], label=label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-covid", action="store_true",
                     help="Include the 6 Mar-Apr 2020 events (excluded by default)")
    args = ap.parse_args()
    exclude_covid = not args.include_covid

    panel_path = latest(Path("../output"))
    run_date = panel_path.name.split("_")[0]
    print(f"Reading {panel_path}")
    print(f"COVID dates (Mar-Apr 2020): {'EXCLUDED (default)' if exclude_covid else 'INCLUDED (--include-covid)'}\n")
    df = pd.read_parquet(panel_path)
    if exclude_covid:
        df = df[~df["Date"].isin(COVID_DATES)]

    rows = []
    for model_name, dep, endog in MODEL_SPECS:
        print("=" * 78)
        print(f"{model_name.upper()}: dep={dep}, endog={endog}, instrument=PC1{{ED1,2yr,5yr,30yr}}")
        print("=" * 78)
        for group_col in GROUPING_COLS:
            print(f"--- by {group_col} ---")
            fits = {}
            for g in ERA_ORDER:
                sub = df[df[group_col] == g]
                fit = run_group(g, sub, dep, endog)
                fits[g] = fit
                if fit is not None:
                    print(f"  {g:<14} n={fit.n_obs:<4} coef={fit.params[endog]:+.3f} "
                          f"se={fit.se[endog]:.3f}  CD-F={fit.cragg_donald_f:.1f}")
                else:
                    print(f"  {g:<14} -- too few observations, skipped")

            af_pval = None
            base, other = fits.get(ERA_ORDER[0]), fits.get(ERA_ORDER[1])
            if base is not None and other is not None:
                _, af_pval, _ = andrews_fair_test(base, other, [endog])
                print(f"  Andrews-Fair, {ERA_ORDER[0]} vs {ERA_ORDER[1]}: p={af_pval:.3f}")

            for g, fit in fits.items():
                if fit is None:
                    continue
                rows.append({
                    "model": model_name, "dep": dep, "endog": endog, "grouping": group_col,
                    "group": g, "n": fit.n_obs, "coef": fit.params[endog], "se": fit.se[endog],
                    "cragg_donald_f": fit.cragg_donald_f,
                    "andrews_fair_p": af_pval,
                })
        print()

    suffix = "" if exclude_covid else "_with_covid"
    out_path = Path("../output") / f"{run_date}_production_estimates{suffix}.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

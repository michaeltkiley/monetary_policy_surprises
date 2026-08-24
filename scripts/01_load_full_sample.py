"""
Production, stage 1: build the full-history event panel (1994-present) with
extended regime tags, for a first-pass look at how the JMCB/FRL-style
pass-through results look post-2012 -- NOT a full replication, just an
extension of the same measurement-error-corrected IV approach validated in
../replication_scripts/ to the current sample.

Uses TWO USMPD sheets for two different purposes, deliberately not the
same one throughout:
  - 'Statements' (fixed 30-min window, every meeting) is used ONLY to
    derive the era/era_ratio classification. Mixing window lengths into
    the era definition itself -- 30 min for meetings without a press
    conference, up to 100 min for meetings with one -- would confound
    "is this period actually ZLB-pinned" with "did this particular
    meeting happen to have a press conference," since not every meeting
    has one (none before 2011, every one since Jan 2019 under Powell).
  - 'Monetary Events' (30-100 min depending on whether a press conference
    occurred) is used for every regression/display variable, since it's
    the richer, more complete read of what the market actually learned
    at each event -- see 05_jmcb_window_robustness.py in
    ../replication_scripts/ for the original statement-vs-event window
    comparison this choice is based on.
Both sheets cover the same set of FOMC dates, so era/era_ratio labels
computed on Statements are simply joined onto the Monetary Events data by
date.

Regimes (six, matching well-known FOMC-history eras plus one Swanson-
Williams-style split). The two transition types between ZLB and non-ZLB
eras are NOT treated symmetrically: a cut TO zero is a genuine short-run
policy rate cut -- still conventional policy, still made while there was
room to cut further -- so it's the LAST observation of the outgoing
(non-ZLB) era, matching the pre-ZLB/ZLB-2008 convention validated in the
replication phase. A hike OFF zero (liftoff) is different in kind: it's the
FOMC's declaration that the ZLB constraint no longer binds, so it's the
FIRST observation of the incoming (non-ZLB) era, not the last ZLB one.

zlb1 is further split into zlb1_deep and zlb1_reactivating, following
Swanson and Williams (2014, AER), "Measuring the Effect of the Zero Lower
Bound on Medium- and Longer-Term Interest Rates": the funds rate sitting at
zero doesn't mean every horizon is equally constrained -- medium-term
yields can start responding to news again well before the funds rate
itself lifts off, once liftoff becomes an actively-repriced prospect. The
split point (Oct 29, 2014) was found empirically, not assumed: the rolling
(8-event, ~1yr) ratio of std(UST2Y)/std(UST10Y) -- near its ~1.5 pre-ZLB
level when 2yr is "active," collapsing toward zero when it's pinned at the
ZLB -- stays suppressed (~0.33-0.35) from mid-2012 through mid-2014, then
rises through late 2014 and stays permanently above 1.0 from this meeting
onward (through the Dec 2015 liftoff and beyond). The same exercise for the
zlb2 exit (2020-2022) found no comparable advance-reactivation signal --
the ratio stays suppressed right through Jan 2022 and only crosses above
1.0 at the March 2022 hike itself, so zlb2 is NOT split; the 2021-2022
tightening was evidently a much faster, less-telegraphed exit than
2013-2015's was, with no meaningful advance repricing window.

  pre_zlb           : ... -> 2008-12-16 (cut TO zero; stays in pre_zlb)
  zlb1_deep         : 2008-12-16 -> 2014-10-29 (UST2Y still pinned)
  zlb1_reactivating : 2014-10-29 -> 2015-12-16 (UST2Y reactivated; liftoff moves to normalize)
  normalize         : 2015-12-16 -> 2020-03-15 (COVID cut TO zero; stays here)
  zlb2              : 2020-03-15 -> 2022-03-16 (no advance-reactivation signal found; not split)
  current           : 2022-03-16 -> present

Two combined groupings (the baseline for the first pass; the six regimes
above are the robustness cut). zlb1_deep and zlb1_reactivating are both
part of zlb_era -- the funds rate itself was at zero throughout zlb1
regardless of whether 2yr had reactivated, so this split doesn't change
the baseline era assignment, only the finer robustness view:
  zlb_era      : zlb1_deep union zlb1_reactivating union zlb2
  non_zlb_era  : pre_zlb union normalize union current

Non-policy 'Unscheduled' events with a zero fed-funds surprise (MP1==0) are
excluded throughout the full history, not just the original 2008-2012
window -- generalizing the rule already validated there (confirmed against
the full sample: crisis-era intermeeting cuts all have nonzero MP1 and are
kept; facility/framework-announcement dates like 2020-08-27 or 2019-10-04
have MP1==0 and are dropped).

Usage:
    python3 01_load_full_sample.py [--force]
"""
import argparse
import glob
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ZLB1_START = pd.Timestamp("2008-12-16")
ZLB1_END = pd.Timestamp("2015-12-16")
ZLB2_START = pd.Timestamp("2020-03-15")
ZLB2_END = pd.Timestamp("2022-03-16")


def latest(data_dir: Path, pattern: str) -> Path:
    matches = sorted(glob.glob(str(data_dir / pattern)))
    if not matches:
        raise FileNotFoundError(f"No {pattern} found in {data_dir}")
    return Path(matches[-1])


def regime(d: pd.Timestamp) -> str:
    """Strict, nominal-rate-based regime: 'zlb1'/'zlb2' are single undivided
    periods bounded only by actual policy-rate cuts/hikes, matching the
    original date-based scheme before any Swanson-Williams-style refinement.
    See era_ratio_classification() below for the activity-based alternative."""
    if d <= ZLB1_START:
        return "pre_zlb"
    if d < ZLB1_END:          # liftoff itself (d == ZLB1_END) falls through to normalize
        return "zlb1"
    if d <= ZLB2_START:
        return "normalize"
    if d < ZLB2_END:          # current-cycle liftoff (d == ZLB2_END) falls through to current
        return "zlb2"
    return "current"


GROUPING = {
    "pre_zlb": "non_zlb_era", "normalize": "non_zlb_era", "current": "non_zlb_era",
    "zlb1": "zlb_era", "zlb2": "zlb_era",
}

ACTIVITY_COLS = ["ED4", "OIS1Y", "UST2Y"]


def era_ratio_classification(df: pd.DataFrame) -> pd.Series:
    """Activity-based alternative to the strict nominal-rate 'era' column.
    Within the nominal ZLB windows (fed funds ~0), a meeting is 'pinned'
    (counts as zlb_era) if the average of {ED4,OIS1Y,UST2Y}/UST5Y rolling
    (4-meeting) std-ratio is below 60% of its pre-2009 average, else
    'active' (reclassified as non_zlb_era, since markets are pricing a
    near-term move even though the policy rate hasn't changed yet). A
    single isolated 'active' meeting doesn't count as its own regime -- it
    is folded back into the surrounding pinned stretch (needs >=2
    consecutive meetings to register as a genuine switch). Outside the
    nominal ZLB windows, this returns the same value as the strict 'era'
    column unchanged (the funds rate isn't at zero, so this
    classification doesn't apply)."""
    W = 4
    THRESH_PCT = 0.60
    df = df.sort_values("Date").reset_index(drop=True)
    era_full = df["regime"].map(GROUPING)  # NaN for excluded_non_policy

    # Compute the rolling ratio on the excluded-events-dropped sequence, so
    # a skipped non-policy communication doesn't shift which 4 *meetings*
    # fall in the window -- matches how this was validated by hand.
    sub = df.loc[era_full.notna(), ["Date"] + ACTIVITY_COLS + ["UST5Y"]].reset_index(drop=True)
    ratios = pd.DataFrame(index=sub.index)
    for col in ACTIVITY_COLS:
        ratios[col] = sub[col].rolling(W).std() / sub["UST5Y"].rolling(W).std()
    sub["m"] = ratios.mean(axis=1)

    pre_avg = sub.loc[sub["Date"] < "2009-01-01", "m"].dropna().mean()
    threshold = THRESH_PCT * pre_avg

    in_zlb_window_sub = ((sub["Date"] > ZLB1_START) & (sub["Date"] < ZLB1_END)) | \
                         ((sub["Date"] > ZLB2_START) & (sub["Date"] < ZLB2_END))

    active = pd.Series(False, index=sub.index)
    active.loc[in_zlb_window_sub] = (sub.loc[in_zlb_window_sub, "m"] >= threshold).to_numpy()

    # fold isolated single-meeting active runs back into pinned
    run_id = (active != active.shift()).cumsum()
    run_len = active.groupby(run_id).transform("size")
    active_confirmed = active & (run_len >= 2)

    sub["era_ratio"] = era_full[era_full.notna()].reset_index(drop=True)
    sub.loc[in_zlb_window_sub & active_confirmed, "era_ratio"] = "non_zlb_era"
    sub.loc[in_zlb_window_sub & ~active_confirmed, "era_ratio"] = "zlb_era"

    era_ratio = df["Date"].map(sub.set_index("Date")["era_ratio"])
    return era_ratio


def daily_changes(fred_path: Path) -> pd.DataFrame:
    df = pd.read_csv(fred_path, na_values=".")
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df = df.sort_values("observation_date").set_index("observation_date")
    cols = [c for c in ["DGS10", "DGS20", "DAAA", "DBAA"] if c in df.columns]
    changes = df[cols].apply(lambda s: s.dropna().diff())
    changes = changes.reindex(df.index)
    changes.columns = [f"{c}_chg" for c in changes.columns]
    return changes.reset_index().rename(columns={"observation_date": "Date"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--out-dir", default="../output")
    ap.add_argument("--run-date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.out_dir) / f"{args.run_date}_full_panel.parquet"
    if out_path.exists() and not args.force:
        print(f"{out_path} already exists, skipping (use --force to rebuild)")
        return

    usmpd_path = latest(Path(args.data_dir), "*_usmpd.xlsx")

    # Era classification (both 'era' and 'era_ratio') is computed from the
    # Statements sheet only -- a fixed 30-min window throughout -- even
    # though the regressions below use Monetary Events. Mixing window
    # lengths into the era definition itself would confound "is this
    # period actually ZLB-pinned" with "did this particular meeting happen
    # to have a press conference," since not every meeting has one.
    print(f"Reading {usmpd_path} [Statements -> era classification]")
    stmt = pd.read_excel(usmpd_path, sheet_name="Statements")
    stmt["Date"] = pd.to_datetime(stmt["Date"])
    stmt = stmt.sort_values("Date").reset_index(drop=True)
    stmt["regime"] = stmt["Date"].apply(regime)
    stmt_non_policy = (stmt["Unscheduled"] == 1) & (stmt["MP1"] == 0)
    stmt.loc[stmt_non_policy, "regime"] = "excluded_non_policy"
    stmt_era_ratio = era_ratio_classification(stmt)
    era_ratio_by_date = dict(zip(stmt["Date"], stmt_era_ratio))

    print(f"Reading {usmpd_path} [Monetary Events -> regression data]")
    df = pd.read_excel(usmpd_path, sheet_name="Monetary Events")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["SP500_logret"] = 100 * np.log(1 + df["SP500"] / 100)

    fred_path = latest(Path(args.data_dir), "*_fred_corporate.csv")
    print(f"Reading {fred_path}")
    changes = daily_changes(fred_path)
    df = df.merge(changes, on="Date", how="left")

    df["regime"] = df["Date"].apply(regime)
    non_policy = (df["Unscheduled"] == 1) & (df["MP1"] == 0)
    n_excluded = int(non_policy.sum())
    df.loc[non_policy, "regime"] = "excluded_non_policy"
    df["era"] = df["regime"].map(GROUPING)  # NaN for excluded_non_policy
    df["era_ratio"] = df["Date"].map(era_ratio_by_date)
    df.loc[df["era"].isna(), "era_ratio"] = None  # respect this df's own exclusions

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(f"Wrote {out_path} ({len(df)} events, {df['Date'].min().date()} to {df['Date'].max().date()})")
    print(f"Excluded as non-policy communications: {n_excluded}")

    print("\nFive-regime counts (strict, nominal-rate-based):")
    print(df["regime"].value_counts())
    print("\nCombined-era counts, strict dates (baseline for this first pass):")
    print(df["era"].value_counts())
    print("\nCombined-era counts, activity-based (60% rule, >=2-meeting stretches):")
    print(df["era_ratio"].value_counts())

    moved = df[(df["era"] != df["era_ratio"]) & df["era"].notna()]
    print(f"\nMeetings reclassified by the activity rule: {len(moved)}")
    print(moved[["Date", "era", "era_ratio"]].to_string(index=False))


if __name__ == "__main__":
    main()

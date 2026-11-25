"""
Forward guidance by horizon and phase, Fed / ECB / BoE.

Phases (the organising story of docs/forward_guidance_note.md):
  I    pre-guidance
  II   near-term guidance introduced (Fed 'considerable period'/'measured
       pace' Aug 2003 - Jun 2006; ECB 'strong vigilance' Nov 2005 - Jul 2008;
       BoE: none)
  III  guidance expanded in the GFC / at the lower bound (Fed Dec 2008
       'for some time' -> calendar -> thresholds -> dots; ECB Jul 2013;
       BoE Carney Aug 2013)
  IV   retreat: medium-term guidance dropped for 'meeting-by-meeting'
       (Fed Jul 27 2022, ECB Jul 21 2022, BoE from Aug 2022)
  V    Warsh (Fed, Jun 2026 onward): statement guidance dropped
Gaps without explicit guidance before/in the crisis (Fed Aug 2006 - Nov
2008, ECB Aug 2008 - Jun 2013, BoE Aug 2007 - Jul 2013) are their own rows.

Horizons:
              US           EA          UK
  current     MP1          OIS 1M      SONIA/OIS 1M (2009 on only)
  next mtgs   FF4          OIS 3M      1st 3M rate future (Libor, SONIA 2021-)
  1 year      ED4          OIS 1Y      1-yr gilt
  2 years     UST2Y        Bund 2Y     2-yr gilt
  10 years    UST10Y       Bund 10Y    10-yr gilt

Outputs: ../docs/fg_note/phase_table.csv, phase_tests.csv,
         fig7_term_structure_by_phase.png, fig8_next_meetings_rolling.png
Usage: python3 12_fg_phases.py   (after 10_ and 11_)
"""
import glob
import importlib
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fg = importlib.import_module("10_forward_guidance_note")
xc = importlib.import_module("11_fg_cross_country")

H = ["cur", "near", "y1", "y2", "y10"]
HLAB = ["Current\nmeeting", "Next few\nmeetings", "1 year", "2 years", "10 years"]

PHASES = {
    "Fed": [("I Pre-guidance", "1994-01-01"),
            ("II Near-term guidance", "2003-08-12"),
            ("Crisis, no guidance", "2006-08-08"),
            ("III Expanded (GFC/ZLB)", "2008-12-16"),
            ("IV Retreat", "2022-07-27"),
            ("V Warsh", "2026-05-22")],
    "ECB": [("I Pre-guidance", "1999-01-01"),
            ("II Near-term guidance", "2005-11-01"),
            ("Crisis, no guidance", "2008-08-01"),
            ("III Expanded (GFC/ZLB)", "2013-07-04"),
            ("IV Retreat", "2022-07-21")],
    "BoE": [("I Pre-guidance", "1997-01-01"),
            ("Crisis, no guidance", "2007-08-01"),
            ("III Expanded (GFC/ZLB)", "2013-08-01"),
            ("IV Retreat", "2022-07-01")],
}
SUB = {  # sub-periods reported in the text
    "Fed": [("III: Mar-Jul 2022 hikes w/ guidance", "2022-03-16", "2022-07-26"),
            ("IV: 2022-23 hikes", "2022-07-27", "2023-12-31"),
            ("IV: 2024-May 2026", "2024-01-01", "2026-05-21")],
    "ECB": [("I: 1999-2003 Duisenberg", "1999-01-01", "2003-10-31"),
            ("I: 2003-05 rates on hold", "2003-11-01", "2005-10-31"),
            ("IV excl. Mar 2023 (SVB)", "2022-07-21", "2099-01-01")],
    "BoE": [("IV: 2022-Jun 2024 hikes/hold", "2022-07-01", "2024-07-31"),
            ("IV: Aug 2024- 'gradual and careful' cuts", "2024-08-01", "2099-01-01")],
}
TESTS = {  # (from, to, direction to test)
    "Fed": [("I Pre-guidance", "II Near-term guidance", "smaller"),
            ("Crisis, no guidance", "III Expanded (GFC/ZLB)", "smaller"),
            ("III Expanded (GFC/ZLB)", "IV Retreat", "larger"),
            ("IV Retreat", "V Warsh", "larger"),
            ("III Expanded (GFC/ZLB)", "V Warsh", "larger")],
    "ECB": [("I Pre-guidance", "II Near-term guidance", "smaller"),
            ("Crisis, no guidance", "III Expanded (GFC/ZLB)", "smaller"),
            ("III Expanded (GFC/ZLB)", "IV Retreat", "larger")],
    "BoE": [("Crisis, no guidance", "III Expanded (GFC/ZLB)", "smaller"),
            ("III Expanded (GFC/ZLB)", "IV Retreat", "larger")],
}


def panel(dd: Path) -> dict:
    us = fg.load(dd)
    us = us.rename(columns={"MP1": "cur", "FF4": "near", "ED4": "y1", "UST2Y": "y2", "UST10Y": "y10"})
    ea = xc.load_ea(sorted(glob.glob(str(dd / "*_eampd.xlsx")))[-1])["me"]
    ea = ea.assign(cur=ea["OIS_1M"], near=ea["OIS_3M"], y1=ea["OIS_1Y"], y2=ea["DE2Y"], y10=ea["DE10Y"])
    s = pd.read_excel(sorted(glob.glob(str(dd / "*_ukmpd.xlsx")))[-1], sheet_name="surprises")
    s["Date"] = s["Datetime"].dt.normalize()
    s["near"] = s["FSScm1"].where(s["FSScm1"].notna(), s["SON3c1"])
    m = {"GBP1MOIS=": "cur", "near": "near", "GB1YT=RR": "y1", "GB2YT=RR": "y2", "GB10YT=RR": "y10"}
    uk = (s.groupby("Date")[list(m)].sum(min_count=1).rename(columns=m) * 100.0).reset_index()
    out = {}
    for bank, d in [("Fed", us), ("ECB", ea), ("BoE", uk)]:
        d = d[["Date"] + H + (["Unscheduled"] if "Unscheduled" in d else [])].copy()
        starts = pd.to_datetime([p[1] for p in PHASES[bank]])
        idx = np.searchsorted(starts.values, d["Date"].values, side="right") - 1
        d["phase"] = [PHASES[bank][i][0] for i in idx]
        out[bank] = d.sort_values("Date").reset_index(drop=True)
    return out


def row(g):
    r = {"n": len(g), "start": g.Date.min().date(), "end": g.Date.max().date()}
    for h in H:
        r[h] = fg.rms(g[h])
    r["near_ge5"] = np.mean(g["near"].abs() >= 5)
    r["cur_ge5"] = np.mean(g["cur"].abs() >= 5) if g["cur"].notna().any() else np.nan
    return r


def perm(a, b, larger, B=20000, seed=0):
    a, b = a.dropna().to_numpy(), b.dropna().to_numpy()
    if len(a) < 3 or len(b) < 3:
        return np.nan
    rng = np.random.default_rng(seed)
    pool, obs = np.concatenate([a, b]), fg.rms(b)
    sims = np.array([fg.rms(rng.choice(pool, len(b), replace=False)) for _ in range(B)])
    return np.mean(sims >= obs) if larger else np.mean(sims <= obs)


def main():
    dd, fd = Path("../data"), Path("../docs/fg_note")
    P = panel(dd)
    pd.set_option("display.width", 250)
    rows = []
    for bank, d in P.items():
        for name, _ in PHASES[bank]:
            rows.append({"bank": bank, "phase": name, **row(d[d.phase == name])})
        for name, a, b in SUB[bank]:
            g = d[(d.Date >= a) & (d.Date <= b)]
            if "SVB" in name:
                g = g[g.Date != "2023-03-16"]
            rows.append({"bank": bank, "phase": "  " + name, **row(g)})
        if bank == "Fed":
            for name, _ in PHASES[bank]:
                g = d[(d.phase == name) & (d.Unscheduled == 0)]
                rows.append({"bank": bank, "phase": "  sched. only: " + name, **row(g)})
    T = pd.DataFrame(rows).set_index(["bank", "phase"])
    print(T.round(2).to_string())
    T.round(3).to_csv(fd / "phase_table.csv")

    tests = []
    for bank, lst in TESTS.items():
        d = P[bank]
        for a, b, direction in lst:
            r = {"bank": bank, "from": a, "to": b, "test": direction}
            for h in H:
                r[h] = perm(d.loc[d.phase == a, h], d.loc[d.phase == b, h], direction == "larger")
            tests.append(r)
    tests = pd.DataFrame(tests)
    print("\nOne-sided permutation p-values (is the 'to' phase smaller/larger?):")
    print(tests.round(3).to_string(index=False))
    tests.round(3).to_csv(fd / "phase_tests.csv", index=False)

    figures(P, T, fd)


COL = {"I Pre-guidance": "#257de0", "II Near-term guidance": "#f47230",
       "Crisis, no guidance": "#999999", "III Expanded (GFC/ZLB)": "#00b894",
       "IV Retreat": "#6a4ce2", "V Warsh": "#191919"}
MK = {"I Pre-guidance": "o", "II Near-term guidance": "s", "Crisis, no guidance": "x",
      "III Expanded (GFC/ZLB)": "D", "IV Retreat": "^", "V Warsh": "*"}


def figures(P, T, fd):
    INK, INK2 = fg.INK, fg.INK2
    fig, axs = plt.subplots(1, 3, figsize=(13, 6.0), dpi=150, sharey=True)
    for ax, bank in zip(axs, ["Fed", "ECB", "BoE"]):
        fg.style(ax)
        for name, _ in PHASES[bank]:
            r = T.loc[(bank, name)]
            ls = ":" if name == "Crisis, no guidance" else "-"
            ax.plot(range(5), [r[h] for h in H], ls=ls, lw=2, marker=MK[name],
                    ms=8 if name == "V Warsh" else 6, color=COL[name],
                    label=f"{name} (n={int(r['n'])})")
        ax.set_xticks(range(5), HLAB, fontsize=8.5, color=INK)
        ax.set_title(bank, loc="left", fontsize=11, color=INK)
        ax.legend(frameon=False, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2)
    axs[0].set_ylabel("RMS announcement surprise (bp)", color=INK2, fontsize=9)
    fig.suptitle("Surprise term structure by guidance phase", x=0.01, ha="left", fontsize=12, color=INK)
    fig.text(0.01, 0.005, "BoE current meeting = 1M OIS, available from 2009 only (no pre-guidance point). "
             "Next few meetings: FF4 (Fed), OIS 3M (ECB), first 3M rate future (BoE).", fontsize=7.3, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(fd / "fig7_term_structure_by_phase.png")
    plt.close(fig)

    # rolling 2-year (time-based) RMS of the next-few-meetings surprise
    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=150)
    fg.style(ax)
    bc = {"Fed": "#257de0", "ECB": "#f47230", "BoE": "#00b894"}
    for bank, d in P.items():
        s = d.set_index("Date")["near"].dropna().pow(2).rolling("730D", min_periods=6).mean().pow(0.5)
        ax.plot(s.index, s.values, color=bc[bank], lw=2, label=bank)
    top = 12
    ax.set_ylim(0, top)
    for x, lab, yy in [("2003-08-12", "Fed 'considerable\nperiod'", 0.98), ("2005-11-03", "ECB 'strong\nvigilance'", 0.86),
                       ("2008-12-16", "Fed ZLB\nguidance", 0.98), ("2013-07-04", "ECB/BoE\nguidance", 0.98),
                       ("2022-07-21", "Meeting-by-\nmeeting", 0.98), ("2026-06-17", "Warsh", 0.98)]:
        ax.axvline(pd.Timestamp(x), color=INK2, lw=0.8, ls=":")
        ax.text(pd.Timestamp(x), top * yy, " " + lab, fontsize=7.5, color=INK2, va="top")
    ax.set_ylabel("RMS surprise, trailing 2 years (bp)", color=INK2, fontsize=9)
    ax.set_title("Surprises about the next few meetings: Fed FF4, ECB OIS 3M, BoE first 3M rate future",
                 loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=9, loc="upper right", bbox_to_anchor=(1, 0.8))
    fig.tight_layout()
    fig.savefig(fd / "fig8_next_meetings_rolling.png")
    plt.close(fig)


if __name__ == "__main__":
    main()

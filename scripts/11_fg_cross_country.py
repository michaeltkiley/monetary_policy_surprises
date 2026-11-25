"""
Cross-country extension of 10_forward_guidance_note.py: where do ECB and
Bank of England announcement surprises land on the curve, by guidance regime?

Data (fetched by 00_fetch_intl_mpd.py):
  EA-MPD  Altavilla, Brugnolini, Gurkaynak, Motto, Ragusa (2019, JME),
          'Monetary Event Window' (press release + press conference),
          plus the two windows separately. Units: bp.
  UKMPD   Braun, Miranda-Agrippino, Saha (2024, JME). MPC announcement and
          (quarterly) press-conference rows are summed into one event per
          decision day, matching USMPD 'Monetary Events'. Units: pp -> bp.

Horizons used, matched as closely as the databases allow:
                  US (USMPD)     EA (EA-MPD)      UK (UKMPD)
  front           MP1            OIS 1M           1st 3M rate future (Libor,
                                                   SONIA from 2021)
  ~1 year         ED4            OIS 1Y           1-yr gilt
  2 years         UST2Y          Bund 2Y          2-yr gilt
  10 years        UST10Y         Bund 10Y         10-yr gilt
The UK front instrument is a 3-month rate future, not a current-meeting
contract, so UK front surprises are not strictly comparable to MP1/OIS 1M.

Path share: EA -- GSS rotation computed here from OIS 1M, 3M, 6M, 1Y
(path has no loading on 1M). UK -- the database's own Target and Path
factors. Share = sum(path^2) / (sum(path^2) + sum(target^2)).

Outputs: ../output/fg_xc_*.csv, ../docs/fg_note/era_table_{ea,uk}.csv,
         ../docs/fg_note/fig5_cross_country.png, fig6_ecb_pc_share.png

Usage: python3 11_fg_cross_country.py
"""
import argparse
import glob
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import importlib
fg = importlib.import_module("10_forward_guidance_note")

H = ["front", "y1", "y2", "y10"]
HLABEL = {"front": "Front (current meeting / ~3M)", "y1": "~1 year", "y2": "2 years", "y10": "10 years"}

# Regimes. Each date is the first event of the regime.
EA_ERAS = [
    ("Duisenberg 1999-2003",            "1999-01-01", "Duisenberg '99-'03"),
    ("Trichet pre-'vigilance'",         "2003-11-01", "Trichet '03-'05"),
    ("Trichet 'strong vigilance'",      "2005-11-01", "'Vigilance' '05-'08"),
    ("Crisis, no guidance",             "2008-08-01", "Crisis '08-'13"),
    ("Forward guidance",                "2013-07-04", "FG '13-'22"),
    ("Meeting-by-meeting",              "2022-07-21", "Mtg-by-mtg '22-'25"),
]
UK_ERAS = [
    ("MPC pre-crisis",                  "1997-01-01", "MPC '97-'07"),
    ("Crisis, cuts to 0.5%",            "2007-08-01", "Crisis '07-'09"),
    ("Low rates, no guidance",          "2009-03-05", "0.5%, no FG '09-'13"),
    ("Carney forward guidance",         "2013-08-01", "Carney FG '13-'20"),
    ("COVID ELB",                       "2020-03-01", "ELB '20-'21"),
    ("Hikes, meeting-by-meeting",       "2021-12-16", "Hikes '21-'24"),
    ("Cuts, 'gradual and careful'",     "2024-08-01", "Cuts '24-'26"),
]


def assign(d, eras):
    starts = pd.to_datetime([e[1] for e in eras])
    idx = np.searchsorted(starts.values, d["Date"].values, side="right") - 1
    d["era"] = [eras[i][0] for i in idx]
    return d


def parse_ea_date(x):
    if isinstance(x, (datetime, pd.Timestamp)):
        return pd.Timestamp(x)
    return pd.to_datetime(str(x), dayfirst=True)


def load_ea(path):
    out = {}
    for sheet, key in [("Monetary Event Window", "me"), ("Press Release Window", "pr"),
                       ("Press Conference Window", "pc")]:
        d = pd.read_excel(path, sheet_name=sheet)
        d["Date"] = d["date"].map(parse_ea_date)
        d = d.sort_values("Date").reset_index(drop=True)
        d["front"], d["y1"], d["y2"], d["y10"] = d["OIS_1M"], d["OIS_1Y"], d["DE2Y"], d["DE10Y"]
        out[key] = d
    me = out["me"]
    # GSS on OIS 1M, 3M, 6M, 1Y; path has zero loading on 1M
    X = me[["OIS_1M", "OIS_3M", "OIS_6M", "OIS_1Y"]]
    ok = X.notna().all(axis=1)
    f = gss(X[ok].to_numpy())
    me.loc[ok, "target"], me.loc[ok, "path"] = f[:, 0], f[:, 1]
    for k in out:
        assign(out[k], EA_ERAS)
    return out


def load_uk(path):
    s = pd.read_excel(path, sheet_name="surprises")
    f = pd.read_excel(path, sheet_name="factors")
    s = s.merge(f[["Datetime", "isMPC", "Target", "Path"]], on=["Datetime", "isMPC"])
    s["Date"] = s["Datetime"].dt.normalize()
    s["front_raw"] = s["FSScm1"].where(s["FSScm1"].notna(), s["SON3c1"])
    cols = {"front_raw": "front", "GB1YT=RR": "y1", "GB2YT=RR": "y2", "GB10YT=RR": "y10",
            "Target": "target", "Path": "path"}
    # sum MPC + same-day press conference into one monetary event (min_count keeps NaN)
    g = s.groupby("Date")[list(cols)].sum(min_count=1).rename(columns=cols) * 100.0
    g["has_pc"] = s.groupby("Date")["isMPC"].apply(lambda v: (~v).any())
    g = g.reset_index()
    return assign(g, UK_ERAS)


def gss(X):
    """Same rotation as 10_forward_guidance_note.gss_factors, on an array.
    Target scaled to column 0, path to the last column (both bp)."""
    mu, sd = X.mean(0), X.std(0)
    Z = (X - mu) / sd
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    F = U[:, :2] * S[:2]
    F = F / F.std(0)
    L = np.linalg.lstsq(F, Z, rcond=None)[0]
    a1, a2 = L[0, 0], L[1, 0]
    pdir = np.array([-a2, a1]) / np.hypot(a1, a2)
    tdir = np.array([a1, a2]) / np.hypot(a1, a2)
    Fr = np.column_stack([F @ tdir, F @ pdir])
    Fr[:, 0] *= np.polyfit(Fr[:, 0], X[:, 0], 1)[0]
    A = np.column_stack([np.ones(len(Fr)), Fr])
    Fr[:, 1] *= np.linalg.lstsq(A, X[:, -1], rcond=None)[0][2]
    return Fr


def stats(g):
    out = {"n": len(g)}
    for h in H:
        out[f"rms_{h}"] = fg.rms(g[h])
    out["y2_to_y10"] = out["rms_y2"] / out["rms_y10"]
    p = g[["path", "target"]].dropna()
    out["path_share"] = (p.path ** 2).sum() / ((p.path ** 2).sum() + (p.target ** 2).sum())
    out["guidance_events"] = np.mean((g["front"].abs() < 1) & (g["y2"].abs() >= 5))
    out["front_zero_share"] = np.mean(g["front"].abs() < 1)
    return out


def era_table(d, eras):
    rows = []
    for name, _, short in eras:
        g = d[d.era == name]
        s = stats(g)
        s.update(era=name, label=short, start=g.Date.min().date(), end=g.Date.max().date())
        rows.append(s)
    return pd.DataFrame(rows).set_index("era")


def perm(a, b, B=20000, seed=0, larger=True):
    rng = np.random.default_rng(seed)
    a, b = a.dropna().to_numpy(), b.dropna().to_numpy()
    pool, obs = np.concatenate([a, b]), fg.rms(a)
    sims = np.array([fg.rms(rng.choice(pool, len(a), replace=False)) for _ in range(B)])
    return obs, fg.rms(b), np.mean(sims >= obs) if larger else np.mean(sims <= obs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--out-dir", default="../output")
    ap.add_argument("--fig-dir", default="../docs/fg_note")
    args = ap.parse_args()
    dd, od, fd = Path(args.data_dir), Path(args.out_dir), Path(args.fig_dir)
    od.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)

    ea = load_ea(sorted(glob.glob(str(dd / "*_eampd.xlsx")))[-1])
    uk = load_uk(sorted(glob.glob(str(dd / "*_ukmpd.xlsx")))[-1])
    print(f"EA events {len(ea['me'])} ({ea['me'].Date.min().date()} - {ea['me'].Date.max().date()})")
    print(f"UK events {len(uk)} ({uk.Date.min().date()} - {uk.Date.max().date()})")

    show = ["start", "end", "n", "rms_front", "rms_y1", "rms_y2", "rms_y10", "y2_to_y10",
            "path_share", "guidance_events", "front_zero_share"]
    tabs = {}
    for key, d, eras in [("ea", ea["me"], EA_ERAS), ("uk", uk, UK_ERAS)]:
        t = era_table(d, eras)
        tabs[key] = t
        print(f"\n== {key.upper()} ==")
        print(t[show].round(2).to_string())
        t.to_csv(od / f"fg_xc_{key}.csv")
        t[show].round(3).to_csv(fd / f"era_table_{key}.csv")

    # Tests
    me = ea["me"]
    print("\nECB: meeting-by-meeting (Jul 2022-) vs forward guidance (2013-22), one-sided larger:")
    for h in H:
        o, b, p = perm(me.loc[me.era == "Meeting-by-meeting", h], me.loc[me.era == "Forward guidance", h])
        print(f"  {h:6s} {o:5.1f} vs {b:5.1f} bp  p={p:.3f}")
    print("ECB: 'strong vigilance' (2005-08) vs Duisenberg+early Trichet, one-sided smaller:")
    pre = me[me.era.isin(["Duisenberg 1999-2003", "Trichet pre-'vigilance'"])]
    for h in H:
        o, b, p = perm(me.loc[me.era == "Trichet 'strong vigilance'", h], pre[h], larger=False)
        print(f"  {h:6s} {o:5.1f} vs {b:5.1f} bp  p={p:.3f}")
    print("ECB: forward guidance (2013-22) vs crisis no-guidance (2008-13), one-sided smaller:")
    for h in H:
        o, b, p = perm(me.loc[me.era == "Forward guidance", h], me.loc[me.era == "Crisis, no guidance", h],
                       larger=False)
        print(f"  {h:6s} {o:5.1f} vs {b:5.1f} bp  p={p:.3f}")
    print("UK: Carney FG (2013-20) vs low rates no FG (2009-13), one-sided smaller:")
    for h in H:
        o, b, p = perm(uk.loc[uk.era == "Carney forward guidance", h],
                       uk.loc[uk.era == "Low rates, no guidance", h], larger=False)
        print(f"  {h:6s} {o:5.1f} vs {b:5.1f} bp  p={p:.3f}")
    print("UK: hikes 2021-24 vs Carney FG, one-sided larger:")
    for h in H:
        o, b, p = perm(uk.loc[uk.era == "Hikes, meeting-by-meeting", h],
                       uk.loc[uk.era == "Carney forward guidance", h])
        print(f"  {h:6s} {o:5.1f} vs {b:5.1f} bp  p={p:.3f}")

    # ECB: where is the 2Y news delivered? press conference share of 2Y variance
    pr, pc = ea["pr"], ea["pc"]
    pcs = []
    for name, _, short in EA_ERAS:
        a, b = pr.loc[pr.era == name, "y2"], pc.loc[pc.era == name, "y2"]
        a1, b1 = pr.loc[pr.era == name, "front"], pc.loc[pc.era == name, "front"]
        pcs.append(dict(era=name, label=short,
                        pc_share_2y=np.nansum(b ** 2) / (np.nansum(a ** 2) + np.nansum(b ** 2)),
                        pc_share_front=np.nansum(b1 ** 2) / (np.nansum(a1 ** 2) + np.nansum(b1 ** 2))))
    pcs = pd.DataFrame(pcs).set_index("era")
    print("\nECB press-conference share of squared surprise:")
    print(pcs.round(2).to_string())
    pcs.round(3).to_csv(fd / "ecb_press_conference_share.csv")

    # Common calendar: 2022-25 tightening/easing cycle, all three
    us = pd.read_csv(od / "fg_events.csv", parse_dates=["Date"])
    us = us.rename(columns={"MP1": "front", "ED4": "y1", "UST2Y": "y2", "UST10Y": "y10"})
    common = []
    for lab, d in [("Fed", us), ("ECB", me), ("BoE", uk)]:
        for per, a, b in [("2013-19", "2013-07-01", "2019-12-31"), ("2022-25", "2022-07-01", "2025-10-31")]:
            g = d[(d.Date >= a) & (d.Date <= b)]
            s = stats(g) if "path" in g else {}
            if lab == "Fed":
                s = {"n": len(g), **{f"rms_{h}": fg.rms(g[h]) for h in H}}
                s["y2_to_y10"] = s["rms_y2"] / s["rms_y10"]
                s["guidance_events"] = np.mean((g.front.abs() < 1) & (g.y2.abs() >= 5))
            s.update(cb=lab, period=per)
            common.append(s)
    common = pd.DataFrame(common).set_index(["cb", "period"])
    print("\nCommon windows:")
    print(common[["n", "rms_front", "rms_y1", "rms_y2", "rms_y10", "y2_to_y10", "guidance_events"]]
          .round(2).to_string())
    common.round(3).to_csv(fd / "common_windows.csv")

    figures(tabs, pcs, fd)


def figures(tabs, pcs, fd):
    INK, INK2, GRID, SURF = fg.INK, fg.INK2, fg.GRID, fg.SURF
    COL = {"front": "#257de0", "y1": "#f47230", "y2": "#00b894", "y10": "#6a4ce2"}
    us = pd.read_csv(fd / "era_table.csv", index_col=0).iloc[:8]
    us = us.rename(columns={"rms_MP1": "rms_front", "rms_ED4": "rms_y1", "rms_UST2Y": "rms_y2",
                            "rms_UST10Y": "rms_y10"})
    us["label"] = [e[2] for e in fg.ERAS]
    panels = [("Federal Reserve (USMPD)", us), ("ECB (EA-MPD)", tabs["ea"]), ("Bank of England (UKMPD)", tabs["uk"])]
    fig, axs = plt.subplots(3, 1, figsize=(11, 11.5), dpi=150)
    ymax = max(p[1][[f"rms_{h}" for h in H]].to_numpy().max() for p in panels) * 1.12
    for ax, (title, t) in zip(axs, panels):
        fg.style(ax)
        x = np.arange(len(t))
        w = 0.19
        for i, h in enumerate(H):
            ax.bar(x + (i - 1.5) * w, t[f"rms_{h}"], w * 0.9, color=COL[h], label=HLABEL[h])
        ax.set_xticks(x, [f"{l}\n(n={int(n)})" for l, n in zip(t["label"], t["n"])], fontsize=8.5, color=INK)
        ax.set_ylim(0, ymax)
        ax.set_ylabel("RMS surprise (bp)", color=INK2, fontsize=9)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
    axs[0].legend(frameon=False, fontsize=9, ncol=4, loc="upper left")
    fig.text(0.01, 0.004, "Front: MP1 (US), OIS 1M (EA), first 3M rate future (UK). 1Y: ED4 (US), OIS 1Y (EA), "
             "1-yr gilt (UK). 2Y/10Y: Treasuries, Bunds, gilts. Monetary-event windows.", fontsize=7.3, color=INK2)
    fig.tight_layout(rect=(0, 0.015, 1, 1))
    fig.savefig(fd / "fig5_cross_country.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
    fg.style(ax)
    x = np.arange(len(pcs))
    ax.bar(x - 0.2, pcs["pc_share_front"], 0.36, color=COL["front"], label="OIS 1M (current meeting)")
    ax.bar(x + 0.2, pcs["pc_share_2y"], 0.36, color=COL["y2"], label="Bund 2Y")
    ax.axhline(0.5, color=INK2, lw=0.8, ls=":")
    ax.set_xticks(x, pcs["label"], fontsize=8.5, color=INK)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Press-conference share", color=INK2, fontsize=9)
    ax.set_title("ECB: share of squared surprise arriving in the press conference, not the press release",
                 loc="left", fontsize=10.5, color=INK)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(fd / "fig6_ecb_pc_share.png")
    plt.close(fig)


if __name__ == "__main__":
    main()

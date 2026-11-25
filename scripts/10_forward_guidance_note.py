"""
Empirical note: where along the curve do FOMC surprises land?

Idea: if near-term forward guidance works, the market should already know
what the Fed will do at this meeting and over the next few meetings, so the
announcement-window surprise should be small at the very front (current-
meeting fed funds, MP1) and in 1-2 year rates, relative to the 10-year. When
guidance is dropped, or the committee becomes less predictable, the surprise
mass should move back into the 1-2 year sector.

Data: SF Fed US Monetary Policy Event-Study Database (USMPD), 'Monetary
Events' sheet (30-min window, 70-100 min with press conference), 1994-present.
Same event filter as 01_load_full_sample.py: unscheduled non-policy events
with MP1 == 0 are dropped.

Maturities (all in bp):
  MP1    current-meeting fed funds surprise (Kuttner)
  FF4    ~3 months ahead (fed funds futures, 3rd-4th meeting)
  ED4    ~1 year ahead (Eurodollar, SOFR futures after the switch) -- the
         1-year point, since USMPD has no 1-year Treasury and OIS1Y only
         starts in 2004 (used as a robustness check)
  UST2Y, UST10Y  on-the-run Treasury yields

Measures per era:
  rms_*        root-mean-square surprise (bp)
  path share   GSS (2005)-style: share of the 2Y surprise variance that is
               orthogonal to the current-meeting surprise (MP1), with the
               2Y-on-MP1 slope fixed at its full-sample non-ZLB value
  front/10Y    rms(2Y)/rms(10Y): is the surprise concentrated at the short
               end (>1) or does it sit mostly in long rates (<1)?
  guidance events  share of meetings with |MP1| < 1bp but |2Y| >= 5bp
               (pure "path" news, nothing about today's decision)
Also GSS target/path factors from MP1, FF4, ED2, ED3, ED4.

Outputs: ../output/fg_era_table.csv, ../output/fg_events.csv,
         ../docs/fg_note/*.png

Usage:  python3 10_forward_guidance_note.py
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Eras: chairs, with Greenspan split 1990s/2000s and Powell split at liftoff.
# Each boundary is the first event of the new era.
ERAS = [
    ("Greenspan 1990s",     "1994-01-01", "Greenspan '94-'99"),
    ("Greenspan 2000s",     "2000-01-01", "Greenspan '00-'05"),
    ("Bernanke pre-ZLB",    "2006-02-01", "Bernanke '06-'08"),
    ("ZLB 1",               "2008-12-17", "ZLB '09-'15"),
    ("Normalization",       "2015-12-16", "Norm. '15-'20"),
    ("ZLB 2",               "2020-03-15", "ZLB '20-'22"),
    ("Powell 2022-26",      "2022-03-16", "Powell '22-'26"),
    ("Warsh",               "2026-05-22", "Warsh '26"),
]
# Sub-periods used in the text
SUBS = [
    ("Greenspan pre-'considerable period'", "2000-01-01", "2003-08-11"),
    ("'Considerable period' / 'measured pace'", "2003-08-12", "2006-01-31"),
    ("Powell hikes 2022-23", "2022-03-16", "2023-12-31"),
    ("Powell 2024 - May 2026", "2024-01-01", "2026-05-21"),
]
COLS = ["MP1", "FF4", "ED4", "UST2Y", "UST10Y"]
NONZLB = ["Greenspan 1990s", "Greenspan 2000s", "Bernanke pre-ZLB",
          "Normalization", "Powell 2022-26"]


def load(data_dir: Path) -> pd.DataFrame:
    f = sorted(glob.glob(str(data_dir / "*_usmpd.xlsx")))[-1]
    d = pd.read_excel(f, sheet_name="Monetary Events")
    d = d[~((d["Unscheduled"] == 1) & (d["MP1"] == 0))].copy()
    for c in ["MP1", "FF4", "ED2", "ED3", "ED4", "OIS1Y", "UST2Y", "UST10Y"]:
        d[c] = d[c] * 100.0  # percentage points -> bp
    starts = pd.to_datetime([e[1] for e in ERAS])
    idx = np.searchsorted(starts.values, d["Date"].values, side="right") - 1
    d["era"] = [ERAS[i][0] for i in idx]
    return d.reset_index(drop=True)


def gss_factors(d: pd.DataFrame) -> pd.DataFrame:
    """Gurkaynak-Sack-Swanson target/path factors. First two principal
    components of standardized [MP1, FF4, ED2, ED3, ED4], rotated so the
    path factor has no loading on MP1, then scaled so target moves MP1
    one-for-one and path moves ED4 one-for-one (both in bp)."""
    X = d[["MP1", "FF4", "ED2", "ED3", "ED4"]].to_numpy()
    mu, sd = X.mean(0), X.std(0)
    Z = (X - mu) / sd
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    F = U[:, :2] * S[:2]
    F = F / F.std(0)
    L = np.linalg.lstsq(F, Z, rcond=None)[0]  # 2 x 5 loadings
    # rotation: second factor orthogonal to first and zero loading on MP1
    a1, a2 = L[0, 0], L[1, 0]
    # columns of rotation matrix: target dir, path dir
    path_dir = np.array([-a2, a1])  # zero loading on MP1
    path_dir /= np.linalg.norm(path_dir)
    targ_dir = np.array([a1, a2]) / np.hypot(a1, a2)  # orthogonal to path_dir
    Fr = np.column_stack([F @ targ_dir, F @ path_dir])
    # scale: regress MP1 on target, ED4 on path
    b_t = np.polyfit(Fr[:, 0], X[:, 0], 1)[0]
    Fr[:, 0] *= b_t
    b_p = sm.OLS(X[:, 4], sm.add_constant(Fr)).fit().params[2]
    Fr[:, 1] *= b_p
    return pd.DataFrame(Fr, columns=["target", "path"], index=d.index)


def rms(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return np.sqrt(np.mean(x ** 2)) if len(x) else np.nan


def era_stats(g: pd.DataFrame, beta: float) -> dict:
    path2 = g["UST2Y"] - beta * g["MP1"]
    out = {"n": len(g)}
    for c in COLS:
        out[f"rms_{c}"] = rms(g[c])
    out["rms_OIS1Y"] = rms(g["OIS1Y"])
    out["rms_target"] = rms(g["target"])
    out["rms_path"] = rms(g["path"])
    out["path_share_2Y"] = np.sum(path2 ** 2) / np.sum(g["UST2Y"] ** 2)
    out["gss_path_share"] = np.sum(g["path"] ** 2) / (np.sum(g["path"] ** 2) + np.sum(g["target"] ** 2))
    out["front_to_10Y"] = out["rms_UST2Y"] / out["rms_UST10Y"]
    out["ED4_to_10Y"] = out["rms_ED4"] / out["rms_UST10Y"]
    out["guidance_events"] = np.mean((g["MP1"].abs() < 1) & (g["UST2Y"].abs() >= 5))
    out["mp1_zero_share"] = np.mean(g["MP1"].abs() < 1)
    return out


def bootstrap_ci(g, beta, key, B=2000, seed=0):
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(B):
        s = g.iloc[rng.integers(0, len(g), len(g))]
        vals.append(era_stats(s, beta)[key])
    return np.nanpercentile(vals, [5, 95])


def perm_pvalue(a: pd.Series, b: pd.Series, B=20000, seed=0):
    """One-sided: is rms(a) larger than rms of a random draw of len(a) from a u b?"""
    rng = np.random.default_rng(seed)
    pool = np.concatenate([a.to_numpy(), b.to_numpy()])
    obs = rms(a)
    k = len(a)
    hits = sum(rms(rng.choice(pool, k, replace=False)) >= obs for _ in range(B))
    return obs, hits / B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    ap.add_argument("--out-dir", default="../output")
    ap.add_argument("--fig-dir", default="../docs/fg_note")
    args = ap.parse_args()
    out_dir, fig_dir = Path(args.out_dir), Path(args.fig_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    d = load(Path(args.data_dir))
    d[["target", "path"]] = gss_factors(d)

    nz = d[d["era"].isin(NONZLB)]
    beta = sm.OLS(nz["UST2Y"], sm.add_constant(nz["MP1"])).fit().params["MP1"]
    print(f"events: {len(d)}  ({d.Date.min().date()} - {d.Date.max().date()})")
    print(f"2Y-on-MP1 slope (non-ZLB, full sample): {beta:.3f}")

    rows = []
    for name, _, short in ERAS:
        g = d[d["era"] == name]
        s = era_stats(g, beta)
        lo, hi = bootstrap_ci(g, beta, "path_share_2Y")
        s.update(era=name, label=short, start=g.Date.min().date(), end=g.Date.max().date(),
                 path_share_2Y_lo=lo, path_share_2Y_hi=hi)
        rows.append(s)
    for name, a, b in SUBS:
        g = d[(d.Date >= a) & (d.Date <= b)]
        s = era_stats(g, beta)
        s.update(era=name, label=name, start=g.Date.min().date(), end=g.Date.max().date())
        rows.append(s)
    tab = pd.DataFrame(rows).set_index("era")
    show = ["start", "end", "n", "rms_MP1", "rms_FF4", "rms_ED4", "rms_UST2Y", "rms_UST10Y",
            "front_to_10Y", "path_share_2Y", "gss_path_share", "guidance_events", "mp1_zero_share"]
    pd.set_option("display.width", 250)
    print(tab[show].round(2).to_string())
    tab.to_csv(out_dir / "fg_era_table.csv")
    tab[show + ["rms_OIS1Y"]].round(3).to_csv(fig_dir / "era_table.csv")

    # Scheduled-only robustness (drop intermeeting moves)
    print("\nScheduled meetings only:")
    rob = []
    for name, _, _ in ERAS:
        g = d[(d["era"] == name) & (d["Unscheduled"] == 0)]
        s = era_stats(g, beta)
        s["era"] = name
        rob.append(s)
    rob = pd.DataFrame(rob).set_index("era")
    print(rob[["n", "rms_MP1", "rms_ED4", "rms_UST2Y", "rms_UST10Y", "front_to_10Y",
               "path_share_2Y", "guidance_events"]].round(2).to_string())
    rob.to_csv(out_dir / "fg_era_table_scheduled.csv")

    # Warsh vs late Powell: permutation tests on rms surprise
    w = d[d["era"] == "Warsh"]
    p = d[(d.Date >= "2024-01-01") & (d.Date < "2026-05-22")]
    p_all = d[d["era"] == "Powell 2022-26"]
    print("\nWarsh (n=%d) vs Powell: permutation p-value that a random draw of %d meetings"
          " has rms >= Warsh's" % (len(w), len(w)))
    for c in ["MP1", "FF4", "ED4", "UST2Y", "UST10Y"]:
        o1, p1 = perm_pvalue(w[c], p[c])
        o2, p2 = perm_pvalue(w[c], p_all[c])
        print(f"  {c:7s} Warsh rms {o1:5.1f}bp | vs Powell 2024-26 rms {rms(p[c]):5.1f}bp p={p1:.3f}"
              f" | vs Powell 2022-26 rms {rms(p_all[c]):5.1f}bp p={p2:.3f}")
    # 2000s vs 1990s
    a = d[d.era == "Greenspan 1990s"]; b = d[d.era == "Greenspan 2000s"]
    cp = d[(d.Date >= "2003-08-12") & (d.Date <= "2006-01-31")]
    print("\nGreenspan 2003-05 'considerable period/measured pace' vs Greenspan 1990s (one-sided p that a random"
          " draw is as SMALL):")
    for c in ["MP1", "FF4", "ED4", "UST2Y", "UST10Y"]:
        rng = np.random.default_rng(1)
        pool = np.concatenate([cp[c], a[c]])
        obs = rms(cp[c])
        pv = np.mean([rms(rng.choice(pool, len(cp), replace=False)) <= obs for _ in range(20000)])
        print(f"  {c:7s} 03-05 rms {obs:5.1f}bp | 1990s {rms(a[c]):5.1f}bp | p={pv:.3f}")

    d.to_csv(out_dir / "fg_events.csv", index=False)

    # Rolling 16-event windows
    roll = pd.DataFrame({"Date": d["Date"]})
    W = 16
    for c in COLS:
        roll[c] = d[c].pow(2).rolling(W).mean().pow(0.5)
    path2 = d["UST2Y"] - beta * d["MP1"]
    roll["path_share"] = path2.pow(2).rolling(W).sum() / d["UST2Y"].pow(2).rolling(W).sum()
    roll["front_to_10Y"] = roll["UST2Y"] / roll["UST10Y"]
    pp, tt = d["path"].pow(2).rolling(W).sum(), d["target"].pow(2).rolling(W).sum()
    roll["gss_path_share"] = pp / (pp + tt)
    roll.to_csv(out_dir / "fg_rolling.csv", index=False)

    make_figures(d, tab, roll, fig_dir)


# ---------- figures ----------
INK, INK2, GRID, SURF = "#191919", "#666666", "#e6e6e6", "#ffffff"
SERIES = {"MP1": "#257de0", "ED4": "#f47230", "UST2Y": "#00b894", "UST10Y": "#6a4ce2"}
LABEL = {"MP1": "Current meeting (MP1)", "FF4": "~3 months (FF4)",
         "ED4": "~1 year (ED4)", "UST2Y": "2-yr Treasury", "UST10Y": "10-yr Treasury"}
SHADE = [("2008-12-16", "2015-12-16", "ZLB"), ("2020-03-15", "2022-03-16", "ZLB"),
         ("2003-08-12", "2006-01-31", "'considerable period'\n/'measured pace'")]


def style(ax):
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def shade(ax, ytext):
    for a, b, lab in SHADE:
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color="#f0f0f0", zorder=0, lw=0)
        ax.text(pd.Timestamp(a) + (pd.Timestamp(b) - pd.Timestamp(a)) / 2, ytext, lab,
                ha="center", va="top", fontsize=8, color=INK2)
    ax.axvline(pd.Timestamp("2026-05-22"), color=INK2, lw=1, ls=":")
    ax.text(pd.Timestamp("2026-05-22"), ytext, " Warsh", ha="left", va="top", fontsize=8, color=INK2)


def make_figures(d, tab, roll, fig_dir):
    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": SURF})

    # Fig 1: era bars, rms surprise by maturity
    eras = [e[0] for e in ERAS]
    t = tab.loc[eras]
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=150)
    style(ax)
    keys = ["MP1", "ED4", "UST2Y", "UST10Y"]
    w = 0.19
    x = np.arange(len(eras))
    for i, k in enumerate(keys):
        ax.bar(x + (i - 1.5) * w, t[f"rms_{k}"], w * 0.9, color=SERIES[k], label=LABEL[k])
    ax.set_xticks(x, [f"{e[2]}\n(n={int(t.loc[e[0], 'n'])})" for e in ERAS], fontsize=9, color=INK)
    ax.set_ylabel("RMS surprise (bp)", color=INK2, fontsize=9)
    ax.set_title("How big are FOMC announcement surprises, and where on the curve?",
                 loc="left", fontsize=12, color=INK)
    ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper left", bbox_to_anchor=(0, 1.0))
    ax.set_ylim(0, max(t[[f"rms_{k}" for k in keys]].max()) * 1.18)
    fig.text(0.01, 0.005, "Source: SF Fed USMPD 'Monetary Events' (30-100 min windows), "
             "1994 - Oct 2026. Warsh: 4 meetings (Jun-Oct 2026).", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(fig_dir / "fig1_rms_by_era.png")
    plt.close(fig)

    # Fig 2: rolling rms by maturity
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=150)
    style(ax)
    for k in keys:
        ax.plot(roll["Date"], roll[k], color=SERIES[k], lw=2, label=LABEL[k])
    top = np.nanmax(roll[keys].to_numpy()) * 1.05
    ax.set_ylim(0, top)
    shade(ax, top * 0.99)
    ax.set_ylabel("RMS surprise, trailing 16 events (bp)", color=INK2, fontsize=9)
    ax.set_title("Surprise size by maturity, rolling 16 FOMC events (~2 years)",
                 loc="left", fontsize=12, color=INK)
    ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper right", bbox_to_anchor=(1, 0.9))
    fig.tight_layout()
    fig.savefig(fig_dir / "fig2_rolling_rms.png")
    plt.close(fig)

    # Fig 3: path share and front/10Y ratio (two panels, separate axes)
    fig, axs = plt.subplots(2, 1, figsize=(11, 6.4), dpi=150, sharex=True)
    for ax in axs:
        style(ax)
    axs[0].plot(roll["Date"], roll["gss_path_share"], color="#257de0", lw=2)
    axs[0].set_ylim(0, 1.05)
    shade(axs[0], 1.04)
    axs[0].set_title("Path share: path factor / (path + target), GSS decomposition of "
                     "MP1, FF4, ED2-ED4", loc="left", fontsize=11, color=INK)
    axs[1].plot(roll["Date"], roll["front_to_10Y"], color="#00b894", lw=2)
    axs[1].axhline(1, color=INK2, lw=0.8)
    ymax = np.nanmax(roll["front_to_10Y"]) * 1.08
    axs[1].set_ylim(0, ymax)
    shade(axs[1], ymax * 0.99)
    axs[1].set_title("Front-end concentration: RMS 2-yr surprise / RMS 10-yr surprise "
                     "(>1 = surprise sits at the short end)", loc="left", fontsize=11, color=INK)
    fig.text(0.01, 0.005, "Trailing 16 events. Target and path factors in bp (target scaled to "
             "MP1, path to ED4); share of summed squares.", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(fig_dir / "fig3_path_share_front_ratio.png")
    plt.close(fig)

    # Fig 4: event scatter MP1 vs 2Y, by era group
    fig, ax = plt.subplots(figsize=(7.5, 6), dpi=150)
    style(ax)
    ax.grid(axis="x", color=GRID, lw=0.8)
    groups = [("Greenspan / Bernanke pre-ZLB (1994-2008)",
               ["Greenspan 1990s", "Greenspan 2000s", "Bernanke pre-ZLB"], "#257de0", "o"),
              ("ZLB (2009-15, 2020-22)", ["ZLB 1", "ZLB 2"], "#f47230", "s"),
              ("Powell 2022-26", ["Powell 2022-26"], "#00b894", "D")]
    for lab, es, col, m in groups:
        g = d[d.era.isin(es)]
        ax.scatter(g["MP1"], g["UST2Y"], s=22, color=col, marker=m, alpha=0.75,
                   edgecolor=SURF, lw=0.6, label=lab)
    g = d[d.era == "Warsh"]
    ax.scatter(g["MP1"], g["UST2Y"], s=70, color=INK, marker="*", label="Warsh 2026", zorder=5)
    for _, r in g.iterrows():
        ax.annotate(r.Date.strftime("%b"), (r.MP1, r.UST2Y), xytext=(5, 3),
                    textcoords="offset points", fontsize=8, color=INK)
    ax.set_xlim(-30, 20)
    ax.set_ylim(-25, 20)
    ax.axhline(0, color=INK2, lw=0.6); ax.axvline(0, color=INK2, lw=0.6)
    ax.set_xlabel("Current-meeting surprise, MP1 (bp)", color=INK2, fontsize=9)
    ax.set_ylabel("2-yr Treasury surprise (bp)", color=INK2, fontsize=9)
    ax.set_title("Path news: 2-yr moves with no surprise about today's decision",
                 loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.text(0.01, 0.005, "Axes clipped at -30/+20 (MP1) and -25/+20 (2Y); a few intermeeting "
             "cuts lie outside.", fontsize=7, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(fig_dir / "fig4_mp1_vs_2y.png")
    plt.close(fig)


if __name__ == "__main__":
    main()

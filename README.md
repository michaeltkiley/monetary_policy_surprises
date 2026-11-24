# monetary_policy_surprises — Policy Surprise Dashboard

Live at [michaeltkiley.github.io/monetary_policy_surprises](https://michaeltkiley.github.io/monetary_policy_surprises/),
linked from [michaeltkiley.github.io](https://michaeltkiley.github.io/).

Four tabs: market-implied policy rate expectations (Fed funds futures +
SOFR options), monetary policy surprises around FOMC announcements, and
their pass-through to equity prices and corporate bond yields, before vs.
after the zero lower bound.

## Pipeline

```
scripts/00_fetch_usmpd.py           SF Fed: US Monetary Policy Event-Study Database
scripts/00_fetch_fred_corporate.py  FRED: DGS10/DGS20/DAAA/DBAA
scripts/05_fetch_fred_dff.py        FRED: daily effective fed funds rate
scripts/06_fetch_dtcc_ois.py        DTCC public swap tape: cache OIS trades (see DTCC section)
scripts/07_build_dtcc_ois_curves.py OIS expected-rate paths + SOFR OIS par curve from that cache
scripts/01_load_full_sample.py      builds the full-history event panel + era classification
scripts/02_production_estimates.py  IV-GMM pass-through estimates (equity, corporate)
scripts/03_build_dashboard_data.py  assembles the dashboard JSON payload
scripts/04_build_dashboard.py       injects it into dashboard_template.html -> docs/index.html
```

`00_fetch_usmpd.py`, `00_fetch_fred_corporate.py`, and `05_fetch_fred_dff.py`
all pull full-history, public data on every run — same idempotent,
stateless pattern as the [termprem](https://github.com/michaeltkiley/termprem)
tracker.

## DTCC OIS curves (experimental, not yet in the dashboard)

Free, end-of-day OIS-based alternatives to the CME-based expected-rate paths,
built from DTCC's public swap-trade tape (`pddata.dtcc.com`, CFTC public price
dissemination). These are *trades*, not dealer quotes.

```
scripts/06_fetch_dtcc_ois.py          fetch + cache every cleared USD SOFR / fed funds OIS trade, per day
scripts/07_build_dtcc_ois_curves.py   derive curves from the cache (re-runnable without refetching)
```

Outputs under `data/` (gitignored): `dtcc_expected_curve_daily.csv` (TEST
combined curve: published EFFR, meeting-by-meeting fed funds OIS nodes to ~1Y,
annual SOFR OIS forwards from 1Y to 10Y), `dtcc_expected_path_daily.csv`
(all nodes before assembly), `dtcc_meeting_fwd_daily.csv`,
`dtcc_sofr_par_daily.csv` (2Y/5Y/10Y/30Y etc.), `dtcc_meeting_calendar.csv`.
`python3 07_build_dtcc_ois_curves.py --report` prints coverage, package-vs-
outright, forecast-error-vs-realized-EFFR and SOFR-OIS-vs-Treasury diagnostics.

Testing-grade caveats: the combined curve has no node for roughly months 6-12
(meeting nodes thin out; the annual forwards start at 1Y); SOFR OIS is
converted to EFFR-equivalent with a short-end basis (~2bp median) that is an
assumption at long horizons, where forwards also contain term premia; long
forwards are differenced from interpolated par rates, so day-to-day noise is
~4-7bp. Off-market-coupon swaps (>15bp from the day/tenor median) are dropped.

Key caveats: DTCC retains only a rolling ~2 years of files, so run `06`
regularly -- the per-day cache under `data/dtcc/trades/` is the archive.
Meeting-dated swaps are mostly fed funds OIS; coverage thins beyond ~4
meetings (SOFR-OIS par pillars take over). Fed funds OIS is preferred and SOFR
OIS is basis-adjusted to EFFR where it is thin. Closing window is 3-5pm ET,
widened (flagged in a `window` column) when too few trades.

## The Rate Expectations tab is different

Three of the four tabs run entirely on public data. The fourth — market-
implied policy expectations from CME Fed funds futures and SOFR options —
depends on a daily settlement file with **no free backfill**: it has to be
captured the day it's published or it's gone. That capture, parsing, and
the accumulating probability database live in a **separate, private** repo
(`cme-rate-data`), not here — see its README for the full chain (Gmail →
Drive → GitHub → parse/probs → export).

The build only reads the small, already-derived slice that repo exports:
`latest.json` — a market-implied Fed-funds rate path and two bucketed SOFR
probability distributions — and `history.json`, the same slice for every
past settlement date, which backs the tab's two date pickers (defaults:
latest date vs. start of the prior month; without the file the pickers are
hidden and the default pair still shows). Neither is committed here: the
workflow checks them out of `cme-rate-data/export/` at build time (secret
`CME_DATA_PAT`, a fine-grained token with read-only Contents access to that
repo) into a gitignored `cme_derived/`, and deploys the built page straight
to GitHub Pages (Settings → Pages → Source: GitHub Actions), so the data
ships only inside the page. No raw settlement prices or option strikes are
involved; see `scripts/lib_cme.py` for how the files are read. A
`repository_dispatch` from the private repo's workflow after each CME update
is one of this repo's triggers — the others are a daily schedule, so the
non-CME tabs stay fresh even on days without a CME update, and pushes to
`main`.

To run the full pipeline locally (needs `cme_derived/latest.json`, and
optionally `history.json`, copied from `cme-rate-data/export/`):

```
cd scripts
python3 00_fetch_usmpd.py && python3 00_fetch_fred_corporate.py && python3 05_fetch_fred_dff.py && \
  python3 01_load_full_sample.py --force && python3 02_production_estimates.py && \
  python3 03_build_dashboard_data.py && python3 04_build_dashboard.py
```

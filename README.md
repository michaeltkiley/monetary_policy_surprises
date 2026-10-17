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
scripts/01_load_full_sample.py      builds the full-history event panel + era classification
scripts/02_production_estimates.py  IV-GMM pass-through estimates (equity, corporate)
scripts/03_build_dashboard_data.py  assembles the dashboard JSON payload
scripts/04_build_dashboard.py       injects it into dashboard_template.html -> docs/index.html
```

`00_fetch_usmpd.py`, `00_fetch_fred_corporate.py`, and `05_fetch_fred_dff.py`
all pull full-history, public data on every run — same idempotent,
stateless pattern as the [termprem](https://github.com/michaeltkiley/termprem)
tracker.

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

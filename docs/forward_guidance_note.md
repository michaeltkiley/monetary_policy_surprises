# Where do FOMC surprises land? Near-term forward guidance across chairs, 1994–2026

*Empirical note, draft, November 2026. Code: `scripts/10_forward_guidance_note.py`. Data: SF Fed US Monetary Policy Event-Study Database (USMPD), 277 FOMC events, Feb 1994 – Oct 2026.*

## 1. The question

Forward guidance is under attack, most visibly from the new Fed chair. At his first meeting (June 17, 2026) Kevin Warsh cut the statement down, dropped forward guidance and said "I can't give you any forward guidance about what we're going to do next." This note uses a simple measurement to judge the debate empirically. **If near-term guidance works, markets should already know what the Fed will do at this meeting and over the next year or two. Announcement-window surprises should then be small at the front of the curve (the current meeting, 3 months, 1 year and 2 years) relative to long rates.** Where the surprise lands on the curve is a revealed-preference measure of how much near-term guidance is in effect.

## 2. Measurement

For each FOMC event the USMPD records tight-window changes (30 min, or 70–100 min when there is a press conference) in:

| Horizon | Instrument |
|---|---|
| Current meeting | MP1 (Kuttner surprise in fed funds futures) |
| ~3 months | FF4 |
| ~1 year | ED4 (Eurodollar, later SOFR, futures). USMPD has no 1-yr Treasury; OIS1Y from 2004 is used as a check. |
| 2 years / 10 years | UST2Y, UST10Y |

Statistics by era (all in bp):

* **RMS surprise** at each horizon.
* **Path share**: the Gürkaynak–Sack–Swanson (2005) decomposition of [MP1, FF4, ED2, ED3, ED4] into a *target* factor and a *path* factor. The path factor has no effect on the current meeting. The path share is path² / (path² + target²).
* **Front/10Y ratio**: RMS(2Y) / RMS(10Y). Above 1 means the news is mainly about the near-term policy path. Below 1 means it is mainly about long rates (QE, term premia).
* **Guidance events**: the share of meetings with |MP1| < 1 bp but |2Y| ≥ 5 bp. These are pure path news: the decision itself was known, the path was not.

Eras: Greenspan 1994–99; Greenspan 2000–Jan 2006; Bernanke pre-ZLB; ZLB 2009–15; normalization 2015–20; ZLB 2020–22; Powell 2022–May 2026; Warsh (June 2026 onward, **4 meetings**). The event filter is the same as the dashboard's: non-policy unscheduled events with MP1 = 0 are dropped.

## 3. Results

![RMS surprise by era](fg_note/fig1_rms_by_era.png)

| Era | n | MP1 | FF4 | ED4 (1y) | 2Y | 10Y | 2Y/10Y | Path share | Guidance events |
|---|---|---|---|---|---|---|---|---|---|
| Greenspan 1994–99 | 50 | 7.2 | 4.8 | 6.8 | 6.1 | 4.6 | 1.34 | 0.46 | 4% |
| Greenspan 2000–03 | 31 | 13.1 | 9.9 | 9.1 | 8.1 | 4.1 | 1.96 | 0.24 | 3% |
| Greenspan Aug 2003–Jan 2006 ("considerable period"/"measured pace") | 21 | **1.2** | **1.5** | 7.7 | 5.6 | 4.7 | 1.18 | **0.92** | 19% |
| Bernanke 2006–08 | 28 | 11.3 | 7.0 | 8.3 | 6.7 | 4.1 | 1.63 | 0.33 | 7% |
| ZLB 2009–15 | 55 | 1.1 | 1.2 | 4.6 | 3.9 | **7.6** | **0.51** | 0.89 | 16% |
| Normalization 2015–20 | 35 | 5.4 | 3.5 | 6.0 | 5.5 | 3.7 | 1.49 | 0.56 | 11% |
| ZLB 2020–22 | 19 | 4.5 | 1.6 | 4.4 | 5.4 | **8.3** | **0.65** | 0.59 | 5% |
| Powell 2022–23 (hikes) | 15 | 1.8 | 3.9 | **14.0** | **10.5** | 5.5 | 1.90 | 0.96 | **47%** |
| Powell 2024–May 2026 | 19 | 2.9 | 3.6 | 6.9 | 5.6 | 3.8 | 1.50 | 0.79 | 37% |
| **Warsh, Jun–Oct 2026** | 4 | 3.6 | **8.1** | **12.6** | **10.5** | 4.7 | **2.26** | 0.89 | 25% |

Full table (with bootstrap bands and OIS1Y): `fg_note/era_table.csv`. The ZLB 2020–22 and Greenspan 2000–03 rows include large intermeeting cuts. Dropping unscheduled events lowers their MP1 sharply but leaves the conclusions unchanged (`output/fg_era_table_scheduled.csv`).

![Rolling RMS by maturity](fg_note/fig2_rolling_rms.png)

![Path share and front-end concentration](fg_note/fig3_path_share_front_ratio.png)

**Finding 1: the ZLB shows forward guidance clearly, but at the long end.** In 2009–15, current-meeting and 3-month surprises are about 1 bp. One- and two-year surprises shrink to 4–5 bp, while 10-year surprises roughly double to 7.6 bp. The 2Y/10Y ratio is the only sustained stretch below 1 in the sample. Its troughs are about 0.3, in 2010–11 (QE2) and 2013–14 (taper). The 2Y reactivates in late 2014, ahead of liftoff (the Swanson–Williams pattern, also used in `01_load_full_sample.py`). ZLB 2020–22 shows the same pattern. Calendar and threshold guidance pinned the 1–2 year sector, and the remaining news moved out to term premia and the expected length of the zero rate period.

**Finding 2: the Greenspan shift is from the 1990s to 2003–05, not to "the 2000s" as a whole.** Splitting Greenspan's 2000s at August 2003 matters:

* **2000–03** looks like the 1990s, only with more surprise: MP1 at 13 bp and a path share of 0.24. Policy surprises were mostly surprises about the decision itself.
* **Aug 2003–Jan 2006** ("considerable period", then "measured pace") is where the regime changes. MP1 falls from 7.2 bp in the 1990s to 1.2 bp, and FF4 from 4.8 to 1.5. Both declines are significant on a permutation test, p < 0.001. The path share jumps from about 0.2–0.5 to 0.92, about the ZLB level.
* The 1-yr and 2-yr surprises did **not** fall (7.7 and 5.6 bp vs 6.8 and 6.1 bp; p = 0.67 and 0.41). Guidance with a positive policy rate eliminated surprises at the 0–3 month horizon. It moved uncertainty to the 1–2 year horizon instead of removing it.

**Finding 3: 2022–26 is "guidance about the meeting, not about the path."** Under Powell after liftoff, MP1 stays small (1.8–2.9 bp): decisions were telegraphed through speeches, the SEP and press leaks. Surprises at 1 year (14 bp) and 2 years (10.5 bp) in 2022–23 are the largest for any era in the sample. Nearly half of meetings (47%) were pure path news. The path share is at its sample high (0.96). This is the signature of "meeting-by-meeting, data-dependent" policy: the market knew what the Fed would do in the meeting, but not what it would do next. The 2024–26 period is calmer but has the same structure.

**Finding 4: Warsh. Dropping guidance raised front-end surprises, not meeting-day surprises.** Over Warsh's four meetings:

* RMS surprises at 3 months (8.1 bp), 1 year (12.6 bp) and 2 years (10.5 bp) are about double the 2024–26 Powell average.
* By permutation test against Powell 2024–26 meetings: FF4 p = 0.008, ED4 p = 0.021, 2Y p = 0.012.
* MP1 (p = 0.18) and the 10-year (p = 0.23) are not unusual.
* The 2Y/10Y ratio of 2.26 is the highest of any era.

June 2026 is the clearest case: MP1 = 0 and the decision was fully expected, yet the 2Y rose 14 bp and ED4 16.5 bp. That move combines the stripped statement with projections showing hikes. In July and October the 2Y fell while the 10Y rose (+2 bp each time). That twist is very rare: 2Y down at least 3 bp with 10Y up at least 2 bp happened at only 2 of the 273 earlier events (Sept 17, 2001 and Nov 4, 2009), and at 2 of Warsh's 4. It is a candidate signature of a credibility or term-premium channel.

![MP1 vs 2Y](fg_note/fig4_mp1_vs_2y.png)

**Caveats.** Four meetings is not a regime. The June meeting combines a new chair, a new statement format and a hawkish SEP, so "first-meeting news" and "no guidance" cannot be separated yet. The Warsh results also hold only against the calm 2024–26 benchmark. Against all of Powell 2022–26, which includes the 2022 hiking shock, the 1y and 2y differences are not significant (p ≈ 0.2). The window length changed with press conferences (2011 on, every meeting since 2019), which mechanically raises path news later in the sample. ED4 changed from Eurodollar to SOFR futures in 2022. The 2Y/10Y ratio mixes path news with term-premium news.

## 4. Implications and research questions

1. **Guidance moves surprises along the curve rather than removing them.** In 2003–05, at the ZLB and under Powell, guidance (or telegraphed decisions) eliminated meeting-day surprises. The uncertainty did not disappear. It showed up at the 1–2 year horizon (2003–05, 2022–26) or the 10-year (ZLB). A natural test of the critics' claim is whether *total* surprise variance (summed over the curve, or measured as a variance-weighted duration) is lower under guidance. Here it is not obviously lower.
2. **Is the Warsh shift a level shift or a first-meeting transition?** Prediction: if dropping guidance permanently raises 3m–2y surprise variance, the Warsh numbers should stay roughly 2x the 2024–26 Powell level. If June was a one-time reset, they should revert. Every new meeting adds a point; the script reruns on the live USMPD file.
3. **Do larger front-end surprises pass through more to the real economy?** The 2Y-to-corporate-spread and 2Y-to-equity pass-through estimates on the dashboard (`02_production_estimates.py`) can be split by guidance regime. If the 1–2 year sector drives credit conditions, a no-guidance Fed has more power per meeting and more volatility per meeting.
4. **Is the Greenspan 2003–05 regime the right model for Warsh?** Commentary has compared Warsh's approach to Greenspan's. The data separate two very different Greenspan regimes: the 1990s (surprise about the decision, path share ~0.4) and 2003–05 (no surprise about the decision, path share ~0.9). If "Greenspan-style" means the 1990s, MP1 surprises should rise again. So far they have not: Warsh's path share is 0.89.
5. **What explains the 2Y-down/10Y-up twist?** Event-level decomposition of the July and October 2026 meetings using TIPS (inflation compensation vs real) and the dashboard's OIS curve would show whether long-end selloffs on dovish front-end news reflect inflation risk premia under a less predictable Fed.
6. **Measurement extensions.** Add the Bauer–Swanson (2023) orthogonalized surprise and the press-conference/statement split (USMPD has both sheets). Extend to ECB and BoE, where guidance regimes also changed, to test whether "surprise moves out the curve" is a general result.

---
*Reproduce:* `cd scripts && python3 00_fetch_usmpd.py && python3 10_forward_guidance_note.py` (needs pandas, numpy, statsmodels, matplotlib, openpyxl). Console output includes the permutation tests quoted above.

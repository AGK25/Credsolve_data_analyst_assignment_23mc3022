# Collections Analytics — "Has recovery really improved 11%?"

**Answer: no. 96% of the reported improvement is the difference between a 28-day February and a
31-day March.**

| | |
|---|---:|
| Reported Feb → Mar improvement | **+11.19%** |
| Attributable to month length | **+10.71%** |
| Genuine change in daily collection rate | **+0.43%** |

The components are multiplicative and reconstruct the headline exactly: 1.1071 × 1.0043 = 1.1119.
Measured per day, the seven months are flat — fitted trend **−0.62%/month**, 95% CI
[−1.63%, +0.38%], p = 0.23 across 212 days.

---

## Run it

```bash
# 1. place the 17 source CSVs in data/raw/   (borrowers.csv, accounts.csv, ...)
# 2. start the database and install dependencies
docker compose up -d          # Postgres 16 on :5432
pip install -r requirements.txt
# 3. run everything
make all                      # ingest → staging → forensics → golden → metrics → analysis → verify
```

Verified end-to-end from a dropped database: `make all` rebuilds every table, figure, chart and
document from the raw CSVs alone and finishes `ALL 30 CHECKS PASSED`.

`make all` runs the whole pipeline and finishes with 30 assertions. The build **fails** if the
lineage stops reconciling or if a headline figure drifts from its independent re-derivation.

Individual stages: `make load staging forensics golden metrics analysis dashboard verify`.

## Deliverables

| # | Deliverable | Location |
|---|---|---|
| 1 | SQL repository | [`sql/`](sql/) — 5 files, all executed and verified |
| 2 | Analysis notebook | [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb) |
| 3 | Golden dataset + pipeline | [`outputs/golden_dataset/`](outputs/golden_dataset/), [`pipeline/`](pipeline/) |
| 4 | Data quality report | [`docs/DATA_QUALITY_REPORT.md`](docs/DATA_QUALITY_REPORT.md) |
| 5 | Executive dashboard | [`dashboard/executive_dashboard.html`](https://sparkly-clafoutis-c4ab0f.netlify.app/) |
| 6 | Executive memo | [`docs/EXECUTIVE_MEMO.md`](https://drive.google.com/file/d/1Y9ZwUmKabP5NMR1uuFHa33OWATqCMGK_/view?usp=sharing) |
| 7 | Architecture diagram | [`docs/ARCHITECTURE.md`](https://drive.google.com/file/d/1jJPsPYaveHolJWn9rGeHivLnbZR6U_u_/view?usp=sharing) |
| + | Decision log | [`docs/DECISIONS.md`](docs/DECISIONS.md) — 17 judgment calls, each with the alternative rejected |

## The three findings that matter

**1. The 11% is a calendar artifact.** Someone compared monthly totals and quoted the single
favourable month-pair as a run rate. Every other pair in the series is negative or small.

**2. The obvious cleaning rule would have destroyed ₹25.01 Cr.** `payment_reference` repeats
across 3,745 values, which looks like mass double-counting. It is not — 7,366 of those rows point
at different accounts, amounts *and* timestamps. Deduplicating on it deletes 4,678 rows of which
only **500** are genuine duplicates. The naive fix is ten times larger than the problem.

**3. No operational lever in this data predicts payment.**

| | |
|---|---:|
| Accounts never contacted that paid | 43.2% |
| Accounts that answered a call and paid | 44.7% |
| Difference | +1.5 pp (p = 0.26) |
| Effect we had 80% power to detect | 3.7 pp |
| Between-agent variation explained by skill | 1.5% |
| "PTP kept" → money arrived in 30 days | **7.8%** (benchmark: 70–90%) |
| "PTP broken" → money arrived in 30 days | 6.6% |

Null results are reported against the effect size each test *could* have detected. "We found
nothing" is uninformative; "an effect above 3.7 pp would have shown up" is a finding.

## How the pipeline is built

```
raw → staging → clean → golden → metrics
 │       │        │        │        └── certified definitions; per-day only
 │       │        │        └─────────── conformed facts + dims, declared grain
 │       │        └──────────────────── dedup, entity resolution, validity rules
 │       └───────────────────────────── typed + timezone-normalised, asserted 1:1 with raw
 └───────────────────────────────────── verbatim CSV, all TEXT, nothing rejected
                                             ↓
                                        reject.ledger — every removed row, with a reason code
```

Two invariants are enforced at build time:

- `count(stg.x) = count(raw.x)` for all 17 tables — staging fixes *representation*, never content.
- `raw − rejected = golden` for every entity — so "quantify the impact of your cleaning decisions"
  has an exact answer rather than an estimate.

| Entity | Raw | Rejected | Golden | Reconciles |
|---|---:|---:|---:|:--:|
| payments | 25,500 | 1,442 | 24,058 | ✓ |
| calls | 91,350 | 4,593 | 86,757 | ✓ |
| borrowers | 30,600 | 19,585 | 11,015 | ✓ |
| accounts | 30,000 | 0 | 30,000 | ✓ |

Deduplication reconciles exactly against the injected surplus: 25,500 − 25,000 distinct payment_ids
= 500 removed; 91,350 − 90,000 = 1,350.

## What we tested — including what we ruled out

16 forensic tests across the seven hypotheses in Part 2. **12 confirmed, 4 rejected.** Rejections
carry the same weight as confirmations: an analysis that only reports what it found is a fishing
expedition.

Ruled out: portfolio mix change · denominator manipulation · disposition-code migration ·
operational double-charges. Mix shift is the most common real-world explanation for a jump like
this; eliminating it is what leaves the calendar standing as the only explanation.

Also found not computable from this data, and reported as such rather than estimated: **right-party
contact rate, PTP kept rate, and cost per rupee recovered** — 3 of the 10 metrics the brief asks
us to challenge. Likewise **calling time** and **agent tenure**: hour-of-day is statistically
uniform (χ² p = 0.45) and the agents dimension has no resolvable identity, so both analyses are
withdrawn with proof rather than produced from noise.

## The counterfactual

Part 4 assumes targeting changed mid-year. **It did not.** The strongest candidate break date
(25 March) reaches p = 0.061 uncorrected — and p = 1.00 after correcting for having tested 152
candidate dates. Difference-in-differences at that date, with parallel trends satisfied (p = 0.70)
and 0 of 4 placebo dates showing a false effect, returns **₹+21 per account-week, 95% CI
[−₹94, +₹136]**.

**Counterfactual: recovery would have been the same** — ₹116.9 Cr vs ₹117.5 Cr actual, CI spanning
the actual figure.

## The ₹10 Cr recommendation

**Spend ₹1.2 Cr proving which lever works; hold ₹8.8 Cr until it reports.**

This data cannot rank the six options — every one shows an effect indistinguishable from zero.
A 90-day randomised holdout across 60,000 accounts is sized to detect a 2 pp difference at 80%
power, comfortably below the 3.7 pp the observational data could not rule out. If forced to pick
one area today it is **borrower targeting**, on the economic grounds that its cost scales with
accounts rather than headcount and is therefore reversible — reasoning that is transparent and
should be weighed as such, not as an empirical result.

## Repository layout

```
sql/          00_schemas · 01_staging · 02_forensics · 03_clean_golden · 04_metrics
pipeline/     load_raw · statistical_investigation · counterfactual · export_golden
              build_dashboard · verify
docs/         EXECUTIVE_MEMO · DATA_QUALITY_REPORT · ARCHITECTURE · DECISIONS
dashboard/    executive_dashboard.html   (generated; never hand-edited)
outputs/      summary.json · golden_dataset/ · statistical_results.json
data/raw/     the 17 source CSVs
```

Every figure in the memo, the dashboard and this README is generated from SQL and re-derived
independently in `pipeline/verify.py`. None is typed by hand.

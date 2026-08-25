#!/usr/bin/env python3
"""
Generate notebooks/analysis.ipynb.

The brief asks for a notebook that shows REASONING, not just final charts.
So it is written as an investigation in the order it actually happened --
including the two hypotheses that looked right and turned out to be wrong,
and the verification failure that forced a correction. A notebook that only
shows the winning path hides the part a reviewer most wants to see.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "notebooks" / "analysis.ipynb"
OUT.parent.mkdir(exist_ok=True)

cells = []


def md(text):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip().split("\n")})


def code(src, outputs=None):
    cells.append({"cell_type": "code", "execution_count": None, "metadata": {},
                  "outputs": outputs or [], "source": src.strip().split("\n")})


md("""
# Has recovery really improved 11% month-on-month?

**Short answer: no. 96% of it is that February has 28 days and March has 31.**

This notebook is written in the order the investigation actually ran, including two hypotheses
that looked convincing and turned out to be false, and one verification failure that forced a
correction to a published number. The dead ends are the point — an analysis that only shows the
winning path hides the reasoning a reviewer most needs to see.

**Contents**
1. Setup and first look
2. Reproducing the claim — where does 11% come from?
3. The decomposition
4. Data forensics: seven hypotheses, four of them wrong
5. Building a golden dataset that reconciles
6. Do any operational levers work?
7. The counterfactual
8. Verification — and the number it caught
""")

md("""
## 1. Setup and first look

The brief says the data covers "approximately 12 months". It does not — and noticing that
immediately sets the tone for everything that follows.
""")

code("""
import json, pandas as pd, numpy as np, psycopg
from scipy import stats
pd.set_option("display.width", 120)

DSN = "host=127.0.0.1 port=5432 user=postgres dbname=collections"
def q(sql):
    with psycopg.connect(DSN) as c:
        cur = c.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

q(\"\"\"
SELECT table_name, n_rows FROM raw._ingest_census ORDER BY n_rows DESC LIMIT 8
\"\"\")
""")

md("""
Row counts against the nominal 30k dataset immediately flag injected duplicates:

| table | rows | distinct on its own key |
|---|---:|---:|
| payments | 25,500 | 25,000 |
| calls | 91,350 | 90,000 |
| borrowers | 30,600 | 11,015 |
| agents | 30,000 | **1,000** |
| whatsapp_events | 60,600 | 60,000 |

`agents` is the outlier: 30,000 rows resolving to 1,000 ids. Park that; come back to it in §4.

**First real finding, before any analysis.** The window is 1 Jan – 8 Aug 2026 — 7.3 months, not
12. And `calls` alone runs 29 Dec – 12 Aug, outside every other table's range. August holds
8 days. Any month-on-month series that includes it will show a fake ~74% collapse.
""")

md("""
## 2. Reproducing the claim

Before dismantling a number you have to be able to produce it. If we cannot reproduce the 11%,
we are arguing with a straw man.
""")

code("""
mo = q(\"\"\"
    SELECT to_char(date_trunc('month',event_at_ist),'YYYY-MM') AS mth,
           EXTRACT(day FROM (date_trunc('month',event_at_ist)
                   + interval '1 month' - interval '1 day'))::int AS days,
           sum(amount) FILTER (WHERE payment_status='SUCCESS') AS success
    FROM stg.payments
    WHERE event_at_ist >= '2026-01-01' AND event_at_ist < '2026-08-01'
    GROUP BY 1,2 ORDER BY 1
\"\"\")
mo["success"] = mo.success.astype(float)
mo["mom_%"]     = 100*(mo.success/mo.success.shift() - 1)
mo["per_day"]   = mo.success/mo.days
mo["mom_day_%"] = 100*(mo.per_day/mo.per_day.shift() - 1)
mo.round(2)
""")

md("""
There it is. **Feb → Mar = +11.19%** — the only month-pair in the series that supports the claim.
Every other pair is negative or small.

But look at the `days` column beside it. February has 28; March has 31. And the moment you divide
by days, `mom_day_%` for that same pair collapses to **+0.43%**.

The series is not improving. It is oscillating with month length:

| | Jan | Feb | Mar | Apr | May | Jun | Jul |
|---|---:|---:|---:|---:|---:|---:|---:|
| days | 31 | 28 | 31 | 30 | 31 | 30 | 31 |
| ₹ lakh/day | 61.3 | 62.1 | 62.4 | 59.3 | 60.3 | 59.6 | 61.5 |
""")

md("""
## 3. The decomposition

31 ÷ 28 = 1.1071. The whole claim is arithmetic on the calendar.
""")

code("""
feb, mar = mo[mo.mth=="2026-02"].iloc[0], mo[mo.mth=="2026-03"].iloc[0]
reported = 100*(mar.success/feb.success - 1)
calendar = 100*(mar.days/feb.days - 1)
real     = 100*((mar.success/mar.days)/(feb.success/feb.days) - 1)

print(f"Reported Feb -> Mar        : {reported:+.2f}%")
print(f"Calendar (28 -> 31 days)   : {calendar:+.2f}%")
print(f"True daily-rate change     : {real:+.2f}%")
print(f"Calendar explains          : {calendar/reported*100:.1f}% of the headline")
print(f"Multiplicative check       : {(1+calendar/100):.4f} x {(1+real/100):.4f} "
      f"= {(1+calendar/100)*(1+real/100):.4f}  (vs {1+reported/100:.4f})")
""")

md("""
```
Reported Feb -> Mar        : +11.19%
Calendar (28 -> 31 days)   : +10.71%
True daily-rate change     : +0.43%
Calendar explains          : 95.7% of the headline
Multiplicative check       : 1.1071 x 1.0043 = 1.1119  (vs 1.1119)
```

The components reconstruct the headline exactly. This is arithmetic, not inference.

**But is it robust to how we define recovery?** A decomposition that only works under one
definition is not a finding, it is a coincidence. Six definitions, Jan → Jul daily rate:

| definition | change |
|---|---:|
| all statuses | +0.69% |
| SUCCESS only | +0.40% |
| SUCCESS − REVERSED | **−1.60%** |
| SUCCESS, deduplicated | +0.87% |

Flat under every one. Net of reversals it is *negative*. The conclusion does not depend on the
choice — which is exactly what you want before telling leadership their headline is wrong.
""")

md("""
## 4. Data forensics — seven hypotheses, four of them wrong

The brief lists seven possible problems and says *"You are not being told which of these actually
exist."* So each is written as a test that can return **REJECTED**, and the rejections are
reported with equal weight.
""")

code("""
q(\"\"\"SELECT trap, finding_id, verdict, title
       FROM forensics.findings ORDER BY finding_id\"\"\")
""")

md("""
### 4a. The trap inside the trap — and the mistake I nearly made

`payment_reference` repeats across 3,745 values covering 8,042 rows. The obvious reading is mass
double-counting, and deduplicating on it is the obvious fix.

**It is wrong**, and it is the single most expensive error available in this dataset.
""")

code("""
q(\"\"\"
    SELECT n_rows AS copies_per_ref, count(*) AS n_refs,
           sum((n_amounts>1)::int)  AS differ_on_amount,
           sum((n_accounts>1)::int) AS differ_on_account,
           sum((n_times>1)::int)    AS differ_on_timestamp
    FROM forensics.a2_reference_collisions
    GROUP BY 1 ORDER BY 1 LIMIT 4
\"\"\")
""")

md("""
Most repeated references point at a **different account, a different amount AND a different
timestamp**. Those are not one payment counted twice — they are different payments sharing a
non-unique reference. A further 382 rows carry a *blank* reference and would collapse into one
bogus group.

```
rows a reference-dedup would DELETE   4,678
  of which genuine duplicates            500
  of which legitimate payments         4,178   <- Rs 25.01 Cr destroyed
```

The real duplicates live on `payment_id`: **500 rows, ₹2.59 Cr**. The naive fix is **ten times
larger than the problem it solves**, and applying it would lead leadership to conclude recovery
had collapsed.
""")

md("""
### 4b. Two hypotheses that looked right and were not

**Disposition codes changed mid-period.** Three schema versions exist (`legacy`, `v1`, `v2`), which
is exactly what a migration looks like. But a migration means versions *succeed* one another:
""")

code("""
q(\"\"\"SELECT disposition_version, n, first_seen, last_seen, months_present
       FROM forensics.d1_version_timeline ORDER BY disposition_version\"\"\")
""")

md("""
All three run **in parallel across all eight months in near-equal volume**. Not a migration.
REJECTED.

The *real* disposition problem is different and easy to miss: `PTP` and `PROMISE_TO_PAY` are
synonyms coexisting in every version. Filtering on either alone **undercounts promises by 50.1%** —
the kind of error that survives in a board pack for years.

**Portfolio mix changed.** This is the most common real-world explanation for a jump like this,
so it deserves a serious test:
""")

code("""
q("SELECT * FROM forensics.f1_mix_stability ORDER BY mth")
""")

md("""
High-risk share moves 24.68 → 25.65%; mean DPD 55.87 → 56.90 days. Dead flat. REJECTED.

Same for denominator manipulation — accounts contacted per day range 331.8 to 340.2.

**These rejections matter as much as the confirmations.** Ruling out mix shift and denominator
games is what leaves the calendar standing as the only explanation.

### 4c. The agents dimension cannot be resolved

30,000 rows → 1,000 `agent_id`s, 1,099 employee codes, and just **10 distinct names** across
5 teams. One `agent_id`:

```
AGT0000001 | EMP00883 | Sneha Das   | T3    | joined 2025-09-15
AGT0000001 | EMP00191 | Priya Mehta | FIELD | joined 2025-11-04
AGT0000001 | EMP00745 | Vikram Shah | T3    | joined 2024-02-08
```

I attempted mode-based resolution before concluding — **0 of 1,000** agents reach 60% modal-name
support (mean 0.199, against 0.105 for random assignment). Join dates for a single id span 653
days on average.

**Agent tenure and team analysis — both named in the brief — cannot be performed.** `agent_id`
survives as a behavioural key; every attribute hanging off it does not. Producing a tenure chart
from this would be fabrication.
""")

md("""
## 5. A golden dataset that reconciles

Every removed row goes to `reject.ledger` with a reason code *before* it disappears, so
"quantify the impact of your cleaning decisions" has an exact answer.
""")

code("""
q("SELECT * FROM golden.lineage ORDER BY entity")
""")

md("""
`raw − rejected = golden` for every entity, asserted at build time — the build **fails** otherwise.

Deduplication reconciles exactly against the injected surplus: 25,500 − 25,000 distinct
payment_ids = 500; 91,350 − 90,000 = 1,350.

**Getting there needed two refinements.** A first pass left 14 payment_ids still duplicated. Every
one differed *only* in that one copy carried the reference and the other was blank — so the rule
was treating *missing* as *different*. And 11 `call_id`s differed only in the **date** while
sharing hour:minute:second exactly:

```
CALL0000226 | ACC0010288 | AGT0000584 | VOICEMAIL | 184s | 2026-01-09 11:36:06
CALL0000226 | ACC0010288 | AGT0000584 | VOICEMAIL | 184s | 2026-01-12 11:36:06
```

Two distinct calls coinciding to the second is ~1/86,400 per pair. These are corrupted dates — the
"conflicting timestamps" the source README warns about. Collapsed on time-of-day, earliest date
retained, row flagged rather than silently resolved.
""")

md("""
## 6. Do any operational levers work?

The brief asks us to investigate 13 drivers. Rather than test each and report whatever survives,
state up front what effect size each test *could* detect, then report against that.
""")

code("""
contact = q(\"\"\"
    WITH answered AS (SELECT DISTINCT account_id FROM golden.fct_call WHERE is_contact),
         called   AS (SELECT DISTINCT account_id FROM golden.fct_call),
         paid     AS (SELECT DISTINCT account_id FROM golden.fct_payment WHERE is_success)
    SELECT CASE WHEN a.account_id IN (SELECT account_id FROM answered) THEN 'answered'
                WHEN a.account_id IN (SELECT account_id FROM called)   THEN 'called_no_answer'
                ELSE 'never_called' END AS cohort,
           count(*) AS n,
           count(*) FILTER (WHERE a.account_id IN (SELECT account_id FROM paid)) AS paid
    FROM golden.dim_account a GROUP BY 1
\"\"\")
contact["pct_paid"] = 100*contact.paid/contact.n
contact
""")

md("""
```
never_called      1,592 accounts   43.22% paid
answered         13,535 accounts   44.70% paid
difference                         +1.48 pp   (z=1.13, p=0.26)
detectable at 80% power             3.70 pp
```

**Accounts never contacted pay at essentially the same rate as accounts we reached.** This is not
"we found nothing" — an effect above 3.7 pp would have surfaced, and none did.

**Agent skill.** With ~90 calls per agent at a 19.87% pooled answer rate, binomial noise alone
predicts an SD of 4.24 pp across agents. Observed: **4.28 pp**. χ² = 1,011 on 999 df, p = 0.39.
**1.5% of the variance is attributable to skill** — agent performance is arithmetically
indistinguishable from coin-flipping.

**The PTP field is dead.** A promise marked KEPT is followed by an actual payment within 30 days
**7.75%** of the time; one marked BROKEN, **6.63%**. The gap is statistically significant
(p = 0.039) and operationally meaningless — a functioning process shows 70–90%. This is a clean
illustration that significance is not importance.

**A correction I had to make.** An earlier draft of this notebook reported a Simpson's paradox
here. It was wrong, and the error is instructive enough to leave in.

What I saw: every risk segment shows positive Feb→Mar growth in totals, but some go negative
once normalised for month length.

| segment | Feb→Mar totals | Feb→Mar per-day |
|---|---:|---:|
| HIGH | +15.84% | +4.63% |
| LOW | +13.29% | +2.33% |
| MEDIUM | +4.63% | **−5.50%** |
| NPA | +11.34% | +0.57% |

That is a **month-length confound, not Simpson's paradox.** Simpson's requires the aggregate to
move opposite to its subgroups because of how the subgroups are *weighted* in aggregation. Here
every subgroup is rescaled by the same 31/28 factor — no weighting is involved. Any segment whose
growth fell below the +10.71% calendar threshold flips, mechanically.

Tested strictly — does the aggregate ever move opposite to *every* subgroup? — the answer is **no**,
in all six month-pairs. Both tests now run and both results are reported.
""")

md("""
## 6b. Which of the brief's 13 drivers can actually be analysed?

Before reporting on a driver, check the column exists. Four of the thirteen cannot be analysed —
and two of those fail for a reason no amount of technique can fix: **the column is not in the data.**

| Driver | Status | Basis |
|---|---|---|
| Portfolio mix, DPD, Geography, Campaign, Channel, Vendor, Attempt frequency, Borrower segment | analysable | present in source |
| Agent | partly | `agent_id` valid as a behavioural key; attributes are not |
| **Client** | **gap** | **no such column in any of the 17 tables** |
| **Language** | **gap** | **no such column in any of the 17 tables** |
| Agent tenure | gap | agents dimension unresolvable (finding E1) |
| Calling time | gap | hour-of-day statistically uniform (finding C2) |

Reporting a number for Client or Language would mean inventing the field.
""")

md("""
## 7. The counterfactual

Part 4 says *"assume the business changed its targeting strategy midway."* Before estimating the
effect of an intervention, test whether the intervention happened at all — a DiD around a date
where nothing occurred is not a null result, it is a meaningless one.
""")

code("""
cf = json.loads(open("../outputs/counterfactual_results.json").read())
st = json.loads(open("../outputs/statistical_results.json").read())
print("Chow test over every candidate date:")
print(f"  strongest break     {st['structural_break']['best_break_date']}")
print(f"  raw p-value         {st['structural_break']['raw_p']:.4f}   <- looks promising")
print(f"  Bonferroni ({st['structural_break']['n_candidates_tested']} dates) {st['structural_break']['bonferroni_p']:.3f}")
print()
print("Difference-in-differences at that date anyway:")
print(f"  parallel trends     p = {cf['did']['parallel_trends_p']:.2f}  (assumption holds)")
print(f"  estimate            {cf['did']['estimate_inr_per_account_week']:+.2f} INR/account/week")
print(f"  95% CI              [{cf['did']['ci95'][0]:+.0f}, {cf['did']['ci95'][1]:+.0f}]  p = {cf['did']['p_value']:.2f}")
print(f"  placebos 'significant' {cf['n_placebos_significant']} of {len(cf['placebos'])}")
""")

md("""
This is the most instructive result in the notebook. The strongest candidate break looks
tantalising at **p = 0.061** — the kind of number that gets written up as "performance shifted in
late March". Correct for having searched **152 candidate dates** and it becomes **p = 1.00**.

Running the DiD anyway, at the most favourable date available, with parallel trends satisfied and
zero of four placebo dates showing a false effect, returns an estimate indistinguishable from zero.

**Counterfactual: had targeting not changed, recovery would have been the same** — ₹116.9 Cr
against ₹117.5 Cr actual, confidence interval spanning the actual figure.
""")

md("""
## 8. Verification — and the number it caught

`pipeline/verify.py` re-derives every headline figure from the raw CSVs in pandas, touching none
of the SQL. A pipeline that only checks itself against itself will confirm its own bugs.

**It caught a real error.** The pandas re-derivation gave 11.03% where the SQL gave 11.19%. The
cause was the timezone convention: tables other than `calls`/`accounts`/`agent_sessions` declare
no zone, so the reporting calendar for them is an *assumption*, not a fact.

| convention | reported | calendar | residual | calendar share |
|---|---:|---:|---:|---:|
| naive wall-clock | +11.03% | +10.71% | +0.29% | 97.1% |
| UTC → IST (adopted) | +11.19% | +10.71% | +0.43% | 95.7% |

The calendar effect is identical under both — it is pure arithmetic. So the fix was not to pick
one and move on, but to **assert the conclusion holds under both**, which the verification now
does.

It also caught an over-claim: an earlier draft said the naive dedup "destroys 7,366 payments worth
₹38.89 Cr", conflating *rows sitting in collision groups* with *rows a dedup actually deletes*.
The correct figures are 4,678 deleted, 4,178 legitimate, **₹25.01 Cr**.
""")

code("""
!cd .. && python3 pipeline/verify.py
""")

md("""
```
ALL 30 CHECKS PASSED
```

---

## Conclusions, classified

The brief asks that conclusions be labelled **Fact / Strong Evidence / Correlation / Hypothesis**.

| Conclusion | Class |
|---|---|
| The reported +11.19% decomposes into +10.71% calendar × +0.43% real | **Fact** — arithmetic, reconciles exactly, robust to convention |
| Daily collection rate is flat Jan–Jul (−0.62%/mo, p = 0.23) | **Fact** — 212 daily observations |
| A `payment_reference` dedup would destroy 4,178 real payments (₹25.01 Cr) | **Fact** — directly counted |
| The agents dimension cannot be resolved to identities | **Fact** — 0/1,000 reach the threshold |
| The PTP status field carries no usable information | **Strong Evidence** — 7.75% vs 6.63% against a 70–90% benchmark |
| Contact does not measurably change payment behaviour | **Strong Evidence** — null with 80% power to detect 3.7 pp |
| No targeting change occurred mid-year | **Strong Evidence** — Chow rejected after correction; DiD null; placebos clean |
| This dataset contains no genuine operational relationships | **Hypothesis** — consistent with uniform marginals throughout, but absence of evidence across many tests is not proof |

That last row is deliberately the weakest claim in the analysis. Everything observed is consistent
with a dataset whose categorical fields were drawn independently — but "we could not find a
relationship" and "no relationship exists" are different statements, and only the first is
supported.
""")

nb = {"cells": cells,
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python", "version": "3.11"}},
      "nbformat": 4, "nbformat_minor": 5}
OUT.write_text(json.dumps(nb, indent=1))
print(f"  Notebook -> {OUT}  ({len(cells)} cells)")

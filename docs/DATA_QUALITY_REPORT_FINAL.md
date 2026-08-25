# Data Quality Report

**Scope:** 17 source tables, 639,328 raw rows · **Window:** 1 Jan – 31 Jul 2026 (IST)

Every issue below was found by a test that could have returned "no problem." Four did, and those
rejections are reported here with the same weight as the confirmations — an investigation that only
reports what it found is a fishing expedition, not an audit.

**Result: 17 tests · 13 confirmed · 4 explanations ruled out.**

---

## 1. Detection methodology

The pipeline is layered so that the cost of every cleaning decision is measurable:

```
raw -> stg -> clean -> golden
 |      |       |        └── conformed analytical tables, declared grain
 |      |       └─────────── dedup, entity resolution, validity rules
 |      └─────────────────── typed + timezone-normalised, 1:1 with raw
 └────────────────────────── verbatim CSV, every column TEXT, nothing rejected
```

Two choices make the rest auditable:

- **Raw loads every column as TEXT.** A typed loader would silently discard malformed rows —
  precisely the rows that constitute the evidence. Zero rows were rejected at ingest, so the raw
  count is a trustworthy denominator.
- **Nothing is ever deleted.** Every removed row is written to `reject.ledger` with a reason code
  before it disappears. The build asserts `raw − rejected = golden` for every entity and **fails**
  if it does not hold.

Independently, every quantitative claim in the metrics layer was re-derived from the raw CSVs by a
separate script (`06_verify_claims.py`). **One claim failed that audit and was corrected**; the
correction is recorded in §3.4.

## 2. Lineage — Raw → Rejected → Golden

| Entity | Raw | Rejected | Clean | Golden | Reconciles |
|---|---:|---:|---:|---:|:--:|
| payments | 25,500 | 1,442 | 25,000 | 24,058 | ✓ |
| calls | 91,350 | 4,593 | 90,000 | 86,757 | ✓ |
| borrowers | 30,600 | 19,585 | 11,015 | 11,015 | ✓ |
| accounts | 30,000 | 0 | 30,000 | 30,000 | ✓ |
| agents | 30,000 | 29,000 | 1,000 | 1,000 | ✓ |

| Reason code | Rows | ₹ impact |
|---|---:|---:|
| `SUPERSEDED_VERSION` (agents) | 29,000 | — |
| `SUPERSEDED_VERSION` (borrowers) | 19,585 | — |
| `OUT_OF_WINDOW` (calls) | 3,243 | — |
| `DUPLICATE_REINGEST` (calls) | 1,339 | — |
| `OUT_OF_WINDOW` (payments, partial Aug) | 942 | ₹4.85 Cr |
| `DUPLICATE_REINGEST` (payments) | 500 | ₹2.59 Cr |
| `CONFLICTING_TIMESTAMP` (calls) | 11 | — |

Deduplication reconciles **exactly** against the injected surplus: payments 25,500 − 25,000
distinct IDs = 500 removed; calls 91,350 − 90,000 = 1,350 removed.

---

## 3. Confirmed issues

### 3.1 The duplicate-payment trap has a trap inside it

`payment_reference` repeats across 3,745 values covering 8,042 rows. It looks like large-scale
double-counting. It is not.

- **7,366 of those rows** point at a **different account, a different amount and a different
  timestamp**. They are distinct payments that happen to share a reference — the field is simply
  not unique.
- A further **382 rows carry a blank reference** and would collapse into one bogus group.
- The **real** duplicates are on `payment_id`: **500 rows**, worth **₹2.59 Cr**.

Deduplicating on `payment_reference` — the intuitive move — would delete **4,678 rows**. Only
**500** are genuine duplicates. The other **4,178 are legitimate payments**, worth **₹25.01 Cr of
successful collections** — roughly **ten times** the problem it purports to fix. It is implemented
in `sql/02_forensics.sql` as a documented counter-example, not applied.

> Two numbers describe this trap and they should not be conflated: **7,366 rows** sit in reference
> groups that are provably genuine collisions, but a dedup keeping one row per group **deletes
> 4,678** of them. The second number is the cost; the first is the exposure. All ₹ figures in this
> report are stated on a **SUCCESS-only** basis, since "recovery" means money actually collected.

**Treatment.** Deduplicate on the columns that *identify* the event, and recover nullable
enrichment fields across copies rather than letting their absence split the group.

> **A missing value is not a different value.** A first pass that included `payment_reference` in
> the key left 14 payment_ids still duplicated — every one differing *only* in that one copy carried
> the reference and the other was blank. Identical account, amount, status, method, provider and
> timestamp. Treating NULL as distinct was wrong.

### 3.2 Conflicting timestamps

Eleven `call_id`s survived deduplication differing **only in the date**, while sharing
hour:minute:second **exactly**:

```
CALL0000226 | ACC0010288 | AGT0000584 | VOICEMAIL | 184s | 2026-01-09 11:36:06
CALL0000226 | ACC0010288 | AGT0000584 | VOICEMAIL | 184s | 2026-01-12 11:36:06
```

Two genuinely distinct calls coinciding to the second has probability ~1/86,400; across 11 pairs it
is not credible. **Treatment:** collapse on time-of-day, retain the earliest date, flag the row.
Event counts — which every rate metric depends on — are correct either way; only the day bucket is
uncertain, and it is flagged rather than silently resolved.

### 3.3 The agents dimension has no resolvable identity

30,000 rows resolve to **1,000 agent_ids, 1,099 employee codes, and just 10 distinct names across
5 teams**. A single `agent_id` carries 9.5 different names on average; a single name maps to 948.9
different agent_ids. The relationship is many-to-many in both directions, and join dates for one
id span **653 days on average**.

```
AGT0000001 | EMP00883 | Sneha Das   | T3    | joined 2025-09-15
AGT0000001 | EMP00191 | Priya Mehta | FIELD | joined 2025-11-04
AGT0000001 | EMP00745 | Vikram Shah | T3    | joined 2024-02-08
```

This is not a change-history table. A real one shows one field changing at a time while identity
fields stay fixed; here every column changes at once, at random. It is row-level noise stamped onto
real agent IDs.

Mode-based resolution was attempted before concluding. **Zero of 1,000 agents** reach a 60%
modal-name support threshold; mean support is 0.199, barely above the 0.105 random assignment
would give.

**Business impact.** Agent tenure, team and vendor analysis — all named in the brief — **cannot be
performed**. `agent_id` remains valid as a behavioural key; every attribute hanging off it does not.

### 3.4 Two disposition codes for one concept, and one field that describes nothing

`PTP` and `PROMISE_TO_PAY` both appear in **every** schema version (legacy 1,296/1,332;
v1 1,285/1,309; v2 1,323/1,285). Filtering on either alone **undercounts promises by 50.1%**.
**Treatment:** synonyms collapsed at the clean layer so the error cannot recur downstream.

Separately, `WRONG_NUMBER` is assigned **independently of what happened on the call**:

| call_status | Share of WRONG_NUMBER dispositions |
|---|---:|
| FAILED | 21.2% |
| BUSY | 20.1% |
| NO_ANSWER | 20.1% |
| ANSWERED | 19.5% |
| VOICEMAIL | 19.2% |

**At least 61.3%** of WRONG_NUMBER dispositions sit on calls that never connected, where a
wrong-number determination is impossible.

> **Correction on record.** An earlier draft of the metrics layer stated this as "20% of
> WRONG_NUMBER dispositions attach to unanswered calls." Independent re-derivation from the raw
> CSVs showed the figure was inverted: **80.5% are unanswered, 19.5% answered.** The claim was
> corrected and the underlying argument strengthened — near-uniform distribution across all five
> outcomes is stronger evidence of independence than any single percentage.

### 3.5 Timezones are real and do shift the calendar

`calls` declares a per-row timezone; two thirds of rows are non-UTC (Kolkata 30,485, Dubai 30,464,
UTC 30,401), and the label is statistically independent of the vendor's timezone. Applying it moves
**8,924 calls (9.8%) onto a different calendar day** and **310 across a month boundary**.

But the deeper finding is that **hour-of-day is uniform** — call volume χ² = 23.1 on 23 df
(p = 0.45), answer rate χ² = 24.0 (p = 0.40). The brief asks us to investigate *calling time* as a
driver. **There is no diurnal pattern to find.** Any "best hour to call" recommendation from this
data would be fabricated from noise.

### 3.6 No single source of truth for account status

**26,296 of 30,000 accounts (87.7%)** have a status in `accounts` that disagrees with the most
recent event in `account_status_history`. Separately, **30,191 of 60,000 history rows (50.3%)** are
recorded *before* the event they describe — chronologically impossible.

**Decision:** `account_status_history` is the source of truth. It is an append-only log with an
explicit event time, so it can be replayed as-of any date; `accounts.status` is a single mutable
column with no timestamp, unauditable and unreconstructable. The snapshot value is retained
alongside as `status_snapshot` so the disagreement stays visible.

### 3.7 Attribution is inference, never measurement

`payments` carries no campaign, call or message key. Any "campaign X drove ₹Y" statement is the
output of an undocumented attribution rule. Measured across **all four outreach channels** (calls,
WhatsApp, SMS, field visits), its sensitivity is severe:

| Last-touch window | Payments attributed | Amount credited |
|---|---:|---:|
| 1 day | 3.26% | ₹4.09 Cr |
| 7 days | 20.11% | ₹25.55 Cr |
| 30 days | 59.71% | ₹75.99 Cr |
| 90 days | 83.42% | ₹105.77 Cr |

A **26× swing** in credited recovery from a choice nobody wrote down. On a calls-only basis the
range is 1.39% → 59.44%; the multi-channel figures above are the correct basis for any
channel-level cost claim, and are what the metrics layer uses.

### 3.8 Other confirmed issues

- **1,827 calls (2.0%)** carry a blank `agent_id`. Investigation confirmed these are NULL values,
  not references to missing agents — plausibly the agentic-voice channel named in the brief.
  Flagged as a hypothesis, not a defect.
- **985 distinct borrower_ids in `golden_calls` (7,360 call rows, 8.2%)** do not exist in
  `golden_borrowers`. No treatment applied; any borrower-level analysis undercounts this segment.
- **8,518 of 11,015 borrowers (77.3%)** had conflicting names, phones or cities across versions.
  Latest version retained; fields not merged, because merging invents a record that never existed.
- **All 30,000 accounts opened before the window** (Jan 2024 – Nov 2025). This is a closed cohort,
  which should show recovery *decay* as collectable accounts are exhausted. Flat performance is
  therefore mildly negative news, not neutral.
- **August 2026 is partial** — 8 of 31 days. Excluded from every month-on-month comparison. Its
  daily rate is normal (₹58.9 L/day vs ₹59.8 L average); only the total is truncated.
- **The extract covers 7.3 months, not the ~12 the brief describes.**
- **No cost data exists in any of the 17 tables** — no agent salary, telephony rate, or contact
  cost. Cost per ₹ recovered and true ROI are therefore not computable.
- **No language or client column exists.** Two dimensions named in the brief cannot be analysed;
  closest proxies are city/state and campaign_name.
- **Text corruption:** some values lose a leading "M" ("Mehta" → "ehta", "Mumbai" → "umbai").
  Cosmetic, affects no numeric result, recorded for completeness.

---

## 4. Hypotheses tested and rejected

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Portfolio mix changed | **Rejected** | High-risk share 24.69–25.68%; mean DPD 55.8–56.8 days |
| Denominator manipulation | **Rejected** | Accounts attempted 9,531–10,417/month; reached 2,188–2,491; contact rate 22.50–24.02%, no drift |
| Disposition codes migrated | **Rejected** | All 3 versions present in all 7 months at near-equal volume — coexistence, not migration |
| Operational double-charges | **Rejected** | 0 candidate pairs (same account, same amount, <24 h apart, distinct payment_ids) |

These matter as much as the confirmations. Mix shift is the most common real-world explanation for
a jump like this; ruling it out is what leaves the calendar as the only explanation standing.

---

## 5. Net business impact

| | |
|---|---:|
| Overstatement removed by deduplication | ₹2.59 Cr |
| Legitimate recovery **preserved** by rejecting the naive dedup rule | ₹25.01 Cr |
| Reversals excluded from "recovery" | ₹9.16 Cr |
| Overstatement from counting FAILED and PENDING as recovery | ₹44.85 Cr |
| Promises whose status field is uninformative | 18,000 |
| Metrics found not computable from this data | 3 of 10 |

**The single largest quality win is a decision not taken.** Applying the obvious
`payment_reference` deduplication would have removed ₹25.01 Cr of real collections — 21% of the
period's net recovery — and led leadership to conclude that performance had collapsed.

---

## 6. A note on the timezone convention

Tables other than `calls`, `accounts` and `agent_sessions` declare no timezone, so the reporting
calendar for them is a convention (D-004), not a fact. It is the one assumption in this analysis
that materially touches the headline, so it is tested rather than asserted:

| Convention | Reported Feb → Mar | Calendar effect | Residual | Calendar share |
|---|---:|---:|---:|---:|
| Naive wall-clock | +11.03% | +10.71% | +0.29% | 97.1% |
| UTC → IST (adopted) | +11.19% | +10.71% | +0.43% | 95.7% |

The calendar effect is identical under both because it is pure arithmetic. **The conclusion — that
the calendar explains 96% or more of the reported improvement — does not depend on the convention
chosen.** `pipeline/verify.py` asserts this and fails the build if it stops holding.

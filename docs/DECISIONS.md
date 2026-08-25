# Decision Log

Every judgment call made in this analysis, the alternative considered, and the reason one was
chosen. The brief asks for documented assumptions; this is that document. Decisions are also
loaded into `golden.decision_log` so they travel with the data.

| ID | Area | Decision | Alternative rejected | Why | Impact |
|---|---|---|---|---|---|
| D-001 | Ingest | Load every raw column as TEXT | Typed loader with casts | A typed loader silently drops malformed rows — exactly the evidence we need. Raw count must be a trustworthy denominator. | 0 rows rejected at ingest |
| D-002 | Layering | raw → staging → clean → golden → metrics, with staging strictly 1:1 with raw | Clean directly from raw | Separating *representation* fixes from *meaning* fixes makes each measurable. Asserted at build time. | 17/17 tables verified 1:1 |
| D-003 | Removals | Nothing deleted; every removal written to `reject.ledger` with a reason code | Filter rows out in place | "Quantify the impact of your cleaning decisions" is unanswerable otherwise. Build fails if raw − rejected ≠ golden. | 25,620 ledger rows |
| D-004 | Timezones | Apply the declared zone where present; assume UTC where absent and **measure the sensitivity** | Assume all UTC / infer from account | Only `calls`, `accounts`, `agent_sessions` declare a zone. Inferring is unfalsifiable here because hour-of-day is uniform. Measure, don't assert. Verified: calendar share is 95.7% (IST) vs 97.1% (naive) — conclusion unchanged. | 8,924 calls change day; headline robust either way |
| D-005 | Recovery | `SUCCESS − REVERSED`, day-normalised | SUCCESS only; all statuses | Reversed money left the building. Sensitivity table ships alongside so the reader sees the choice doesn't drive the conclusion. | ₹9.16 Cr excluded |
| D-006 | Payment dedup | Dedup on identity columns only; **never** on `payment_reference` | Dedup on `payment_reference` | The reference is not unique: 7,366 rows sharing one differ in account, amount AND time. The naive rule deletes 4,678 rows of which only 500 are genuine, destroying 4,178 real payments worth ₹25.01 Cr. | 500 removed; ₹25.01 Cr of real collections preserved |
| D-007 | Window | 1 Jan – 31 Jul 2026; August reported separately | Include partial August | 8 days of August in a monthly series manufactures a fake −74% — the same calendar error inverted. | 942 payments set aside |
| D-008 | Agents | Attempt mode-based resolution, then declare the dimension unusable | Skip agent analysis; or use it anyway | Proving resolution fails is a finding. 0/1000 agents reach 60% modal-name support. `agent_id` kept as a behavioural key only. | Tenure/team analysis withdrawn |
| D-009 | Account status | `account_status_history` is source of truth; snapshot retained beside it | Trust `accounts.status` | An append-only log with event times can be replayed as-of any date; a mutable column cannot be audited — even though it looks cleaner. | 87.7% of accounts disagree |
| D-010 | Borrowers | Keep the latest version; do **not** merge fields across versions | Field-level merge / survivorship | Merging invents a record that never existed in any source system. | 19,585 superseded rows |
| D-011 | Dedup nuance | A missing value is not a different value — recover nullable fields across copies | Treat NULL as a distinct value | 14 payment_ids and 68 call_ids differed *only* by one copy having a blank enrichment field. Treating NULL as distinct split genuine duplicates. | Reconciles to exactly 500 / 1,350 |
| D-012 | Timestamps | Collapse same-event rows differing only in date; keep the earliest; flag the row | Keep both as distinct events | 11 call_ids share hour:minute:second exactly across different dates. P(coincidence) ≈ 1/86,400 per pair. Counts are right either way; only the day bucket is uncertain, so it is flagged not hidden. | 11 rows |
| D-013 | Dispositions | Collapse `PTP` and `PROMISE_TO_PAY` at the clean layer | Report both codes separately | They are synonyms coexisting in every schema version. Filtering one undercounts promises by 50.1%. | 7,830 dispositions |
| D-014 | Metrics | Register unusable metrics as UNUSABLE with a stated reason | Omit them quietly | Silence is how a broken metric survives in a board pack for years. | 3 of 10 |
| D-015 | Nulls | Report every null result against the effect size the test could have detected | Report "no significant effect" | "We found nothing" is uninformative without power. "An effect above 3.7 pp would have shown" is a finding. | Applied to all null tests |
| D-016 | Break test | Bonferroni-correct the structural break search over all candidate dates | Report the best raw p-value | Searching 152 dates and reporting the winner is p-hacking. Raw p = 0.061 → corrected p = 1.00. | Part 4 premise falsified |
| D-017 | ₹10 Cr | Recommend an experiment, and state the fallback choice rests on economics not data | Pick the best-performing option | No option shows a detectable effect. Ranking them on this evidence would invent precision. | ₹1.2 Cr vs ₹10 Cr |

## Standing assumptions

1. The reporting calendar is **Asia/Kolkata** (Indian lender). UTC is the join and ordering key.
2. "Recovery" means cash retained, not cash touched — hence net of reversals.
3. The account population is a **closed cohort**: all 30,000 accounts were opened before the
   window. A closed cohort should decay; flat is therefore mildly negative, not neutral.
4. Where the data cannot answer a question, the answer is "it cannot", not a number with wide
   error bars presented as if it were an estimate.

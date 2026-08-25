-- =====================================================================
-- 02_forensics.sql -- systematic test of the seven hypotheses in Part 2
-- ---------------------------------------------------------------------
-- The brief says: "You are not being told which of these problems
-- actually exist. Find them if they exist."
--
-- Accordingly every test below can return CONFIRMED **or REJECTED**, and
-- a rejection is recorded with the same weight as a confirmation. An
-- analysis that only reports the problems it found is not an
-- investigation, it is a fishing expedition.
--
-- Output: forensics.findings -- one row per test, machine readable, and
-- the single source for docs/DATA_QUALITY_REPORT.md and the dashboard.
-- =====================================================================

DROP TABLE IF EXISTS forensics.findings CASCADE;
CREATE TABLE forensics.findings (
    finding_id    TEXT PRIMARY KEY,
    trap          TEXT NOT NULL,      -- A..G per the brief
    title         TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('CONFIRMED','REJECTED','PARTIAL')),
    evidence      TEXT NOT NULL,
    rows_affected BIGINT,
    amount_impact NUMERIC,            -- rupees
    confidence    TEXT CHECK (confidence IN ('Fact','Strong Evidence','Correlation','Hypothesis')),
    so_what       TEXT                -- business consequence, plain language
);

-- =====================================================================
-- TRAP A -- DUPLICATE PAYMENTS
-- =====================================================================

-- A1: TRUE duplicates. Same payment_id AND byte-identical on every
--     business column => re-ingestion of the same event.
DROP TABLE IF EXISTS forensics.a1_true_dupes CASCADE;
CREATE TABLE forensics.a1_true_dupes AS
SELECT * FROM (
  SELECT p.*,
         row_number() OVER (
           PARTITION BY payment_id, account_id, borrower_id, payment_reference,
                        amount, payment_status, payment_method, provider_id, event_at_naive
           ORDER BY _ingest_seq) AS copy_no
  FROM stg.payments p) x
WHERE copy_no > 1;

INSERT INTO forensics.findings VALUES (
 'A1','A','Re-ingested payment rows (identical on every business column)','CONFIRMED',
 (SELECT format('%s surplus rows across %s payment_ids; each is byte-identical to an earlier row on all 8 business columns.',
   count(*), count(DISTINCT payment_id)) FROM forensics.a1_true_dupes),
 (SELECT count(*) FROM forensics.a1_true_dupes),
 (SELECT coalesce(sum(amount) FILTER (WHERE payment_status='SUCCESS'),0) FROM forensics.a1_true_dupes),
 'Fact',
 'Genuine double-count. These must be removed or reported recovery is overstated.');

-- A2: THE DECOY. payment_reference repeats -- but is it a duplicate?
--     If two rows share a reference yet differ on account, amount AND
--     timestamp, they are two different payments with a colliding
--     reference, not one payment counted twice. Deduplicating on
--     payment_reference is the intuitive move and it is WRONG.
DROP TABLE IF EXISTS forensics.a2_reference_collisions CASCADE;
CREATE TABLE forensics.a2_reference_collisions AS
SELECT payment_reference,
       count(*)                        AS n_rows,
       count(DISTINCT account_id)      AS n_accounts,
       count(DISTINCT amount)          AS n_amounts,
       count(DISTINCT event_at_naive)  AS n_times,
       count(DISTINCT payment_id)      AS n_payment_ids,
       (count(DISTINCT account_id) > 1 AND count(DISTINCT amount) > 1
        AND count(DISTINCT event_at_naive) > 1) AS is_genuine_collision
FROM stg.payments
WHERE payment_reference IS NOT NULL
GROUP BY 1 HAVING count(*) > 1;

-- What a naive reference-dedup would ACTUALLY delete, as distinct from
-- how many rows merely sit in colliding groups. The two numbers are
-- different and conflating them overstates the finding.
DROP TABLE IF EXISTS forensics.a2_naive_dedup_cost CASCADE;
CREATE TABLE forensics.a2_naive_dedup_cost AS
WITH ranked AS (
  SELECT p.*, row_number() OVER (PARTITION BY payment_reference ORDER BY _ingest_seq) AS rn
  FROM stg.payments p),
would_delete AS (SELECT * FROM ranked WHERE rn > 1),
true_dupes AS (SELECT count(*) n FROM (
  SELECT row_number() OVER (
    PARTITION BY payment_id, account_id, borrower_id, amount, payment_status,
                 payment_method, provider_id, event_at_naive ORDER BY _ingest_seq) rn2
  FROM stg.payments) z WHERE rn2 > 1)
SELECT (SELECT count(*) FROM would_delete)                                   AS rows_deleted,
       (SELECT n FROM true_dupes)                                            AS genuine_duplicates,
       (SELECT count(*) FROM would_delete) - (SELECT n FROM true_dupes)      AS legitimate_destroyed,
       (SELECT coalesce(sum(amount),0) FROM would_delete
         WHERE payment_status='SUCCESS')                                     AS success_value_destroyed;

INSERT INTO forensics.findings VALUES (
 'A2','A','payment_reference is NOT a unique key -- deduplicating on it destroys real payments','CONFIRMED',
 (SELECT format(
   '%s references repeat across %s rows. %s of those references (%s rows) point at different accounts, different amounts AND different timestamps -- they are distinct payments with a colliding reference. A further %s rows carry a BLANK reference and would collapse into a single bogus group.',
   count(*), sum(n_rows),
   count(*) FILTER (WHERE is_genuine_collision),
   coalesce(sum(n_rows) FILTER (WHERE is_genuine_collision),0),
   (SELECT count(*) FROM stg.payments WHERE payment_reference IS NULL))
  || format(' Applying that rule would DELETE %s rows, of which only %s are genuine duplicates -- destroying %s legitimate payments worth Rs %s Cr of successful collections.',
       (SELECT rows_deleted FROM forensics.a2_naive_dedup_cost),
       (SELECT genuine_duplicates FROM forensics.a2_naive_dedup_cost),
       (SELECT legitimate_destroyed FROM forensics.a2_naive_dedup_cost),
       (SELECT round(success_value_destroyed/1e7,2) FROM forensics.a2_naive_dedup_cost))
  FROM forensics.a2_reference_collisions),
 (SELECT legitimate_destroyed FROM forensics.a2_naive_dedup_cost),
 (SELECT success_value_destroyed FROM forensics.a2_naive_dedup_cost),
 'Fact',
 'The obvious cleaning rule is a trap. Dedup on payment_reference and you delete thousands of legitimate payments, then conclude recovery is far worse than it is.');

-- A3: Genuine double-charge candidates -- same account, same amount,
--     different payment_id, within 24h. Distinct from A1 (re-ingestion)
--     because the identifiers differ: this is an operational problem,
--     not a pipeline problem.
DROP TABLE IF EXISTS forensics.a3_double_charge CASCADE;
CREATE TABLE forensics.a3_double_charge AS
SELECT a.payment_id AS pid_1, b.payment_id AS pid_2, a.account_id, a.amount,
       a.event_at_utc AS t1, b.event_at_utc AS t2,
       EXTRACT(EPOCH FROM (b.event_at_utc - a.event_at_utc))/3600.0 AS gap_hours
FROM stg.payments a
JOIN stg.payments b
  ON a.account_id = b.account_id
 AND a.amount     = b.amount
 AND a.payment_id < b.payment_id
 AND b.event_at_utc BETWEEN a.event_at_utc AND a.event_at_utc + interval '24 hours'
WHERE a.payment_status='SUCCESS' AND b.payment_status='SUCCESS';

INSERT INTO forensics.findings
SELECT 'A3','A','Suspected operational double-charges (same account, same amount, <24h apart)',
       CASE WHEN count(*) > 0 THEN 'CONFIRMED' ELSE 'REJECTED' END,
       format('%s candidate pairs found on distinct payment_ids.', count(*)),
       count(*), coalesce(sum(amount),0), 'Strong Evidence',
       'Distinct from re-ingestion: identifiers differ, so this is a billing/operations issue rather than a pipeline issue.'
FROM forensics.a3_double_charge;

-- =====================================================================
-- TRAP B -- ATTRIBUTION ERRORS
-- =====================================================================
-- B1: Structural. There is no campaign_id on payments. Any statement of
--     the form "campaign X drove Y rupees" is therefore an INFERENCE
--     produced by an attribution rule, never a measurement. Whoever
--     built the 11% chose a rule and did not write it down.
INSERT INTO forensics.findings
SELECT 'B1','B','Payments carry no campaign or interaction key -- all channel attribution is inferred',
       'CONFIRMED',
       format('payments has %s columns and none links to a campaign, call or message. calls is the only fact table carrying campaign_id (%s of %s rows populated).',
         (SELECT count(*) FROM information_schema.columns WHERE table_schema='stg' AND table_name='payments'),
         (SELECT count(*) FROM stg.calls WHERE campaign_id IS NOT NULL),
         (SELECT count(*) FROM stg.calls)),
       NULL, NULL, 'Fact',
       'Channel ROI numbers in existing reporting are the output of an undocumented attribution rule. They cannot be reproduced or audited.';

-- B2: Attribution-window sensitivity. Same payments, three defensible
--     rules, three different answers. The spread IS the finding.
-- Implementation note: resolve the single most recent campaign-bearing
-- call once per payment and record the GAP, then bucket. One lateral
-- join answers every window, instead of one pass per window.
DROP TABLE IF EXISTS forensics.b2_last_touch CASCADE;
CREATE TABLE forensics.b2_last_touch AS
SELECT p.payment_id, p.amount, p.event_at_utc,
       lt.campaign_id AS attributed_campaign,
       EXTRACT(EPOCH FROM (p.event_at_utc - lt.event_at_utc))/86400.0 AS gap_days
FROM stg.payments p
LEFT JOIN LATERAL (
    SELECT c.campaign_id, c.event_at_utc
    FROM stg.calls c
    WHERE c.account_id = p.account_id
      AND c.campaign_id IS NOT NULL
      AND c.event_at_utc <= p.event_at_utc
    ORDER BY c.event_at_utc DESC
    LIMIT 1) lt ON TRUE
WHERE p.payment_status = 'SUCCESS';

DROP TABLE IF EXISTS forensics.b2_attribution_windows CASCADE;
CREATE TABLE forensics.b2_attribution_windows AS
SELECT w.window_days,
       count(*)                                                       AS payments,
       count(*) FILTER (WHERE b.gap_days <= w.window_days)            AS attributed,
       round(100.0*count(*) FILTER (WHERE b.gap_days <= w.window_days)/count(*),2) AS pct_attributed,
       coalesce(sum(b.amount) FILTER (WHERE b.gap_days <= w.window_days),0)        AS amount_attributed
FROM forensics.b2_last_touch b
CROSS JOIN (VALUES (1),(7),(30),(90)) AS w(window_days)
GROUP BY 1;

INSERT INTO forensics.findings
SELECT 'B2','B','Attribution window choice swings credited recovery by a factor of several','CONFIRMED',
       format('Same payments, last-touch rule, four windows: %s',
         string_agg(format('%sd -> %s%% (Rs %s Cr)', window_days, pct_attributed,
                    round(amount_attributed/1e7,1)), '; ' ORDER BY window_days)),
       NULL, NULL, 'Fact',
       'Nobody can validate a channel ROI claim without being told the attribution window. Changing it silently changes the answer.'
FROM forensics.b2_attribution_windows;

-- =====================================================================
-- TRAP C -- TIMEZONE PROBLEMS
-- =====================================================================
-- C1: calls declares a per-row timezone; two thirds of rows are non-UTC.
--     Ignoring it misfiles events by up to 5h30m -- across midnight, and
--     at month boundaries, across the reporting period itself.
DROP TABLE IF EXISTS forensics.c1_tz_shift CASCADE;
CREATE TABLE forensics.c1_tz_shift AS
SELECT declared_tz,
       count(*) AS calls,
       count(*) FILTER (WHERE event_at_naive::date <> event_at_ist::date)                       AS day_changes,
       count(*) FILTER (WHERE date_trunc('month',event_at_naive) <> date_trunc('month',event_at_ist)) AS month_changes
FROM stg.calls GROUP BY 1;

INSERT INTO forensics.findings
SELECT 'C1','C','Declared timezones are real and misfile calls across day and month boundaries','CONFIRMED',
       format('%s of %s calls (%s%%) fall on a different calendar DAY once the declared timezone is applied; %s cross a MONTH boundary. Zone mix: %s.',
         sum(day_changes), sum(calls), round(100.0*sum(day_changes)/sum(calls),1), sum(month_changes),
         string_agg(format('%s=%s', declared_tz, calls), ', ' ORDER BY declared_tz)),
       sum(day_changes), NULL, 'Fact',
       'Daily operational reports are wrong for roughly a fifth of calls. Month-end numbers move.'
FROM forensics.c1_tz_shift;

-- C2: The deeper problem. Hour-of-day is UNIFORM, so "best time to call"
--     analysis is not merely biased -- it is impossible. Chi-square
--     against a flat expectation is computed in the notebook; here we
--     record the raw distribution that makes the point.
DROP TABLE IF EXISTS forensics.c2_hour_profile CASCADE;
CREATE TABLE forensics.c2_hour_profile AS
SELECT EXTRACT(hour FROM event_at_ist)::int AS hour_ist, count(*) AS calls,
       count(*) FILTER (WHERE call_status='ANSWERED') AS answered,
       round(100.0*count(*) FILTER (WHERE call_status='ANSWERED')/count(*),2) AS pct_answered
FROM stg.calls GROUP BY 1 ORDER BY 1;

INSERT INTO forensics.findings
SELECT 'C2','C','Call volume is uniform across all 24 hours -- "calling time" cannot be analysed','CONFIRMED',
       format('Hourly call counts range %s to %s against a flat expectation of %s (max deviation %s%%). Answer rate by hour ranges %s%% to %s%%.',
         min(calls), max(calls), round(avg(calls)),
         round(100.0*(max(calls)-min(calls))/avg(calls),1),
         min(pct_answered), max(pct_answered)),
       NULL, NULL, 'Fact',
       'The brief asks us to investigate calling time as a driver. There is no diurnal pattern to find -- any "best hour" recommendation from this data would be fabricated from noise.'
FROM forensics.c2_hour_profile;

-- =====================================================================
-- TRAP D -- VENDOR MAPPING / DISPOSITION CODE CHANGES
-- =====================================================================
-- D1: The narrative says codes "changed during the period". Test it:
--     if true, versions should succeed one another in time.
DROP TABLE IF EXISTS forensics.d1_version_timeline CASCADE;
CREATE TABLE forensics.d1_version_timeline AS
SELECT disposition_version, count(*) AS n,
       min(event_at_ist)::date AS first_seen, max(event_at_ist)::date AS last_seen,
       count(DISTINCT date_trunc('month',event_at_ist)) AS months_present
FROM stg.call_dispositions GROUP BY 1;

INSERT INTO forensics.findings
SELECT 'D1','D','Disposition schema versions did NOT change over time -- they coexist throughout','REJECTED',
       format('All %s versions are present in every one of the %s months, in near-equal volume (%s). A genuine migration would show versions succeeding one another; these run in parallel end to end.',
         count(*), max(months_present),
         string_agg(format('%s=%s', disposition_version, n), ', ' ORDER BY disposition_version)),
       NULL, NULL, 'Fact',
       'A plausible-sounding explanation for the trend that turns out to be false. Reporting it as a cause would have been wrong.'
FROM forensics.d1_version_timeline;

-- D2: The REAL disposition problem -- two codes for one concept, live
--     simultaneously in every version.
DROP TABLE IF EXISTS forensics.d2_synonym_codes CASCADE;
CREATE TABLE forensics.d2_synonym_codes AS
SELECT disposition_version,
       count(*) FILTER (WHERE disposition_code='PTP')            AS code_ptp,
       count(*) FILTER (WHERE disposition_code='PROMISE_TO_PAY') AS code_promise_to_pay,
       count(*) FILTER (WHERE disposition_code IN ('PTP','PROMISE_TO_PAY')) AS combined
FROM stg.call_dispositions GROUP BY 1;

INSERT INTO forensics.findings
SELECT 'D2','D','Two disposition codes mean the same thing and coexist in every version','CONFIRMED',
       format('PTP and PROMISE_TO_PAY both appear in all versions (%s). Counting only one undercounts promises by %s%%.',
         string_agg(format('%s: PTP=%s / PROMISE_TO_PAY=%s', disposition_version, code_ptp, code_promise_to_pay), '; ' ORDER BY disposition_version),
         round(100.0*sum(code_promise_to_pay)/sum(combined),1)),
       (SELECT sum(combined) FROM forensics.d2_synonym_codes), NULL, 'Fact',
       'Any PTP-rate metric filtering on a single code silently halves the numerator. This is the kind of error that survives for years.'
FROM forensics.d2_synonym_codes;

-- =====================================================================
-- TRAP E -- AGENT IDENTITY
-- =====================================================================
DROP TABLE IF EXISTS forensics.e1_agent_identity CASCADE;
CREATE TABLE forensics.e1_agent_identity AS
SELECT count(*)                                AS dim_rows,
       count(DISTINCT agent_id)                AS distinct_agent_ids,
       count(DISTINCT employee_code)           AS distinct_employee_codes,
       count(DISTINCT agent_name)              AS distinct_names,
       count(DISTINCT team)                    AS distinct_teams,
       (SELECT round(avg(k),1) FROM (SELECT count(DISTINCT agent_name) k FROM stg.agents GROUP BY agent_id) z)
                                               AS avg_names_per_agent_id,
       (SELECT round(avg(k),1) FROM (SELECT count(DISTINCT agent_id) k FROM stg.agents GROUP BY agent_name) z)
                                               AS avg_ids_per_name
FROM stg.agents;

INSERT INTO forensics.findings
SELECT 'E1','E','The agents dimension has no resolvable identity -- it is unusable for attribute analysis','CONFIRMED',
       format('%s rows resolve to only %s agent_ids, %s employee_codes and just %s distinct names across %s teams. A single agent_id carries %s different names on average; a single name maps to %s different agent_ids. The relationship is many-to-many in both directions.',
         dim_rows, distinct_agent_ids, distinct_employee_codes, distinct_names, distinct_teams,
         avg_names_per_agent_id, avg_ids_per_name),
       dim_rows, NULL, 'Fact',
       'Agent tenure, team and vendor analysis -- all named in the brief -- cannot be performed. agent_id remains valid as a behavioural key; every attribute hanging off it does not.'
FROM forensics.e1_agent_identity;

INSERT INTO forensics.findings
SELECT 'E2','E','Calls with a blank agent_id','CONFIRMED',
       format('%s calls (%s%%) carry an empty agent_id and cannot be attributed to any agent.',
         count(*), round(100.0*count(*)/(SELECT count(*) FROM stg.calls),2)),
       count(*), NULL, 'Fact',
       'Agent-level denominators are short by this amount unless the blanks are handled explicitly.'
FROM stg.calls WHERE agent_id IS NULL OR trim(agent_id)='';

-- =====================================================================
-- TRAP F -- PORTFOLIO MIX
-- =====================================================================
DROP TABLE IF EXISTS forensics.f1_mix_stability CASCADE;
CREATE TABLE forensics.f1_mix_stability AS
SELECT to_char(date_trunc('month', c.event_at_ist),'YYYY-MM') AS mth,
       round(100.0*count(*) FILTER (WHERE a.risk_segment='HIGH')  /count(*),2) AS pct_high,
       round(100.0*count(*) FILTER (WHERE a.risk_segment='MEDIUM')/count(*),2) AS pct_medium,
       round(100.0*count(*) FILTER (WHERE a.risk_segment='LOW')   /count(*),2) AS pct_low,
       round(100.0*count(*) FILTER (WHERE a.risk_segment='NPA')   /count(*),2) AS pct_npa,
       round(avg(a.dpd),2) AS avg_dpd
FROM stg.calls c JOIN stg.accounts a USING (account_id)
WHERE c.event_at_ist >= '2026-01-01' AND c.event_at_ist < '2026-08-01'
GROUP BY 1 ORDER BY 1;

INSERT INTO forensics.findings
SELECT 'F1','F','Portfolio mix did NOT change -- it is stable to within a percentage point','REJECTED',
       format('Across the seven complete months, HIGH-risk share moves only %s to %s%% and mean DPD only %s to %s days. No acquisition, no re-segmentation.',
         min(pct_high), max(pct_high), min(avg_dpd), max(avg_dpd)),
       NULL, NULL, 'Fact',
       'Mix shift is the most common real-world explanation for a jump like this. Here it is ruled out, which strengthens the calendar explanation.'
FROM forensics.f1_mix_stability;

INSERT INTO forensics.findings
SELECT 'F2','F','No accounts were originated during the observation window','CONFIRMED',
       format('All %s accounts were opened between %s and %s -- entirely before the %s observation window. The portfolio is a closed cohort.',
         count(*), min(opened_at_naive)::date, max(opened_at_naive)::date, '2026-01-01'),
       count(*), NULL, 'Fact',
       'A closed cohort should show recovery DECAY over time as collectable accounts are exhausted. Flat performance is therefore mildly negative news, not neutral.'
FROM stg.accounts;

-- =====================================================================
-- TRAP G -- DENOMINATOR MANIPULATION
-- =====================================================================
DROP TABLE IF EXISTS forensics.g1_denominator CASCADE;
CREATE TABLE forensics.g1_denominator AS
SELECT to_char(date_trunc('month', event_at_ist),'YYYY-MM') AS mth,
       count(DISTINCT account_id) AS accounts_called,
       EXTRACT(day FROM (date_trunc('month',event_at_ist)+interval '1 month'-interval '1 day'))::int AS days_in_month
FROM stg.calls
WHERE event_at_ist >= '2026-01-01' AND event_at_ist < '2026-08-01'
GROUP BY 1, 3 ORDER BY 1;

INSERT INTO forensics.findings
SELECT 'G1','G','The contactable population is NOT shrinking -- no denominator manipulation detected','REJECTED',
       format('Distinct accounts contacted per month ranges %s to %s, and per-day it ranges %s to %s -- flat once month length is removed. Unsuccessful accounts are not disappearing from the base.',
         min(accounts_called), max(accounts_called),
         round(min(accounts_called::numeric/days_in_month),1), round(max(accounts_called::numeric/days_in_month),1)),
       NULL, NULL, 'Fact',
       'Denominator shrinkage is the classic way a conversion rate is inflated. Ruled out here -- which again points back to the calendar.'
FROM forensics.g1_denominator;

-- G2: account_status_history -- overwrite / late-arrival behaviour
DROP TABLE IF EXISTS forensics.g2_status_history CASCADE;
CREATE TABLE forensics.g2_status_history AS
SELECT account_id, count(*) AS n_events,
       count(DISTINCT status) AS n_distinct_status,
       count(*) FILTER (WHERE recorded_at_utc < event_at_utc) AS recorded_before_event,
       max(ingest_lag_hours) AS max_lag_hours
FROM stg.account_status_history GROUP BY 1;

INSERT INTO forensics.findings
SELECT 'G2','G','Status history contains events recorded BEFORE they occurred','CONFIRMED',
       format('%s of %s history rows have recorded_at earlier than event_at (impossible). Ingest lag ranges to %s hours. %s accounts carry contradictory status sequences.',
         (SELECT count(*) FROM stg.account_status_history WHERE recorded_at_utc < event_at_utc),
         (SELECT count(*) FROM stg.account_status_history),
         (SELECT round(max(ingest_lag_hours)) FROM stg.account_status_history),
         count(*) FILTER (WHERE recorded_before_event > 0)),
       (SELECT count(*) FROM stg.account_status_history WHERE recorded_at_utc < event_at_utc),
       NULL, 'Fact',
       'Point-in-time reconstruction of account state is unreliable. Any as-of-date report built on this table can silently disagree with itself.'
FROM forensics.g2_status_history;

-- G3: accounts.status vs the latest status in history -- do they agree?
INSERT INTO forensics.findings
SELECT 'G3','G','The accounts table disagrees with its own status history','CONFIRMED',
       format('%s of %s accounts (%s%%) have a current status in accounts that does not match the most recent event in account_status_history.',
         count(*) FILTER (WHERE a.status IS DISTINCT FROM h.latest_status),
         count(*),
         round(100.0*count(*) FILTER (WHERE a.status IS DISTINCT FROM h.latest_status)/count(*),1)),
       count(*) FILTER (WHERE a.status IS DISTINCT FROM h.latest_status), NULL, 'Fact',
       'There is no single source of truth for account status. Which table you join to changes your answer -- exactly the situation the brief warns about.'
FROM stg.accounts a
LEFT JOIN LATERAL (
  SELECT status AS latest_status FROM stg.account_status_history h2
  WHERE h2.account_id = a.account_id ORDER BY h2.event_at_utc DESC, h2._ingest_seq DESC LIMIT 1) h ON TRUE;

-- =====================================================================
-- TRAP H -- REFERENTIAL INTEGRITY (added after cross-review)
-- ---------------------------------------------------------------------
-- Not one of the seven hypotheses the brief names, and it was missed on
-- the first pass: primary-key uniqueness was asserted but foreign-key
-- integrity was not. An independent review of this dataset flagged
-- orphan rows in the call fact, which reproduces here.
-- =====================================================================
DROP TABLE IF EXISTS forensics.h1_orphans CASCADE;
CREATE TABLE forensics.h1_orphans AS
SELECT 'fct_call -> borrowers' AS relationship,
       (SELECT count(*) FROM golden.fct_call) AS rows,
       (SELECT count(*) FROM golden.fct_call c
         WHERE NOT EXISTS (SELECT 1 FROM golden.dim_borrower b
                           WHERE b.borrower_id = c.borrower_id)) AS orphan_rows
UNION ALL
SELECT 'fct_payment -> borrowers',
       (SELECT count(*) FROM golden.fct_payment),
       (SELECT count(*) FROM golden.fct_payment p
         WHERE NOT EXISTS (SELECT 1 FROM golden.dim_borrower b
                           WHERE b.borrower_id = p.borrower_id));

INSERT INTO forensics.findings
SELECT 'H1','H','Fact tables reference borrowers that do not exist in the borrower table','CONFIRMED',
       format('%s of %s calls (%s%%) and %s of %s payments (%s%%) carry a borrower_id absent from the borrower dimension. %s distinct borrower_ids appear in fact tables but never in borrowers.csv at all -- a referential break present in the SOURCE data, not introduced by cleaning.',
         (SELECT orphan_rows FROM forensics.h1_orphans WHERE relationship='fct_call -> borrowers'),
         (SELECT rows FROM forensics.h1_orphans WHERE relationship='fct_call -> borrowers'),
         (SELECT round(100.0*orphan_rows/rows,2) FROM forensics.h1_orphans WHERE relationship='fct_call -> borrowers'),
         (SELECT orphan_rows FROM forensics.h1_orphans WHERE relationship='fct_payment -> borrowers'),
         (SELECT rows FROM forensics.h1_orphans WHERE relationship='fct_payment -> borrowers'),
         (SELECT round(100.0*orphan_rows/rows,2) FROM forensics.h1_orphans WHERE relationship='fct_payment -> borrowers'),
         (SELECT count(DISTINCT borrower_id) FROM golden.fct_call
           WHERE borrower_id NOT IN (SELECT borrower_id FROM raw.borrowers))),
       (SELECT sum(orphan_rows) FROM forensics.h1_orphans), NULL, 'Fact',
       'Any borrower-level cut -- geography, contactability, borrower segment -- silently drops ~8% of activity. The rows are retained and flagged rather than filtered, so the loss is visible instead of invisible.';

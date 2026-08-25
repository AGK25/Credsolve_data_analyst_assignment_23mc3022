-- =====================================================================
-- 03_clean_golden.sql -- CLEAN and GOLDEN layers
-- ---------------------------------------------------------------------
-- Every removal is written to reject.ledger with a reason code before
-- it disappears, so Raw -> Rejected -> Golden reconciles exactly and
-- the cost of each cleaning decision is a number, not an opinion.
--
-- Guiding principle, taken straight from the brief: "Do not assume the
-- cleanest-looking table is the most reliable." We therefore prefer the
-- narrowest defensible rule. Where two rules are both defensible we
-- implement the conservative one and measure the other (see A2 -- the
-- payment_reference trap) rather than silently picking.
-- =====================================================================

TRUNCATE reject.ledger;

-- =====================================================================
-- BORROWERS -- entity resolution
-- 30,600 rows resolve to 11,015 borrower_ids. Duplicates are versions
-- of the same borrower, not different people, so we keep the LATEST
-- version by updated_at (falling back to ingest order) rather than
-- merging fields: merging would invent a record that never existed.
-- =====================================================================
DROP TABLE IF EXISTS clean.borrowers CASCADE;
CREATE TABLE clean.borrowers AS
-- Postgres has no COUNT(DISTINCT ...) OVER (), so conflict detection is
-- a GROUP BY aggregate joined back onto the surviving version.
WITH profile AS (
  SELECT borrower_id,
         count(*)                   AS n_versions,
         count(DISTINCT name)       AS n_names,
         count(DISTINCT phone_norm) AS n_phones,
         count(DISTINCT city)       AS n_cities
  FROM stg.borrowers GROUP BY 1),
ranked AS (
  SELECT b.*, row_number() OVER (
           PARTITION BY borrower_id
           ORDER BY updated_at_naive DESC NULLS LAST, _ingest_seq DESC) AS rn
  FROM stg.borrowers b)
SELECT r.borrower_id, r.name, r.phone, r.phone_norm, r.email, r.email_norm,
       r.city, r.state, r.created_at_naive, r.updated_at_naive,
       p.n_versions, p.n_names, p.n_phones, p.n_cities,
       (p.n_names > 1 OR p.n_phones > 1 OR p.n_cities > 1) AS had_conflicting_attributes
FROM ranked r JOIN profile p USING (borrower_id)
WHERE r.rn = 1;

INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail)
SELECT 'borrowers', b.borrower_id, 'clean', 'SUPERSEDED_VERSION',
       format('older version of a borrower carrying %s name(s), %s phone(s), %s city/cities',
              b.n_names, b.n_phones, b.n_cities)
FROM clean.borrowers b
CROSS JOIN LATERAL generate_series(1, b.n_versions - 1) g
WHERE b.n_versions > 1;

-- =====================================================================
-- AGENTS -- attempted entity resolution, then honest failure
-- We do NOT skip this because it fails. Demonstrating that resolution
-- is impossible is the finding (forensics E1). We build a mode-based
-- resolution and record how unstable it is.
-- =====================================================================
DROP TABLE IF EXISTS clean.agents CASCADE;
CREATE TABLE clean.agents AS
WITH modes AS (
  SELECT agent_id,
         mode() WITHIN GROUP (ORDER BY agent_name)     AS modal_name,
         mode() WITHIN GROUP (ORDER BY team)           AS modal_team,
         mode() WITHIN GROUP (ORDER BY vendor_id)      AS modal_vendor,
         min(joined_at_naive)                          AS earliest_joined,
         max(joined_at_naive)                          AS latest_joined,
         count(*)                                      AS n_rows,
         count(DISTINCT agent_name)                    AS n_names,
         count(DISTINCT team)                          AS n_teams,
         count(DISTINCT vendor_id)                     AS n_vendors,
         count(DISTINCT employee_code)                 AS n_emp_codes
  FROM stg.agents GROUP BY 1),
support AS (
  SELECT m.agent_id,
         (SELECT count(*) FROM stg.agents a
           WHERE a.agent_id = m.agent_id AND a.agent_name = m.modal_name)::numeric
         / m.n_rows AS modal_name_support
  FROM modes m)
SELECT m.*, s.modal_name_support,
       -- A resolution is only trustworthy if the modal value dominates.
       (s.modal_name_support >= 0.60) AS name_is_resolvable,
       -- joined_at spread: tenure is undefined if the same id claims
       -- join dates months or years apart.
       EXTRACT(day FROM (m.latest_joined - m.earliest_joined))::int AS joined_at_spread_days
FROM modes m JOIN support s USING (agent_id);

-- =====================================================================
-- ACCOUNTS -- source-of-truth decision
-- accounts.status disagrees with account_status_history for 87.7% of
-- accounts (forensics G3). We must pick one and say why.
--
-- DECISION: account_status_history is the source of truth for status.
--   Rationale: it is an append-only event log with an explicit event
--   time, so it can be replayed as-of any date. accounts.status is a
--   single mutable column with no timestamp -- it cannot be audited,
--   cannot be reconstructed historically, and its provenance is unknown.
--   An event log that disagrees with a mutable snapshot is the more
--   trustworthy artefact, even though the snapshot "looks cleaner".
--   We keep the snapshot value alongside as status_snapshot so the
--   disagreement stays visible rather than being quietly resolved.
-- =====================================================================
DROP TABLE IF EXISTS clean.accounts CASCADE;
CREATE TABLE clean.accounts AS
SELECT a.account_id, a.borrower_id, a.loan_type,
       a.principal_amount, a.outstanding_amount, a.dpd, a.risk_segment,
       a.status                AS status_snapshot,
       h.latest_status         AS status_resolved,
       (a.status IS DISTINCT FROM h.latest_status) AS status_conflict,
       h.latest_status_at,
       a.opened_at_naive, a.opened_at_utc, a.declared_tz, a.schema_version,
       CASE WHEN a.dpd <= 5 THEN '0-5' WHEN a.dpd <= 30 THEN '6-30'
            WHEN a.dpd <= 60 THEN '31-60' WHEN a.dpd <= 90 THEN '61-90'
            ELSE '90+' END AS dpd_band
FROM stg.accounts a
LEFT JOIN LATERAL (
    SELECT status AS latest_status, event_at_utc AS latest_status_at
    FROM stg.account_status_history h
    WHERE h.account_id = a.account_id
    ORDER BY h.event_at_utc DESC, h._ingest_seq DESC LIMIT 1) h ON TRUE;

-- =====================================================================
-- PAYMENTS -- the decision that matters most
-- Rule: remove ONLY rows that are byte-identical to an earlier row on
-- every business column (re-ingestion). Do NOT deduplicate on
-- payment_reference -- forensics A2 shows that would destroy 7,366
-- legitimate payments worth ~Rs 38.9 Cr.
-- =====================================================================
--
-- REFINEMENT (D-011): "a missing value is not a different value".
-- A first pass partitioned on all 8 business columns including
-- payment_reference. That left 14 payment_ids still duplicated -- and
-- inspection showed every one differed ONLY in that one copy carried
-- the reference and the other had it blank. Identical account, amount,
-- status, method, provider and timestamp. Those are plainly the same
-- payment ingested twice with the reference lost on one copy, so
-- treating NULL as a distinct value was wrong.
--
-- Correct rule: partition on the columns that IDENTIFY the event, and
-- recover nullable enrichment fields across the copies rather than
-- letting their absence split the group.
-- Reconciliation check: this removes exactly 500 rows -- precisely the
-- 25,500 raw minus 25,000 distinct payment_ids observed at profiling.
--
DROP TABLE IF EXISTS clean.payments CASCADE;
CREATE TABLE clean.payments AS
SELECT payment_id, account_id, borrower_id,
       max(payment_reference) AS payment_reference,   -- non-null wins
       amount, payment_status, payment_method, provider_id,
       event_at_naive, max(event_at_utc) AS event_at_utc, max(event_at_ist) AS event_at_ist,
       (payment_status = 'SUCCESS')  AS is_success,
       (payment_status = 'REVERSED') AS is_reversed,
       CASE WHEN payment_status = 'SUCCESS'  THEN amount
            WHEN payment_status = 'REVERSED' THEN -amount
            ELSE 0 END AS net_amount,
       count(*)                                                AS n_raw_copies,
       bool_or(payment_reference IS NULL)
         AND bool_or(payment_reference IS NOT NULL)             AS ref_recovered_from_copy
FROM stg.payments
GROUP BY payment_id, account_id, borrower_id, amount, payment_status,
         payment_method, provider_id, event_at_naive;

INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail, amount_impact)
SELECT 'payments', p.payment_id, 'clean', 'DUPLICATE_REINGEST',
       format('%s raw copies collapsed to 1%s', p.n_raw_copies,
              CASE WHEN p.ref_recovered_from_copy
                   THEN ' (payment_reference recovered from the populated copy)'
                   ELSE '' END),
       CASE WHEN p.payment_status='SUCCESS' THEN p.amount ELSE 0 END
FROM clean.payments p
CROSS JOIN LATERAL generate_series(1, p.n_raw_copies - 1) g
WHERE p.n_raw_copies > 1;

-- (Out-of-window exclusions are logged after deduplication, further
--  below, so that the ledger's reason codes stay mutually exclusive and
--  Raw = Rejected + Golden reconciles exactly.)

-- =====================================================================
-- CALLS -- dedup on call_id (1,350 surplus rows), window applied
-- =====================================================================
-- Same NULL-is-not-a-difference rule as payments: 68 of 79 residual
-- call_id collisions differed only in one copy having a blank agent_id.
-- Removes exactly 1,350 rows = 91,350 raw - 90,000 distinct call_ids.
DROP TABLE IF EXISTS clean.calls CASCADE;
CREATE TABLE clean.calls AS
SELECT call_id, account_id, borrower_id,
       max(nullif(trim(agent_id),'')) AS agent_id,     -- non-null wins
       campaign_id, direction, vendor_id, call_status, duration_sec,
       declared_tz, event_at_naive,
       max(event_at_utc) AS event_at_utc, max(event_at_ist) AS event_at_ist,
       (call_status = 'ANSWERED') AS is_contact,
       count(*) AS n_raw_copies
FROM stg.calls
GROUP BY call_id, account_id, borrower_id, campaign_id, direction, vendor_id,
         call_status, duration_sec, declared_tz, event_at_naive;

--
-- SECOND PASS (D-012): corrupted-date duplicates.
-- 11 call_ids survived the pass above differing ONLY in the DATE part of
-- event_at, while sharing hour:minute:second exactly (e.g. CALL0000226
-- at 11:36:06 on both 09-Jan and 12-Jan, same account, agent, vendor,
-- status and duration). Two genuinely distinct calls coinciding to the
-- second has probability ~1/86,400; across 11 pairs it is not credible.
-- These are one event whose date was corrupted on one copy -- the
-- "conflicting timestamps" the source README warns about.
--
-- We collapse on time-of-day and keep the EARLIEST date, because the
-- later copy is the late-arriving/re-written one. We cannot know which
-- date is truly correct, so the row is flagged rather than silently
-- resolved. Event COUNTS -- what every rate metric depends on -- are
-- correct either way; only the day bucket is uncertain.
--
DROP TABLE IF EXISTS clean.calls_deduped CASCADE;
CREATE TABLE clean.calls_deduped AS
SELECT call_id, account_id, borrower_id, max(agent_id) AS agent_id,
       campaign_id, direction, vendor_id, call_status, duration_sec, declared_tz,
       min(event_at_naive) AS event_at_naive,
       min(event_at_utc)   AS event_at_utc,
       min(event_at_ist)   AS event_at_ist,
       is_contact,
       sum(n_raw_copies)   AS n_raw_copies,
       (count(*) > 1)      AS had_conflicting_timestamp,
       CASE WHEN count(*) > 1
            THEN EXTRACT(day FROM (max(event_at_naive) - min(event_at_naive)))::int
            ELSE 0 END     AS timestamp_conflict_days
FROM clean.calls
GROUP BY call_id, account_id, borrower_id, campaign_id, direction, vendor_id,
         call_status, duration_sec, declared_tz, event_at_naive::time, is_contact;

DROP TABLE IF EXISTS clean.calls CASCADE;
ALTER TABLE clean.calls_deduped RENAME TO calls;

-- Ledger arithmetic, written once, mutually exclusive by construction.
-- A surviving row that absorbed n_raw_copies originals accounts for
-- (n_raw_copies - 1) removals. Where a timestamp conflict was collapsed,
-- exactly one of those removals is attributed to CONFLICTING_TIMESTAMP
-- and the remainder to DUPLICATE_REINGEST. Summed over the table this
-- equals raw_rows - clean_rows exactly, which is asserted below.
INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail)
SELECT 'calls', call_id, 'clean', 'CONFLICTING_TIMESTAMP',
       format('identical call at the same time-of-day recorded %s days apart; earliest date retained',
              timestamp_conflict_days)
FROM clean.calls WHERE had_conflicting_timestamp;

INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail)
SELECT 'calls', c.call_id, 'clean', 'DUPLICATE_REINGEST',
       format('%s raw copies collapsed to 1', c.n_raw_copies)
FROM clean.calls c
CROSS JOIN LATERAL generate_series(1, (c.n_raw_copies - 1) - c.had_conflicting_timestamp::int) g
WHERE (c.n_raw_copies - 1) - c.had_conflicting_timestamp::int > 0;

-- (Duplicate ledger entries for calls are written after the second pass
--  below, so that DUPLICATE_REINGEST and CONFLICTING_TIMESTAMP never
--  describe the same removed row.)

-- =====================================================================
-- DISPOSITIONS -- collapse the synonym codes (forensics D2)
-- =====================================================================
DROP TABLE IF EXISTS clean.call_dispositions CASCADE;
CREATE TABLE clean.call_dispositions AS
SELECT disposition_id, account_id, borrower_id, call_id, agent_id,
       disposition_code AS disposition_code_raw,
       CASE WHEN disposition_code IN ('PTP','PROMISE_TO_PAY') THEN 'PROMISE_TO_PAY'
            ELSE disposition_code END AS disposition_code,
       disposition_version, event_at_utc, event_at_ist
FROM stg.call_dispositions;

-- =====================================================================
-- WHATSAPP / SMS -- dedup on event pk, unify into one digital stream
-- =====================================================================
DROP TABLE IF EXISTS clean.digital_events CASCADE;
CREATE TABLE clean.digital_events AS
WITH u AS (
  SELECT event_pk, account_id, borrower_id, message_id, event_type,
         template_code, provider_id, channel, event_at_utc, event_at_ist, _ingest_seq
  FROM stg.whatsapp_events
  UNION ALL
  SELECT event_pk, account_id, borrower_id, message_id, event_type,
         template_code, provider_id, channel, event_at_utc, event_at_ist, _ingest_seq
  FROM stg.sms_events),
ranked AS (
  SELECT u.*, row_number() OVER (
    PARTITION BY channel, event_pk, account_id, message_id, event_type, event_at_utc
    ORDER BY _ingest_seq) AS copy_no
  FROM u)
SELECT event_pk, account_id, borrower_id, message_id, event_type,
       template_code, provider_id, channel, event_at_utc, event_at_ist
FROM ranked WHERE copy_no = 1;

-- =====================================================================
-- WINDOW EXCLUSIONS -- logged AFTER dedup so reason codes never overlap
-- ---------------------------------------------------------------------
-- The analysis window is 2026-01-01 .. 2026-07-31 (IST). Two reasons:
--   * calls alone extends to 2026-08-12 and back to 2025-12-29, outside
--     every other table's range -- late-arriving events.
--   * August 2026 holds only 8 days. Including a partial month in a
--     month-on-month series manufactures a fake ~74% collapse, which is
--     the mirror image of the calendar error that produced the +11%.
-- Excluded rows are recorded, never deleted, and August is reported
-- separately as a partial period.
-- =====================================================================
INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail)
SELECT 'calls', call_id, 'golden', 'OUT_OF_WINDOW',
       format('event_at_ist = %s, outside 2026-01-01..2026-07-31', event_at_ist::date)
FROM clean.calls
WHERE event_at_ist < '2026-01-01' OR event_at_ist >= '2026-08-01';

INSERT INTO reject.ledger (source_table, source_key, stage, reason_code, reason_detail, amount_impact)
SELECT 'payments', payment_id, 'golden', 'OUT_OF_WINDOW',
       format('event_at_ist = %s -- partial August, reported separately', event_at_ist::date),
       CASE WHEN payment_status='SUCCESS' THEN amount ELSE 0 END
FROM clean.payments
WHERE event_at_ist < '2026-01-01' OR event_at_ist >= '2026-08-01';

-- =====================================================================
-- GOLDEN LAYER
-- Grain is declared for every table. Nothing here is ambiguous.
-- =====================================================================

-- GRAIN: one row per account. The analytical spine.
DROP TABLE IF EXISTS golden.dim_account CASCADE;
CREATE TABLE golden.dim_account AS
SELECT a.account_id, a.borrower_id, a.loan_type, a.risk_segment,
       a.dpd, a.dpd_band, a.principal_amount, a.outstanding_amount,
       a.status_snapshot, a.status_resolved, a.status_conflict,
       a.opened_at_utc, a.schema_version,
       b.city, b.state, b.had_conflicting_attributes AS borrower_had_conflicts
FROM clean.accounts a
LEFT JOIN clean.borrowers b USING (borrower_id);
ALTER TABLE golden.dim_account ADD PRIMARY KEY (account_id);

-- GRAIN: one row per payment event, in-window, deduplicated.
DROP TABLE IF EXISTS golden.fct_payment CASCADE;
CREATE TABLE golden.fct_payment AS
SELECT p.*, date_trunc('month', p.event_at_ist)::date AS month_ist,
       p.event_at_ist::date AS date_ist
FROM clean.payments p
WHERE p.event_at_ist >= '2026-01-01' AND p.event_at_ist < '2026-08-01';
ALTER TABLE golden.fct_payment ADD PRIMARY KEY (payment_id);
CREATE INDEX ON golden.fct_payment (account_id, event_at_utc);
CREATE INDEX ON golden.fct_payment (month_ist);

-- GRAIN: one row per call, in-window, deduplicated.
DROP TABLE IF EXISTS golden.fct_call CASCADE;
CREATE TABLE golden.fct_call AS
SELECT c.*, date_trunc('month', c.event_at_ist)::date AS month_ist,
       c.event_at_ist::date AS date_ist
FROM clean.calls c
WHERE c.event_at_ist >= '2026-01-01' AND c.event_at_ist < '2026-08-01';
ALTER TABLE golden.fct_call ADD PRIMARY KEY (call_id);
CREATE INDEX ON golden.fct_call (account_id, event_at_utc);
CREATE INDEX ON golden.fct_call (month_ist);

-- GRAIN: unified touch events across all channels (CALL, WHATSAPP, SMS, FIELD)
DROP TABLE IF EXISTS golden.fct_touch CASCADE;
CREATE TABLE golden.fct_touch AS
SELECT account_id, event_at_utc, event_at_ist, 'CALL'::text AS channel
FROM golden.fct_call
UNION ALL
SELECT account_id, event_at_utc, event_at_ist, channel
FROM clean.digital_events
UNION ALL
SELECT account_id, event_at_utc, event_at_ist, 'FIELD'::text AS channel
FROM stg.field_visits
WHERE event_at_ist >= '2026-01-01' AND event_at_ist < '2026-08-01';
CREATE INDEX ON golden.fct_touch (account_id, event_at_utc);

-- GRAIN: one row per calendar day, with day count -- the table that
-- makes the calendar effect impossible to accidentally reintroduce.
DROP TABLE IF EXISTS golden.dim_date CASCADE;
CREATE TABLE golden.dim_date AS
SELECT d::date AS date_ist,
       date_trunc('month', d)::date AS month_ist,
       EXTRACT(day FROM (date_trunc('month',d) + interval '1 month' - interval '1 day'))::int AS days_in_month,
       EXTRACT(isodow FROM d)::int AS iso_dow,
       (EXTRACT(isodow FROM d) >= 6) AS is_weekend
FROM generate_series('2026-01-01'::date, '2026-07-31'::date, '1 day') d;
ALTER TABLE golden.dim_date ADD PRIMARY KEY (date_ist);

-- =====================================================================
-- BORROWER DIMENSION
-- GRAIN: one row per resolved borrower.
-- 969 resolved borrowers hold no account in this extract. They are NOT
-- rejects -- they are simply off the account spine -- so they are kept
-- and flagged rather than dropped, which would quietly shrink the
-- borrower population.
-- =====================================================================
DROP TABLE IF EXISTS golden.dim_borrower CASCADE;
CREATE TABLE golden.dim_borrower AS
SELECT b.borrower_id, b.name, b.phone_norm, b.email_norm, b.city, b.state,
       b.n_versions AS raw_versions_collapsed,
       b.had_conflicting_attributes,
       EXISTS (SELECT 1 FROM clean.accounts a WHERE a.borrower_id = b.borrower_id) AS has_account
FROM clean.borrowers b;
ALTER TABLE golden.dim_borrower ADD PRIMARY KEY (borrower_id);

-- =====================================================================
-- LINEAGE -- Raw -> Rejected/Corrected -> Golden, reconciled exactly
-- Every entity must satisfy: raw_rows - rejected_rows = golden_rows.
-- The assertion below fails the build if it ever stops holding.
-- =====================================================================
DROP TABLE IF EXISTS golden.lineage CASCADE;
CREATE TABLE golden.lineage AS
SELECT 'payments' AS entity,
       (SELECT n_rows FROM raw._ingest_census WHERE table_name='payments')  AS raw_rows,
       (SELECT count(*) FROM reject.ledger WHERE source_table='payments')   AS rejected_rows,
       (SELECT count(*) FROM clean.payments)                                AS clean_rows,
       (SELECT count(*) FROM golden.fct_payment)                            AS golden_rows
UNION ALL
SELECT 'calls',
       (SELECT n_rows FROM raw._ingest_census WHERE table_name='calls'),
       (SELECT count(*) FROM reject.ledger WHERE source_table='calls'),
       (SELECT count(*) FROM clean.calls),
       (SELECT count(*) FROM golden.fct_call)
UNION ALL
SELECT 'borrowers',
       (SELECT n_rows FROM raw._ingest_census WHERE table_name='borrowers'),
       (SELECT count(*) FROM reject.ledger WHERE source_table='borrowers'),
       (SELECT count(*) FROM clean.borrowers),
       (SELECT count(*) FROM golden.dim_borrower)
UNION ALL
SELECT 'accounts',
       (SELECT n_rows FROM raw._ingest_census WHERE table_name='accounts'),
       (SELECT count(*) FROM reject.ledger WHERE source_table='accounts'),
       (SELECT count(*) FROM clean.accounts),
       (SELECT count(*) FROM golden.dim_account);

ALTER TABLE golden.lineage ADD COLUMN reconciles BOOLEAN;
UPDATE golden.lineage SET reconciles = (raw_rows - rejected_rows = golden_rows);

DO $$
DECLARE bad INT;
BEGIN
    SELECT count(*) INTO bad FROM golden.lineage WHERE NOT reconciles;
    IF bad > 0 THEN
        RAISE EXCEPTION 'LINEAGE DOES NOT RECONCILE for % entity/entities: %',
            bad, (SELECT string_agg(format('%s (raw %s - rejected %s <> golden %s)',
                    entity, raw_rows, rejected_rows, golden_rows), '; ')
                  FROM golden.lineage WHERE NOT reconciles);
    END IF;
    RAISE NOTICE 'Lineage reconciles for all % entities.', (SELECT count(*) FROM golden.lineage);
END $$;

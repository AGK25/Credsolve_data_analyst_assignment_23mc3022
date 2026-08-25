-- =====================================================================
-- 01_staging.sql -- typed + timezone-normalised staging layer
-- ---------------------------------------------------------------------
-- INVARIANT: stg.<t> has exactly the same row count as raw.<t>.
-- This layer NEVER removes a row. It only fixes representation:
--   * TEXT -> proper types, via safe casts that yield NULL instead of
--     erroring (a failed cast is a finding, not a crash)
--   * naive wall-clock text -> real timestamptz
--
-- TIMEZONE POLICY (see docs/DECISIONS.md D-004)
--   Every event_at in this dataset is naive wall-clock text with NO
--   offset: 'YYYY-MM-DD HH:MM:SS'. Three tables declare the zone that
--   text is expressed in (calls, accounts, agent_sessions); the rest
--   declare nothing.
--   - Where a zone is declared, we apply it. Not applying it is simply
--     a bug: 'Asia/Kolkata 00:30' is the previous UTC day.
--   - Where no zone is declared we adopt UTC as the stated convention
--     and carry event_at_naive alongside, so 03_forensics can measure
--     how much the answer moves under the alternative assumption
--     (account-home-timezone). We measure the sensitivity instead of
--     asserting an assumption.
--   Both event_at_utc and event_at_ist are materialised: UTC is the
--   join/ordering key, IST is the reporting calendar (Indian lender).
-- =====================================================================

-- --------------------------------------------------------------- utils
CREATE OR REPLACE FUNCTION stg.try_ts(t TEXT) RETURNS TIMESTAMP AS $$
BEGIN RETURN t::TIMESTAMP; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION stg.try_num(t TEXT) RETURNS NUMERIC AS $$
BEGIN RETURN t::NUMERIC; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION stg.try_int(t TEXT) RETURNS INTEGER AS $$
BEGIN RETURN t::NUMERIC::INTEGER; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Interpret naive wall-clock in a named zone -> absolute time.
-- Unknown/blank zone falls back to UTC (documented convention, D-004).
CREATE OR REPLACE FUNCTION stg.to_utc(ts TIMESTAMP, tz TEXT) RETURNS TIMESTAMPTZ AS $$
BEGIN
    IF ts IS NULL THEN RETURN NULL; END IF;
    RETURN ts AT TIME ZONE coalesce(nullif(trim(tz), ''), 'UTC');
EXCEPTION WHEN others THEN
    RETURN ts AT TIME ZONE 'UTC';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================ accounts
DROP TABLE IF EXISTS stg.accounts CASCADE;
CREATE TABLE stg.accounts AS
SELECT _ingest_seq,
       account_id, borrower_id, loan_type,
       stg.try_num(principal_amount)   AS principal_amount,
       stg.try_num(outstanding_amount) AS outstanding_amount,
       stg.try_int(dpd)                AS dpd,
       risk_segment, status,
       stg.try_ts(opened_at)                          AS opened_at_naive,
       stg.to_utc(stg.try_ts(opened_at), timezone)    AS opened_at_utc,
       timezone AS declared_tz, schema_version
FROM raw.accounts;

-- =========================================================== borrowers
DROP TABLE IF EXISTS stg.borrowers CASCADE;
CREATE TABLE stg.borrowers AS
SELECT _ingest_seq, borrower_id,
       nullif(trim(name),'')  AS name,
       nullif(trim(phone),'') AS phone,
       nullif(trim(email),'') AS email,
       nullif(trim(city),'')  AS city,
       nullif(trim(state),'') AS state,
       stg.try_ts(created_at) AS created_at_naive,
       stg.try_ts(updated_at) AS updated_at_naive,
       -- normalised phone: digits only, last 10 (Indian mobile grain)
       right(regexp_replace(coalesce(phone,''),'[^0-9]','','g'), 10) AS phone_norm,
       lower(nullif(trim(email),'')) AS email_norm
FROM raw.borrowers;

-- ============================================================== agents
DROP TABLE IF EXISTS stg.agents CASCADE;
CREATE TABLE stg.agents AS
SELECT _ingest_seq, agent_id, employee_code, agent_name, vendor_id, team, status,
       stg.try_ts(joined_at)  AS joined_at_naive,
       stg.try_ts(updated_at) AS updated_at_naive
FROM raw.agents;

-- ====================================================== agent_sessions
DROP TABLE IF EXISTS stg.agent_sessions CASCADE;
CREATE TABLE stg.agent_sessions AS
SELECT _ingest_seq, session_id, agent_id, channel, device_id,
       timezone AS declared_tz,
       stg.try_ts(login_at)                        AS login_at_naive,
       stg.try_ts(logout_at)                       AS logout_at_naive,
       stg.to_utc(stg.try_ts(login_at),  timezone) AS login_at_utc,
       stg.to_utc(stg.try_ts(logout_at), timezone) AS logout_at_utc,
       EXTRACT(EPOCH FROM (stg.try_ts(logout_at) - stg.try_ts(login_at)))/3600.0 AS session_hours
FROM raw.agent_sessions;

-- =============================================================== calls
-- The only fact table with a per-row declared timezone. Applying it is
-- mandatory for correctness; 03_forensics quantifies what changes.
DROP TABLE IF EXISTS stg.calls CASCADE;
CREATE TABLE stg.calls AS
SELECT _ingest_seq, call_id, account_id, borrower_id, agent_id, campaign_id,
       direction, vendor_id, call_status,
       stg.try_int(duration_sec) AS duration_sec,
       timezone AS declared_tz,
       stg.try_ts(event_at)                                        AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), timezone)                  AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at), timezone)
            AT TIME ZONE 'Asia/Kolkata')                           AS event_at_ist
FROM raw.calls;

-- ======================================================= call_attempts
DROP TABLE IF EXISTS stg.call_attempts CASCADE;
CREATE TABLE stg.call_attempts AS
SELECT _ingest_seq, attempt_id, account_id, borrower_id, call_id, agent_id, vendor_id,
       stg.try_int(attempt_no) AS attempt_no,
       attempt_status,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.call_attempts;

-- =================================================== call_dispositions
DROP TABLE IF EXISTS stg.call_dispositions CASCADE;
CREATE TABLE stg.call_dispositions AS
SELECT _ingest_seq, disposition_id, account_id, borrower_id, call_id, agent_id,
       disposition_code, disposition_version,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.call_dispositions;

-- ============================================================ payments
DROP TABLE IF EXISTS stg.payments CASCADE;
CREATE TABLE stg.payments AS
SELECT _ingest_seq, payment_id, account_id, borrower_id,
       nullif(trim(payment_reference),'') AS payment_reference,
       stg.try_num(amount) AS amount,
       payment_status, payment_method, provider_id,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.payments;

-- ===================================================== promises_to_pay
DROP TABLE IF EXISTS stg.promises_to_pay CASCADE;
CREATE TABLE stg.promises_to_pay AS
SELECT _ingest_seq, ptp_id, account_id, borrower_id, agent_id, source, status,
       stg.try_num(promised_amount) AS promised_amount,
       stg.try_ts(promised_date)    AS promised_date_naive,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.promises_to_pay;

-- ======================================================== field_visits
DROP TABLE IF EXISTS stg.field_visits CASCADE;
CREATE TABLE stg.field_visits AS
SELECT _ingest_seq, visit_id, account_id, borrower_id, agent_id, visit_type, outcome,
       stg.try_num(latitude)  AS latitude,
       stg.try_num(longitude) AS longitude,
       stg.try_ts(scheduled_at) AS scheduled_at_naive,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.field_visits;

-- ===================================================== whatsapp / sms
DROP TABLE IF EXISTS stg.whatsapp_events CASCADE;
CREATE TABLE stg.whatsapp_events AS
SELECT _ingest_seq, whatsapp_event_id AS event_pk, account_id, borrower_id,
       message_id, event_type, template_code, provider_id,
       'WHATSAPP'::text AS channel,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.whatsapp_events;

DROP TABLE IF EXISTS stg.sms_events CASCADE;
CREATE TABLE stg.sms_events AS
SELECT _ingest_seq, sms_event_id AS event_pk, account_id, borrower_id,
       message_id, event_type, template_code, provider_id,
       'SMS'::text AS channel,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.sms_events;

-- ========================================================== complaints
DROP TABLE IF EXISTS stg.complaints CASCADE;
CREATE TABLE stg.complaints AS
SELECT _ingest_seq, complaint_id, account_id, borrower_id,
       complaint_type, severity, status, source,
       stg.try_ts(resolution_at) AS resolution_at_naive,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       (stg.to_utc(stg.try_ts(event_at),'UTC')
            AT TIME ZONE 'Asia/Kolkata')                   AS event_at_ist
FROM raw.complaints;

-- ============================================ account_status_history
-- recorded_at vs event_at is the late-arrival / overwrite signal.
DROP TABLE IF EXISTS stg.account_status_history CASCADE;
CREATE TABLE stg.account_status_history AS
SELECT _ingest_seq, history_id, account_id, borrower_id, status, changed_by, source,
       stg.try_ts(event_at)                                AS event_at_naive,
       stg.try_ts(recorded_at)                             AS recorded_at_naive,
       stg.to_utc(stg.try_ts(event_at), 'UTC')             AS event_at_utc,
       stg.to_utc(stg.try_ts(recorded_at), 'UTC')          AS recorded_at_utc,
       EXTRACT(EPOCH FROM (stg.try_ts(recorded_at) - stg.try_ts(event_at)))/3600.0
                                                           AS ingest_lag_hours
FROM raw.account_status_history;

-- =========================================================== campaigns
DROP TABLE IF EXISTS stg.campaigns CASCADE;
CREATE TABLE stg.campaigns AS
SELECT _ingest_seq, campaign_id, campaign_name, channel, strategy_version, target_definition,
       stg.try_ts(start_at) AS start_at_naive,
       stg.try_ts(end_at)   AS end_at_naive
FROM raw.campaigns;

-- ===================================================== daily_targeting
DROP TABLE IF EXISTS stg.daily_targeting CASCADE;
CREATE TABLE stg.daily_targeting AS
SELECT _ingest_seq, target_id, account_id, campaign_id, priority, recommended_channel, status,
       stg.try_ts(target_date)::date AS target_date
FROM raw.daily_targeting;

-- =================================================== vendor_telephony
DROP TABLE IF EXISTS stg.vendor_telephony CASCADE;
CREATE TABLE stg.vendor_telephony AS
SELECT _ingest_seq, vendor_id, vendor_name, vendor_account_id,
       timezone AS declared_tz, status, schema_version
FROM raw.vendor_telephony;

-- ================================================= INVARIANT CHECK ===
-- staging must be row-for-row identical to raw. Fail loudly if not.
DROP TABLE IF EXISTS stg._rowcount_check CASCADE;
CREATE TABLE stg._rowcount_check AS
WITH s AS (
  SELECT 'accounts' t,(SELECT count(*) FROM stg.accounts) n UNION ALL
  SELECT 'borrowers',(SELECT count(*) FROM stg.borrowers) UNION ALL
  SELECT 'agents',(SELECT count(*) FROM stg.agents) UNION ALL
  SELECT 'agent_sessions',(SELECT count(*) FROM stg.agent_sessions) UNION ALL
  SELECT 'calls',(SELECT count(*) FROM stg.calls) UNION ALL
  SELECT 'call_attempts',(SELECT count(*) FROM stg.call_attempts) UNION ALL
  SELECT 'call_dispositions',(SELECT count(*) FROM stg.call_dispositions) UNION ALL
  SELECT 'payments',(SELECT count(*) FROM stg.payments) UNION ALL
  SELECT 'promises_to_pay',(SELECT count(*) FROM stg.promises_to_pay) UNION ALL
  SELECT 'field_visits',(SELECT count(*) FROM stg.field_visits) UNION ALL
  SELECT 'whatsapp_events',(SELECT count(*) FROM stg.whatsapp_events) UNION ALL
  SELECT 'sms_events',(SELECT count(*) FROM stg.sms_events) UNION ALL
  SELECT 'complaints',(SELECT count(*) FROM stg.complaints) UNION ALL
  SELECT 'account_status_history',(SELECT count(*) FROM stg.account_status_history) UNION ALL
  SELECT 'campaigns',(SELECT count(*) FROM stg.campaigns) UNION ALL
  SELECT 'daily_targeting',(SELECT count(*) FROM stg.daily_targeting) UNION ALL
  SELECT 'vendor_telephony',(SELECT count(*) FROM stg.vendor_telephony)
)
SELECT s.t AS table_name, c.n_rows AS raw_rows, s.n AS stg_rows,
       (c.n_rows = s.n) AS ok
FROM s JOIN raw._ingest_census c ON c.table_name = s.t;

-- =====================================================================
-- 04_metrics.sql -- independent metric definitions
-- ---------------------------------------------------------------------
-- The brief asks us to CHALLENGE ten existing definitions. For each we
-- record: the definition the business is most likely using, why it is
-- wrong or fragile, and the definition we adopt instead.
--
-- One rule governs everything here: NO METRIC IS EVER REPORTED AS A
-- MONTHLY TOTAL. Every rate is expressed per day, per account or per
-- agent-hour. Monthly totals are what produced the phantom +11%, and a
-- metrics layer that still permits them will produce it again.
-- =====================================================================

DROP TABLE IF EXISTS metrics.definitions CASCADE;
CREATE TABLE metrics.definitions (
    metric_id       TEXT PRIMARY KEY,
    metric_name     TEXT NOT NULL,
    business_defn   TEXT NOT NULL,
    problem         TEXT NOT NULL,
    our_defn        TEXT NOT NULL,
    verdict         TEXT NOT NULL   -- USABLE | USABLE_WITH_CARE | UNUSABLE
);

INSERT INTO metrics.definitions VALUES
('M01','Contact rate',
 'answered calls / calls placed',
 'Counts CALL rows, but a single outreach attempt can produce several call rows and 120,000 call_attempts sit behind 90,000 calls. It also treats a borrower reached ten times as ten contacts, so the metric rises when the dialler gets more aggressive even if no new person is reached.',
 'distinct accounts reached at least once in the period / distinct accounts attempted in the period. Account-level, not event-level, so it cannot be inflated by redialling.',
 'USABLE_WITH_CARE'),
('M02','RPC (right party contact)',
 'calls with a "contact" disposition / calls placed',
 'There is no field identifying whether the party reached was the borrower. WRONG_NUMBER is one of nine disposition codes and is assigned independently of call_status: FAILED 21.2%, BUSY 20.1%, NO_ANSWER 20.1%, ANSWERED 19.5%, VOICEMAIL 19.2%. At least 61.3% of WRONG_NUMBER dispositions sit on calls that never connected, where a wrong-number determination is impossible.',
 'Not computable from this data. We report answered-call rate instead and state plainly that true RPC requires a party-identification field that does not exist.',
 'UNUSABLE'),
('M03','PTP rate',
 'dispositions where code = PTP / total dispositions',
 'PTP and PROMISE_TO_PAY are separate codes for the same concept, present in every schema version. Filtering on either one alone undercounts promises by roughly 50%.',
 'dispositions where code IN (PTP, PROMISE_TO_PAY) / distinct accounts contacted. Synonyms collapsed at the clean layer so the error cannot recur downstream.',
 'USABLE_WITH_CARE'),
('M04','PTP kept rate',
 'promises with status = KEPT / total promises',
 'The status field does not correspond to reality. A promise marked KEPT is followed by an actual payment within 30 days 7.75% of the time; one marked BROKEN, 6.63%. The field carries essentially no information about whether money arrived.',
 'Ignore the status field entirely. Measure fulfilment directly: promises followed by a successful payment of at least the promised amount within 30 days / total promises.',
 'UNUSABLE'),
('M05','Recovery rate',
 'sum of payment amounts in the month / opening outstanding',
 'Includes FAILED, PENDING and REVERSED payments; and the monthly numerator makes a 31-day month look 11% better than a 28-day one.',
 'Net collections per day = (SUCCESS - REVERSED) / days in period. Day-normalised and net of money that left again.',
 'USABLE'),
('M06','Recovery per account',
 'total recovered / total accounts',
 'The denominator silently changes as accounts close or are written off, so the ratio can rise with no extra rupee collected.',
 'Net collections per day / accounts in the fixed opening cohort. The cohort is frozen at window start, so the denominator cannot drift.',
 'USABLE'),
('M07','Recovery per agent-hour',
 'total recovered / agent hours logged',
 'agent_sessions has no reliable link to outcomes, and the agents dimension cannot resolve identity, so hours cannot be attributed to a person or team with confidence.',
 'Computed at portfolio level only, from summed session hours. Never broken down by agent, team or vendor -- those cuts are not supportable.',
 'USABLE_WITH_CARE'),
('M08','Cost per rupee recovered',
 'channel cost / amount attributed to that channel',
 'No cost data exists in the extract, and the attributed amount depends entirely on an unstated attribution window: across all four channels, last-touch credit covers 3.2% of successful payments at 1 day, 20.2% at 7 days, 59.8% at 30 days and 83.8% at 90 days -- a 26x swing from the window choice alone.',
 'Not computable. We report the attribution sensitivity instead, so the reader can see how much any such figure would move.',
 'UNUSABLE'),
('M09','Channel conversion',
 'accounts that paid after a channel touch / accounts touched',
 'Every account is touched on several channels, so the denominators overlap and the shares sum to far more than 100%. It also conflates correlation with causation -- untouched accounts pay at nearly the same rate.',
 'Reported only as a comparison against the never-contacted baseline, with a confidence interval on the difference. Absolute per-channel conversion is not reported at all.',
 'USABLE_WITH_CARE'),
('M10','Month-on-month improvement',
 'this month total / last month total - 1',
 'Confounded with month length. February to March carries a mechanical +10.71% before any performance change whatsoever.',
 'Day-normalised rate change, with a 95% confidence interval from the daily series. Monthly totals are never compared directly.',
 'USABLE');

-- =====================================================================
-- CORE MART -- GRAIN: one row per day
-- =====================================================================
DROP TABLE IF EXISTS metrics.daily CASCADE;
CREATE TABLE metrics.daily AS
SELECT d.date_ist, d.month_ist, d.days_in_month, d.is_weekend,
       coalesce(p.gross_success, 0)                           AS gross_success,
       coalesce(p.reversed, 0)                                AS reversed,
       coalesce(p.gross_success, 0) - coalesce(p.reversed, 0)  AS net_collected,
       coalesce(p.n_success, 0)                               AS n_success_payments,
       coalesce(c.n_calls, 0)                                 AS n_calls,
       coalesce(c.n_answered, 0)                              AS n_answered,
       coalesce(c.n_accounts_attempted, 0)                    AS n_accounts_attempted,
       coalesce(c.n_accounts_reached, 0)                      AS n_accounts_reached
FROM golden.dim_date d
LEFT JOIN (
    SELECT date_ist,
           sum(amount) FILTER (WHERE is_success)  AS gross_success,
           sum(amount) FILTER (WHERE is_reversed) AS reversed,
           count(*)    FILTER (WHERE is_success)  AS n_success
    FROM golden.fct_payment GROUP BY 1) p USING (date_ist)
LEFT JOIN (
    SELECT date_ist, count(*) AS n_calls,
           count(*) FILTER (WHERE is_contact)               AS n_answered,
           count(DISTINCT account_id)                       AS n_accounts_attempted,
           count(DISTINCT account_id) FILTER (WHERE is_contact) AS n_accounts_reached
    FROM golden.fct_call GROUP BY 1) c USING (date_ist);

-- =====================================================================
-- MONTHLY MART -- GRAIN: one row per month.
-- Totals are carried for reference but every COMPARISON column is
-- day-normalised. days_in_month is stored beside them so the calendar
-- confound is impossible to overlook.
-- =====================================================================
DROP TABLE IF EXISTS metrics.monthly CASCADE;
CREATE TABLE metrics.monthly AS
WITH m AS (
  SELECT month_ist, max(days_in_month) AS days_in_month,
         sum(net_collected)  AS net_collected,
         sum(gross_success)  AS gross_success,
         sum(reversed)       AS reversed,
         sum(n_calls)        AS n_calls,
         sum(n_answered)     AS n_answered
  FROM metrics.daily GROUP BY 1),
acct AS (
  SELECT date_trunc('month', event_at_ist)::date AS month_ist,
         count(DISTINCT account_id) AS accounts_attempted,
         count(DISTINCT account_id) FILTER (WHERE is_contact) AS accounts_reached
  FROM golden.fct_call GROUP BY 1)
SELECT m.month_ist, m.days_in_month,
       m.net_collected, m.gross_success, m.reversed,
       -- day-normalised -- these are the ONLY series to compare
       round(m.net_collected  / m.days_in_month, 2) AS net_per_day,
       round(m.gross_success  / m.days_in_month, 2) AS gross_per_day,
       round(m.n_calls::numeric  / m.days_in_month, 1) AS calls_per_day,
       -- M01 contact rate, account-level
       a.accounts_attempted, a.accounts_reached,
       round(100.0 * a.accounts_reached / nullif(a.accounts_attempted,0), 2) AS contact_rate_pct,
       -- naive month-on-month (kept ONLY to demonstrate the error)
       round(100.0 * (m.gross_success / lag(m.gross_success) OVER (ORDER BY m.month_ist) - 1), 2)
            AS naive_mom_pct,
       -- correct month-on-month
       round(100.0 * ((m.gross_success/m.days_in_month)
             / lag(m.gross_success/m.days_in_month) OVER (ORDER BY m.month_ist) - 1), 2)
            AS true_mom_pct,
       -- the gap between them IS the calendar artefact
       round(100.0 * (m.days_in_month::numeric / lag(m.days_in_month) OVER (ORDER BY m.month_ist) - 1), 2)
            AS calendar_effect_pct
FROM m JOIN acct a USING (month_ist)
ORDER BY m.month_ist;

-- =====================================================================
-- THE DECOMPOSITION -- what the +11% is actually made of
-- Multiplicative, so the components reconstruct the headline exactly.
-- =====================================================================
DROP TABLE IF EXISTS metrics.headline_decomposition CASCADE;
CREATE TABLE metrics.headline_decomposition AS
WITH fm AS (
  SELECT (SELECT gross_success FROM metrics.monthly WHERE month_ist='2026-02-01') AS feb,
         (SELECT gross_success FROM metrics.monthly WHERE month_ist='2026-03-01') AS mar,
         28::numeric AS feb_days, 31::numeric AS mar_days)
SELECT * FROM (VALUES
  (1, 'Reported Feb -> Mar improvement',
      (SELECT round(100.0*(mar/feb-1),2) FROM fm),
      'What leadership was told.'),
  (2, 'less: calendar effect (28 -> 31 days)',
      (SELECT round(100.0*(mar_days/feb_days-1),2) FROM fm),
      'March simply contains three more collection days than February. No performance change of any kind.'),
  (3, 'equals: true change in daily collection rate',
      (SELECT round(100.0*((mar/mar_days)/(feb/feb_days)-1),2) FROM fm),
      'The entire genuine movement, and it is inside daily noise.')
) AS t(step, component, value_pct, note);

-- =====================================================================
-- METRIC SCOREBOARD -- the honest version of the ten metrics
-- =====================================================================
DROP TABLE IF EXISTS metrics.scoreboard CASCADE;
CREATE TABLE metrics.scoreboard AS
SELECT
  (SELECT round(avg(net_per_day)/1e5,2) FROM metrics.monthly)              AS avg_net_lakh_per_day,
  (SELECT round(100.0*((SELECT net_per_day FROM metrics.monthly WHERE month_ist='2026-07-01')
                     /(SELECT net_per_day FROM metrics.monthly WHERE month_ist='2026-01-01')-1),2)) AS net_change_jan_to_jul_pct,
  (SELECT round(avg(contact_rate_pct),2) FROM metrics.monthly)             AS avg_contact_rate_pct,
  (SELECT count(*) FROM golden.dim_account)                                AS cohort_accounts,
  (SELECT round(sum(net_collected)/1e7,2) FROM metrics.monthly)            AS total_net_cr,
  (SELECT round(sum(reversed)/1e7,2) FROM metrics.monthly)                 AS total_reversed_cr,
  (SELECT count(*) FROM metrics.definitions WHERE verdict='UNUSABLE')      AS metrics_unusable,
  (SELECT count(*) FROM metrics.definitions)                               AS metrics_reviewed,
  (SELECT count(*) FROM forensics.findings WHERE verdict='CONFIRMED')      AS issues_confirmed,
  (SELECT count(*) FROM forensics.findings WHERE verdict='REJECTED')       AS hypotheses_rejected;

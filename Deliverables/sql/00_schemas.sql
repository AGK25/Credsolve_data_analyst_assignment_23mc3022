-- =====================================================================
-- 00_schemas.sql -- pipeline layers
-- ---------------------------------------------------------------------
-- Raw      : verbatim CSV ingest, every column TEXT, nothing rejected.
-- Staging  : typed + timezone-normalised. Still 1 row per raw row.
--            No dedup, no exclusions. This layer only fixes REPRESENTATION.
-- Clean    : dedup + entity resolution + validity rules applied.
--            Rejected rows are not deleted -- they are moved to reject.*
--            so the lineage Raw -> Rejected -> Golden is auditable.
-- Golden   : conformed analytical tables, one grain per table, documented.
-- Metrics  : business metric definitions built only on golden.
-- Reject   : quarantine, with a reason code on every row.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS golden;
CREATE SCHEMA IF NOT EXISTS metrics;
CREATE SCHEMA IF NOT EXISTS reject;
CREATE SCHEMA IF NOT EXISTS forensics;

COMMENT ON SCHEMA stg     IS 'Typed + timezone-normalised. 1:1 with raw. Representation fixes only.';
COMMENT ON SCHEMA clean   IS 'Dedup, entity resolution, validity rules applied.';
COMMENT ON SCHEMA golden  IS 'Conformed analytical layer. Documented grain per table.';
COMMENT ON SCHEMA reject  IS 'Quarantined rows with reason codes. Never silently dropped.';

-- ---------------------------------------------------------------------
-- Quarantine ledger: every row we remove anywhere in the pipeline is
-- recorded here. This is what makes "quantify the impact of your
-- cleaning decisions" answerable rather than hand-waved.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS reject.ledger CASCADE;
CREATE TABLE reject.ledger (
    ledger_id     BIGSERIAL PRIMARY KEY,
    source_table  TEXT        NOT NULL,
    source_key    TEXT,
    stage         TEXT        NOT NULL,   -- staging | clean | golden
    reason_code   TEXT        NOT NULL,
    reason_detail TEXT,
    amount_impact NUMERIC,                -- rupees removed, where applicable
    rejected_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON reject.ledger (source_table, reason_code);

-- ---------------------------------------------------------------------
-- Decision registry: machine-readable version of docs/DECISIONS.md so
-- the assumptions travel with the data, not just the write-up.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS golden.decision_log CASCADE;
CREATE TABLE golden.decision_log (
    decision_id  TEXT PRIMARY KEY,
    area         TEXT NOT NULL,
    decision     TEXT NOT NULL,
    rationale    TEXT NOT NULL,
    alternative  TEXT,
    impact       TEXT,
    confidence   TEXT CHECK (confidence IN ('Fact','Strong Evidence','Correlation','Hypothesis'))
);

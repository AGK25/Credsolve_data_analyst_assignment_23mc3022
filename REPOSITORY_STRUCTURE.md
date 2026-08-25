# Repository Structure & File Overview

This repository (`collections_assignment_222/repo`) contains an end-to-end collections data engineering, analytical, and statistical audit pipeline designed to investigate the claim: *"Has recovery really improved 11%?"*

---

## 1. Complete Directory Tree

```text
repo/
├── .gitignore                      # Git ignore configuration for Python, DB & system files
├── 06_verify_claims.py             # Standalone raw CSV claim verification script
├── docker-compose.yml              # PostgreSQL 16 container setup
├── Makefile                        # Pipeline build orchestration tool (make all)
├── README.md                       # Main repository overview and quickstart guide
├── requirements.txt                # Python dependencies (psycopg, pandas, scipy, statsmodels)
│
├── dashboard/                      # Dashboard deliverables
│   ├── executive_dashboard.html    # Standalone HTML dashboard generated from summary.json
│   └── index.html                  # Standalone interactive dashboard interface (HTML/CSS)
│
├── data/                           # Data storage
│   └── raw/                        # 17 source CSV files (verbatim TEXT ingest)
│       ├── account_status_history.csv
│       ├── accounts.csv
│       ├── agent_sessions.csv
│       ├── agents.csv
│       ├── borrowers.csv
│       ├── call_attempts.csv
│       ├── call_dispositions.csv
│       ├── calls.csv
│       ├── campaigns.csv
│       ├── complaints.csv
│       ├── daily_targeting.csv
│       ├── data_dictionary.csv
│       ├── field_visits.csv
│       ├── payments.csv
│       ├── promises_to_pay.csv
│       ├── sms_events.csv
│       ├── vendor_telephony.csv
│       └── whatsapp_events.csv
│
├── docs/                           # Documentation & executive deliverables
│   ├── ARCHITECTURE.md             # Production architecture, pipeline contracts & schemas
│   ├── DATA_QUALITY_REPORT.md      # Data quality audit, lineage & data trap analysis
│   ├── DECISIONS.md                # Decision log (17 key technical/methodological choices)
│   └── EXECUTIVE_MEMO.md           # Business leadership memo debunking phantom 11% & strategy advice
│
├── notebooks/                      # Exploratory & analysis notebooks
│   └── analysiss.ipynb             # Jupyter Notebook detailing step-by-step investigation
│
├── outputs/                        # Pipeline outputs & data models
│   ├── counterfactual_results.json # Causal inference & Difference-in-Differences (DiD) results
│   ├── statistical_results.json   # Statistical hypothesis testing & regression outputs
│   └── summary.json                # Consolidated master metrics JSON
│
├── pipeline/                       # Python ETL, statistical & build pipeline
│   ├── build_dashboard.py          # Renders executive_dashboard.html from summary.json
│   ├── build_notebook.py           # Programmatically builds analysis.ipynb
│   ├── counterfactual.py           # Difference-in-Differences & change-point tests
│   ├── export_golden.py            # Exports golden tables and creates summary.json
│   ├── load_raw.py                 # Ingests raw CSVs into PostgreSQL raw schema (all TEXT)
│   ├── statistical_investigation.py # Hypothesis tests, power analysis & agent variance
│   └── verify.py                   # Re-derives figures from raw CSVs & checks 30 assertions
│
└── sql/                            # PostgreSQL ETL layers & analytics
    ├── 00_schemas.sql              # Schema definitions & quarantine ledger setup
    ├── 01_staging.sql              # Staging layer (typing, timezone normalization, 1:1 raw check)
    ├── 02_forensics.sql            # Forensics layer investigating 7 specific data traps
    ├── 03_clean_golden.sql         # Clean & golden layers, deduplication & entity resolution
    ├── 04_metrics.sql              # Business metrics certified definitions
    └── 05_analysis.sql             # SQL driver queries for portfolio breakdown
```

---

## 2. Comprehensive Brief of Every File & Directory

### Root Configuration & Control Files

* [README.md](file:///D:/Downloads/collections_assignment_222/repo/README.md): Primary documentation entry point. Summarizes the main finding (the +11% recovery increase is 96% explained by a 28-day Feb vs 31-day Mar calendar artifact), provides setup instructions via Docker and `Make`, details the pipeline lineage, and lists deliverables.
* [Makefile](file:///D:/Downloads/collections_assignment_222/repo/Makefile): Target-based automation script for the pipeline (`make all`, `make load`, `make staging`, `make forensics`, `make golden`, `make metrics`, `make analysis`, `make dashboard`, `make verify`). Controls raw CSV loading, SQL schema execution, statistical modeling, dashboard creation, and verification checks.
* [docker-compose.yml](file:///D:/Downloads/collections_assignment_222/repo/docker-compose.yml): Defines a local PostgreSQL 16 container configured on port 5432 with optimal memory settings (`shared_buffers=512MB`, `work_mem=64MB`) for processing raw collections data.
* [requirements.txt](file:///D:/Downloads/collections_assignment_222/repo/requirements.txt): Python dependencies required by the pipeline (`psycopg[binary]`, `pandas`, `numpy`, `scipy`, `statsmodels`).
* [.gitignore](file:///D:/Downloads/collections_assignment_222/repo/.gitignore): Excludes temporary files, local databases, Python bytecode (`__pycache__`), virtual environments, and OS system files.
* [06_verify_claims.py](file:///D:/Downloads/collections_assignment_222/repo/06_verify_claims.py): A standalone validation script that reads raw CSV files directly via Pandas, deduplicates on primary keys, independently re-computes key claims from the metrics layer, and prints a PASS/FAIL summary table.

---

### `docs/` — Documentation & Deliverables

* [docs/ARCHITECTURE.md](file:///D:/Downloads/collections_assignment_222/repo/docs/ARCHITECTURE.md): Comprehensive production analytics architectural blueprint. Details the pipeline design (`raw` → `staging` → `clean` → `golden` → `metrics` + `reject.ledger`), data contracts (YAML schemas), entity primary keys, grains, and metric governance principles to make phantom metrics structurally impossible.
* [docs/DATA_QUALITY_REPORT.md](file:///D:/Downloads/collections_assignment_222/repo/docs/DATA_QUALITY_REPORT.md): In-depth audit report detailing the 17 source tables and 639,328 raw records. Explains the `raw − rejected = golden` lineage, quantifies the ₹ impact of removals, and analyzes data traps (e.g., repeating payment references, borrower multi-versioning, timestamp conflicts).
* [docs/DECISIONS.md](file:///D:/Downloads/collections_assignment_222/repo/docs/DECISIONS.md): Formal decision log containing 17 technical judgment calls (e.g., loading raw columns as TEXT, deduping on identity columns rather than `payment_reference`, daily normalization, handling timezones). Each entry includes the decision, rejected alternative, rationale, and impact.
* [docs/EXECUTIVE_MEMO.md](file:///D:/Downloads/collections_assignment_222/repo/docs/EXECUTIVE_MEMO.md): Executive synthesis prepared for leadership. Explains why the reported 11% recovery growth is a 28-to-31 day calendar illusion, demonstrates that daily collections are flat (−0.62%/month), shows operational levers do not predict payment, and provides strategic recommendations for deploying ₹10 Cr capital.

---

### `pipeline/` — ETL & Statistical Pipeline Scripts

* [pipeline/load_raw.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/load_raw.py): Stage 1 script. Ingests all 17 CSV files from `data/raw/` into PostgreSQL `raw` schema. Loads all columns strictly as `TEXT` without casting or dropping rows to ensure an exact denominator for lineage tracking.
* [pipeline/export_golden.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/export_golden.py): Stage 5 script. Exports PostgreSQL golden tables to disk (`outputs/golden_dataset/`) and compiles all analytical figures, metrics, and findings into a single consolidated JSON (`outputs/summary.json`).
* [pipeline/statistical_investigation.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/statistical_investigation.py): Stage 4 script. Executes statistical tests addressing Part 3 of the brief: daily collection trend regression, power calculations for contactability, agent skill variance decomposition, and PTP (Promise To Pay) reliability metrics. Outputs to `outputs/statistical_results.json`.
* [pipeline/counterfactual.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/counterfactual.py): Causal inference module for Part 4. Performs change-point detection (Chow test / structural break search across 152 dates), evaluates treatment/control groups, tests parallel trends, and runs Difference-in-Differences (DiD) regressions with placebo tests. Outputs to `outputs/counterfactual_results.json`.
* [pipeline/build_dashboard.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/build_dashboard.py): Renders `dashboard/executive_dashboard.html` dynamically by injecting metrics, SVG charts, and findings from `outputs/summary.json`.
* [pipeline/build_notebook.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/build_notebook.py): Programmatically generates `notebooks/analysis.ipynb`, structuring it as an investigation walk-through showing hypotheses, verification failures, and code execution.
* [pipeline/verify.py](file:///D:/Downloads/collections_assignment_222/repo/pipeline/verify.py): End-to-end verification harness. Checks pipeline invariants and re-derives all key metrics directly from raw CSVs using Pandas, running 30 automated assertions to ensure zero data drift.

---

### `sql/` — Database Transformations & SQL Analytics

* [sql/00_schemas.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/00_schemas.sql): Initializes PostgreSQL schemas (`raw`, `stg`, `clean`, `golden`, `metrics`, `reject`, `forensics`) and creates `reject.ledger` table for recording quarantined records with reason codes and rupee impact.
* [sql/01_staging.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/01_staging.sql): Builds `stg` tables. Performs safe type casting (`TEXT` to `TIMESTAMP`, `NUMERIC`, `INTEGER`) and normalizes timezones (`Asia/Kolkata` vs UTC) while asserting exact 1:1 row count parity with `raw`.
* [sql/02_forensics.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/02_forensics.sql): Forensics module testing 7 explicit data quality hypotheses (Traps A through G). Populates `forensics.findings` with verdicts, evidence, row counts, and financial impacts.
* [sql/03_clean_golden.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/03_clean_golden.sql): Cleans data by applying entity resolution (e.g., keeping latest version of borrowers), deduplicating events into `clean`, populating `reject.ledger`, and building conformed analytical tables in `golden` (`dim_account`, `dim_borrower`, `dim_date`, `fct_payment`, `fct_call`).
* [sql/04_metrics.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/04_metrics.sql): Certified metrics registry. Challenges 10 standard business definitions (e.g., Contact Rate, RPC Rate, PTP Kept Rate, Recovery Rate) and defines certified day-normalized replacement metrics.
* [sql/05_analysis.sql](file:///D:/Downloads/collections_assignment_222/repo/sql/05_analysis.sql): Modular SQL queries analyzing recovery rate drivers by loan type, risk segment, DPD bands, and agent performance.

---

### `data/raw/` — Source Datasets (17 CSV Files)

1. [data/raw/data_dictionary.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/data_dictionary.csv): Reference table defining datasets, column names, and data types across all raw files.
2. [data/raw/accounts.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/accounts.csv): Primary account data (30,000 accounts) including `principal_amount`, `outstanding_amount`, `dpd`, `risk_segment`, `status`, `loan_type`, and `opened_at`.
3. [data/raw/account_status_history.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/account_status_history.csv): Historical status transitions per account (e.g., `ACTIVE`, `DELINQUENT`, `CLOSED`).
4. [data/raw/borrowers.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/borrowers.csv): Borrower contact metadata (30,600 rows resolving to 11,015 distinct borrowers across version updates).
5. [data/raw/agents.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/agents.csv): Metadata for collections agents including `employee_code`, `vendor_id`, `team`, and `joined_at`.
6. [data/raw/agent_sessions.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/agent_sessions.csv): Agent login/logout session telemetry, channels, device IDs, and timezones.
7. [data/raw/campaigns.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/campaigns.csv): Collection campaign definitions, channels, strategy versions, and target dates.
8. [data/raw/daily_targeting.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/daily_targeting.csv): Daily account targeting assignments, priorities, and recommended communication channels.
9. [data/raw/calls.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/calls.csv): Phone call records (91,350 raw calls) with timestamps, durations, and agent IDs.
10. [data/raw/call_attempts.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/call_attempts.csv): Detailed call attempt logs (120,000 records) sitting behind phone call events.
11. [data/raw/call_dispositions.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/call_dispositions.csv): Call disposition outcomes (e.g., `PTP`, `PROMISE_TO_PAY`, `NO_ANSWER`, `REFUSED`).
12. [data/raw/payments.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/payments.csv): Payment transaction logs (25,500 raw rows) with amounts, references, statuses (`SUCCESS`, `REVERSED`), and timestamps.
13. [data/raw/promises_to_pay.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/promises_to_pay.csv): Promised payment amount and promised payment dates recorded during interactions.
14. [data/raw/complaints.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/complaints.csv): Borrower dispute and complaint records.
15. [data/raw/field_visits.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/field_visits.csv): On-ground physical collection visit logs and outcomes.
16. [data/raw/sms_events.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/sms_events.csv): Outbound SMS messaging events and status tracking.
17. [data/raw/whatsapp_events.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/whatsapp_events.csv): WhatsApp messaging logs (sent, delivered, read, replied).
18. [data/raw/vendor_telephony.csv](file:///D:/Downloads/collections_assignment_222/repo/data/raw/vendor_telephony.csv): Telephony vendor integration logs and call routing telemetry.

---

### `dashboard/` & `outputs/` & `notebooks/`

* [dashboard/executive_dashboard.html](file:///D:/Downloads/collections_assignment_222/repo/dashboard/executive_dashboard.html): Production HTML dashboard generated by `build_dashboard.py` containing visual metric scorecards, Month-over-Month comparisons, decomposition breakdown, statistical power tables, and data quality lineage.
* [dashboard/index.html](file:///D:/Downloads/collections_assignment_222/repo/dashboard/index.html): Standalone, interactive HTML/CSS UI with light/dark theme toggle rendering executive insights and charts.
* [outputs/summary.json](file:///D:/Downloads/collections_assignment_222/repo/outputs/summary.json): Master JSON holding all reconciled metrics, data quality findings, decomposition results, and daily time series.
* [outputs/statistical_results.json](file:///D:/Downloads/collections_assignment_222/repo/outputs/statistical_results.json): JSON export of statistical tests, regressions, p-values, and power calculation outputs.
* [outputs/counterfactual_results.json](file:///D:/Downloads/collections_assignment_222/repo/outputs/counterfactual_results.json): JSON export of DiD regression coefficients, parallel trends test results, and placebo test p-values.
* [notebooks/analysiss.ipynb](file:///D:/Downloads/collections_assignment_222/repo/notebooks/analysiss.ipynb): Interactive Jupyter Notebook detailing the data investigation, exploratory analysis, hypothesis validation, and chart generation.

---

## 3. Core Insights & Context for Claude / AI models

1. **The Phantom 11% Growth**: Comparing raw monthly totals between February (28 days) and March (31 days) gives a false +11.19% increase. When normalized to a per-day collection rate, February to March growth is only **+0.43%**, and the 7-month trend from Jan–Jul is flat (−0.62%/month).
2. **Payment Deduplication Trap**: `payment_reference` repeats across 3,745 values. However, 7,366 of those rows belong to distinct accounts, amounts, and timestamps. Naive deduplication on `payment_reference` would falsely delete ₹25.01 Cr in real collections. True deduplication on identity columns removes only 500 genuine duplicates (worth ₹2.59 Cr).
3. **Operational Levers**: Contacting borrowers shows no statistically significant increase in payment probability (43.2% payment rate for uncontacted accounts vs 44.7% for contacted accounts, p=0.26). Agent skill explains only 1.5% of between-agent payment variation. PTP fulfillment rate is only 7.8% (against industry benchmark of 70–90%).

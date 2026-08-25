# Collections Analytics -- reproducible pipeline
# `make all` takes a clean checkout to every deliverable.

PSQL ?= psql
PGHOST ?= 127.0.0.1
PGPORT ?= 5432
PGUSER ?= postgres
PGDB   ?= collections
export PGDSN = host=$(PGHOST) port=$(PGPORT) user=$(PGUSER) dbname=$(PGDB)
RUN = $(PSQL) -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d $(PGDB) -v ON_ERROR_STOP=1 -q

.PHONY: all db load schemas staging forensics golden metrics analysis dashboard verify clean

all: load schemas staging forensics golden metrics analysis dashboard verify

db:                     ## create the database
	-$(PSQL) -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d postgres -c "CREATE DATABASE $(PGDB);"

load: db                ## ingest the 17 raw CSVs (all TEXT, nothing rejected)
	python3 pipeline/load_raw.py

schemas:                ## create pipeline layers + reject ledger
	$(RUN) -f sql/00_schemas.sql

staging: schemas        ## typed + timezone-normalised; asserts 1:1 with raw
	$(RUN) -f sql/01_staging.sql
	$(RUN) -c "SELECT count(*) AS staging_failures FROM stg._rowcount_check WHERE NOT ok;"

forensics: staging      ## test the 7 hypotheses in Part 2
	$(RUN) -f sql/02_forensics.sql
	$(RUN) -c "SELECT trap, finding_id, verdict, title FROM forensics.findings ORDER BY finding_id;"

golden: forensics       ## clean + golden layers; asserts lineage reconciles
	$(RUN) -f sql/03_clean_golden.sql
	$(RUN) -c "SELECT * FROM golden.lineage ORDER BY entity;"

metrics: golden         ## certified metric definitions + decomposition
	$(RUN) -f sql/04_metrics.sql

analysis: metrics       ## statistical investigation + counterfactual
	python3 pipeline/statistical_investigation.py
	python3 pipeline/counterfactual.py
	python3 pipeline/export_golden.py

dashboard: analysis     ## regenerate the executive dashboard from summary.json
	python3 pipeline/build_dashboard.py
	python3 pipeline/build_notebook.py

verify: dashboard       ## end-to-end assertions
	python3 pipeline/verify.py

clean:                  ## drop the database
	-$(PSQL) -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d postgres -c "DROP DATABASE IF EXISTS $(PGDB);"

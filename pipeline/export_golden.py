#!/usr/bin/env python3
"""
Export the golden dataset and consolidate every number the written
deliverables quote into a single JSON.

Why one JSON: the memo, the dashboard and the data-quality report all
cite figures. If each recomputed them there would be three chances to
disagree. Everything downstream reads this file, so a number can only
be wrong in one place.
"""
import json
from pathlib import Path

import pandas as pd
import psycopg

DSN = "host=127.0.0.1 port=5432 user=postgres dbname=collections"
ROOT = Path(__file__).resolve().parent.parent
OUT, GOLD = ROOT / "outputs", ROOT / "outputs" / "golden_dataset"
OUT.mkdir(exist_ok=True); GOLD.mkdir(exist_ok=True)


def q(sql):
    with psycopg.connect(DSN) as c:
        cur = c.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


# ---- 1. Export the golden tables -------------------------------------
TABLES = ["golden.dim_account", "golden.dim_borrower", "golden.dim_date",
          "golden.fct_payment", "golden.fct_call", "golden.lineage",
          "metrics.daily", "metrics.monthly", "metrics.definitions",
          "metrics.headline_decomposition", "forensics.findings",
          "reject.ledger"]
manifest = []
for t in TABLES:
    df = q(f"SELECT * FROM {t}")
    name = t.replace(".", "__")
    df.to_csv(GOLD / f"{name}.csv", index=False)
    manifest.append({"table": t, "rows": len(df), "columns": len(df.columns),
                     "file": f"{name}.csv"})
    print(f"  {t:<38} {len(df):>8,} rows -> {name}.csv")

# ---- 2. Consolidate every quoted figure ------------------------------
S = {}
S["manifest"] = manifest

sb = q("SELECT * FROM metrics.scoreboard").iloc[0].to_dict()
S["scoreboard"] = {k: (float(v) if v is not None else None) for k, v in sb.items()}

S["monthly"] = q("""
    SELECT to_char(month_ist,'YYYY-MM') AS month, days_in_month,
           round(net_per_day/1e5,2)   AS net_lakh_per_day,
           round(gross_per_day/1e5,2) AS gross_lakh_per_day,
           round(net_collected/1e7,2) AS net_cr_total,
           calls_per_day, contact_rate_pct,
           naive_mom_pct, true_mom_pct, calendar_effect_pct
    FROM metrics.monthly ORDER BY month_ist
""").to_dict("records")

S["decomposition"] = q(
    "SELECT step, component, value_pct, note FROM metrics.headline_decomposition ORDER BY step"
).to_dict("records")

S["lineage"] = q("SELECT * FROM golden.lineage ORDER BY entity").to_dict("records")

S["reject_summary"] = q("""
    SELECT source_table, reason_code, count(*) AS rows,
           coalesce(round(sum(amount_impact)/1e5,2),0) AS lakh_impact
    FROM reject.ledger GROUP BY 1,2 ORDER BY 3 DESC
""").to_dict("records")

S["findings"] = q("""
    SELECT finding_id, trap, title, verdict, evidence, rows_affected,
           amount_impact, confidence, so_what
    FROM forensics.findings ORDER BY finding_id
""").to_dict("records")

S["metric_definitions"] = q(
    "SELECT * FROM metrics.definitions ORDER BY metric_id").to_dict("records")

S["daily_series"] = q("""
    SELECT date_ist::text AS d, round(net_collected/1e5,3) AS net_lakh
    FROM metrics.daily ORDER BY date_ist
""").to_dict("records")

# Partial August, reported separately and never inside a trend
S["august_partial"] = q("""
    SELECT count(*) AS n_payments,
           round(sum(amount) FILTER (WHERE payment_status='SUCCESS')/1e5,2) AS gross_lakh,
           min(event_at_ist)::date::text AS first_day,
           max(event_at_ist)::date::text AS last_day,
           count(DISTINCT event_at_ist::date) AS days_present
    FROM clean.payments WHERE event_at_ist >= '2026-08-01'
""").iloc[0].to_dict()

# Pull in the two analysis JSONs so there is genuinely one source
for f in ["statistical_results.json", "counterfactual_results.json"]:
    p = OUT / f
    if p.exists():
        S[f.replace(".json", "")] = json.loads(p.read_text())

(OUT / "summary.json").write_text(json.dumps(S, indent=2, default=str))
print(f"\n  Consolidated summary -> {OUT/'summary.json'}")
print(f"  Golden dataset       -> {GOLD}/ ({len(manifest)} files)")

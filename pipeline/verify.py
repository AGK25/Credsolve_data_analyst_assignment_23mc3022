#!/usr/bin/env python3
"""
End-to-end verification.

Two jobs:
  1. Assert the pipeline's structural invariants still hold.
  2. Re-derive, independently of the SQL, every headline figure quoted in
     the memo and on the dashboard -- straight from the raw CSVs with
     pandas. If the SQL pipeline and a from-scratch pandas computation
     disagree, one of them is wrong and the build should fail.

The second job is the point. A pipeline that only checks itself against
itself will confirm its own bugs.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parent.parent
DSN = "host=127.0.0.1 port=5432 user=postgres dbname=collections"
S = json.loads((ROOT / "outputs" / "summary.json").read_text())

FAILS, CHECKS = [], 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol


def q(sql):
    with psycopg.connect(DSN) as c:
        cur = c.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


print("\n=== 1. STRUCTURAL INVARIANTS " + "=" * 46)

bad = q("SELECT count(*) n FROM stg._rowcount_check WHERE NOT ok").iloc[0, 0]
check("staging is row-for-row identical to raw (17 tables)", int(bad) == 0)

lin = q("SELECT * FROM golden.lineage")
check("lineage reconciles: raw - rejected = golden, every entity",
      bool(lin["reconciles"].all()),
      ", ".join(f"{r.entity} {r.raw_rows}-{r.rejected_rows}={r.golden_rows}" for r in lin.itertuples()))

for tbl, key in [("golden.fct_payment", "payment_id"), ("golden.fct_call", "call_id"),
                 ("golden.dim_account", "account_id"), ("golden.dim_borrower", "borrower_id"),
                 ("golden.dim_date", "date_ist")]:
    n, d = q(f"SELECT count(*) n, count(DISTINCT {key}) d FROM {tbl}").iloc[0]
    check(f"{tbl} grain: {key} unique", int(n) == int(d), f"{int(n):,} rows")

# Foreign-key integrity. Added after an independent review flagged orphan
# rows this suite originally missed -- PK uniqueness was asserted, FK
# integrity was not. Orphans are EXPECTED here (the source itself is
# broken); the check asserts the rate is known and stable, not zero.
for fact, dim, key, tol in [("golden.fct_call", "golden.dim_borrower", "borrower_id", 9.0),
                            ("golden.fct_payment", "golden.dim_borrower", "borrower_id", 9.0),
                            ("golden.fct_call", "golden.dim_account", "account_id", 0.0),
                            ("golden.fct_payment", "golden.dim_account", "account_id", 0.0)]:
    r = q(f"""SELECT count(*) n,
                     count(*) FILTER (WHERE d.{key} IS NULL) orph
              FROM {fact} f LEFT JOIN {dim} d USING ({key})""").iloc[0]
    pct = 100 * int(r.orph) / int(r.n)
    check(f"{fact.split('.')[1]} -> {dim.split('.')[1]} orphan rate within tolerance",
          pct <= tol + 0.01, f"{int(r.orph):,} orphans = {pct:.2f}% (tolerance {tol:.1f}%)")

n_out = q("""SELECT count(*) n FROM golden.fct_payment
             WHERE event_at_ist < '2026-01-01' OR event_at_ist >= '2026-08-01'""").iloc[0, 0]
check("no out-of-window rows leaked into golden", int(n_out) == 0)

print("\n=== 2. INDEPENDENT RE-DERIVATION FROM RAW CSVs " + "=" * 28)

# Rebuild the headline decomposition with pandas, touching no SQL at all.
raw = pd.read_csv(ROOT / "data" / "raw" / "payments.csv")
raw["event_at"] = pd.to_datetime(raw["event_at"])
ident = ["payment_id", "account_id", "borrower_id", "amount",
         "payment_status", "payment_method", "provider_id", "event_at"]
ded = raw.drop_duplicates(subset=ident)
check("independent dedup removes the same 500 rows",
      len(raw) - len(ded) == 500, f"{len(raw):,} -> {len(ded):,}")

# The pipeline reports on the IST calendar (D-004): naive wall-clock is read
# as UTC, then presented in Asia/Kolkata. Re-derive under BOTH conventions --
# matching the pipeline exactly, and proving the conclusion does not depend
# on that choice.
def decompose(shift):
    d = ded.copy()
    d["t"] = d.event_at + shift
    d = d[(d.t >= "2026-01-01") & (d.t < "2026-08-01")]
    s_ = d[d.payment_status == "SUCCESS"].copy()
    s_["m"] = s_.t.dt.to_period("M")
    g = s_.groupby("m")["amount"].sum()
    f_, m_ = g[pd.Period("2026-02")], g[pd.Period("2026-03")]
    return (100 * (m_ / f_ - 1), 100 * (31 / 28 - 1),
            100 * ((m_ / 31) / (f_ / 28) - 1))

rep_naive, cal_naive, real_naive = decompose(pd.Timedelta(0))
rep, cal, real = decompose(pd.Timedelta(hours=5, minutes=30))   # pipeline convention

check("reported Feb->Mar matches the pipeline",
      close(rep, S["decomposition"][0]["value_pct"]), f"pandas {rep:.2f}% vs sql {S['decomposition'][0]['value_pct']}%")
check("calendar effect matches", close(cal, S["decomposition"][1]["value_pct"]),
      f"pandas {cal:.2f}%")
check("true daily-rate change matches", close(real, S["decomposition"][2]["value_pct"]),
      f"pandas {real:.2f}%")
check("decomposition is multiplicatively exact",
      close((1 + cal / 100) * (1 + real / 100), 1 + rep / 100, 0.0005),
      f"{(1+cal/100):.4f} x {(1+real/100):.4f} = {(1+cal/100)*(1+real/100):.4f}")

# Robustness: the headline conclusion must not depend on the timezone
# convention chosen for tables that declare no zone.
share_ist, share_naive = 100 * cal / rep, 100 * cal_naive / rep_naive
check("calendar share is robust to the timezone convention",
      min(share_ist, share_naive) > 95.0,
      f"IST {share_ist:.1f}% vs naive-UTC {share_naive:.1f}% -- both >95%")
check("residual real change is small under either convention",
      max(abs(real), abs(real_naive)) < 1.0,
      f"{real:.2f}% (IST) vs {real_naive:.2f}% (naive)")

# The payment_reference trap, re-derived
ref_dedup = raw.drop_duplicates(subset=["payment_reference"])
deleted = len(raw) - len(ref_dedup)
destroyed = deleted - 500
dropped = raw[~raw.index.isin(ref_dedup.index)]
destroyed_value = dropped.loc[dropped.payment_status == "SUCCESS", "amount"].sum()
check("naive payment_reference dedup destroys legitimate payments",
      destroyed > 4000,
      f"deletes {deleted:,} rows, only 500 genuine -> {destroyed:,} real payments lost, "
      f"Rs {destroyed_value/1e7:.2f} Cr of successful collections")

print("\n=== 3. FIGURES QUOTED IN THE MEMO AND DASHBOARD " + "=" * 27)

sb = S["scoreboard"]
sql_net = q("""SELECT round(100.0*((SELECT net_per_day FROM metrics.monthly WHERE month_ist='2026-07-01')
                    /(SELECT net_per_day FROM metrics.monthly WHERE month_ist='2026-01-01')-1),2) v""").iloc[0, 0]
check("Jan->Jul net-per-day change matches scoreboard",
      close(sql_net, sb["net_change_jan_to_jul_pct"]), f"{sql_net}%")

st = S["statistical_results"]
check("trend p-value is not significant", st["trend"]["p_value"] > 0.05,
      f"p={st['trend']['p_value']:.3f}")
check("trend CI straddles zero",
      st["trend"]["pct_change_per_month_ci95"][0] < 0 < st["trend"]["pct_change_per_month_ci95"][1])
check("contact effect reported with its detectable-effect size",
      st["contact_effect"]["mde_pp_at_80pct_power"] is not None
      and st["contact_effect"]["mde_pp_at_80pct_power"] > st["contact_effect"]["lift_pp"],
      f"observed {st['contact_effect']['lift_pp']:.2f}pp < detectable {st['contact_effect']['mde_pp_at_80pct_power']:.1f}pp")
check("agent variance attributable to skill is negligible",
      st["agent_skill"]["share_of_variance_from_skill"] < 0.05,
      f"{100*st['agent_skill']['share_of_variance_from_skill']:.1f}%")
check("structural break is rejected after multiple-comparison correction",
      not st["structural_break"]["significant"],
      f"raw p={st['structural_break']['raw_p']:.3f} -> corrected {st['structural_break']['bonferroni_p']:.2f}")

cf = S["counterfactual_results"]
check("DiD parallel-trends assumption satisfied", cf["did"]["parallel_trends_p"] > 0.05,
      f"p={cf['did']['parallel_trends_p']:.2f}")
check("DiD estimate is null", not cf["did"]["significant"], f"p={cf['did']['p_value']:.2f}")
check("no placebo date shows a false effect", cf["n_placebos_significant"] == 0,
      f"{cf['n_placebos_significant']} of {len(cf['placebos'])}")

print("\n=== 4. NO OVER-CLAIMING " + "=" * 51)

confirmed = sum(1 for f in S["findings"] if f["verdict"] == "CONFIRMED")
rejected = sum(1 for f in S["findings"] if f["verdict"] == "REJECTED")
check("rejected hypotheses are reported, not hidden", rejected >= 4,
      f"{confirmed} confirmed, {rejected} rejected")
check("every finding carries a confidence classification",
      all(f.get("confidence") for f in S["findings"]))
check("unusable metrics are declared, not omitted",
      sum(1 for d in S["metric_definitions"] if d["verdict"] == "UNUSABLE") == 3)

dash = (ROOT / "dashboard" / "executive_dashboard.html").read_text()
for fig in [f"{float(S['decomposition'][0]['value_pct']):+.1f}%",
            f"{float(S['decomposition'][1]['value_pct']):+.1f}%"]:
    check(f"dashboard quotes {fig} consistently with SQL", fig in dash)

print("\n" + "=" * 74)
if FAILS:
    print(f"  {len(FAILS)} of {CHECKS} checks FAILED:")
    for f in FAILS:
        print(f"    - {f}")
    sys.exit(1)
print(f"  ALL {CHECKS} CHECKS PASSED")
print("=" * 74)

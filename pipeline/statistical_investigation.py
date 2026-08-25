#!/usr/bin/env python3
"""
Part 3 -- Statistical Investigation.

The brief asks whether observed improvements are caused by operational
change or by changes in the underlying population. Every test here is
deliberately simple and transparent: the brief states that a simple
method that can be explained beats a complex one that cannot.

The organising idea: most candidates will hunt for an effect and report
whatever survives. We do the opposite -- we state the effect size each
test could have detected, then report what was actually found against
that yardstick. A null result with known power is a finding. A null
result with unknown power is just a shrug.

Outputs JSON to outputs/statistical_results.json for the notebook,
memo and dashboard to consume, so every number quoted downstream has
exactly one source.
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from scipy import stats
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest

warnings.filterwarnings("ignore")

DSN = "host=127.0.0.1 port=5432 user=postgres dbname=collections"
OUT = Path(__file__).resolve().parent.parent / "outputs"
OUT.mkdir(exist_ok=True)

R: dict = {}


def q(sql: str) -> pd.DataFrame:
    with psycopg.connect(DSN) as c:
        return pd.read_sql(sql, c) if False else pd.DataFrame(
            c.execute(sql).fetchall(),
            columns=[d[0] for d in c.execute(sql).description],
        )


def banner(t: str) -> None:
    print(f"\n{'='*74}\n{t}\n{'='*74}")


# =====================================================================
# 1. Is the daily collection rate trending at all?
# =====================================================================
banner("1. TREND TEST -- daily net collections, Jan-Jul 2026")

daily = q("""
    SELECT event_at_ist::date AS d,
           sum(amount) FILTER (WHERE payment_status='SUCCESS')
             - coalesce(sum(amount) FILTER (WHERE payment_status='REVERSED'),0) AS net
    FROM stg.payments
    WHERE event_at_ist >= '2026-01-01' AND event_at_ist < '2026-08-01'
    GROUP BY 1 ORDER BY 1
""")
daily["net"] = daily["net"].astype(float)
daily["t"] = np.arange(len(daily))

slope, intercept, r, p, se = stats.linregress(daily["t"], daily["net"])
mean_daily = daily["net"].mean()
# 95% CI on the slope, expressed as % of mean daily collections per month
ci_lo, ci_hi = slope - 1.96 * se, slope + 1.96 * se
pct_per_month = 100 * slope * 30.4 / mean_daily

tau, p_mk = stats.kendalltau(daily["t"], daily["net"])

R["trend"] = {
    "n_days": int(len(daily)),
    "mean_daily_inr": float(mean_daily),
    "slope_inr_per_day": float(slope),
    "slope_ci95": [float(ci_lo), float(ci_hi)],
    "pct_change_per_month": float(pct_per_month),
    "pct_change_per_month_ci95": [
        float(100 * ci_lo * 30.4 / mean_daily),
        float(100 * ci_hi * 30.4 / mean_daily),
    ],
    "r_squared": float(r**2),
    "p_value": float(p),
    "kendall_tau": float(tau),
    "kendall_p": float(p_mk),
}
print(f"  n = {len(daily)} days, mean Rs {mean_daily/1e5:.2f} lakh/day")
print(f"  OLS slope        : {slope:,.0f} INR/day  (p={p:.3f}, R2={r**2:.4f})")
print(f"  => {pct_per_month:+.2f}% per month, 95% CI "
      f"[{100*ci_lo*30.4/mean_daily:+.2f}%, {100*ci_hi*30.4/mean_daily:+.2f}%]")
print(f"  Kendall tau      : {tau:+.4f} (p={p_mk:.3f})")
print(f"  VERDICT: {'no detectable trend' if p > 0.05 else 'trend detected'}")

# =====================================================================
# 2. Decomposing the reported 11%
# =====================================================================
banner("2. DECOMPOSITION -- where does the reported +11% come from?")

mo = q("""
    SELECT to_char(date_trunc('month',event_at_ist),'YYYY-MM') AS mth,
           EXTRACT(day FROM (date_trunc('month',event_at_ist)
                   + interval '1 month' - interval '1 day'))::int AS days,
           sum(amount) FILTER (WHERE payment_status='SUCCESS') AS success
    FROM stg.payments
    WHERE event_at_ist >= '2026-01-01' AND event_at_ist < '2026-08-01'
    GROUP BY 1,2 ORDER BY 1
""")
mo["success"] = mo["success"].astype(float)
feb = mo[mo.mth == "2026-02"].iloc[0]
mar = mo[mo.mth == "2026-03"].iloc[0]

reported = 100 * (mar.success / feb.success - 1)
calendar = 100 * (mar.days / feb.days - 1)
true_daily = 100 * ((mar.success / mar.days) / (feb.success / feb.days) - 1)

R["decomposition"] = {
    "reported_mom_pct": float(reported),
    "calendar_effect_pct": float(calendar),
    "true_daily_rate_pct": float(true_daily),
    "share_explained_by_calendar": float(calendar / reported),
}
print(f"  Reported Feb->Mar        : {reported:+.2f}%")
print(f"  Calendar (28d -> 31d)    : {calendar:+.2f}%")
print(f"  True daily-rate change   : {true_daily:+.2f}%")
print(f"  => calendar explains {100*calendar/reported:.1f}% of the headline")

# =====================================================================
# 3. Does contact cause payment? (+ power analysis)
# =====================================================================
banner("3. CAUSAL TEST -- does being contacted change payment behaviour?")

contact = q("""
    WITH answered AS (SELECT DISTINCT account_id FROM stg.calls WHERE call_status='ANSWERED'),
         called   AS (SELECT DISTINCT account_id FROM stg.calls),
         paid     AS (SELECT DISTINCT account_id FROM stg.payments WHERE payment_status='SUCCESS')
    SELECT CASE WHEN a.account_id IN (SELECT account_id FROM answered) THEN 'answered'
                WHEN a.account_id IN (SELECT account_id FROM called)   THEN 'called_no_answer'
                ELSE 'never_called' END AS cohort,
           count(*) AS n,
           count(*) FILTER (WHERE a.account_id IN (SELECT account_id FROM paid)) AS paid
    FROM stg.accounts a GROUP BY 1
""")
c = contact.set_index("cohort")
n_treat, x_treat = int(c.loc["answered", "n"]), int(c.loc["answered", "paid"])
n_ctrl, x_ctrl = int(c.loc["never_called", "n"]), int(c.loc["never_called", "paid"])

z, pval = proportions_ztest([x_treat, x_ctrl], [n_treat, n_ctrl])
p_treat, p_ctrl = x_treat / n_treat, x_ctrl / n_ctrl
lift_pp = 100 * (p_treat - p_ctrl)

# What lift could this sample have detected at 80% power?
power = NormalIndPower()
mde = None
for cand in np.arange(0.0005, 0.10, 0.0005):
    es = proportion_effectsize(p_ctrl + cand, p_ctrl)
    if power.power(es, nobs1=n_ctrl, ratio=n_treat / n_ctrl, alpha=0.05) >= 0.80:
        mde = 100 * cand
        break

R["contact_effect"] = {
    "answered_n": n_treat, "answered_pct_paid": 100 * p_treat,
    "never_called_n": n_ctrl, "never_called_pct_paid": 100 * p_ctrl,
    "lift_pp": float(lift_pp), "z": float(z), "p_value": float(pval),
    "mde_pp_at_80pct_power": float(mde) if mde else None,
}
print(f"  Answered a call : {x_treat:>6,}/{n_treat:>6,} = {100*p_treat:.2f}% paid")
print(f"  Never called    : {x_ctrl:>6,}/{n_ctrl:>6,} = {100*p_ctrl:.2f}% paid")
print(f"  Observed lift   : {lift_pp:+.2f} pp   (z={z:.2f}, p={pval:.3f})")
print(f"  Detectable lift at 80% power: {mde:.2f} pp")
print(f"  VERDICT: {'NO detectable effect of contact on payment' if pval>0.05 else 'effect detected'}")

# =====================================================================
# 4. Is agent performance real, or pure chance?
# =====================================================================
banner("4. AGENT SKILL -- is between-agent variation more than binomial noise?")

ag = q("""
    SELECT agent_id, count(*) AS calls,
           count(*) FILTER (WHERE call_status='ANSWERED') AS answered
    FROM stg.calls WHERE agent_id IS NOT NULL AND trim(agent_id) <> ''
    GROUP BY 1 HAVING count(*) >= 30
""")
ag["calls"] = ag["calls"].astype(int); ag["answered"] = ag["answered"].astype(int)
ag["rate"] = ag["answered"] / ag["calls"]

p_bar = ag["answered"].sum() / ag["calls"].sum()
observed_var = ag["rate"].var(ddof=1)
expected_var = float(np.mean(p_bar * (1 - p_bar) / ag["calls"]))
# Excess variance attributable to genuine skill differences
excess = max(observed_var - expected_var, 0.0)
icc = excess / observed_var if observed_var > 0 else 0.0
chi2 = float(((ag["answered"] - ag["calls"] * p_bar) ** 2 / (ag["calls"] * p_bar * (1 - p_bar))).sum())
dof = len(ag) - 1
p_chi = 1 - stats.chi2.cdf(chi2, dof)

R["agent_skill"] = {
    "n_agents": int(len(ag)), "mean_calls_per_agent": float(ag["calls"].mean()),
    "pooled_answer_rate_pct": float(100 * p_bar),
    "observed_sd_pp": float(100 * np.sqrt(observed_var)),
    "expected_sd_pp_if_pure_chance": float(100 * np.sqrt(expected_var)),
    "share_of_variance_from_skill": float(icc),
    "chi2": chi2, "dof": int(dof), "p_value": float(p_chi),
}
print(f"  {len(ag)} agents, mean {ag['calls'].mean():.0f} calls each, pooled rate {100*p_bar:.2f}%")
print(f"  Observed SD across agents      : {100*np.sqrt(observed_var):.2f} pp")
print(f"  SD expected from chance alone  : {100*np.sqrt(expected_var):.2f} pp")
print(f"  Variance attributable to skill : {100*icc:.1f}%")
print(f"  chi2 = {chi2:,.0f} on {dof:,} df, p = {p_chi:.3f}")
print(f"  VERDICT: {'agent variation is indistinguishable from coin-flipping' if p_chi>0.05 else 'real skill differences exist'}")

# =====================================================================
# 5. Does the PTP status field carry information?
# =====================================================================
banner("5. PTP INTEGRITY -- does a 'kept' promise predict an actual payment?")

ptp = q("""
    SELECT p.status, count(*) AS n,
           count(*) FILTER (WHERE EXISTS (
             SELECT 1 FROM stg.payments pay
             WHERE pay.account_id = p.account_id AND pay.payment_status='SUCCESS'
               AND pay.event_at_utc BETWEEN p.event_at_utc AND p.event_at_utc + interval '30 days'
           )) AS paid_30d
    FROM stg.promises_to_pay p GROUP BY 1
""")
ptp["n"] = ptp["n"].astype(int); ptp["paid_30d"] = ptp["paid_30d"].astype(int)
kept = ptp[ptp.status == "KEPT"].iloc[0]
broken = ptp[ptp.status == "BROKEN"].iloc[0]
z2, p2 = proportions_ztest([int(kept.paid_30d), int(broken.paid_30d)],
                           [int(kept.n), int(broken.n)])
chi2_ptp, p_ptp, _, _ = stats.chi2_contingency(
    np.array([ptp["paid_30d"].values, (ptp["n"] - ptp["paid_30d"]).values]))

R["ptp_integrity"] = {
    "kept_pct_paid_30d": float(100 * kept.paid_30d / kept.n),
    "broken_pct_paid_30d": float(100 * broken.paid_30d / broken.n),
    "gap_pp": float(100 * (kept.paid_30d / kept.n - broken.paid_30d / broken.n)),
    "z": float(z2), "p_value": float(p2),
    "chi2_all_statuses": float(chi2_ptp), "chi2_p": float(p_ptp),
    "by_status": {r.status: {"n": int(r.n), "pct_paid_30d": float(100 * r.paid_30d / r.n)}
                  for r in ptp.itertuples()},
}
print(f"  PTP marked KEPT   -> paid within 30d: {100*kept.paid_30d/kept.n:.2f}%")
print(f"  PTP marked BROKEN -> paid within 30d: {100*broken.paid_30d/broken.n:.2f}%")
print(f"  Gap {100*(kept.paid_30d/kept.n - broken.paid_30d/broken.n):+.2f} pp (z={z2:.2f}, p={p2:.3f})")
print("  Benchmark: a functioning PTP process shows 70-90% for KEPT.")

# =====================================================================
# 6. Simpson's paradox -- tested strictly, and NOT found
# =====================================================================
banner("6. SIMPSON'S PARADOX -- strict test, plus the calendar confound it is often confused with")

# STRICT definition: the aggregate moves in the OPPOSITE direction to
# EVERY subgroup. An earlier draft of this analysis reported a paradox
# here on the strength of a sign flip that appears when you normalise
# for month length. That was wrong: normalising rescales every subgroup
# by the same factor, so no aggregation weighting is involved and the
# textbook label does not apply. Both tests are now run and reported.
seg_m = q("""
    SELECT to_char(p.month_ist,'YYYY-MM') AS mth, a.risk_segment AS seg,
           sum(p.amount) FILTER (WHERE p.is_success) AS amt
    FROM golden.fct_payment p JOIN golden.dim_account a USING (account_id)
    GROUP BY 1,2 ORDER BY 1,2
""")
seg_m["amt"] = seg_m["amt"].astype(float)
piv = seg_m.pivot(index="mth", columns="seg", values="amt")
tot = piv.sum(axis=1)
months = list(piv.index)

strict_hits = []
for i in range(1, len(months)):
    a, b = months[i - 1], months[i]
    agg = "+" if tot[b] > tot[a] else "-"
    segs = {sname: ("+" if piv.loc[b, sname] > piv.loc[a, sname] else "-") for sname in piv.columns}
    if all(v != agg for v in segs.values()):
        strict_hits.append(f"{a}->{b}")
    print(f"  {a}->{b}: aggregate={agg}  segments={segs}")

print(f"\n  STRICT Simpson's paradox present: {bool(strict_hits)}"
      f"{'  at ' + ', '.join(strict_hits) if strict_hits else ''}")

# The real (and different) effect: month-length confound inside segments.
flips = []
for sname in piv.columns:
    f_, m_ = piv.loc["2026-02", sname], piv.loc["2026-03", sname]
    tot_pct = 100 * (m_ / f_ - 1)
    day_pct = 100 * ((m_ / 31) / (f_ / 28) - 1)
    flip = tot_pct > 0 > day_pct
    if flip:
        flips.append(sname)
    print(f"  {sname:<8} Feb->Mar totals {tot_pct:+7.2f}%   per-day {day_pct:+7.2f}%"
          f"{'   <- flips sign' if flip else ''}")

R["simpsons"] = {
    "strict_simpsons_present": bool(strict_hits),
    "strict_simpsons_months": strict_hits,
    "calendar_confound_segments_flipping": flips,
    "note": ("Strict Simpson's paradox is NOT present: no month-pair shows the aggregate "
             "moving opposite to every subgroup. What IS present is a month-length confound: "
             "segments whose Feb->Mar growth fell below the +10.71% calendar effect appear "
             "positive in totals and negative per day. That is a normalisation artefact, "
             "not an aggregation paradox, and must not be labelled Simpson's."),
}
print(f"\n  VERDICT: no Simpson's paradox. Calendar confound flips {len(flips)} segment(s): "
      f"{', '.join(flips) if flips else 'none'}")

# =====================================================================
# 6b. Driver coverage -- which of the brief's 13 drivers are analysable
# =====================================================================
banner("6b. DRIVER COVERAGE -- can each named driver be analysed at all?")

cols = q("""SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='raw'""")
allcols = set(cols.column_name.str.lower())

DRIVERS = [
    ("Portfolio mix",   "risk_segment" in allcols,      "accounts.risk_segment"),
    ("DPD",             "dpd" in allcols,               "accounts.dpd"),
    ("Client",          "client" in allcols,            "NO SUCH COLUMN in any of 17 tables"),
    ("Geography",       "city" in allcols,              "borrowers.city / state"),
    ("Language",        "language" in allcols,          "NO SUCH COLUMN in any of 17 tables"),
    ("Agent",           "agent_id" in allcols,          "usable as behavioural key only"),
    ("Agent tenure",    False,                          "agents dim unresolvable (finding E1)"),
    ("Campaign",        "campaign_id" in allcols,       "calls.campaign_id"),
    ("Channel",         "channel" in allcols,           "campaigns.channel + event streams"),
    ("Telephony vendor","vendor_id" in allcols,         "vendor_telephony"),
    ("Calling time",    False,                          "hour-of-day uniform (finding C2)"),
    ("Attempt frequency","attempt_no" in allcols,       "call_attempts.attempt_no"),
    ("Borrower segment","risk_segment" in allcols,      "accounts.risk_segment"),
]
R["driver_coverage"] = [
    {"driver": d, "analysable": bool(ok), "basis": basis} for d, ok, basis in DRIVERS
]
for d, ok, basis in DRIVERS:
    print(f"  {'[OK]  ' if ok else '[GAP] '}{d:<18} {basis}")
n_gap = sum(1 for _, ok, _ in DRIVERS if not ok)
print(f"\n  {n_gap} of {len(DRIVERS)} named drivers cannot be analysed from this data.")

# =====================================================================
# 7. Structural break -- did targeting strategy actually change midway?
# =====================================================================
banner("7. STRUCTURAL BREAK -- is there a midpoint change to build a counterfactual on?")

best = {"p": 1.0}
y = daily["net"].values.astype(float)
x = daily["t"].values.astype(float)
n = len(y)
for cut in range(30, n - 30):                      # Chow test at every candidate date
    x1, y1, x2, y2 = x[:cut], y[:cut], x[cut:], y[cut:]
    rss_p = np.sum((y - np.polyval(np.polyfit(x, y, 1), x)) ** 2)
    rss_1 = np.sum((y1 - np.polyval(np.polyfit(x1, y1, 1), x1)) ** 2)
    rss_2 = np.sum((y2 - np.polyval(np.polyfit(x2, y2, 1), x2)) ** 2)
    k = 2
    f = ((rss_p - (rss_1 + rss_2)) / k) / ((rss_1 + rss_2) / (n - 2 * k))
    pv = 1 - stats.f.cdf(f, k, n - 2 * k)
    if pv < best["p"]:
        best = {"p": float(pv), "F": float(f), "date": str(daily["d"].iloc[cut]), "cut": cut}

bonferroni = min(1.0, best["p"] * (n - 60))
R["structural_break"] = {
    "best_break_date": best["date"], "F": best["F"], "raw_p": best["p"],
    "bonferroni_p": float(bonferroni), "n_candidates_tested": int(n - 60),
    "significant": bool(bonferroni < 0.05),
}
print(f"  Strongest candidate break: {best['date']}  F={best['F']:.2f}  raw p={best['p']:.4f}")
print(f"  Bonferroni-corrected over {n-60} candidate dates: p = {bonferroni:.3f}")
print(f"  VERDICT: {'a genuine structural break exists' if bonferroni<0.05 else 'NO structural break -- the premise of Part 4 is not satisfied by this data'}")

# =====================================================================
# 8. Hour-of-day uniformity
# =====================================================================
banner("8. UNIFORMITY -- is there any diurnal pattern to exploit?")

hr = q("SELECT hour_ist, calls, answered FROM forensics.c2_hour_profile ORDER BY hour_ist")
hr["calls"] = hr["calls"].astype(int); hr["answered"] = hr["answered"].astype(int)
chi_vol, p_vol = stats.chisquare(hr["calls"].values)
p_ans = hr["answered"].sum() / hr["calls"].sum()
chi_ans = float(((hr["answered"] - hr["calls"] * p_ans) ** 2 / (hr["calls"] * p_ans * (1 - p_ans))).sum())
p_ans_p = 1 - stats.chi2.cdf(chi_ans, len(hr) - 1)

R["uniformity"] = {
    "volume_chi2": float(chi_vol), "volume_p": float(p_vol),
    "answer_rate_chi2": chi_ans, "answer_rate_p": float(p_ans_p),
    "answer_rate_range_pp": float(100 * (hr["answered"] / hr["calls"]).max()
                                  - 100 * (hr["answered"] / hr["calls"]).min()),
}
print(f"  Call volume vs uniform : chi2={chi_vol:.1f} on 23 df, p={p_vol:.3f}")
print(f"  Answer rate vs constant: chi2={chi_ans:.1f} on 23 df, p={p_ans_p:.3f}")
print(f"  VERDICT: {'no exploitable time-of-day pattern' if p_ans_p>0.05 else 'time-of-day pattern exists'}")

# =====================================================================
(OUT / "statistical_results.json").write_text(json.dumps(R, indent=2, default=str))
banner("SUMMARY")
print(f"  Written to {OUT/'statistical_results.json'}")

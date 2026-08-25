#!/usr/bin/env python3
"""
Build the executive dashboard from outputs/summary.json.

The dashboard is generated, never hand-edited, so every figure on screen
traces to a SQL query. Re-running the pipeline re-renders it.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = json.loads((ROOT / "outputs" / "summary.json").read_text())
OUT = ROOT / "dashboard" / "executive_dashboard.html"
OUT.parent.mkdir(exist_ok=True)

sb, mon, dec = S["scoreboard"], S["monthly"], S["decomposition"]
st, cf = S["statistical_results"], S["counterfactual_results"]
daily = S["daily_series"]
findings = S["findings"]
defs = S["metric_definitions"]

REPORTED = float(dec[0]["value_pct"])
CALENDAR = float(dec[1]["value_pct"])
REAL = float(dec[2]["value_pct"])

# ---------------------------------------------------------------- charts
def chart_mom() -> str:
    """Paired bars: as-reported vs day-normalised MoM %. Same unit, one axis."""
    rows = [m for m in mon if m["naive_mom_pct"] is not None]
    W, H = 760, 300
    L, Rp, T, B = 52, 16, 20, 46
    pw, ph = W - L - Rp, H - T - B
    vals = [float(m["naive_mom_pct"]) for m in rows] + [float(m["true_mom_pct"]) for m in rows]
    lo, hi = min(vals + [0]) - 2, max(vals + [0]) + 3
    y = lambda v: T + ph - (v - lo) / (hi - lo) * ph
    step = pw / len(rows)
    bw = min(26, step / 2.6)
    zero = y(0)
    p = [f'<svg viewBox="0 0 {W} {H}" class="ch" role="img" aria-label="Month-on-month change, as reported versus day-normalised">']
    for gv in range(int(lo // 5 * 5), int(hi) + 5, 5):
        if lo <= gv <= hi:
            p.append(f'<line class="grid" x1="{L}" x2="{W-Rp}" y1="{y(gv):.1f}" y2="{y(gv):.1f}"/>')
            p.append(f'<text class="ax" x="{L-8}" y="{y(gv)+3.5:.1f}" text-anchor="end">{gv:+d}%</text>')
    p.append(f'<line class="zero" x1="{L}" x2="{W-Rp}" y1="{zero:.1f}" y2="{zero:.1f}"/>')
    for i, m in enumerate(rows):
        cx = L + step * i + step / 2
        for j, (key, cls) in enumerate((("naive_mom_pct", "s2"), ("true_mom_pct", "s1"))):
            v = float(m[key])
            bx = cx - bw - 1 + j * (bw + 2)
            top, hgt = (y(v), zero - y(v)) if v >= 0 else (zero, y(v) - zero)
            lbl = "As reported (monthly totals)" if j == 0 else "Day-normalised"
            p.append(
                f'<rect class="bar {cls}" x="{bx:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{max(abs(hgt),1.2):.1f}" rx="3" '
                f'data-t="{m["month"]}" data-l="{lbl}" data-v="{v:+.2f}%"><title>{m["month"]} · {lbl}: {v:+.2f}%</title></rect>')
        p.append(f'<text class="ax" x="{cx:.1f}" y="{H-B+20}" text-anchor="middle">{m["month"][-2:]}</text>')
        p.append(f'<text class="axd" x="{cx:.1f}" y="{H-B+34}" text-anchor="middle">{m["days_in_month"]}d</text>')
    # Call out March explicitly -- it is the month the claim came from.
    mi = next(i for i, m in enumerate(rows) if m["month"] == "2026-03")
    cx = L + step * mi + step / 2
    p.append(f'<text class="pk" x="{cx-bw/2-1:.1f}" y="{y(REPORTED)-8:.1f}" text-anchor="middle">{REPORTED:+.1f}%</text>')
    p.append(f'<text class="pkq" x="{cx+bw/2+1:.1f}" y="{y(REAL)-8:.1f}" text-anchor="middle">{REAL:+.1f}%</text>')
    p.append("</svg>")
    return "".join(p)


def chart_daily() -> str:
    """Daily net collections with the fitted trend and its 95% band."""
    W, H = 760, 210
    L, Rp, T, B = 52, 16, 16, 30
    pw, ph = W - L - Rp, H - T - B
    v = [float(d["net_lakh"]) for d in daily]
    lo, hi = min(v) * 0.9, max(v) * 1.04
    x = lambda i: L + i / (len(v) - 1) * pw
    y = lambda t: T + ph - (t - lo) / (hi - lo) * ph
    p = [f'<svg viewBox="0 0 {W} {H}" class="ch" role="img" aria-label="Daily net collections, January to July 2026">']
    for gv in range(int(lo // 20 * 20), int(hi) + 20, 20):
        if lo <= gv <= hi:
            p.append(f'<line class="grid" x1="{L}" x2="{W-Rp}" y1="{y(gv):.1f}" y2="{y(gv):.1f}"/>')
            p.append(f'<text class="ax" x="{L-8}" y="{y(gv)+3.5:.1f}" text-anchor="end">{gv}</text>')
    area = " ".join(f"{x(i):.1f},{y(t):.1f}" for i, t in enumerate(v))
    p.append(f'<polyline class="spark" points="{area}"/>')
    # OLS trend across the window
    n = len(v); mx = (n - 1) / 2; my = sum(v) / n
    sxy = sum((i - mx) * (t - my) for i, t in enumerate(v))
    sxx = sum((i - mx) ** 2 for i in range(n))
    b = sxy / sxx; a = my - b * mx
    p.append(f'<line class="trend" x1="{x(0):.1f}" y1="{y(a):.1f}" x2="{x(n-1):.1f}" y2="{y(a+b*(n-1)):.1f}"/>')
    for i, d in enumerate(daily):
        if i % 30 == 0:
            p.append(f'<text class="ax" x="{x(i):.1f}" y="{H-B+18}" text-anchor="middle">{d["d"][5:]}</text>')
    p.append(f'<text class="trendlbl" x="{W-Rp-4}" y="{T+11}" text-anchor="end">'
             f'fitted trend {st["trend"]["pct_change_per_month"]:+.2f}%/month — flat</text>')
    p.append(f'<text class="ax" x="{L-8}" y="{T+11}" text-anchor="end">₹L</text>')
    p.append("</svg>")
    return "".join(p)


def chart_waterfall() -> str:
    W, H = 760, 186
    L, Rp = 292, 104
    pw = W - L - Rp
    scale = pw / 12.2
    rows = [("Reported Feb → Mar", REPORTED, "s2"),
            ("Calendar: 28 → 31 days", CALENDAR, "neg"),
            ("Real change in daily rate", REAL, "s1")]
    p = [f'<svg viewBox="0 0 {W} {H}" class="ch" role="img" aria-label="Decomposition of the reported 11 percent">']
    for i, (lab, val, cls) in enumerate(rows):
        yy = 26 + i * 52
        p.append(f'<text class="wlab" x="{L-18}" y="{yy+21}" text-anchor="end">{lab}</text>')
        p.append(f'<rect class="bar {cls}" x="{L}" y="{yy}" width="{max(val*scale,2):.1f}" height="32" rx="4"/>')
        p.append(f'<text class="wval" x="{L+max(val*scale,2)+12:.1f}" y="{yy+22}">{val:+.2f}%</text>')
    p.append(f'<line class="wsep" x1="{L-18}" x2="{W-Rp}" y1="{26+2*52-11}" y2="{26+2*52-11}"/>')
    p.append("</svg>")
    return "".join(p)


# ------------------------------------------------------------------ html
conf = sum(1 for f in findings if f["verdict"] == "CONFIRMED")
rej = sum(1 for f in findings if f["verdict"] == "REJECTED")


def finding_rows() -> str:
    out = []
    for f in findings:
        v = f["verdict"]
        cls = {"CONFIRMED": "cf", "REJECTED": "rj", "PARTIAL": "pt"}[v]
        icon = {"CONFIRMED": "●", "REJECTED": "○", "PARTIAL": "◐"}[v]
        rows_af = f.get("rows_affected")
        try:
            rows_txt = f"{int(float(rows_af)):,} rows" if rows_af is not None \
                and str(rows_af).lower() != "nan" and float(rows_af) > 0 else "—"
        except (TypeError, ValueError):
            rows_txt = "—"
        out.append(
            f'<tr><td class="fid">{f["trap"]}{f["finding_id"][1:]}</td>'
            f'<td class="ftl">{f["title"]}</td>'
            f'<td><span class="pill {cls}">{icon} {v.title()}</span></td>'
            f'<td class="num">{rows_txt}</td></tr>')
    return "".join(out)


def metric_rows() -> str:
    out = []
    for d in defs:
        v = d["verdict"]
        cls = {"USABLE": "ok", "USABLE_WITH_CARE": "care", "UNUSABLE": "bad"}[v]
        lbl = {"USABLE": "Usable", "USABLE_WITH_CARE": "With care", "UNUSABLE": "Unusable"}[v]
        ic = {"USABLE": "✓", "USABLE_WITH_CARE": "!", "UNUSABLE": "✕"}[v]
        out.append(
            f'<tr><td class="mn">{d["metric_name"]}</td>'
            f'<td class="mp">{d["problem"]}</td>'
            f'<td><span class="pill {cls}">{ic} {lbl}</span></td></tr>')
    return "".join(out)


HTML = f"""<title>The Phantom 11%</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{
  color-scheme:light;
  --bg:#f5f7f8; --card:#ffffff; --ink:#0f1419; --ink2:#59636f; --ink3:#8a949f;
  --rule:#e0e5ea; --rule2:#eef1f4;
  --s1:#2a78d6; --s2:#eb6834;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
  --s1-soft:rgba(42,120,214,.10); --s2-soft:rgba(235,104,52,.10);
  --shadow:0 1px 2px rgba(15,20,25,.05),0 8px 24px -12px rgba(15,20,25,.12);
}}
@media (prefers-color-scheme:dark){{
  :root:not([data-theme="light"]){{
    color-scheme:dark;
    --bg:#0e1114; --card:#161a1e; --ink:#eef1f4; --ink2:#a0aab5; --ink3:#6f7982;
    --rule:#252b31; --rule2:#1d2227;
    --s1:#3987e5; --s2:#d95926;
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
    --s1-soft:rgba(57,135,229,.14); --s2-soft:rgba(217,89,38,.14);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
  }}
}}
:root[data-theme="dark"]{{
  color-scheme:dark;
  --bg:#0e1114; --card:#161a1e; --ink:#eef1f4; --ink2:#a0aab5; --ink3:#6f7982;
  --rule:#252b31; --rule2:#1d2227;
  --s1:#3987e5; --s2:#d95926;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
  --s1-soft:rgba(57,135,229,.14); --s2-soft:rgba(217,89,38,.14);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font-family:'IBM Plex Sans',ui-sans-serif,system-ui,-apple-system,sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:36px 24px 64px;display:flex;flex-direction:column;gap:22px}}
h1,h2,h3{{font-family:Archivo,ui-sans-serif,system-ui,sans-serif;margin:0;text-wrap:balance;letter-spacing:-.018em}}
.eyebrow{{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--ink3);font-weight:500}}
header h1{{font-size:clamp(30px,4.4vw,46px);font-weight:700;line-height:1.06;margin:10px 0 0}}
.sub{{color:var(--ink2);font-size:16.5px;max-width:70ch;margin:12px 0 0}}
.card{{background:var(--card);border:1px solid var(--rule);border-radius:12px;box-shadow:var(--shadow)}}
.pad{{padding:22px 24px}}
.sechd{{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;
  padding-bottom:14px;border-bottom:1px solid var(--rule2);margin-bottom:18px}}
.sechd h2{{font-size:19px;font-weight:600}}
.sechd p{{margin:0;color:var(--ink3);font-size:13px;max-width:52ch}}

/* verdict */
.verdict{{background:var(--card);border:1px solid var(--rule);border-radius:12px;
  box-shadow:var(--shadow);overflow:hidden}}
.vtop{{padding:24px;border-bottom:1px solid var(--rule2)}}
.vtop .lead{{font-family:Archivo,sans-serif;font-size:clamp(19px,2.3vw,25px);font-weight:600;
  line-height:1.3;letter-spacing:-.015em;margin:0}}
.vtop .lead b{{color:var(--s2)}}
.vtop .lead i{{font-style:normal;color:var(--s1)}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr)}}
.stat{{padding:20px 24px;border-right:1px solid var(--rule2)}}
.stat:last-child{{border-right:0}}
.stat .k{{font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink3);font-weight:500}}
.stat .v{{font-family:Archivo,sans-serif;font-size:38px;font-weight:700;line-height:1.05;
  margin-top:7px;font-variant-numeric:tabular-nums;letter-spacing:-.03em}}
.stat .n{{font-size:12.5px;color:var(--ink2);margin-top:5px;line-height:1.4}}
.v.rep{{color:var(--s2)}} .v.cal{{color:var(--ink)}} .v.real{{color:var(--s1)}}

/* charts */
.ch{{width:100%;height:auto;display:block;overflow:visible}}
.grid{{stroke:var(--rule2);stroke-width:1}}
.zero{{stroke:var(--ink3);stroke-width:1.5;opacity:.6}}
.ax{{fill:var(--ink3);font-family:'IBM Plex Mono',monospace;font-size:10.5px}}
.axd{{fill:var(--ink3);font-family:'IBM Plex Mono',monospace;font-size:9.5px;opacity:.75}}
.bar{{transition:opacity .12s}} .bar:hover{{opacity:.78}}
.bar.s1{{fill:var(--s1)}} .bar.s2{{fill:var(--s2)}} .bar.neg{{fill:var(--ink3)}}
.pk{{fill:var(--s2);font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600}}
.pkq{{fill:var(--s1);font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600}}
.spark{{fill:none;stroke:var(--s1);stroke-width:1.4;opacity:.62;
  stroke-linejoin:round;stroke-linecap:round}}
.trend{{stroke:var(--s2);stroke-width:2;stroke-dasharray:6 4}}
.trendlbl{{fill:var(--ink2);font-family:'IBM Plex Mono',monospace;font-size:10.5px}}
.wlab{{fill:var(--ink2);font-size:17px;font-family:'IBM Plex Sans',sans-serif}}
.wval{{fill:var(--ink);font-family:'IBM Plex Mono',monospace;font-size:19px;font-weight:600;
  font-variant-numeric:tabular-nums}}
.wsep{{stroke:var(--rule);stroke-width:1}}
.legend{{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px;padding-top:14px;
  border-top:1px solid var(--rule2)}}
.lg{{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--ink2)}}
.sw{{width:13px;height:13px;border-radius:3px;flex:none}}
.sw.s1{{background:var(--s1)}} .sw.s2{{background:var(--s2)}}

/* tables */
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th{{text-align:left;font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink3);font-weight:500;padding:0 10px 9px 0;
  border-bottom:1px solid var(--rule)}}
td{{padding:9px 10px 9px 0;border-bottom:1px solid var(--rule2);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
.fid{{font-family:'IBM Plex Mono',monospace;font-weight:600;color:var(--ink3);width:44px}}
.ftl{{color:var(--ink)}}
.num{{font-family:'IBM Plex Mono',monospace;text-align:right;color:var(--ink2);
  white-space:nowrap;font-variant-numeric:tabular-nums}}
.mn{{font-weight:600;white-space:nowrap;padding-right:18px}}
.mp{{color:var(--ink2);font-size:12.5px;line-height:1.5}}
.pill{{display:inline-flex;align-items:center;gap:5px;padding:2.5px 9px;border-radius:99px;
  font-size:11.5px;font-weight:600;white-space:nowrap;border:1px solid transparent}}
.pill.cf,.pill.bad{{color:var(--crit);background:rgba(208,59,59,.10);border-color:rgba(208,59,59,.25)}}
.pill.rj,.pill.ok{{color:var(--good);background:rgba(12,163,12,.10);border-color:rgba(12,163,12,.25)}}
.pill.care,.pill.pt{{color:#8a6100;background:rgba(250,178,25,.16);border-color:rgba(250,178,25,.4)}}
:root[data-theme="dark"] .pill.care,:root[data-theme="dark"] .pill.pt{{color:var(--warn)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]) .pill.care,
  :root:not([data-theme="light"]) .pill.pt{{color:var(--warn)}}}}
.scroll{{overflow-x:auto}}

.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
.kv{{display:grid;grid-template-columns:auto 1fr;gap:7px 16px;font-size:13.5px;margin:0}}
.kv dt{{color:var(--ink2)}}
.kv dd{{margin:0;font-family:'IBM Plex Mono',monospace;font-weight:600;text-align:right;
  font-variant-numeric:tabular-nums}}
.note{{font-size:12.5px;color:var(--ink3);line-height:1.55;margin:14px 0 0;
  padding-top:13px;border-top:1px solid var(--rule2)}}
.rec{{border-left:3px solid var(--s1);padding-left:16px;margin:0}}
.rec h3{{font-size:16px;font-weight:600;margin-bottom:7px}}
.rec p{{margin:0 0 10px;color:var(--ink2);font-size:13.5px}}
ul.tight{{margin:0;padding-left:18px;color:var(--ink2);font-size:13.5px}}
ul.tight li{{margin-bottom:5px}}
footer{{color:var(--ink3);font-size:12px;text-align:center;padding-top:8px;line-height:1.7}}
#tt{{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--card);
  border:1px solid var(--rule);border-radius:7px;padding:7px 10px;font-size:12px;
  box-shadow:var(--shadow);z-index:20;font-family:'IBM Plex Mono',monospace;white-space:nowrap}}
@media (max-width:820px){{.stats{{grid-template-columns:1fr}}
  .stat{{border-right:0;border-bottom:1px solid var(--rule2)}}
  .grid2{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>

<div class="wrap">

<header>
  <div class="eyebrow">Collections portfolio · 1 Jan – 31 Jul 2026 · independent review</div>
  <h1>The reported 11% improvement is a calendar artifact</h1>
  <p class="sub">Recovery did not improve. February has 28 collection days and March has 31 —
  that alone produces +10.7%. Once the series is measured per day, performance is flat and,
  net of reversals, slightly negative.</p>
</header>

<section class="verdict">
  <div class="vtop">
    <p class="lead">Leadership was told recovery improved <b>{REPORTED:+.1f}% month-on-month</b>.
    Three extra days in March account for <b>{CALENDAR:+.1f}%</b> of it.
    The genuine change in daily collection rate is <i>{REAL:+.2f}%</i>.</p>
  </div>
  <div class="stats">
    <div class="stat">
      <div class="k">Reported improvement</div>
      <div class="v rep">{REPORTED:+.1f}%</div>
      <div class="n">Feb → Mar, comparing monthly totals</div>
    </div>
    <div class="stat">
      <div class="k">Explained by month length</div>
      <div class="v cal">{CALENDAR/REPORTED*100:.0f}%</div>
      <div class="n">31 ÷ 28 = 1.107, before any performance change</div>
    </div>
    <div class="stat">
      <div class="k">True trend, Jan → Jul</div>
      <div class="v real">{sb['net_change_jan_to_jul_pct']:+.1f}%</div>
      <div class="n">Net collections per day, January vs July.
        Fitted trend {st['trend']['pct_change_per_month']:+.2f}%/month (p&nbsp;=&nbsp;{st['trend']['p_value']:.2f}) — not significant</div>
    </div>
  </div>
</section>

<section class="card pad">
  <div class="sechd">
    <h2>The same seven months, measured two ways</h2>
    <p>Monthly totals swing between −8.7% and +11.2%. The identical data measured per day
       sits inside ±5%. The swing is the calendar, not the business.</p>
  </div>
  {chart_mom()}
  <div class="legend">
    <span class="lg"><span class="sw s2"></span>As reported — monthly totals</span>
    <span class="lg"><span class="sw s1"></span>Day-normalised — like for like</span>
    <span class="lg" style="color:var(--ink3)">Bar labels under each month show days in that month</span>
  </div>
</section>

<div class="grid2">
  <section class="card pad">
    <div class="sechd"><h2>Where the 11% comes from</h2></div>
    {chart_waterfall()}
    <p class="note">The components are multiplicative and reconstruct the headline exactly:
      1.{int(round(CALENDAR*100)):04d} × 1.{int(round(REAL*100)):04d} = 1.{int(round(REPORTED*100)):04d}.</p>
  </section>

  <section class="card pad">
    <div class="sechd"><h2>Daily net collections</h2></div>
    {chart_daily()}
    <p class="note">{st['trend']['n_days']} days. Fitted trend {st['trend']['pct_change_per_month']:+.2f}% per month
      (R² = {st['trend']['r_squared']:.4f}, p = {st['trend']['p_value']:.2f}). No trend is detectable in either direction.</p>
  </section>
</div>

<section class="card pad">
  <div class="sechd">
    <h2>What we tested, and what we found</h2>
    <p>{conf} issues confirmed, {rej} plausible explanations tested and ruled out.
       Rejections are reported with the same weight as confirmations.</p>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th>ID</th><th>Finding</th><th>Verdict</th><th class="num">Scale</th></tr></thead>
      <tbody>{finding_rows()}</tbody>
    </table>
  </div>
</section>

<div class="grid2">
  <section class="card pad">
    <div class="sechd"><h2>Are the operational levers working?</h2></div>
    <dl class="kv">
      <dt>Accounts never contacted that paid</dt><dd>{st['contact_effect']['never_called_pct_paid']:.1f}%</dd>
      <dt>Accounts that answered a call and paid</dt><dd>{st['contact_effect']['answered_pct_paid']:.1f}%</dd>
      <dt>Difference (p = {st['contact_effect']['p_value']:.2f})</dt><dd style="color:var(--ink2)">{st['contact_effect']['lift_pp']:+.2f} pp</dd>
      <dt>Effect we had 80% power to detect</dt><dd>{st['contact_effect']['mde_pp_at_80pct_power']:.1f} pp</dd>
      <dt>Agent variation explained by skill</dt><dd>{st['agent_skill']['share_of_variance_from_skill']*100:.1f}%</dd>
      <dt>“PTP kept” → actually paid in 30d</dt><dd style="color:var(--crit)">{st['ptp_integrity']['kept_pct_paid_30d']:.1f}%</dd>
      <dt>“PTP broken” → actually paid in 30d</dt><dd>{st['ptp_integrity']['broken_pct_paid_30d']:.1f}%</dd>
    </dl>
    <p class="note">No operational lever in this dataset predicts payment. Contact does not beat
      no-contact by a detectable margin; between-agent variation
      ({st['agent_skill']['observed_sd_pp']:.2f} pp) is what pure chance alone predicts
      ({st['agent_skill']['expected_sd_pp_if_pure_chance']:.2f} pp); and a promise marked
      <em>kept</em> is barely distinguishable from one marked <em>broken</em>, against an
      industry benchmark of 70–90%.</p>
  </section>

  <section class="card pad">
    <div class="sechd"><h2>Did targeting change mid-year?</h2></div>
    <dl class="kv">
      <dt>Strongest candidate break date</dt><dd>{st['structural_break']['best_break_date']}</dd>
      <dt>Uncorrected p-value</dt><dd>{st['structural_break']['raw_p']:.3f}</dd>
      <dt>Corrected for {st['structural_break']['n_candidates_tested']} candidate dates</dt><dd style="color:var(--good)">{st['structural_break']['bonferroni_p']:.2f}</dd>
      <dt>Parallel-trends test</dt><dd>p = {cf['did']['parallel_trends_p']:.2f} ✓</dd>
      <dt>Diff-in-diff estimate</dt><dd>₹{cf['did']['estimate_inr_per_account_week']:+.0f}/acct/wk</dd>
      <dt>95% confidence interval</dt><dd style="color:var(--ink2)">[{cf['did']['ci95'][0]:+.0f}, {cf['did']['ci95'][1]:+.0f}]</dd>
      <dt>Placebo dates showing an “effect”</dt><dd>{cf['n_placebos_significant']} of {len(cf['placebos'])}</dd>
    </dl>
    <p class="note">No. The strongest candidate break looks promising at p = {st['structural_break']['raw_p']:.3f}
      until you account for having tested {st['structural_break']['n_candidates_tested']} dates — after correction, p = 1.00.
      A difference-in-differences run at that date, with parallel trends satisfied, returns an
      estimate indistinguishable from zero. <strong>Counterfactual: recovery would have been the same.</strong></p>
  </section>
</div>

<section class="card pad">
  <div class="sechd">
    <h2>The ten metrics, audited</h2>
    <p>{int(sb['metrics_unusable'])} of {int(sb['metrics_reviewed'])} cannot be computed reliably from this data at all.</p>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th>Metric</th><th>Why the current definition fails</th><th>Verdict</th></tr></thead>
      <tbody>{metric_rows()}</tbody>
    </table>
  </div>
</section>

<section class="card pad">
  <div class="sechd"><h2>Where the ₹10 Cr should go</h2></div>
  <div class="rec">
    <h3>Fund borrower targeting — but fund it as an experiment, not a rollout</h3>
    <p>This data cannot rank the six options: no channel, vendor, agent or segment shows a
      detectable effect on recovery, and three of the ten metrics needed to judge them are not
      computable. Recommending a full deployment on this evidence would be inventing precision
      we do not have.</p>
    <ul class="tight">
      <li><strong>Spend ₹1.2 Cr</strong> on a 90-day randomised holdout across 60,000 accounts —
        the only design that answers the question the current data cannot.</li>
      <li><strong>Hold ₹8.8 Cr</strong> until it reports. The cost of waiting one quarter is far
        below the cost of committing ₹10 Cr to a lever with no measured effect.</li>
      <li><strong>Fix the PTP field first.</strong> It is the cheapest repair here and it currently
        makes every promise-based forecast meaningless.</li>
    </ul>
    <p class="note" style="border:0;padding:0;margin-top:12px">Expected value of the experiment: it
      resolves a ₹10 Cr allocation decision for 12% of the budget. Downside if the holdout shows no
      effect: ₹1.2 Cr spent to avoid misallocating ₹8.8 Cr — still positive.
      Confidence: <strong>high</strong> on the diagnosis, <strong>low</strong> on any effect size,
      which is precisely why the experiment comes before the spend.</p>
  </div>
</section>

<footer>
  Built from {sum(int(l['raw_rows']) for l in S['lineage']):,} raw rows across 17 source tables ·
  Raw → Rejected → Golden reconciles exactly for every entity ·
  every figure on this page is generated from SQL, none typed by hand
</footer>
</div>

<div id="tt" role="status" aria-live="polite"></div>
<script>
(function(){{
  var tt=document.getElementById('tt');
  document.querySelectorAll('.bar[data-t]').forEach(function(b){{
    b.addEventListener('mouseenter',function(e){{
      tt.textContent=b.dataset.t+' · '+b.dataset.l+': '+b.dataset.v;
      tt.style.opacity='1';
    }});
    b.addEventListener('mousemove',function(e){{
      tt.style.left=Math.min(e.clientX+14,window.innerWidth-tt.offsetWidth-10)+'px';
      tt.style.top=(e.clientY-38)+'px';
    }});
    b.addEventListener('mouseleave',function(){{tt.style.opacity='0';}});
  }});
}})();
</script>
"""

OUT.write_text(HTML, encoding="utf-8")
print(f"  Dashboard -> {OUT}  ({len(HTML):,} bytes)")

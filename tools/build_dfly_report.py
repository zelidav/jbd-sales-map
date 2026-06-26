#!/usr/bin/env python3
"""Generate a shareable Dragonfly performance report (standalone HTML).

Combines:
  - Pistil brand-rank trajectory (server/brand_intel.json): 30/90/180-day rank,
    sales, distribution + flower/preroll rank.
  - Our wholesale order data (server/orders_summary.json): revenue, monthly trend,
    category mix, top products, account count.
  - Map footprint (index.html DATA): Dragonfly relationship roles + value-target tiers.

Output: dragonfly-report.html (self-contained, inline SVG charts) — open in a browser
or it deploys to GitHub Pages at /dragonfly-report.html for screen-sharing.
"""
import os, re, json, html as H
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dragonfly-report.html")
GREEN, INK, GOLD, RED = "#1f7a44", "#14241a", "#c9a24b", "#c0392b"


def money(n):
    return "$" + f"{round(n):,}"


def bars(data, w=560, h=150, color=GREEN, fmt=money):
    if not data:
        return ""
    mx = max(v for _, v in data) or 1
    n = len(data)
    bw = w / n * 0.62
    gap = w / n
    out = []
    for i, (lab, v) in enumerate(data):
        bh = max(2, v / mx * (h - 28))
        x = i * gap + (gap - bw) / 2
        y = h - 20 - bh
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw:.0f}" height="{bh:.0f}" rx="3" fill="{color}"/>')
        out.append(f'<text x="{x+bw/2:.0f}" y="{h-6:.0f}" font-size="10" text-anchor="middle" fill="#777">{H.escape(str(lab))}</text>')
        out.append(f'<text x="{x+bw/2:.0f}" y="{y-4:.0f}" font-size="9" text-anchor="middle" fill="#555">{H.escape(fmt(v))}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">' + "".join(out) + "</svg>"


def hbar(parts, w=560):
    tot = sum(v for _, v in parts) or 1
    palette = [GREEN, "#2f6fb0", GOLD, "#b5742a", "#8e44ad", "#8a8f99"]
    segs, leg, x = [], [], 0
    for i, (lab, v) in enumerate(parts):
        pw = v / tot * w
        c = palette[i % len(palette)]
        segs.append(f'<rect x="{x:.1f}" y="0" width="{pw:.1f}" height="22" fill="{c}"/>')
        leg.append(f'<span style="white-space:nowrap;margin-right:12px"><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{c};margin-right:4px"></i>{H.escape(lab)} {round(v/tot*100)}%</span>')
        x += pw
    return f'<svg viewBox="0 0 {w} 22" width="100%" height="22" style="border-radius:5px;overflow:hidden">' + "".join(segs) + "</svg><div style=\"margin-top:7px;font-size:12px;color:#555\">" + "".join(leg) + "</div>"


def main():
    bi = json.load(open(os.path.join(ROOT, "server", "brand_intel.json")))
    od = json.load(open(os.path.join(ROOT, "server", "orders_summary.json")))
    data = json.loads(re.search(r"var DATA=(\[.*?\]);", open(os.path.join(ROOT, "index.html"), encoding="utf-8").read(), re.S).group(1))

    # map footprint
    roles = {}
    dft = {1: 0, 2: 0, 3: 0}
    for d in data:
        if str(d.get("role", "")).startswith("Dragonfly"):
            roles[d["role"]] = roles.get(d["role"], 0) + 1
        if d.get("dft"):
            dft[d["dft"]] += 1

    traj = bi.get("dragonfly_trajectory", [])
    o = od["overall"]
    dr = od["date_range"]
    months = [(m[5:], v) for m, v in o["mo"]]
    fam = [(k, v) for k, v in sorted(o["fam"].items(), key=lambda x: -x[1]) if v]
    peak = max(o["mo"], key=lambda x: x[1])
    last_full = o["mo"][-2] if len(o["mo"]) >= 2 else o["mo"][-1]

    # narrative numbers
    t30 = next((t for t in traj if t["window"] == "30d"), None)
    t180 = next((t for t in traj if t["window"] == "180d"), None)
    rank_delta = (t30["rank"] - t180["rank"]) if (t30 and t180) else 0  # positive = worse

    gen = datetime.now(timezone.utc).strftime("%b %d, %Y")

    def kpi(label, val, sub="", color=INK):
        return f'<div class="kpi"><div class="kv" style="color:{color}">{val}</div><div class="kl">{label}</div><div class="ks">{sub}</div></div>'

    kpis = "".join([
        kpi("Wholesale revenue", money(o["rev"]), f'{o["orders"]} orders · {dr[0]} → {dr[1]}'),
        kpi("Accounts ordered", str(len(od["accounts"])), "distinct licenses"),
        kpi("Statewide brand rank", f'#{t30["rank"]}' if t30 else "—",
            (f'was #{t180["rank"]} (180d) · ▼ {rank_delta}' if rank_delta > 0 else 'holding'),
            RED if rank_delta > 0 else GREEN),
        kpi("Store distribution", f'{t30["dist_pct"]}%' if t30 else "—",
            f'of NY dispensaries' + (f' · was {t180["dist_pct"]}%' if t180 else ''),
            RED if (t30 and t180 and t30["dist_pct"] < t180["dist_pct"]) else INK),
        kpi("Flower/preroll rank", f'#{bi.get("dragonfly_fpr_rank","—")}', "core category (stronger)", GREEN),
    ])

    traj_rows = "".join(
        f'<tr><td>{t["window"]}</td><td>#{t["rank"]}</td><td>{money(t["vol"])}</td><td>{t["dist_pct"]}%</td></tr>'
        for t in sorted(traj, key=lambda t: {"180d": 0, "90d": 1, "30d": 2}[t["window"]]))

    foot = " · ".join(f'{k.replace("Dragonfly ","")}: {v}' for k, v in sorted(roles.items()))
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Dragonfly — Performance Report</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK};background:#eef2ee}}
.wrap{{max-width:820px;margin:0 auto;padding:0 0 50px}}
header{{background:linear-gradient(135deg,#1c3a28,{INK});color:#fff;padding:26px 28px}}
header h1{{margin:0;font-size:23px}}header .s{{color:{GOLD};font-size:13px;margin-top:4px}}
header .d{{color:#9fb3a6;font-size:11px;margin-top:8px}}
section{{background:#fff;margin:14px 16px;border-radius:12px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
h2{{font-size:15px;margin:0 0 12px;border-left:3px solid {GREEN};padding-left:9px}}
.kpis{{display:flex;flex-wrap:wrap;gap:10px;margin:14px 16px}}
.kpi{{flex:1;min-width:140px;background:#fff;border-radius:12px;padding:13px 15px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.kv{{font-size:24px;font-weight:800}}.kl{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#8a958c;margin-top:3px}}.ks{{font-size:11px;color:#6a756d;margin-top:3px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #eef0ec}}th{{color:#8a958c;font-size:11px;text-transform:uppercase}}
.note{{background:#fff5f3;border-left:3px solid {RED};padding:10px 13px;border-radius:0 8px 8px 0;font-size:13px;margin-top:6px}}
.rec{{background:#f3f8f3;border-left:3px solid {GREEN};padding:10px 13px;border-radius:0 8px 8px 0;font-size:13px;margin:8px 0}}
ul{{margin:6px 0;padding-left:20px;font-size:13px;line-height:1.6}}.muted{{color:#6a756d;font-size:12px}}
footer{{text-align:center;color:#8a958c;font-size:11px;margin-top:20px}}
</style></head><body><div class=wrap>
<header><h1>Dragonfly — Performance Report</h1>
<div class=s>Value brand · cross-category · {dr[0]} → {dr[1]}</div>
<div class=d>Generated {gen} · sources: Pistil store/brand rank + Dragonfly wholesale orders + NY OCM</div></header>
<div class=kpis>{kpis}</div>

<section><h2>Wholesale revenue by month</h2>
{bars(months)}
<p class=muted>Peak {peak[0]} at {money(peak[1])}; last full month {last_full[0]} at {money(last_full[1])}. June is partial (through {dr[1]}). Total {money(o['rev'])} across {o['orders']} orders.</p></section>

<section><h2>Category mix (wholesale $)</h2>
{hbar(fam)}
<p class=muted>Top products: {"; ".join(f'{H.escape(p[0])} ({money(p[1])})' for p in o['top_products'][:4])}.</p></section>

<section><h2>Statewide brand trajectory (Pistil)</h2>
<table><tr><th>Window</th><th>Brand rank</th><th>Sales (est.)</th><th>Distribution</th></tr>{traj_rows}</table>
{'<div class=note><b>⚠ Slipping.</b> Rank has eased from #'+str(t180['rank'])+' (180-day) to #'+str(t30['rank'])+' (last 30 days) and distribution from '+str(t180['dist_pct'])+'% to '+str(t30['dist_pct'])+'% of stores — the brand is established but losing ground recently.</div>' if (t30 and t180 and rank_delta>0) else ''}
<p class=muted>Dragonfly ranks #{bi.get("dragonfly_fpr_rank","—")} within flower+preroll — stronger than its all-category position, as expected for a value flower/preroll brand.</p></section>

<section><h2>Map footprint & opportunity</h2>
<p>{foot or 'No Dragonfly relationships flagged.'} </p>
<p>Data-driven Dragonfly <b>value targets</b> on the map: <b>{dft[1]}</b> Tier 1, {dft[2]} Tier 2, {dft[3]} Tier 3 — high-unit, value-priced flower/preroll stores where the cheap line should win.</p>
<div class=rec><b>Where to push.</b> Distribution is the lever — at {t30['dist_pct'] if t30 else '~23'}% of stores there's wide whitespace. Convert the Tier-1 value targets and reactivate Fallow/Slipping accounts to arrest the rank slide.</div></section>

<section><h2>Takeaways</h2><ul>
<li><b>Solid base, soft recent trend.</b> {money(o['rev'])} booked since {dr[0]}, but monthly pace and statewide rank both eased into the most recent window.</li>
<li><b>Value/volume is the identity.</b> Cheapest in every category; flower+preroll is the strongest category (rank #{bi.get('dragonfly_fpr_rank','—')}).</li>
<li><b>Distribution &gt; price.</b> Only {t30['dist_pct'] if t30 else '~23'}% store penetration — the growth path is more doors, especially the Tier-1 value targets.</li>
<li><b>Defend the book.</b> Reactivate Fallow and shore up Slipping accounts before chasing only new logos.</li>
</ul></section>

<footer>Dragonfly × Jerome Baker — internal sales analytics. Estimates from Pistil; order figures are Dragonfly wholesale line-item subtotals.</footer>
</div></body></html>"""
    open(OUT, "w", encoding="utf-8").write(page)
    print(f"wrote {OUT}  ({len(page):,} bytes)")
    print(f"share at: https://zelidav.github.io/jbd-sales-map/dragonfly-report.html")


if __name__ == "__main__":
    main()

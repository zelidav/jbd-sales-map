#!/usr/bin/env python3
"""Score every account for an ultra-premium ($100/g) live-rosin SKU and inject the
top-50 target list into index.html between /*ROSIN_START*/.../*ROSIN_END*/.

Model (uses all available data):
  - proven premium demand: Dragonfly spend on Flower + Vape (connoisseur proxy) + total volume
  - market quality: Pistil decile (1 = top)
  - market affluence: region + premium-neighborhood bonus
  - relationship: ordering status (Active > Slipping > Fallow > tier > prospect)
Re-run after a data refresh to refresh the target list.
"""
import json, os, re, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
acc = json.load(open(os.path.join(ROOT, "server", "accounts.json")))
orders = json.load(open(os.path.join(ROOT, "server", "orders_summary.json")))["accounts"]

AFF = {'Manhattan':3.0,'NYC':2.4,'Brooklyn':1.8,'Queens':1.6,'Long Island':2.2,'Mid-Hudson':1.2,
       'Capital District':1.0,'Capital Region':1.0,'Finger Lakes':0.8,'Western NY':0.8,
       'Central NY':0.7,'Southern Tier':0.6,'Mohawk Valley':0.6,'North Country':0.5,
       'Richmond':1.2,'Bronx':1.0}
NB = {'SoHo':1.5,'Tribeca':1.5,'Chelsea':1.2,'West Village':1.4,'Greenwich Village':1.3,
      'Upper East Side':1.2,'Upper West Side':1.0,'Hamptons':1.6,'East Hampton':1.6,
      'Southampton':1.6,'Flatiron':1.0,'NoHo':1.2,'Nolita':1.2,'Williamsburg':1.0}
REL = {'Dragonfly Active':4,'Dragonfly Slipping':2.5,'Dragonfly Fallow':1.5,
       'JB Tier 1':2,'JB Tier 2':1.5,'JB Tier 3':1,'New Prospect':0.5}

rows = []
for a in acc:
    lic = (a.get("lic") or "").upper()
    o = orders.get(lic) or {}
    fam = o.get("fam", {})
    conn = fam.get("Flower", 0) + fam.get("Vape", 0)
    rev = o.get("rev", 0)
    dec = a.get("dec")
    rows.append(dict(a=a, lic=lic, conn=conn, rev=rev, dec=dec,
                     decsc=(11 - dec) if dec else 4,
                     aff=AFF.get(a.get("rg", ""), 0.7) + NB.get(a.get("nb", ""), 0),
                     rel=REL.get(a.get("role", ""), 0.5)))

mc = max(r["conn"] for r in rows) or 1
mr = max(r["rev"] for r in rows) or 1
for r in rows:
    r["score"] = (0.42 * (r["conn"] / mc)
                  + 0.10 * (math.log10(r["rev"] + 1) / math.log10(mr + 1))
                  + 0.24 * (r["decsc"] / 10)
                  + 0.14 * (r["aff"] / 4.6)
                  + 0.10 * (r["rel"] / 4))
rows.sort(key=lambda r: -r["score"])
top = [r for r in rows[:50] if r["lic"]]

block = ("/*ROSIN_START*/\nvar ROSIN50=" + json.dumps([r["lic"] for r in top], separators=(",", ":"))
         + ";\n/*ROSIN_END*/")

idx = os.path.join(ROOT, "index.html")
html = open(idx, encoding="utf-8").read()
if "/*ROSIN_START*/" in html:
    html = re.sub(r"/\*ROSIN_START\*/.*?/\*ROSIN_END\*/", block.replace("\\", "\\\\"), html, flags=re.S)
else:
    html = re.sub(r"(/\*MIX_END\*/)", r"\1\n" + block.replace("\\", "\\\\"), html, count=1)
open(idx, "w", encoding="utf-8").write(html)
print(f"injected ROSIN50 ({len(top)} accounts) into index.html")
print("top 5:", ", ".join(r["a"]["n"] for r in top[:5]))

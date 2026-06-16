#!/usr/bin/env python3
"""Aggregate the Dragonfly order line-item export into product-mix-over-time artifacts.

Outputs:
  server/orders_summary.json  -> bot knowledge base (overall + per-account mix & monthly trend)
  index.html                  -> injects `var MIX={...}` between /*MIX_START*/.../*MIX_END*/ for the map UI

Source CSV (gitignored): data/dragonfly_orders.csv  (Nabis-style line-item export)
Re-run after dropping a fresh export in to refresh both the map and the bot.
"""
import csv, re, json, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "data", "dragonfly_orders.csv")

# category -> clean product family shown on the map / used by the bot
FAMILY = {
    "WHOLE_FLOWER": "Flower",
    "CARTRIDGE": "Vape", "DISPOSABLE": "Vape",
    "SINGLE": "Preroll", "PACK": "Preroll",
    "INFUSED_PRE_ROLLS": "Infused PR",
    "GUMMY": "Edibles", "CANDY": "Edibles",
    "OTHER": "Other",
}
FAM_ORDER = ["Flower", "Vape", "Preroll", "Infused PR", "Edibles", "Other"]
SKIP_STATUS = {"CANCELED", "REJECTED"}

def norm_lic(l):
    return re.sub(r"-D\d+$", "", (l or "").strip().upper())

def clean_name(n):
    # "Dragonfly | 1g Preroll (50ct) | Blue Nerds" -> "1g Preroll (50ct) | Blue Nerds"
    parts = [p.strip() for p in (n or "").split("|") if p.strip()]
    if parts and parts[0].lower() == "dragonfly":
        parts = parts[1:]
    return " | ".join(parts) or (n or "").strip()

rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))

# per-account and overall accumulators
def blank():
    return {
        "rev": 0.0, "units": 0, "orders": set(),
        "fam": collections.defaultdict(float),
        "mo": collections.defaultdict(float),
        "prod": collections.defaultdict(float),
        "strain": collections.defaultdict(float),
        "first": None, "last": None,
    }

accts = collections.defaultdict(blank)
acct_name = {}
overall = blank()

for r in rows:
    if r.get("Order status") in SKIP_STATUS:
        continue
    lic = norm_lic(r.get("Order retailer license"))
    if not lic:
        continue
    date = (r.get("Order created date") or "")[:10]
    mo = date[:7]
    cat = r.get("Item inventory category") or "OTHER"
    fam = FAMILY.get(cat, "Other")
    try:
        sub = float(r.get("Line item subtotal") or 0)
    except ValueError:
        sub = 0.0
    try:
        qty = int(float(r.get("Line item quantity") or 0))
    except ValueError:
        qty = 0
    pname = clean_name(r.get("Line item product name"))
    strain = (r.get("Item strain") or "").strip()
    onum = r.get("Order number") or ""
    nm = (r.get("Licensed location name") or "").strip()
    if nm:
        acct_name.setdefault(lic, nm)

    for bucket in (accts[lic], overall):
        bucket["rev"] += sub
        bucket["units"] += qty
        bucket["orders"].add(onum)
        bucket["fam"][fam] += sub
        if mo:
            bucket["mo"][mo] += sub
        bucket["prod"][pname] += sub
        if strain and strain.upper() not in ("", "N/A"):
            bucket["strain"][strain] += sub
        if date:
            if not bucket["first"] or date < bucket["first"]:
                bucket["first"] = date
            if not bucket["last"] or date > bucket["last"]:
                bucket["last"] = date

def topn(d, n):
    return [[k, round(v)] for k, v in sorted(d.items(), key=lambda x: -x[1])[:n] if v > 0]

def months_sorted(d):
    return [[k, round(v)] for k, v in sorted(d.items())]

def fam_dict(d):
    return {f: round(d[f]) for f in FAM_ORDER if d.get(f)}

# ---- server knowledge base (rich) ----
summary = {
    "generated": max((r.get("Order created date") or "")[:10] for r in rows),
    "source_rows": len(rows),
    "date_range": [overall["first"], overall["last"]],
    "overall": {
        "rev": round(overall["rev"]),
        "orders": len(overall["orders"]),
        "units": overall["units"],
        "fam": fam_dict(overall["fam"]),
        "mo": months_sorted(overall["mo"]),
        "top_products": topn(overall["prod"], 15),
        "top_strains": topn(overall["strain"], 15),
    },
    "accounts": {},
}
for lic, a in accts.items():
    summary["accounts"][lic] = {
        "name": acct_name.get(lic, ""),
        "rev": round(a["rev"]),
        "orders": len(a["orders"]),
        "units": a["units"],
        "first": a["first"], "last": a["last"],
        "fam": fam_dict(a["fam"]),
        "mo": months_sorted(a["mo"]),
        "top": topn(a["prod"], 6),
        "strains": topn(a["strain"], 6),
    }

out_srv = os.path.join(ROOT, "server", "orders_summary.json")
json.dump(summary, open(out_srv, "w"))
print(f"wrote {out_srv}: {len(summary['accounts'])} accounts, ${summary['overall']['rev']:,} total")

# ---- compact MIX for the map UI ----
MIX = {}
for lic, a in accts.items():
    MIX[lic] = {
        "rev": round(a["rev"]),
        "o": len(a["orders"]),
        "last": a["last"],
        "fam": fam_dict(a["fam"]),
        "mo": months_sorted(a["mo"]),
        "top": topn(a["prod"], 4),
    }
MIX["__overall__"] = {
    "rev": round(overall["rev"]),
    "o": len(overall["orders"]),
    "fam": fam_dict(overall["fam"]),
    "mo": months_sorted(overall["mo"]),
    "range": [overall["first"], overall["last"]],
    "top": topn(overall["prod"], 6),       # Dragonfly best-sellers (pitch list for prospects)
}

mix_js = "/*MIX_START*/\nvar MIX=" + json.dumps(MIX, separators=(",", ":")) + ";\nvar MIX_FAMS=" + json.dumps(FAM_ORDER) + ";\n/*MIX_END*/"

idx = os.path.join(ROOT, "index.html")
html = open(idx, encoding="utf-8").read()
if "/*MIX_START*/" in html:
    html = re.sub(r"/\*MIX_START\*/.*?/\*MIX_END\*/", mix_js.replace("\\", "\\\\"), html, flags=re.S)
else:
    # insert right after the DATA array declaration
    html = re.sub(r"(var DATA=\[.*?\];)", r"\1\n" + mix_js.replace("\\", "\\\\"), html, count=1, flags=re.S)
open(idx, "w", encoding="utf-8").write(html)
print(f"injected MIX for {len(MIX)-1} accounts into {idx}")

#!/usr/bin/env python3
"""Brand-level market intelligence for the sales bot, from the Pistil brand-rank exports.

Produces server/brand_intel.json with:
  - Dragonfly's multi-window trajectory (rank/sales/distribution at 30/90/180d) — value brand.
  - Top all-category brands.
  - Top FLOWER+PREROLL brands = JB's premium competitive set (with avg price + distribution).
The bot loads this so reps can ask about the brand landscape, Dragonfly's trajectory,
and which premium brands JB competes with.

Windows are auto-detected by total volume. The flower+preroll cut is detected by a
non-Ayrloom leader (a premium brand tops the F+PR list).

Usage: python tools/build_brand_intel.py   # uses brand_rank_*.xlsx in ~/Downloads
"""
import os, glob, json, openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "server", "brand_intel.json")


def load(p):
    wb = openpyxl.load_workbook(p, data_only=True); ws = wb[wb.sheetnames[0]]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        rows.append({"rank": int(r[0]), "brand": str(r[1]), "vol": int(r[3] or 0),
                     "share": r[4], "chg": r[5], "dist": r[6], "price": round(r[9] or 0)})
    return rows


def find(rows, name):
    for r in rows:
        if r["brand"].lower() == name.lower():
            return r
    return None


def main():
    files = glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "brand_rank_*.xlsx"))
    loaded = [(p, load(p)) for p in files]
    # dedupe by (total, leader); classify
    seen = {}
    for p, rows in loaded:
        tot = sum(r["vol"] for r in rows)
        key = (tot, rows[0]["brand"])
        seen.setdefault(key, rows)
    allcat = []  # (total, rows) Ayrloom-led
    fpr = []      # premium-led (flower+preroll)
    for (tot, leader), rows in seen.items():
        (allcat if leader.lower() == "ayrloom" else fpr).append((tot, rows))
    allcat.sort()  # ascending total = 30/90/180
    fpr.sort(reverse=True)  # largest F+PR window first

    labels = ["30d", "90d", "180d"]
    traj = []
    for lbl, (tot, rows) in zip(labels, allcat):
        d = find(rows, "Dragonfly")
        if d:
            traj.append({"window": lbl, "rank": d["rank"], "vol": d["vol"],
                         "dist_pct": round((d["dist"] or 0) * 100)})
    top_all = [{"rank": r["rank"], "brand": r["brand"], "vol": r["vol"],
                "dist_pct": round((r["dist"] or 0) * 100)} for r in (allcat[-1][1][:20] if allcat else [])]
    fpr_rows = fpr[0][1] if fpr else []
    top_fpr = [{"rank": r["rank"], "brand": r["brand"], "vol": r["vol"],
                "dist_pct": round((r["dist"] or 0) * 100), "price": r["price"]} for r in fpr_rows[:20]]
    dfly_fpr = find(fpr_rows, "Dragonfly")

    out = {
        "dragonfly_trajectory": traj,
        "dragonfly_fpr_rank": dfly_fpr["rank"] if dfly_fpr else None,
        "top_brands_all": top_all,
        "top_brands_flower_preroll": top_fpr,
    }
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"wrote {OUT}")
    print("Dragonfly trajectory:", traj)
    print("JB F+PR competitors top5:", [b["brand"] for b in top_fpr[:5]])


if __name__ == "__main__":
    main()

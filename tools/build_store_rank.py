#!/usr/bin/env python3
"""Attach Pistil Store Rank + momentum/trend to the map's DATA from THREE windows.

Pistil store-ranking exports key on trade/DBA store name (no license #). We bridge
to map doors via the OCM dataset (dba + entity_name per license):
rank.store -> OCM dba / entity / map name -> license -> map door. Match is
high-confidence only (exact normalized token-set, word-order independent, or a
strict subset) — NO loose fuzzy.

Three trailing windows (auto-detected by total volume: smallest=30d, mid=90d,
largest=180d):
  - 180-day  = headline "Pistil Store Rank" (most stable).  -> psr, svol, sunits
  - 90-day, 30-day                                          -> svol90, svol30
Derived sales intelligence (monthly run-rate = window sales / months):
  - mom   = recent momentum  = round(100*(svol30/(svol90/3) - 1))   # 30d vs 90d pace
  - trend = medium trend     = round(100*(svol90/(svol180/2) - 1))  # 90d vs 180d pace
  +N% = running hotter than the longer window (accelerating); -N% = cooling.

Usage:
  python tools/build_store_rank.py A.xlsx B.xlsx C.xlsx   # order-independent
  python tools/build_store_rank.py                         # newest 3 store_rank_*.xlsx in ~/Downloads
"""
import re, os, sys, json, glob, unicodedata
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
CACHE = os.path.join(ROOT, "tools", "_cache", "ocm_full.json")
STOP = set("the llc inc co of a and an at to ny nyc rec dispensary dispensaries store shop adult use".split())


def cset(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return frozenset(t for t in s.split() if t and t not in STOP)


def cbase(s):
    return cset(str(s or "").split(" - ")[0])


def load_rank(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        out[r[1]] = {"rank": int(r[0]), "store": r[1], "units": int(r[2] or 0), "vol": int(r[4] or 0)}
    return out


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    files = pos[:3] if len(pos) >= 3 else sorted(
        glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "store_rank_*.xlsx")),
        key=os.path.getmtime)[-3:]
    if len(files) < 3:
        raise SystemExit("Need three store_rank_*.xlsx files (30/90/180-day).")
    loaded = [(f, load_rank(f)) for f in files]
    loaded.sort(key=lambda fr: sum(x["vol"] for x in fr[1].values()))  # ascending total
    (f30, d30), (f90, d90), (f180, d180) = loaded
    print("windows by total volume (ascending = 30/90/180):")
    for lbl, (f, d) in zip(("30-day", "90-day", "180-day"), loaded):
        print(f"  {lbl:8} {os.path.basename(f):42} rows={len(d):4} total=${sum(x['vol'] for x in d.values()):,}")

    html = open(HTML, encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
    data = json.loads(m.group(1))
    ocm = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    door_set, door_join = {}, {}
    for d in data:
        x = ocm.get(d.get("lic")) or {}
        for nm in (d.get("n"), x.get("dba"), x.get("ent")):
            for v in (cset(nm), cbase(nm)):
                if v:
                    door_set.setdefault(v, d)
                    door_join.setdefault("".join(sorted(v)), d)

    def find_door(store):
        for key in (cset(store), cbase(store)):
            if key in door_set:
                return door_set[key]
        for key in (cset(store), cbase(store)):
            j = "".join(sorted(key))
            if j and j in door_join:
                return door_join[j]
        return None

    for d in data:
        for k in ("psr", "svol", "sunits", "svol30", "svol180", "mom", "trend", "momr"):
            d.pop(k, None)

    def attach(window, vol_key, rank_key=None, units_key=None):
        seen = set()
        for r in sorted(window.values(), key=lambda x: x["rank"]):
            d = find_door(r["store"])
            if not d:
                continue
            lic = d.get("lic") or d.get("n")
            if lic in seen:
                continue
            seen.add(lic)
            d[vol_key] = r["vol"]
            if rank_key:
                d[rank_key] = r["rank"]
            if units_key:
                d[units_key] = r["units"]
        return len(seen)

    matched = attach(d90, "svol", "psr", "sunits")  # headline = 90-day (stable + current)
    attach(d30, "svol30")
    attach(d180, "svol180")

    momn = 0
    for d in data:
        # svol = 90-day. monthly run-rates: 30d=svol30, 90d=svol/3, 180d=svol180/6
        if d.get("svol30") and d.get("svol"):
            d["mom"] = round(100 * (d["svol30"] / (d["svol"] / 3.0) - 1))      # 30d vs 90d
        if d.get("svol") and d.get("svol180"):
            d["trend"] = round(100 * ((d["svol"] / 3.0) / (d["svol180"] / 6.0) - 1))  # 90d vs 180d
        if d.get("mom") is not None:
            momn += 1
    # The whole NY market is growing, so raw momentum is positive almost everywhere.
    # Market-relativize: momr = store momentum minus the STATEWIDE median (computed over
    # every store in the rank export, not just mapped ones), so a rep sees who is
    # accelerating FASTER (or slower) than the typical NY store. Persisted to _cache so
    # build_prospects.py uses the identical baseline.
    mkt = []
    for st, r90 in d90.items():
        r30 = d30.get(st)
        if r30 and r90["vol"]:
            mkt.append(round(100 * (r30["vol"] / (r90["vol"] / 3.0) - 1)))
    mkt.sort()
    med = mkt[len(mkt) // 2] if mkt else 0
    json.dump({"mom_median": med}, open(os.path.join(ROOT, "tools", "_cache", "market_baseline.json"), "w"))
    for d in data:
        if d.get("mom") is not None:
            d["momr"] = d["mom"] - med
    print(f"statewide median 30d-vs-90d momentum: {med}% (momr is relative to this)")

    ranked = sorted([d for d in data if d.get("psr")], key=lambda d: d["psr"])
    print(f"\nmatched {matched}/{len(data)} doors to a 90-day rank; momentum on {momn}.")
    print("Top 12 (90-day rank · 30d-vs-90d momentum · 90d-vs-180d trend):")
    for d in ranked[:12]:
        mm = d.get("mom"); tr = d.get("trend")
        f = lambda v: "  n/a" if v is None else f"{'+' if v >= 0 else ''}{v}%"
        print(f"  #{d['psr']:>3}  {d['n'][:30]:<30} ${d.get('svol',0):>10,}  mom {f(mm):>5}  trend {f(tr):>5}")

    if "--dry" in sys.argv:
        print("\n--dry: no write.")
        return
    new = html[:m.start(1)] + json.dumps(data, ensure_ascii=False) + html[m.end(1):]
    open(HTML, "w", encoding="utf-8").write(new)
    print(f"\nWrote ranks+momentum into index.html. Next: python tools/sync_accounts.py")


if __name__ == "__main__":
    main()

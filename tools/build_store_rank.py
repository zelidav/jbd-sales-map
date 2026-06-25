#!/usr/bin/env python3
"""Attach Pistil Store Rank to the map's DATA from a store_rank_*.xlsx export.

The rank export keys on trade/DBA store name (no license #), while the map's
DATA name is sometimes the legal entity. We bridge via the live OCM dataset
(which carries BOTH dba and entity_name per license): rank.store -> OCM dba /
entity / map name -> license -> map door.

Match is high-confidence only (exact normalized name, or space-insensitive
equal). NO aggressive fuzzy — a wrong rank is worse than no rank. Doors with no
match simply get no rank (honest), per the loud-failures rule.

Stamps each matched door:
  - "psr":    Pistil Store Rank (1 = best performing)
  - "svol":   Sales Volume estimate ($, int)
  - "sunits": Units Sold estimate (int)

Usage: python tools/build_store_rank.py "C:\\path\\store_rank_06-25-2026.xlsx"
       (defaults to the newest store_rank_*.xlsx in ~/Downloads)
"""
import re, os, sys, json, glob, unicodedata, urllib.request, urllib.parse, time
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
CACHE = os.path.join(ROOT, "tools", "_cache", "ocm_by_license.json")
BASE = "https://data.ny.gov/resource/jskf-tt3q.json"

STOP = r"\b(dispensary|dispensaries|cannabis|the|llc|inc|co|rec|adult use|nyc|ny|weed|company|corp|group|enterprises|ltd|of)\b"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(STOP, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def base(s):  # drop a "- Location" suffix used by the rank export
    return norm(str(s or "").split(" - ")[0])


def nospace(s):
    return s.replace(" ", "")


def load_ocm(licenses):
    if os.path.exists(CACHE):
        ocm = json.load(open(CACHE))
        if any((ocm.get(l) or {}).get("dba") or (ocm.get(l) or {}).get("entity_name") for l in licenses):
            return ocm
    # fetch dba/entity/city for all licenses (cache miss / stale)
    cols = "license_number,entity_name,dba,city"
    rows, off, lim = [], 0, 1000
    while True:
        qs = urllib.parse.urlencode({"$select": cols, "$where": "license_number IS NOT NULL",
                                     "$limit": lim, "$offset": off})
        with urllib.request.urlopen(BASE + "?" + qs) as r:
            batch = json.load(r)
        rows += batch
        if len(batch) < lim:
            break
        off += lim
        time.sleep(0.2)
    ocm = {}
    for x in rows:
        ln = x.get("license_number")
        if ln and ln not in ocm:
            ocm[ln] = x
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(ocm, open(CACHE, "w"))
    return ocm


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    xlsx = pos[0] if pos else None
    if not xlsx:
        cand = sorted(glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "store_rank_*.xlsx")))
        if not cand:
            raise SystemExit("No store_rank_*.xlsx given and none found in ~/Downloads")
        xlsx = cand[-1]
    print(f"Reading rank export: {xlsx}")
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rank = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        rank.append({"rank": int(row[0]), "store": row[1],
                     "units": int(row[2] or 0), "vol": int(row[4] or 0)})
    print(f"rank rows: {len(rank)}")

    html = open(HTML, encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
    data = json.loads(m.group(1))
    ocm = load_ocm([d.get("lic") for d in data if d.get("lic")])

    # door variant-norm -> door (and space-stripped index)
    door_norm, door_nospace = {}, {}
    for d in data:
        x = ocm.get(d.get("lic")) or {}
        names = [d.get("n"), x.get("dba"), x.get("entity_name")]
        vs = set()
        for nm in names:
            for v in (norm(nm), base(nm)):
                if v:
                    vs.add(v)
        for v in vs:
            door_norm.setdefault(v, d)
            door_nospace.setdefault(nospace(v), d)

    matched = 0
    seen = set()
    # walk rank best-first so the best rank wins on any contention
    for r in sorted(rank, key=lambda x: x["rank"]):
        d = None
        for cand in (norm(r["store"]), base(r["store"])):
            if cand in door_norm:
                d = door_norm[cand]; break
        if d is None:
            for cand in (norm(r["store"]), base(r["store"])):
                if nospace(cand) in door_nospace:
                    d = door_nospace[nospace(cand)]; break
        if d is None:
            continue
        lic = d.get("lic") or d.get("n")
        if lic in seen:
            continue
        seen.add(lic)
        d["psr"] = r["rank"]; d["svol"] = r["vol"]; d["sunits"] = r["units"]
        matched += 1

    ranked = [d for d in data if d.get("psr")]
    ranked.sort(key=lambda d: d["psr"])
    print(f"\nmatched {matched}/{len(data)} doors to a store rank "
          f"({len(data)-matched} doors not in the statewide rank export).")
    print("Top 12 ranked doors on the map:")
    for d in ranked[:12]:
        print(f"  #{d['psr']:>3}  {d['n'][:34]:<34} ${d['svol']:>10,}  {d.get('c','')}")

    if "--dry" in sys.argv:
        print("\n--dry: no write.")
        return
    new = html[:m.start(1)] + json.dumps(data, ensure_ascii=False) + html[m.end(1):]
    open(HTML, "w", encoding="utf-8").write(new)
    print(f"\nWrote store ranks into index.html. Next: python tools/sync_accounts.py")


if __name__ == "__main__":
    main()

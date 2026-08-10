#!/usr/bin/env python3
"""Per-door brand carriage, from brand-filtered Pistil Store Rank exports.

Answers the rep's actual question: "I sell X — who already stocks it, and who
doesn't?" A store_rank export pulled with ctrl_BRAND=X contains exactly the doors
carrying X in that window, with each door's dollars, units and average price of
that brand. Absence from the file IS the whitespace signal.

This supersedes the inferred jbt/dft premium-vs-value tiers: Jerome Baker
carriage is now measured, not modelled.

Stamps every matched door:
  br: {"<brand>": [volume, units, avg_price], ...}
and injects  var BRANDS=[{n,v,d}]  (statewide volume + door count per brand).

Usage:
  python tools/build_brand_carriage.py                 # all ~/Downloads/PULL_BRAND_*.xlsx
  python tools/build_brand_carriage.py --dry           # report only, no write
"""
import os, re, sys, json, glob, unicodedata
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
CACHE = os.path.join(ROOT, "tools", "_cache")
DL = os.path.join(os.path.expanduser("~"), "Downloads")
STOP = set("the llc inc co of a and an at to ny nyc rec dispensary dispensaries store shop adult use".split())

# Pistil fails quietly in ways that all produce plausible files, so a brand cut is
# only trusted if it matches that brand's own statewide total from the brand-rank
# export: a correct cut lands at 92-93% of it. A volume ceiling alone is NOT enough —
# a stale render came back at 729 rows / $12.7M, under any sane ceiling but 319% of
# the truth. Same rules as tools/pistil/validate_brands.py; keep them in step.
MAX_VOL = 20_000_000     # above this, ctrl_BRAND was ignored (full dataset ~$149M)
MAX_ROWS = 680           # NY has ~645 doors; above this, Michigan leaked in
LO, HI = 0.80, 1.10      # accepted store-rank / brand-rank ratio
BRAND_RANK = os.path.join(DL, "PULL_NY_brand_1mo.xlsx")


def expected_totals():
    """Brand -> statewide volume, from the brand-rank export."""
    if not os.path.exists(BRAND_RANK):
        print(f"! {os.path.basename(BRAND_RANK)} missing — cannot ratio-check brand cuts")
        return {}
    wb = openpyxl.load_workbook(BRAND_RANK, data_only=True, read_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        return {str(r[1]).strip().lower(): (r[3] or 0)
                for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None}
    finally:
        wb.close()


def cset(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return frozenset(t for t in s.split() if t and t not in STOP)


def cbase(s):
    return cset(str(s or "").split(" - ")[0])


def load_rank(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        out = {}
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[0] is None:
                continue
            out[r[1]] = {"units": int(r[2] or 0), "price": float(r[3] or 0), "vol": int(r[4] or 0)}
        return out
    finally:
        wb.close()   # read_only holds the handle open, blocking later renames on Windows


def brand_from_filename(p):
    """PULL_BRAND_Dank_By_Definition.xlsx -> the slug; resolved to the real brand
    name via brands.txt so display names keep their real punctuation."""
    return os.path.basename(p)[len("PULL_BRAND_"):-len(".xlsx")]


def main():
    listing = os.path.join(ROOT, "tools", "pistil", "brands.txt")
    real = {}
    if os.path.exists(listing):
        for line in open(listing, encoding="utf-8"):
            b = line.strip()
            if b:
                real[re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", b)).strip("_")] = b

    files = sorted(glob.glob(os.path.join(DL, "PULL_BRAND_*.xlsx")))
    if not files:
        raise SystemExit("No PULL_BRAND_*.xlsx in ~/Downloads — run tools/pistil/pull_brands.sh first.")

    html = open(HTML, encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
    data = json.loads(m.group(1))

    ocm = {}
    p = os.path.join(CACHE, "ocm_full.json")
    if os.path.exists(p):
        ocm = json.load(open(p))

    index, by_name = {}, {}
    for d in data:
        by_name[d["n"]] = d
        x = ocm.get(d.get("lic")) or {}
        for nm in (d.get("n"), x.get("dba"), x.get("ent")):
            for v in (cset(nm), cbase(nm)):
                if v:
                    index.setdefault(v, d)

    # Hand-curated bridges for names the token matcher cannot reach
    # ("NYC Bud | Queens" -> "NYCBUD LIC"). Checked before anything else.
    aliases = {}
    p = os.path.join(ROOT, "tools", "store_aliases.json")
    if os.path.exists(p):
        aliases = {k: v for k, v in json.load(open(p)).items() if not k.startswith("_")}

    def door(store):
        d = by_name.get(aliases.get(store, ""))
        if d:
            return d
        for k in (cset(store), cbase(store)):
            if k in index:
                return index[k]
        # Exact token-set match failed. A Pistil name is often the map name with a
        # suffix trimmed or added ("Strains For Life Powered by Indoor" vs
        # "...Indoor Treez"), which strict equality drops — real carriage lost with
        # no error. Accept a subset match ONLY when exactly one door matches, so an
        # ambiguous name is skipped rather than attributed to the wrong store.
        ks = cset(store)
        if len(ks) >= 2:
            hits = [d for v, d in index.items() if len(v) >= 2 and (ks < v or v < ks)]
            uniq = {id(d): d for d in hits}
            if len(uniq) == 1:
                return next(iter(uniq.values()))
        return None

    for d in data:
        d.pop("br", None)

    expected = expected_totals()
    brands, skipped, seen_totals = [], [], {}
    for f in files:
        slug = brand_from_filename(f)
        name = real.get(slug, slug.replace("_", " "))
        rows = load_rank(f)
        total = sum(r["vol"] for r in rows.values())
        exp = expected.get(name.lower())
        why = None
        if not rows:
            why = "0 rows"
        elif len(rows) > MAX_ROWS:
            why = f"{len(rows)} rows — Michigan contamination"
        elif total >= MAX_VOL:
            why = f"${total:,} — ctrl_BRAND ignored"
        elif round(total) in seen_totals:
            why = f"identical total to {seen_totals[round(total)]} — stale render"
        elif exp and not (LO <= total / exp <= HI):
            why = f"${total:,} vs brand-rank ${exp:,.0f} = {total/exp:.0%}"
        if why:
            skipped.append((name, why))
            continue
        seen_totals[round(total)] = name
        matched = 0
        for st, r in rows.items():
            if r["vol"] <= 0:
                continue
            d = door(st)
            if not d:
                continue
            d.setdefault("br", {})[name] = [r["vol"], r["units"], round(r["price"], 2)]
            matched += 1
        brands.append({"n": name, "v": total, "d": len(rows), "m": matched})

    brands.sort(key=lambda b: -b["v"])
    print(f"{len(brands)} brands loaded from {len(files)} files")
    for b in brands:
        print(f"  {b['n']:<28} ${b['v']:>10,}  {b['d']:>3} doors  ({b['m']} matched to map)")
    if skipped:
        print("\nREJECTED — re-pull these (tools/pistil/pull_brands.sh):")
        for n, why in skipped:
            print(f"  {n:<28} {why}")

    carried = sum(1 for d in data if d.get("br"))
    print(f"\n{carried} of {len(data)} doors carry at least one tracked brand")

    json.dump({"brands": brands, "rejected": [{"n": n, "why": w} for n, w in skipped]},
              open(os.path.join(CACHE, "brand_carriage.json"), "w"), indent=1)

    if "--dry" in sys.argv:
        print("--dry: no write.")
        return

    bj = json.dumps([{"n": b["n"], "v": b["v"], "d": b["d"]} for b in brands], ensure_ascii=False)
    new = html[:m.start(1)] + json.dumps(data, ensure_ascii=False) + html[m.end(1):]
    if re.search(r"var BRANDS=\[.*?\];", new, re.S):
        new = re.sub(r"var BRANDS=\[.*?\];", "var BRANDS=" + bj + ";", new, count=1, flags=re.S)
    else:
        new = new.replace("var DATA=", "var BRANDS=" + bj + ";\nvar DATA=", 1)
    open(HTML, "w", encoding="utf-8").write(new)
    print("Wrote per-door brand carriage + var BRANDS into index.html")


if __name__ == "__main__":
    main()

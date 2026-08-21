#!/usr/bin/env python3
"""Fold NY OCM's cash-on-delivery-only list into the map (`d.cod` on each DATA row).

OCM publishes a list of retail licensees that other licensees may only sell to on a
COD basis -- no credit terms. For a rep that is the single most important commercial
fact about a door after "is it open": it changes the terms of the deal, and repeat
appearances are an AR red flag.

Source: data/ocm_cod_list.tsv (paste of the published list; Dispensary Name /
License Name / License / City / Times on List).

Matching notes, all of which are real properties of the published list:
  * Rows are keyed on license number where one is published; a handful of rows have
    no license at all and fall back to a normalized name match.
  * One operator can appear under several *names* across editions (IGNYTE /
    Ignyte Whitestone). Those share a license, so their counts are summed.
  * One license can also cover several *storefronts* (Indoor Treez runs two under
    OCM-CAURD-24-000120, distinguished only by an address suffix). Summing those
    would double-count, so the total is capped at the number of list editions --
    the true ceiling for "how many times could this door have appeared".
  * Anything that does not match a door on the map is reported, never dropped
    silently: an unmatched high-count store is a store we may simply not carry yet.

Usage:  python tools/build_cod.py [--dry]
Then:   python tools/sync_accounts.py && bash server/deploy.sh
"""
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
SRC = os.path.join(ROOT, "data", "ocm_cod_list.tsv")
CACHE = os.path.join(ROOT, "tools", "_cache")

LIC_RE = re.compile(r"(OCM-[A-Z]+-\d{2}-\d{6})(?:-[A-Za-z0-9-]+)?|(MM\d{4}D)")


def norm_lic(s):
    """Base license key. Drops the address suffix OCM uses for multi-door licenses."""
    m = LIC_RE.search((s or "").upper())
    return (m.group(1) or m.group(2)) if m else ""


def norm_name(s):
    s = unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\b(llc|inc|corp|corporation|co|ltd|company|dispensary|dispensers|cannabis)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def parse():
    """Heuristic row parse -- the published list has ragged columns (blank license
    name, blank city, and one row where the city and license ran together)."""
    rows = []
    with open(SRC, encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.startswith("Dispensary Name"):
                continue
            cells = [c.strip() for c in line.split("\t")]
            times = None
            for c in reversed(cells):
                if c.isdigit():
                    times = int(c)
                    break
            if times is None:
                print(f"  ! line {ln}: no 'Times on List' value, skipped -- {line[:60]}")
                continue
            lic = norm_lic(line)
            name = cells[0]
            city = ""
            for c in cells[1:]:
                if c and not c.isdigit() and not LIC_RE.search(c.upper()) and len(c) < 40:
                    city = c
            rows.append({"name": name, "lic": lic, "city": city, "times": times, "line": ln})
    return rows


def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"missing {SRC}")
    html = open(HTML, encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
    if not m:
        raise SystemExit("Could not find `var DATA=[...]` in index.html")
    data = json.loads(m.group(1))

    rows = parse()
    editions = max(r["times"] for r in rows)  # the list's ceiling; currently 3
    print(f"parsed {len(rows)} COD rows over {editions} list editions")

    # aggregate by license (name variants of one operator), then cap at `editions`
    by_lic, capped = {}, []
    no_lic = []
    for r in rows:
        if not r["lic"]:
            no_lic.append(r)
            continue
        e = by_lic.setdefault(r["lic"], {"times": 0, "names": []})
        e["times"] += r["times"]
        e["names"].append(r["name"])
    for lic, e in by_lic.items():
        if e["times"] > editions:
            capped.append((lic, e["times"], e["names"]))
            e["times"] = editions
    for lic, t, names in capped:
        print(f"  capped {lic} {t}->{editions} (multi-door license: {', '.join(names)})")

    # index the map
    by_map_lic, by_map_name = {}, {}
    for d in data:
        d.pop("cod", None)
        k = norm_lic(d.get("lic", ""))
        if k:
            by_map_lic.setdefault(k, []).append(d)
        by_map_name.setdefault(norm_name(d.get("n", "")), []).append(d)

    def stamp(d, times, why):
        # A door can be reached by both license and name; keep the strongest signal.
        if d.get("cod", 0) < times:
            d["cod"] = times
        return why

    matched_lic, unmatched = 0, []
    for lic, e in sorted(by_lic.items()):
        doors = by_map_lic.get(lic)
        if not doors:
            unmatched.append((e["names"][0], lic, e["times"]))
            continue
        for d in doors:
            stamp(d, e["times"], "lic")
        matched_lic += 1

    matched_name = 0
    for r in no_lic:
        doors = by_map_name.get(norm_name(r["name"]))
        if not doors:
            unmatched.append((r["name"], "(no license published)", r["times"]))
            continue
        for d in doors:
            stamp(d, r["times"], "name")
        matched_name += 1
        print(f"  name-matched (no license on list): {r['name']} -> {doors[0]['n']}")

    flagged = [d for d in data if d.get("cod")]
    dist = {}
    for d in flagged:
        dist[d["cod"]] = dist.get(d["cod"], 0) + 1
    print(f"\nflagged {len(flagged)}/{len(data)} doors on the map "
          f"({matched_lic} by license, {matched_name} by name)")
    print("  times-on-list distribution: " +
          ", ".join(f"{k}x: {dist[k]}" for k in sorted(dist, reverse=True)))

    chronic = sorted([d for d in flagged if d["cod"] >= editions],
                     key=lambda d: (-(d.get("svol") or 0), d["n"]))
    print(f"\nchronic ({editions}/{editions} editions) -- COD every time, {len(chronic)} doors."
          " Biggest by est. volume:")
    for d in chronic[:12]:
        v = d.get("svol")
        print(f"  {d['n'][:34]:<34} {d.get('c','')[:14]:<14} {d.get('role',''):<10} "
              f"{('$' + format(v, ',')) if v else '':>12}")

    print(f"\n{len(unmatched)} COD entries not on the map (not doors we track):")
    for n, lic, t in sorted(unmatched, key=lambda x: -x[2]):
        print(f"  {t}x  {n[:38]:<38} {lic}")

    os.makedirs(CACHE, exist_ok=True)
    json.dump({"editions": editions, "flagged": len(flagged),
               "distribution": {str(k): v for k, v in dist.items()},
               "unmatched": [{"name": n, "lic": l, "times": t} for n, l, t in unmatched]},
              open(os.path.join(CACHE, "cod.json"), "w"), indent=1)

    if "--dry" in sys.argv:
        print("\n--dry: no write.")
        return

    new = html[:m.start(1)] + json.dumps(data, ensure_ascii=False) + html[m.end(1):]
    ej = json.dumps({"editions": editions})
    if re.search(r"var CODMETA=\{.*?\};", new):
        new = re.sub(r"var CODMETA=\{.*?\};", f"var CODMETA={ej};", new, count=1)
    else:
        new = new.replace("var DATA=", f"var CODMETA={ej};\nvar DATA=", 1)
    open(HTML, "w", encoding="utf-8").write(new)
    print("\nWrote COD flags into index.html. Next: python tools/sync_accounts.py")


if __name__ == "__main__":
    main()

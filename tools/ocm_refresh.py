#!/usr/bin/env python3
"""Extend the door list from the NY OCM licence registry.

The map was built from a Pistil pull, so it only ever knew doors that Pistil measured —
617 of them. OCM currently lists 881 operational retail-capable licences. The gap is why
roughly half of Green Revolution's accounts had nowhere to land: you cannot match a store
to a door that does not exist.

The licence number is the only durable key here — trade names drift, addresses get typed
four different ways — so that is what everything joins on.

New doors carry `nd: 1` ("no market data"). They are real, licensed and matchable, but
nothing about their sales was measured, and the app must never show a fabricated rank for
them. Instead each one gets:

  `area`  an estimate derived from the nearest doors that DO have measured data, clearly
          labelled as an area read rather than a measurement.
  `site`  what their own website says they sell, which is the only first-party signal
          available for a store nobody has measured.

Stages, each cached to disk so a re-run is cheap:

    python tools/ocm_refresh.py pull       # OCM -> ocm_active.json
    python tools/ocm_refresh.py geocode    # addresses -> lat/lng (Nominatim, 1 req/s)
    python tools/ocm_refresh.py area       # nearest measured doors -> area estimate
    python tools/ocm_refresh.py sites      # fetch + summarise business_website
    python tools/ocm_refresh.py emit       # -> ocm_doors.json, ready to splice into DATA
"""
import io
import json
import math
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "_ocm")
os.makedirs(CACHE, exist_ok=True)

SODA = "https://data.ny.gov/resource/jskf-tt3q.json"
AUTH = ("36hs7bcvls4ev22oeoyn7tphm",
        "2ahju7h7ugjyz406ip7anbymrhr2o8d3ezmvbwsxdq0zk5aw1u")
UA = {"User-Agent": "Mozilla/5.0 (compatible; jbd-sales-map/1.0)"}

# Retail-capable and able to sell to the public.
TYPES = ("OCMRETL", "OCMRETL_PCA", "OCMCAURD22", "OCMCAURD22_PCA",
         "OCMMICR", "OCMRO", "OCMXROD")

STOP = {"the", "a", "of", "and", "co", "llc", "inc", "corp", "ny", "nys",
        "dispensary", "cannabis", "dispensaries", "company", "adult", "use",
        "shop", "store", "ii", "iii"}


def jload(p, d=None):
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else d


def jdump(p, o):
    tmp = p + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="").write(json.dumps(o, ensure_ascii=False))
    os.replace(tmp, p)


def norm(x):
    return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()


def store_name(o):
    nm = (o.get("dba") or "").strip()
    if not nm or norm(nm) in ("na", "n a", "none"):
        nm = (o.get("entity_name") or "").strip()
    return nm


def current_doors():
    s = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    m = re.search(r"var DATA\s*=\s*(\[.*?\]);\s*\n", s, re.S)
    return json.loads(m.group(1))


# ── stages ────────────────────────────────────────────────────────────────────
def pull():
    rows, off = [], 0
    # Active licence, operationally active, AND an actual retail open date — a licence
    # that has never opened its doors is not a door.
    where = ("license_status='Active' and operational_status='Active' and "
             "retail_date_opened_to_public is not null and "
             "license_type_code in(%s)" % ",".join("'%s'" % t for t in TYPES))
    while True:
        r = requests.get(SODA, params={"$where": where, "$limit": 5000, "$offset": off},
                         auth=AUTH, headers=UA, timeout=60)
        r.raise_for_status()
        b = r.json()
        rows += b
        if len(b) < 5000:
            break
        off += 5000
    jdump(os.path.join(CACHE, "ocm_active.json"), rows)
    print("pulled %d operational retail-capable licences" % len(rows))

    doors = current_doors()
    have = {(d.get("lic") or "").strip().upper() for d in doors if d.get("lic")}
    new = [o for o in rows
           if (o.get("license_number") or "").strip().upper() not in have and store_name(o)]
    jdump(os.path.join(CACHE, "new.json"), new)
    addr = sum(1 for o in new if o.get("address_line_1"))
    print("  already mapped: %d   new: %d (with a street address: %d)"
          % (len(rows) - len(new), len(new), addr))
    gone = [d for d in doors
            if d.get("lic") and d["lic"].strip().upper()
            not in {(o.get("license_number") or "").strip().upper() for o in rows}]
    print("  on the map but no longer an operational licence: %d" % len(gone))
    jdump(os.path.join(CACHE, "gone.json"),
          [{"n": d.get("n"), "lic": d.get("lic"), "c": d.get("c")} for d in gone])


def geocode():
    new = jload(os.path.join(CACHE, "new.json"), [])
    cache = jload(os.path.join(CACHE, "geo.json"), {}) or {}
    todo = [o for o in new if o.get("address_line_1")
            and (o.get("license_number") not in cache)]
    print("geocoding %d addresses (%d already cached)" % (len(todo), len(cache)))
    for i, o in enumerate(todo, 1):
        q = ", ".join(x for x in (o.get("address_line_1"), o.get("city"), "NY",
                                  o.get("zip_code")) if x)
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 1,
                                     "countrycodes": "us"},
                             headers=UA, timeout=20)
            j = r.json() if r.ok else []
            cache[o["license_number"]] = ([float(j[0]["lat"]), float(j[0]["lon"])]
                                          if j else None)
        except Exception as exc:                       # noqa: BLE001 — never fatal
            print("   %s: %s" % (o["license_number"], exc))
            cache[o["license_number"]] = None
        if i % 20 == 0:
            jdump(os.path.join(CACHE, "geo.json"), cache)
            print("   %d/%d" % (i, len(todo)))
        time.sleep(1.1)                                 # Nominatim asks for <=1 req/s
    jdump(os.path.join(CACHE, "geo.json"), cache)
    got = sum(1 for v in cache.values() if v)
    print("geocoded %d / %d" % (got, len(cache)))


def haversine(a, b):
    R = 3958.8
    dlat, dlng = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    x = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


def area():
    """Estimate from the nearest measured doors.

    Category-aware on purpose: the app ranks on per-category dollars, so an area read that
    only carried a single blended number could not feed the same filters. Everything here is
    a neighbourhood median and is labelled an estimate — it is never written into the fields
    the app treats as measured.
    """
    new = jload(os.path.join(CACHE, "new.json"), [])
    geo = jload(os.path.join(CACHE, "geo.json"), {}) or {}
    doors = current_doors()
    measured = [d for d in doors
                if d.get("lat") and d.get("lng") and (d.get("svol30") or d.get("svol"))]
    print("measured reference doors: %d" % len(measured))

    def med(xs):
        xs = sorted(x for x in xs if isinstance(x, (int, float)))
        return xs[len(xs) // 2] if xs else None

    out = {}
    for o in new:
        pt = geo.get(o.get("license_number"))
        if not pt:
            continue
        near = sorted(((haversine(pt, (d["lat"], d["lng"])), d) for d in measured),
                      key=lambda x: x[0])[:6]
        if not near:
            continue
        peers = [d for _, d in near]
        vol = med([d.get("svol30") or d.get("svol") for d in peers])

        cats = {}
        for key in ("flower", "joint", "vape", "concentrate", "edible", "beverage",
                    "tincture", "topical"):
            vs = [d.get("cat", {}).get(key, {}).get("v") for d in peers]
            ps = [d.get("cat", {}).get(key, {}).get("p") for d in peers]
            mv, mp = med(vs), med(ps)
            if mv:
                cats[key] = {"v": round(mv), "p": round(mp, 2) if mp else None}

        tiers = [d.get("qt") for d in peers if d.get("qt")]
        tier = max(set(tiers), key=tiers.count) if tiers else None

        out[o["license_number"]] = {
            "n": len(peers),
            "mi": round(near[-1][0], 1),
            "vol": round(vol) if vol else 0,
            "cat": cats,
            "tier": tier,
            "peers": [d.get("n") for d in peers[:3]],
        }
    jdump(os.path.join(CACHE, "area.json"), out)
    print("area estimates for %d new doors" % len(out))
    if out:
        k = next(iter(out))
        print("  example %s: %s" % (k, json.dumps(out[k])[:220]))


def sites():
    """What the store's own site says it sells — the only first-party signal available."""
    new = jload(os.path.join(CACHE, "new.json"), [])
    cache = jload(os.path.join(CACHE, "sites.json"), {}) or {}
    todo = [o for o in new if o.get("business_website")
            and o.get("license_number") not in cache]
    print("fetching %d sites (%d cached)" % (len(todo), len(cache)))

    CATS = {
        "flower": r"\bflower\b|\beighth|\bpre.?roll",
        "concentrate": r"\bconcentrate|\brosin\b|\bhash\b|\bdiamond|\blive resin",
        "vape": r"\bvape|\bcart(ridge)?s?\b|\bdisposable",
        "edible": r"\bedible|\bgumm(y|ies)|\bchocolate",
        "premium": r"\bpremium\b|\bcraft\b|\bexotic\b|\btop.?shelf|\bconnoisseur",
        "value": r"\bbudget\b|\bvalue\b|\bdeal(s)?\b|\bdaily special",
        "delivery": r"\bdelivery\b",
        "menu": r"\bdutchie\b|\bweedmaps\b|\bleafly\b|\bmeadow\b|\bblaze\b|\bjane\b",
    }
    for i, o in enumerate(todo, 1):
        url = (o.get("business_website") or "").strip()
        if not re.match(r"^https?://", url):
            url = "https://" + url
        rec = {"url": url, "ok": False}
        try:
            r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
            if r.ok and r.text:
                txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text,
                             flags=re.S | re.I)
                txt = re.sub(r"<[^>]+>", " ", txt)
                txt = re.sub(r"\s+", " ", txt)[:20000].lower()
                rec["ok"] = True
                rec["signals"] = sorted(k for k, pat in CATS.items()
                                        if re.search(pat, txt))
                t = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
                rec["title"] = re.sub(r"\s+", " ", t.group(1)).strip()[:120] if t else ""
        except Exception as exc:                       # noqa: BLE001
            rec["err"] = type(exc).__name__
        cache[o["license_number"]] = rec
        if i % 15 == 0:
            jdump(os.path.join(CACHE, "sites.json"), cache)
            print("   %d/%d" % (i, len(todo)))
    jdump(os.path.join(CACHE, "sites.json"), cache)
    ok = sum(1 for v in cache.values() if v.get("ok"))
    print("reachable sites: %d / %d" % (ok, len(cache)))


def emit():
    new = jload(os.path.join(CACHE, "new.json"), [])
    geo = jload(os.path.join(CACHE, "geo.json"), {}) or {}
    ar = jload(os.path.join(CACHE, "area.json"), {}) or {}
    st = jload(os.path.join(CACHE, "sites.json"), {}) or {}

    doors, skipped = [], 0
    for o in new:
        lic = o.get("license_number")
        pt = geo.get(lic)
        if not pt:
            skipped += 1
            continue
        d = {
            "n": store_name(o),
            "lic": lic,
            "a": o.get("address_line_1") or "",
            "c": o.get("city") or "",
            "co": o.get("county") or "",
            "opened": (o.get("retail_date_opened_to_public") or "")[:10],
            "lat": round(pt[0], 6),
            "lng": round(pt[1], 6),
            "nd": 1,                                   # no measured market data
            "src": "ocm",
        }
        a = ar.get(lic)
        if a:
            d["area"] = a
        s = st.get(lic)
        if s and s.get("ok"):
            d["site"] = {"url": s["url"], "sig": s.get("signals") or []}
        elif o.get("business_website"):
            d["site"] = {"url": s.get("url") if s else o["business_website"], "sig": []}
        doors.append(d)

    jdump(os.path.join(CACHE, "ocm_doors.json"), doors)
    print("emitted %d new doors  (skipped %d with no geocode)" % (len(doors), skipped))
    print("  with an area estimate : %d" % sum(1 for d in doors if d.get("area")))
    print("  with site signals     : %d" % sum(1 for d in doors if d.get("site", {}).get("sig")))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    {"pull": pull, "geocode": geocode, "area": area, "sites": sites, "emit": emit}[cmd]()

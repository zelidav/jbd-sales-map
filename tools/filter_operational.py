#!/usr/bin/env python3
"""Filter the map's DATA to only ACTIVE / LIVE / OPEN dispensaries.

Cross-checks every door's license number against the live NY OCM licenses
dataset (Socrata jskf-tt3q) and drops any location that isn't operational —
EXCEPT curated/known-open accounts (see KEEP rule), which we trust over OCM lag.

Also stamps each kept record with:
  - "op":     OCM operational_status ("Active" / "Non-Operational" / "" )
  - "opened": retail_date_opened_to_public (YYYY-MM-DD) when present

Loud by design (per the loud-failures rule): prints exactly what was kept and
dropped and why, and refuses to write if it would nuke the whole list.

Usage:  python tools/filter_operational.py          # apply + rewrite index.html
        python tools/filter_operational.py --dry     # report only, no write
"""
import re, json, os, sys, urllib.request, urllib.parse, time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
BASE = "https://data.ny.gov/resource/jskf-tt3q.json"
DRY = "--dry" in sys.argv

# Roles we trust as open regardless of OCM operational_status:
#  - we have orders from them (Active/Slipping) => they are open
#  - JB tiers are a hand-curated priority list of real, operating stores
TRUST_ROLES = {"Dragonfly Active", "Dragonfly Slipping",
               "JB Tier 1", "JB Tier 2", "JB Tier 3"}


def fetch_ocm():
    cols = ("license_number,operational_status,license_status,"
            "retail_date_opened_to_public")
    rows, off, lim = [], 0, 1000
    while True:
        qs = urllib.parse.urlencode({
            "$select": cols, "$where": "license_number IS NOT NULL",
            "$limit": lim, "$offset": off})
        with urllib.request.urlopen(BASE + "?" + qs) as r:
            batch = json.load(r)
        rows += batch
        if len(batch) < lim:
            break
        off += lim
        time.sleep(0.2)
    # Active-wins: if any location row for a license is operational, it's open.
    op = {}
    for x in rows:
        ln = x.get("license_number")
        if not ln:
            continue
        cur = op.get(ln)
        if cur is None or (x.get("operational_status") == "Active"
                           and cur.get("operational_status") != "Active"):
            op[ln] = x
    return op


def main():
    html = open(HTML, encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
    if not m:
        raise SystemExit("Could not find `var DATA=[...]` in index.html")
    data = json.loads(m.group(1))

    print(f"Fetching live OCM operational status… ({len(data)} doors to check)")
    ocm = fetch_ocm()
    print(f"OCM licenses loaded: {len(ocm)}")

    kept, dropped = [], []
    keep_reason, drop_role = Counter(), Counter()
    for d in data:
        ln = d.get("lic")
        role = d.get("role", "")
        x = ocm.get(ln) or {}
        op = x.get("operational_status", "") or ""
        opened = (x.get("retail_date_opened_to_public") or "")[:10]
        if op == "Active":
            keep_reason["OCM operational Active"] += 1
            keep = True
        elif role in TRUST_ROLES:
            keep_reason[f"trusted role ({role}); OCM={op or 'unknown'}"] += 1
            keep = True
        else:
            keep = False
        if keep:
            d = dict(d, op=op or "Unknown", opened=opened)
            kept.append(d)
        else:
            dropped.append(d)
            drop_role[role] += 1

    print(f"\n{'='*60}\nKEEP {len(kept)}  ·  DROP {len(dropped)}  (of {len(data)})\n{'='*60}")
    print("\nWhy kept:")
    for k, v in keep_reason.most_common():
        print(f"  {v:>4}  {k}")
    print("\nDropped (not open, not trusted) by role:")
    for k, v in drop_role.most_common():
        print(f"  {v:>4}  {k}")
    print("\nDropped doors:")
    for d in dropped:
        x = ocm.get(d.get("lic")) or {}
        print(f"  - {d['role']:<18} {d['n']:<34} {d.get('lic','')}  "
              f"OCM op={x.get('operational_status') or '—'}")

    # Safety: never silently gut the dataset.
    if len(kept) < len(data) * 0.5:
        raise SystemExit(f"\nABORT: would drop >50% ({len(dropped)}/{len(data)}). "
                         "Refusing to write — check OCM fetch / license formats.")

    if DRY:
        print("\n--dry: no changes written.")
        return

    new_html = html[:m.start(1)] + json.dumps(kept, ensure_ascii=False) + html[m.end(1):]
    open(HTML, "w", encoding="utf-8").write(new_html)
    print(f"\nWrote {len(kept)} operational doors to index.html")
    print("Next: python tools/sync_accounts.py  (refresh the bot dataset)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the small files David can upload live, in front of an audience.

Two rules shaped these:

1. An upload REPLACES a company's sales -- putSales writes the whole record. So these are
   never for the prepared demo org; they are for a throwaway company created on the spot,
   which is the better beat anyway: a brand going from nothing to a working map while the
   room watches.

2. The point being demonstrated is that the file does not have to look like anything in
   particular. So these are deliberately NOT shaped like the Distru export the demo org was
   built from -- different headers, different column order, different money column, noise
   columns that mean nothing, and bare licences with no -D suffix. If the mapper handles
   these live, the ninety-second onboarding claim is proven rather than asserted.

Small on purpose: an 18MB file is a minute of dead air on stage. These land in seconds.

Usage:  python tools/demo_upload_files.py [outdir]
"""
import collections
import csv
import datetime as dt
import io
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "data", "dragonfly_demo.csv")
RNG = random.Random(4242)

REPS = ["A. Torres", "M. Okafor", "J. Brady", "S. Lindqvist"]
TERMS = ["Net 30", "Net 15", "COD", "Net 30"]


CITY = {}


def door_cities():
    """licence -> city, straight off the licensed map."""
    import json
    idx = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    m = re.search(r"var DATA=(\[.*?\]);", idx, re.S)
    data = json.loads(m.group(1))
    return {d["lic"].upper(): (d.get("c") or "") for d in data if d.get("lic")}


def num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "") or 0)
    except ValueError:
        return 0.0


def load_orders(months):
    """Recent orders, grouped, newest first — a believable 'last N months' export."""
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))
    cutoff = (dt.date(2026, 9, 22) - dt.timedelta(days=30 * months)).isoformat()
    keep = [r for r in rows
            if r["Order created date"] >= cutoff
            and not r["Licensed location name"].startswith("Dragonfly Kitchen")
            and num(r["Line item subtotal"]) > 0]
    by = collections.OrderedDict()
    for r in keep:
        by.setdefault(r["Order number"], []).append(r)
    return by


def leaflink(out, months=4, cap=1400):
    """A wholesale-platform shaped export: different headers, bare licences, noise columns."""
    hdr = ["Retailer", "License Number", "Order #", "Order Date", "Fulfillment Status",
           "Brand", "Product", "Units", "Unit Price", "Net Sales", "Payment Terms",
           "Sales Rep", "Market"]
    by = load_orders(months)
    n, rows = 0, []
    for order, lines in by.items():
        if n >= cap:
            break
        for l in lines:
            if n >= cap:
                break
            units = num(l["Line item quantity"])
            sub = num(l["Line item subtotal"])
            rows.append({
                "Retailer": l["Licensed location name"],
                # bare licence: the same door, written the way a different system writes it
                "License Number": l["Order retailer license"].split("-D")[0],
                "Order #": order,
                "Order Date": l["Order created date"],
                "Fulfillment Status": "Delivered",
                "Brand": "Dragonfly",
                "Product": l["Line item product name"],
                "Units": int(units) if units else 0,
                "Unit Price": "%.2f" % num(l["Line item price per unit"]),
                "Net Sales": "%.2f" % sub,
                "Payment Terms": RNG.choice(TERMS),
                "Sales Rep": RNG.choice(REPS),
                "Market": "New York",
            })
            n += 1
    write(out, hdr, rows)
    return rows


def summary(out, months=6):
    """The other common shape: one row per account, already totalled, no dates or line items.

    This is what most brands can actually produce on request, and it is the harder file --
    no licence column at all, so every row has to be matched on the store name.
    """
    hdr = ["Account", "City", "Orders", "Gross Sales", "Last Ordered", "Owner"]
    by = load_orders(months)
    agg = {}
    for order, lines in by.items():
        name = lines[0]["Licensed location name"]
        # The address string in the export is inconsistent -- trailing "Suite 4",
        # sometimes no city at all. The licensed map already knows the city for this
        # licence, so fill from there and give the matcher a real locator to use.
        city = CITY.get(lines[0]["Order retailer license"].split("-D")[0].upper(), "")
        a = agg.setdefault(name, {"o": 0, "v": 0.0, "last": "", "city": city})
        a["o"] += 1
        a["v"] += sum(num(l["Line item subtotal"]) for l in lines)
        a["last"] = max(a["last"], lines[0]["Order created date"])
    rows = [{"Account": k, "City": v["city"], "Orders": v["o"],
             "Gross Sales": "%.2f" % v["v"], "Last Ordered": v["last"],
             "Owner": RNG.choice(REPS)}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["v"])]
    write(out, hdr, rows)
    return rows


def write(path, hdr, rows):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hdr)
        w.writeheader()
        w.writerows(rows)
    kb = os.path.getsize(path) / 1024
    print("  %-38s %5d rows  %6.0f KB" % (os.path.basename(path), len(rows), kb))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "site-demo")
    os.makedirs(out, exist_ok=True)
    global CITY
    CITY = door_cities()
    print("live-upload files:")
    a = leaflink(os.path.join(out, "beeline-demo-wholesale-platform.csv"))
    b = summary(os.path.join(out, "beeline-demo-account-summary.csv"))
    print("\n  wholesale-platform : line items, bare licences, noise columns")
    print("  account-summary    : pre-totalled, NO licence column, name matching only")
    print("\n  distinct retailers: %d / %d"
          % (len({r['Retailer'] for r in a}), len(b)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

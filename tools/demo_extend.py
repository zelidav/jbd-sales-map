#!/usr/bin/env python3
"""Extend the Dragonfly order file forward to today, for a demo dataset.

The real export stops on 2026-06-15. Loaded as-is, every door reads ~100 days since its
last order, so the whole app shows one colour: lapsed. Nothing that depends on recency —
Grow it, Save it, momentum, win-back, "last ordered 12 days ago" — can be shown at all.

This walks each door forward on ITS OWN observed cadence and basket, so the shape of the
book survives: a door that ordered every three weeks at $3k keeps doing that, a door with
two orders and a long gap stays quiet. On top of that it plants the states a demo needs to
show, because a faithful extrapolation mostly produces "everything continues", which
demonstrates nothing:

    lapse_60/90/120  doors that simply stop, at a chosen number of days back
    surge            doors that accelerate and grow their basket
    decay            doors whose cadence stretches and basket shrinks
    newdoor          doors that first appear inside the window

Output is the SAME 59-column schema as the source, so it exercises the real ingest path —
-D1 licences, order/line structure, $0 sample lines and all.

THIS IS DEMO DATA. It is written for a sandbox org and must never be uploaded to a real
tenant or allowed to inform the shared market model.

Usage:  python tools/demo_extend.py [out.csv] [--to YYYY-MM-DD]
"""
import csv
import collections
import datetime as dt
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "data", "dragonfly_orders.csv")

RNG = random.Random(20260922)          # deterministic: a demo must look the same twice
CUTOFF = dt.date(2026, 6, 15)          # last real order date in the file


def num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "") or 0)
    except ValueError:
        return 0.0


NABIS = os.path.join(os.path.expanduser("~"), "Downloads", "All Sales NABIS 9-26.xlsx")


def load_nabis(hdr):
    """Real Dragonfly history from the Nabis era, May-Sep 2025.

    Same 57-column export shape as the Distru file but written before the Distru cutover,
    so it extends the timeline backwards by five real months instead of invented ones.
    Its licences carry no -D suffix, which is useful: the file then exercises both the
    bare and the decorated form through the same ingest.
    """
    if not os.path.exists(NABIS):
        print("  (no Nabis export found, skipping)")
        return []
    try:
        import openpyxl
    except ImportError:
        print("  (openpyxl missing, skipping Nabis)")
        return []
    wb = openpyxl.load_workbook(NABIS, read_only=True, data_only=True)
    ws = wb["export (2)"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    head = list(rows[0])
    out = []
    for raw in rows[1:]:
        if not any(raw):
            continue
        d = dict(zip(head, raw))
        r = {}
        for k in hdr:
            v = d.get(k, "")
            if hasattr(v, "isoformat"):
                v = v.isoformat()[:10]
            r[k] = "" if v is None else str(v)
        out.append(r)
    return out


def load():
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))
    hdr = list(rows[0].keys())
    nab = load_nabis(hdr)
    if nab:
        print("  + %d real rows from the Nabis export (2025-05 -> 2025-09)" % len(nab))
    return hdr, nab + rows


def by_door(rows):
    """Group real orders per door, newest last."""
    doors = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        key = (r["Order retailer license"], r["Licensed location name"])
        doors[key][r["Order number"]].append(r)
    return doors


def cadence(orders):
    """Median days between a door's orders, and its median order value."""
    dates = sorted({dt.date.fromisoformat(v[0]["Order created date"]) for v in orders.values()})
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    gaps.sort()
    med = gaps[len(gaps) // 2] if gaps else None
    vals = sorted(sum(num(l["Line item subtotal"]) for l in v) for v in orders.values())
    val = vals[len(vals) // 2] if vals else 0.0
    return med, val, dates[-1] if dates else None


def plan(doors, today):
    """Assign each door a behaviour for the demo window."""
    stats = {}
    for key, orders in doors.items():
        med, val, last = cadence(orders)
        stats[key] = {"med": med, "val": val, "last": last, "orders": orders}

    # Rank by real spend: the big doors carry the demo, so put the interesting states on
    # doors a viewer will actually look at rather than burying them in the tail.
    # A door with a single real order has no measurable gap, but it is still a live
    # account and in a growing book many of them reorder. Give those a default cadence so
    # they participate, otherwise a third of the base silently goes dark the moment the
    # real data stops and the demo window dips instead of climbing.
    for k, st in stats.items():
        if not st["med"] and st["val"] > 0 and not k[1].startswith("Dragonfly Kitchen"):
            st["med"] = RNG.choice([28, 35, 42, 49])
    live = [k for k, s in stats.items()
            if s["med"] and 3 <= s["med"] <= 120 and s["val"] > 0
            and not k[1].startswith("Dragonfly Kitchen")]
    live.sort(key=lambda k: -stats[k]["val"])

    # The book should read as GROWING: most doors tighten their cadence and grow their
    # basket across the window, so month-over-month totals rise. A handful of lapses are
    # kept deliberately -- without them there is no win-back story to show, and a chart
    # that only goes up demonstrates one feature instead of four.
    roles = {}
    for i, k in enumerate(live):
        if i < 10:
            roles[k] = "surge"                     # headline doors accelerating
        elif i in (11, 15, 19):
            roles[k] = "lapse_60"                  # recent, still winnable
        elif i in (13, 17):
            roles[k] = "lapse_90"
        elif i in (21, 25):
            roles[k] = "lapse_120"                 # long gone, the hard win-back
        elif i % 7 == 0:
            roles[k] = "decay"                     # slipping, not yet lost
        else:
            roles[k] = "grow"                      # the broad base, quietly compounding
    for k in stats:
        roles.setdefault(k, "skip")
    return stats, roles


def clone_order(lines, order_no, when, scale, seq):
    """A new order for a door, modelled on one of its real ones."""
    out = []
    deliver = when + dt.timedelta(days=RNG.choice([1, 2, 2, 3]))
    for i, src in enumerate(lines):
        r = dict(src)
        qty = num(src["Line item quantity"])
        ppu = num(src["Line item price per unit"])
        newq = max(1, int(round(qty * scale * RNG.uniform(0.75, 1.3))))
        sub = round(newq * ppu, 2)
        r["Order ID"] = "demo-%s-%04d" % (order_no, i)
        r["Order number"] = str(order_no)
        r["Order created date"] = when.isoformat()
        r["Order updated date"] = when.isoformat()
        r["Order delivery date"] = deliver.isoformat()
        r["Line item updated date"] = when.isoformat()
        r["Line item quantity"] = str(newq)
        r["Line item subtotal"] = "%.2f" % sub
        r["Line item number"] = str(seq * 1000 + i)
        r["Line item ID"] = "demo-li-%s-%04d" % (order_no, i)
        r["Order status"] = "COMPLETED"
        r["Order payment status"] = RNG.choice(["PAID", "PAID", "PAID", "UNPAID"])
        r["Order payment due date"] = (when + dt.timedelta(days=30)).isoformat()
        r["Manifest"] = ""
        out.append(r)
    # Order-level fields repeat on every line in this schema; keep that true.
    total = round(sum(num(x["Line item subtotal"]) for x in out), 2)
    for r in out:
        r["Order subtotal"] = "%.2f" % total
        r["Order total"] = "%.2f" % total
        r["Order collected"] = "%.2f" % (total if r["Order payment status"] == "PAID" else 0)
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else os.path.join(ROOT, "data", "dragonfly_demo.csv")
    today = dt.date(2026, 9, 22)
    if "--to" in sys.argv:
        today = dt.date.fromisoformat(sys.argv[sys.argv.index("--to") + 1])

    hdr, rows = load()
    doors = by_door(rows)
    stats, roles = plan(doors, today)

    new = []
    order_no = 990000
    counts = collections.Counter()

    for key, s in stats.items():
        role = roles[key]
        counts[role] += 1
        if role == "skip" or not s["med"]:
            continue
        med, last = s["med"], s["last"]
        templates = list(s["orders"].values())

        # How far forward this door keeps ordering.
        stop = {"lapse_60": today - dt.timedelta(days=60),
                "lapse_90": today - dt.timedelta(days=90),
                "lapse_120": today - dt.timedelta(days=120)}.get(role, today)

        when, seq, n = last, 0, 0
        gap, scale = med, 1.0
        while True:
            if role == "surge":
                gap = max(5, int(med * (0.90 ** n)))
                scale = 1.0 + 0.14 * n
            elif role == "grow":
                # the average door orders a little more often and a little bigger
                gap = max(4, int(med * (0.96 ** n) * RNG.uniform(0.85, 1.1)))
                scale = 1.0 + 0.07 * n
            elif role == "decay":
                gap = int(med * (1.18 ** n))
                scale = max(0.35, 1.0 - 0.12 * n)
            else:
                gap = max(4, int(med * RNG.uniform(0.8, 1.25)))
            when = when + dt.timedelta(days=gap)
            if when > stop or when > today:
                break
            order_no += 1
            seq += 1
            n += 1
            new.extend(clone_order(RNG.choice(templates), order_no, when, scale, seq))
            if n > 40:
                break

    # A few doors that appear for the first time inside the window: "new customer" is a
    # state the app shows, and an extrapolation of existing doors can never produce it.
    # Doors that open inside the window. These must be licences Dragonfly has NEVER sold,
    # or the "first order" is not a first order and the app cannot show a won account.
    # They are taken from the real licensed map, so the names, addresses and licences are
    # genuine even though the orders are not.
    import json as _json
    import re as _re
    idx = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    DOORS = _json.loads(_re.search(r"var DATA=(\[.*?\]);\n", idx, _re.S).group(1))
    had = {k[0].split("-D")[0].upper() for k in stats}
    fresh = [d for d in DOORS
             if d.get("lic") and d["lic"].upper() not in had
             and d.get("a") and d.get("c")]
    RNG.shuffle(fresh)

    # Size each new door off the market report rather than a donor's basket: a store
    # measured at $400k/30d should not place the same order as one measured at $40k.
    # Dragonfly is one brand on that shelf, so its share is a slice of the store's total.
    try:
        PIS = _json.load(io.open(os.path.join(ROOT, "data", "pistil", "ny_stores_30d.json"),
                                 encoding="utf-8"))
    except Exception:
        PIS = []
    def _norm(x):
        return _re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()
    vol = {}
    for row in PIS:
        vol[_norm(row.get("sales_estimates.store_name"))] = row.get(
            "sales_estimates.sum_sale_dollars") or 0
    def measured(name):
        n = _norm(name)
        if n in vol:
            return vol[n]
        for k, v in vol.items():                 # chain locations carry a suffix
            if k and (k.startswith(n) or n.startswith(k)):
                return v
        return None

    donors = [k for k, r in roles.items() if r in ("grow", "surge")]
    med_door = sorted(v for v in vol.values() if v) or [0]
    med_door = med_door[len(med_door) // 2]
    for d in fresh[:16]:
        donor = stats[RNG.choice(donors)]
        templates = list(donor["orders"].values())
        med = donor["med"] or 21
        # a door twice the size of the median orders about twice as much
        mv = measured(d["n"])
        size = 1.0 if not mv or not med_door else max(0.3, min(3.0, mv / med_door))
        age = RNG.choice([18, 27, 39, 52, 64, 78, 91])
        when, seq = today - dt.timedelta(days=age), 0
        counts["newdoor"] += 1
        while when <= today and seq < 9:
            order_no += 1
            seq += 1
            lines = clone_order(RNG.choice(templates), order_no, when,
                                (0.5 + 0.20 * seq) * size, seq)
            # rebadge the order onto the new door: licence carries the -D1 suffix this
            # file uses, so it exercises the suffix-stripping match as well
            for r in lines:
                r["Order retailer license"] = d["lic"] + "-D1"
                r["Licensed location name"] = d["n"]
                r["Licensed location address"] = "%s  %s NY" % (d.get("a", ""), d.get("c", ""))
                r["Licensed location ID"] = "demo-loc-" + d["lic"]
            new.extend(lines)
            when = when + dt.timedelta(days=max(6, int(med * (0.93 ** seq))))

    combined = rows + new
    with io.open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hdr)
        w.writeheader()
        w.writerows(combined)

    real = sum(num(r["Line item subtotal"]) for r in rows)
    add = sum(num(r["Line item subtotal"]) for r in new)
    print("wrote %s" % out_path)
    print("  real rows %-6d  $%s" % (len(rows), format(round(real), ",")))
    print("  added     %-6d  $%s" % (len(new), format(round(add), ",")))
    print("  total     %-6d  $%s" % (len(combined), format(round(real + add), ",")))
    print("  window    %s -> %s" % (CUTOFF, today))
    print("  roles:", dict(counts))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

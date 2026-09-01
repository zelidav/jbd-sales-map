#!/usr/bin/env python3
"""Build a shareable preset-route link for the field map.

A trip is one URL. It opens index.html with the day already built -- start pin, the
stops in the ORDER GIVEN (not re-optimized), end pin -- and a banner that switches
between days. Stops are OCM license numbers; the map resolves them against var DATA,
so a stop that has fallen off the map surfaces as a warning instead of silently
vanishing.

Usage:
    python tools/make_trip_link.py trips/john_syracuse.json
    python tools/make_trip_link.py trips/john_syracuse.json --base https://zelidav.github.io/jbd-sales-map/

Trip JSON:
    t    trip title            rep  who it is for       a  index of the day to open on
    d[]  days, each:
         b     short button label ("Day 1")
         l     long label ("Tue · Long Beach -> Syracuse")
         s     [lat, lng, "Start label"]     e  [lat, lng, "End label"]  (both optional)
         st[]  stops, in order -- OCM license numbers (store names also resolve)
         note  one line shown under the banner; **bold** renders
         m     travel mode: driving | transit | bicycling | walking
"""
import base64, json, sys, os

DEFAULT_BASE = "https://zelidav.github.io/jbd-sales-map/"


def encode(trip):
    raw = json.dumps(trip, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main():
    args = [a for a in sys.argv[1:]]
    base = DEFAULT_BASE
    if "--base" in args:
        i = args.index("--base"); base = args[i + 1]; del args[i:i + 2]
    if not args:
        print(__doc__); sys.exit(1)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    trip = json.load(open(args[0], encoding="utf-8"))
    url = base + "#trip=" + encode(trip)
    for i, d in enumerate(trip.get("d", [])):
        print(f"  {d.get('b', 'Day ' + str(i+1))}: {len(d.get('st', []))} stops — {d.get('l','')}")
    print(f"\n{len(url)} chars\n{url}")


if __name__ == "__main__":
    main()

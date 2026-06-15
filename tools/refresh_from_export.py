#!/usr/bin/env python3
"""Refresh map DATA from a new field-map export WITHOUT clobbering the app.

This decouples the repo from the Finance Docs pipeline: instead of copying a
freshly-generated export over index.html (which wipes the bot, product-mix,
mobile layout, routing, and rosin flag), this swaps ONLY the `var DATA=[...]`
array inside the existing index.html and then re-runs the data builders.

Usage:
  python tools/refresh_from_export.py [path-to-export.html]
Defaults to ~/Downloads/Finance Docs/Dragonfly_JB_Field_Map.html

After running: review the diff, then commit + push as usual.
"""
import os, re, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.expanduser("~/Downloads/Finance Docs/Dragonfly_JB_Field_Map.html")
export = sys.argv[1] if len(sys.argv) > 1 else DEFAULT

if not os.path.exists(export):
    raise SystemExit(f"Export not found: {export}\nPass the path: python tools/refresh_from_export.py <export.html>")

src = open(export, encoding="utf-8").read()
m = re.search(r"var DATA=(\[.*?\]);", src, re.S)
if not m:
    raise SystemExit("Could not find `var DATA=[...]` in the export.")
new_data = m.group(1)

idx = os.path.join(ROOT, "index.html")
html = open(idx, encoding="utf-8").read()
if "var DATA=[" not in html:
    raise SystemExit("index.html has no `var DATA=[` to replace — aborting to avoid damage.")

n_old = len(re.findall(r"\{", re.search(r"var DATA=(\[.*?\]);", html, re.S).group(1)))
html = re.sub(r"var DATA=\[.*?\];", "var DATA=" + new_data.replace("\\", "\\\\") + ";", html, count=1, flags=re.S)
open(idx, "w", encoding="utf-8").write(html)
n_new = len(re.findall(r"\{", new_data))
print(f"Swapped DATA in index.html (~{n_old} -> ~{n_new} records). App code preserved.")

# Re-run the data builders so accounts.json / MIX / rosin stay in sync.
for tool in ("sync_accounts.py", "build_orders.py", "build_rosin.py"):
    p = os.path.join(ROOT, "tools", tool)
    print(f"\n--- {tool} ---")
    r = subprocess.run([sys.executable, p], cwd=os.path.join(ROOT, "tools"))
    if r.returncode != 0:
        print(f"(warning: {tool} exited {r.returncode} — check above)")

print("\nDone. Review `git diff index.html`, then commit + push.")

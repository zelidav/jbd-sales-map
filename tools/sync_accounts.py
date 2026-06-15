#!/usr/bin/env python3
"""Sync the bot's dataset (server/accounts.json) from the map's index.html DATA array.
Run before deploying the bot so it always matches the live map."""
import re, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
m = re.search(r"var DATA=(\[.*?\]);", html, re.S)
if not m:
    raise SystemExit("Could not find `var DATA=[...]` in index.html")
data = json.loads(m.group(1))
out = os.path.join(ROOT, "server", "accounts.json")
json.dump(data, open(out, "w"))
print(f"synced {len(data)} accounts -> {out}")

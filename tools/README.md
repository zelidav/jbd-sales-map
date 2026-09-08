# jbd-sales-map — maintenance

`index.html` is the **source of truth**. It contains the map, the data (`var DATA`),
the product-mix (`var MIX`), the Claude sales
bot, mobile layout, and route optimizer.

## ⚠️ Do NOT copy a fresh export over index.html
Copying `Dragonfly_JB_Field_Map.html` (or any export) over `index.html` wipes the bot,
product-mix popups, mobile layout, route optimizer, and rosin flags. This already
caused one outage. Use the refresh script instead — it swaps **only the data**.

## Refresh the data (new store list)
```sh
python tools/refresh_from_export.py "~/Downloads/Finance Docs/Dragonfly_JB_Field_Map.html"
git add -A && git commit -m "Refresh map data" && git push
```
This swaps `var DATA`, then re-runs `sync_accounts.py`, `build_orders.py`, and
so the bot and product-mix stay in sync. App code is
preserved.

## Refresh order / product-mix data (new sales export)
Drop the new line-item CSV at `data/dragonfly_orders.csv` (gitignored — contains PII),
then:
```sh
python tools/build_orders.py   # rebuilds server/orders_summary.json + MIX in index.html
```

## Redeploy the bot (server change or data refresh)
```sh
bash server/deploy.sh          # Cloud Run: jbd-sales-bot (printful-manager / us-central1)
```
The bot reuses the shared `MM_ANTHROPIC_API_KEY` secret. Its dataset is refreshed from
index.html automatically on deploy.

## Refresh the Pistil intelligence layer (monthly)

Pistil retention is ~90 days, so this needs re-running to stay current. Order matters —
each step writes into `index.html`.

```sh
cd tools/pistil
bash pull.js ...                     # all-category windows (see tools/pistil/README.md)
bash pull_brands.sh brands.txt       # one export per brand — carriage. ~2 min each
python validate_brands.py --fix      # reject bad pulls; re-run pull_brands.sh to fill gaps
cd ../..
python tools/build_store_rank.py     # psr / svol / mom / momr + var WINDOWS
python tools/build_prospects.py      # off-map top performers
python tools/build_category_fit.py   # per-category $/units/price/tier + quality tier + var CATS
# (build_fp_fit.py is gone: the JB/Dragonfly two-brand fit score it wrote was dead
#  data -- nothing in the app read it. Targeting is per-category price tier + volume.)
python tools/build_brand_carriage.py # per-door brand carriage + var BRANDS
python tools/build_brand_profiles.py # what each brand sells -> server/brand_profiles.json
python tools/sync_accounts.py        # refresh the bot dataset (carries cat/qt/br through)
bash server/deploy.sh                # the bot reads brand_profiles.json + the carriage columns
```

**The bot is brand-aware.** The map's "What you're selling" selector posts `brand` with each
chat request; the server appends that brand's product profile and carriage summary to the
system prompt *after* the cached account block, so switching brands never invalidates the
cache. `brand_profiles.json` gives it each brand's real category mix and price points from
the product-rank export, so it never has to guess what a brand sells; `web_search` is there
only to enrich an unknown brand's positioning, and it asks the rep when that is inconclusive.

**Never trust a Pistil export without validating it** — exports carry no date metadata,
an ignored filter silently returns the full dataset, and a short render returns the
*previous* query. See `tools/pistil/README.md` for the traps and the 92–93% brand-ratio
check that catches them.

## Scripts
- `refresh_from_export.py` — swap DATA from an export without clobbering the app
- `sync_accounts.py` — DATA → server/accounts.json (bot dataset)
- `build_orders.py` — order CSV → server/orders_summary.json + `var MIX`
- `build_store_rank.py` — Pistil rank + momentum from three measured windows
- `build_category_fit.py` — per-category price tier + customer-quality tier (`cat`, `qt`, `qs`)
- `build_brand_carriage.py` — per-door brand carriage from brand-filtered pulls (`br`, `var BRANDS`)
- `pistil/pull_brands.sh` · `pistil/validate_brands.py` — pull + verify the brand cuts

### Superseded
`build_fp_fit.py` wrote the two-brand `jbt`/`dft` premium-vs-value target tiers. The map
no longer reads them: per-category price tiers plus **measured** brand carriage replaced
inferred brand fit. The fields remain in DATA and the script still runs, but nothing
consumes its output.

## Send a rep a preset route (one link)

`#trip=<base64 json>` opens `index.html` with the day already in the route builder —
start pin, stops **in the order given** (not re-optimized), end pin — plus a banner
that switches between days of a multi-day trip.

```sh
python tools/make_trip_link.py trips/john_syracuse.json
```

Trips live in `trips/*.json`; stops are OCM license numbers, so a door that has dropped
off the map shows a warning in the banner instead of silently disappearing. See the
docstring in `make_trip_link.py` for the schema.

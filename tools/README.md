# jbd-sales-map — maintenance

`index.html` is the **source of truth**. It contains the map, the data (`var DATA`),
the product-mix (`var MIX`), the rosin target list (`var ROSIN50`), the Claude sales
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
`build_rosin.py` so the bot, product-mix, and rosin list stay in sync. App code is
preserved.

## Refresh order / product-mix data (new sales export)
Drop the new line-item CSV at `data/dragonfly_orders.csv` (gitignored — contains PII),
then:
```sh
python tools/build_orders.py   # rebuilds server/orders_summary.json + MIX in index.html
python tools/build_rosin.py    # rebuilds the top-50 rosin target list
```

## Redeploy the bot (server change or data refresh)
```sh
bash server/deploy.sh          # Cloud Run: jbd-sales-bot (printful-manager / us-central1)
```
The bot reuses the shared `MM_ANTHROPIC_API_KEY` secret. Its dataset is refreshed from
index.html automatically on deploy.

## Scripts
- `refresh_from_export.py` — swap DATA from an export without clobbering the app
- `sync_accounts.py` — DATA → server/accounts.json (bot dataset)
- `build_orders.py` — order CSV → server/orders_summary.json + `var MIX`
- `build_rosin.py` — score + inject `var ROSIN50` (top-50 live-rosin targets)

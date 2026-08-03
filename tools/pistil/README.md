# Pistil export automation

Pulls Pistil Insights exports without hand-clicking, and **verifies every file before
you trust it**. Pistil is a white-labelled Sigma Computing embed whose grid renders to
`<canvas>` — there are no DOM rows to scrape, so exporting is the only way out.

## Why this exists

Pistil exports carry **no date metadata**. The pipeline used to guess each window from
total volume and assume 30/90/180 days. Measured, the three files feeding the live map
were 30, 69 and 93 days — so momentum divided nested windows against each other and a
flat market scored ~+34%. That number became the "statewide baseline". See
`window_days()` in `../build_store_rank.py`.

## Setup (once)

Launch a debuggable Chrome on a **dedicated profile** (Chrome 136+ refuses remote
debugging on the default profile), then sign in to Pistil inside that window:

```powershell
Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  -ArgumentList '--remote-debugging-port=9222','--user-data-dir=C:\Users\zelid\chrome-debug-profile'
```

Check it with `curl http://127.0.0.1:9222/json/version`.

## Pull

```sh
node pull.js <slug> <reportPath> <workbookGuid> <savedFilterGuid> '<controlOverridesJson>'
```

Sets the filters server-side, reloads, downloads XLSX to `~/Downloads/PULL_<slug>.xlsx`,
and re-signs-in automatically if Pistil bounces the session to the login page.

```sh
# July, New York, all categories
node pull.js NY_all_1mo store_rank a9dd9a81-12fd-497d-ad27-c19a1cf0c946 \
  c847fe85-edc2-4b05-9905-4bcc8809ba7d \
  '{"ctrl_STATE_CODE":"NY","ctrl_PISTIL_CATEGORY_ID":"","ctrl_DATE_RANGE":"min:prior-month-1,max:prior-month-1"}'
```

Workbooks (the saved-filter GUID is shared): store_rank
`a9dd9a81-12fd-497d-ad27-c19a1cf0c946` · brand_rank `a1e9a7d9-03d5-4a6b-9f21-358d2eb714b3`
· product_rank `0360a1ba-ee41-4da7-80ec-3cab0777db8e` · territory_rank
`a045b250-2b12-46f2-a311-187e1b0dd4a1` (exports headers only — dashboard element).

Date ranges — always **nested**, so momentum can difference them into the preceding
period, and always excluding the partial current month:

| Window | `ctrl_DATE_RANGE` |
|---|---|
| last full month | `min:prior-month-1,max:prior-month-1` |
| last 2 months | `min:prior-month-2,max:prior-month-1` |
| last 3 months | `min:prior-month-3,max:prior-month-1` |

Categories: `ctrl_PISTIL_CATEGORY_ID` is **numeric** — Flower `110`, Prerolls `120`.

## Verify (do not skip)

```sh
python verify.py            # all ~/Downloads/PULL_*.xlsx
```

Prints each file's **measured** window length, rows, total and $/day. A good pull agrees
with its neighbouring windows to a few percent.

## Traps

1. **The on-screen filter is not what the export uses.** Exports honour the server-side
   saved control state. Seen: UI showed New York, saved state was `MI`, XLSX came out
   Michigan. `pull.js` sets the saved state directly to avoid this.
2. **Partial renders produce plausible but short files.** Downloading before Sigma
   finishes yields a normal-looking, badly incomplete export — brand 1mo once came out
   $1.44M/day vs the correct $3.94M/day. Hence the 75s settle and the cross-check.
3. **Invalid control values are ignored, not rejected.** Category *names* return the full
   unfiltered dataset with no error; unknown numeric ids return 0 rows.
4. **Browsing mutates saved filters** (`isAutoSaving:true`), and this is a shared
   account — leave filters in a sane state when you finish.

## Retention

~90 days. There is no 180-day data and never was; the longest window ever obtained is 93
days. To see further back, stitch older snapshots: a 91-day window pulled today plus a
93-day window pulled in June reaches to late March.

## Never mix cuts

`build_store_rank.py` / `build_prospects.py` default to the newest three
`~/Downloads/store_rank_*.xlsx`. A Flower+Preroll export sitting in that folder gets
picked up alongside all-category ones and silently produces nonsense — F+PR is ~55% of
the market, so momentum comes out wildly wrong with no error.

Two defences:
- Name category-filtered exports so the glob cannot see them, e.g.
  `FPR_ONLY_store_rank_<date>.xlsx`, or pass files explicitly.
- `build_store_rank.py` now refuses to run when the windows' `$/day` differ by more than
  1.35x. Nested windows overlap heavily, so a large spread means different cuts, not
  different periods.

import express from 'express';
import { readFileSync } from 'node:fs';
import * as orgs from './orgs.js';
import * as mailer from './mailer.js';
import { ingest } from './ingest.js';
import Anthropic from '@anthropic-ai/sdk';

const PORT = process.env.PORT || 8080;
const MODEL = process.env.BOT_MODEL || 'claude-sonnet-4-6';
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || '*';

// ----- Rate limiting (best-effort, in-memory per instance) -----
const PER_MIN = Number(process.env.RATE_PER_MIN || 15);
const PER_DAY = Number(process.env.RATE_PER_DAY || 300);
const hits = new Map(); // ip -> { min:[ts...], dayCount, dayStart }

function rateLimited(ip) {
  const t = Date.now();
  let h = hits.get(ip);
  if (!h) { h = { min: [], dayCount: 0, dayStart: t }; hits.set(ip, h); }
  if (t - h.dayStart > 86400000) { h.dayCount = 0; h.dayStart = t; }
  h.min = h.min.filter((x) => t - x < 60000);
  if (h.min.length >= PER_MIN) return 'Too many requests — slow down a moment.';
  if (h.dayCount >= PER_DAY) return 'Daily limit reached for this device.';
  h.min.push(t); h.dayCount += 1;
  return null;
}

// ----- Load the account dataset (synced from the map's index.html at deploy time) -----
const ACCOUNTS = JSON.parse(readFileSync(new URL('./accounts.json', import.meta.url)));

function num(v) { return (v === null || v === undefined || v === '') ? '' : v; }

// Per-door category mix, biggest first: "flower:$19k/Prem/$50.62; vape:$7.5k/Prem/$52.4"
function catStr(d) {
  if (!d.cat) return '';
  return Object.entries(d.cat)
    .sort((a, b) => b[1].v - a[1].v)
    .slice(0, 4)
    .map(([c, x]) => `${c}:$${Math.round(x.v / 1000)}k/${x.t}/$${x.p}`)
    .join('; ');
}
// Which tracked brands this door stocks, with each brand's dollars there. This is
// measured carriage from brand-filtered market pulls, not inference — it stays in
// the CACHED table so the bot can answer carriage questions for any brand without
// the prompt changing per request.
function brandsStr(d) {
  if (!d.br) return '';
  return Object.entries(d.br)
    .sort((a, b) => b[1][0] - a[1][0])
    .map(([b, v]) => `${b}:$${Math.round(v[0] / 1000)}k`)
    .join('; ');
}

function accountTable() {
  const head = 'name | role | off_map_prospect | customer_quality | quality_score | top_categories (cat:$/tier/avg_price) | brands_stocked | market_decile | store_rank | sales_window_usd | sales_30d_usd | momentum_30v90_pct | momentum_vs_market_pct | cod_only_list | days_since_order | hist_rev_usd | city | neighborhood | county | region | rep | poc | phone | license | lat | lng';
  const lines = ACCOUNTS.map((d) => [
    d.n, d.role, d.prospect ? 'yes' : '',
    num(d.qt), num(d.qs), catStr(d), brandsStr(d),
    num(d.dec), num(d.psr), num(d.svol), num(d.svol30),
    num(d.mom), num(d.momr), d.cod ? `${d.cod}x` : '', num(d.days),
    num(d.rev), d.c, d.nb, d.co, d.rg, num(d.rep), num(d.poc), num(d.ph),
    num(d.lic), (d.lat != null ? d.lat.toFixed(4) : ''), (d.lng != null ? d.lng.toFixed(4) : ''),
  ].join(' | '));
  return head + '\n' + lines.join('\n');
}
const TABLE = accountTable();

// ----- What each brand actually sells (product rank, by brand x category) -----
let PROFILES = null;
try { PROFILES = JSON.parse(readFileSync(new URL('./brand_profiles.json', import.meta.url))); }
catch { console.warn('brand_profiles.json not found — brand product profiles disabled'); }

// The rep tells us which brand they are selling. Everything we already know about
// that brand goes in here — appended AFTER the cached block so the big account
// table stays cached across brand switches (prefix match: volatile content last).
function brandContext(brand) {
  if (!brand) {
    return 'NO BRAND SELECTED, and that is fine. The account table below is fully queryable '
      + 'without one: category, price tier, customer quality, decile, store rank, momentum and '
      + 'geography are all per-door facts. Answer the question directly from them. Do NOT ask '
      + 'which brand the rep is selling unless the question is genuinely brand-specific -- who '
      + 'stocks BRAND X, where is the whitespace for BRAND X, does BRAND X fit here. '
      + '"Prem flower stores near Syracuse", "best doors in the Capital District", "who is '
      + 'accelerating on vapes" are complete questions. Answer them.';
  }
  const p = PROFILES && PROFILES[brand];
  const carried = ACCOUNTS.filter((d) => d.br && d.br[brand]);
  const tot = carried.reduce((s, d) => s + d.br[brand][0], 0);

  let s = `THE REP IS SELLING: ${brand}\n`;
  if (p) {
    s += `What ${brand} sells (measured from NY product sales last full month, $${p.vol.toLocaleString()} statewide):\n`;
    for (const c of p.categories.slice(0, 6)) {
      s += `  - ${c.cat}: $${c.vol.toLocaleString()} (${c.share}% of the brand), avg menu price $${c.avg_price}, ${c.units.toLocaleString()} units\n`;
    }
    if (p.top_products?.length) {
      s += `  Top SKUs: ${p.top_products.slice(0, 5).map((x) => x.name).join(' | ')}\n`;
    }
  } else {
    s += `We have NO product data for "${brand}" in the NY product rank. Do not guess what it sells — `
      + `use web_search to find out, and if that is inconclusive ASK the rep what products and price points they carry.\n`;
  }
  if (carried.length) {
    // Spell the carriers out. Asking the model to scan 617 dense rows for one brand
    // is where it goes wrong — a smoke test had it name a door that does not stock
    // the brand. This list is computed, so carriage answers are exact.
    const sorted = carried.slice().sort((a, b) => b.br[brand][0] - a.br[brand][0]);
    const shown = sorted.slice(0, 60);
    s += `\nCarriage: EXACTLY ${carried.length} of the ${ACCOUNTS.length} mapped doors stock ${brand} `
      + `($${Math.round(tot).toLocaleString()} last full month). This is the complete, authoritative list — `
      + `do NOT add a door to it, and do not infer carriage from the category columns:\n`;
    for (const d of shown) {
      s += `  - ${d.n} (${d.c || '?'}) — $${d.br[brand][0].toLocaleString()}\n`;
    }
    if (sorted.length > shown.length) s += `  ...and ${sorted.length - shown.length} more, smallest first.\n`;
    s += `Any door NOT on this list is whitespace for ${brand}.\n`;
  } else {
    s += `\nNo carriage data for ${brand} — we have not pulled a brand-filtered store rank for it, `
      + `so you cannot say who stocks it. Say that plainly rather than inferring from the category columns.\n`;
  }
  return s;
}

// ----- First-party wholesale order history (aggregated from the Nabis line-item export) -----
let ORDERS = null;
try { ORDERS = JSON.parse(readFileSync(new URL('./orders_summary.json', import.meta.url))); }
catch { console.warn('orders_summary.json not found — product-mix knowledge disabled'); }

/* The calling company's OWN sales, if their admin has uploaded any. This is the only
   place "we sell to them" comes from now: the shared table is market data and carries
   no company's customer list. Rendered after the cached block, so one company's
   figures never enter another's cache. */
function salesContext(sales) {
  if (!sales || !sales.accounts || !Object.keys(sales.accounts).length) {
    return "THE REP'S OWN SALES: none uploaded. You do NOT know who this company sells to, "
      + 'how much, or when they last ordered. Treat every door as a prospect, never claim or '
      + 'guess a trading relationship, and if the rep asks about their accounts, say their '
      + 'admin has not uploaded a sales export yet.';
  }
  const rows = Object.entries(sales.accounts)
    .sort((a, b) => (b[1].rev || 0) - (a[1].rev || 0)).slice(0, 400);
  const o = sales.overall || {};
  let s = "THE REP'S OWN SALES (first-party, uploaded by their company - this is who THEY sell to).\n";
  if (o.rev) s += `Overall $${o.rev.toLocaleString()}${o.orders ? ` across ${o.orders} orders` : ''}.\n`;
  if (o.top && o.top.length) s += `Their top products: ${o.top.map((t) => `${t[0]} ($${t[1].toLocaleString()})`).join('; ')}.\n`;
  s += 'PER ACCOUNT (licence | name | $ | orders | last order):\n';
  for (const [lic, a] of rows) {
    s += `${lic} | ${a.name} | $${(a.rev || 0).toLocaleString()} | ${a.orders || '?'} | ${a.last || 'unknown'}\n`;
  }
  s += 'Any door NOT in this list, they have never sold to. Say so plainly rather than guessing.\n';
  return s;
}

/* What the rep currently has on the map. Sent with every message and rendered
   AFTER the cached account block, so a route that changes on every turn never
   invalidates the ~178k-token cache. */
function routeContext(r) {
  if (!r || typeof r !== 'object') return 'CURRENT ROUTE: none — the route builder on the map is empty.';
  const stops = Array.isArray(r.stops) ? r.stops.slice(0, 60) : [];
  if (!stops.length && !r.start && !r.end) return 'CURRENT ROUTE: none — the route builder on the map is empty.';
  let s = 'CURRENT ROUTE ON THE REP\'S MAP (this is live — edit THIS, do not rebuild from scratch unless asked):\n';
  s += `  Start: ${r.start ? String(r.start).slice(0, 200) : 'not set'}\n`;
  s += `  End: ${r.end ? String(r.end).slice(0, 200) : 'not set (open-ended)'}\n`;
  s += `  Travel mode: ${String(r.mode || 'driving').slice(0, 20)}\n`;
  s += stops.length ? '  Stops, in visit order:\n' : '  Stops: none yet\n';
  stops.forEach((st, i) => {
    s += `    ${i + 1}. ${String(st.name || '').slice(0, 120)}`
      + (st.lic ? ` [${String(st.lic).slice(0, 40)}]` : '')
      + (st.city ? ` — ${String(st.city).slice(0, 80)}` : '') + '\n';
  });
  s += 'Refer to these stops by name or license when you change them.\n';
  return s;
}

// ----- Statewide brand-rank intelligence (brand-rank exports) -----
let BRAND = null;
try { BRAND = JSON.parse(readFileSync(new URL('./brand_intel.json', import.meta.url))); }
catch { console.warn('brand_intel.json not found — brand intelligence disabled'); }

function brandText() {
  if (!BRAND) return '';
  let s = 'STATEWIDE BRAND LANDSCAPE (brand rank — every NY licensed brand, whoever the rep sells).\n';
  if (BRAND.top_brands_flower_preroll?.length) {
    s += 'Leading flower/preroll brands (volume, avg price, % of stores stocking): ' +
      BRAND.top_brands_flower_preroll.slice(0, 12).map((b) => `${b.brand} ($${b.vol.toLocaleString()}, $${b.price}, ${b.dist_pct}%)`).join('; ') + '.\n';
  }
  if (BRAND.top_brands_all?.length) {
    s += 'Top brands overall: ' + BRAND.top_brands_all.slice(0, 10).map((b) => `${b.brand} (#${b.rank}, ${b.dist_pct}%)`).join('; ') + '.\n';
  }
  s += 'Use this to place the rep\'s brand against its real competitive set: find the brands in the SAME '
    + 'categories at a SIMILAR price point, and treat those as who the rep is displacing on shelf. '
    + 'Distribution % is the share of NY stores stocking that brand — a useful ceiling for what good coverage looks like.\n';
  return s;
}
const BRAND_TEXT = brandText();

function pct(fam) {
  const tot = Object.values(fam).reduce((a, b) => a + b, 0) || 1;
  return Object.entries(fam).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${Math.round(v / tot * 100)}%`).join(', ');
}
function moStr(mo) { return mo.map(([m, v]) => `${m}=$${v.toLocaleString()}`).join(' '); }

function ordersText() {
  if (!ORDERS) return '';
  const o = ORDERS.overall;
  let s = `OUR OWN WHOLESALE ORDER HISTORY — actual orders we shipped, ${ORDERS.date_range[0]} to ${ORDERS.date_range[1]}.\n`;
  s += `This is first-party order data for the rep's own company, NOT the market-wide baseline — it covers only accounts we sell to. `;
  s += `Use it for reorder cadence, product-mix and change-over-time questions, and to spot accounts going quiet. `;
  s += `Dollars are wholesale line-item subtotals; cancelled/rejected orders excluded. Match accounts by license.\n\n`;
  s += `OVERALL: $${o.rev.toLocaleString()} across ${o.orders} orders.\n`;
  s += `Category mix: ${pct(o.fam)}.\nMonthly $: ${moStr(o.mo)}.\n`;
  s += `Top products: ${o.top_products.map((t) => `${t[0]} ($${t[1].toLocaleString()})`).join('; ')}.\n`;
  s += `Top strains: ${o.top_strains.map((t) => `${t[0]} ($${t[1].toLocaleString()})`).join('; ')}.\n\n`;
  s += `PER-ACCOUNT (license | name: total / orders / last order | category mix | monthly $ | top SKUs):\n`;
  for (const [lic, a] of Object.entries(ORDERS.accounts)) {
    s += `${lic} | ${a.name}: $${a.rev.toLocaleString()} / ${a.orders} ord / last ${a.last} | mix: ${pct(a.fam)} | monthly: ${moStr(a.mo)} | top: ${a.top.map((t) => t[0]).join('; ')}\n`;
  }
  return s;
}
const ORDERS_TEXT = ordersText();

const TODAY = process.env.TODAY || new Date().toISOString().slice(0, 10);

const INSTRUCTIONS = `You are Retail Intel NY — a field-sales strategist for reps selling cannabis products into licensed New York dispensaries. Today is ${TODAY}.

You are BRAND-AGNOSTIC. Reps who use you sell different brands. If the rep has told you which one, it appears in a "THE REP IS SELLING" block below, and anything you then say about that brand's product fit must be grounded in its real product mix rather than assumptions.

ANSWER WHAT WAS ASKED. Most questions do not need a brand at all. Every door in the table carries its own category mix, price tier, customer quality, decile, store rank, momentum and location, so a question phrased in those terms is already complete -- "prem flower stores near Syracuse", "top vape doors in Brooklyn", "who is cooling in the Hudson Valley". Answer it from the data. Ask which brand only when the answer genuinely depends on one: carriage, whitespace, or whether a specific brand fits a specific door. Never open with a clarifying question that the account table already answers.

You have the full live account list below (the same data shown on the field map). Use ONLY this data plus web_search — never invent stores, numbers, or contacts. If something isn't in the data, say so.

FIELD MEANINGS
- role: the account's status in the rep's own pipeline (an "Active"/"Slipping"/"Lapsed" account is one that orders, or used to order, from the rep's own company; "New Prospect" is a licensed dispensary not yet sold to). Treat these as relationship status, not as a brand judgement.
- off_map_prospect = "yes": a high-performing store the rep does NOT currently serve, surfaced from the store rank. Prime targets — cite rank, sales and momentum, and pursue the accelerating ones first.
- customer_quality (H/M/L) + quality_score (0-100): how good a customer this door is overall — 50% sales volume, 30% average price, 20% momentum, ranked against every other NY door. H = a door worth winning.
- top_categories: what the door actually sells, biggest first, as "category:$volume/price_tier/avg_price". The price tier (Value / Mid / Prem) is where THIS door sits on price within THAT category across NY. A door that is Prem on flower sells expensive flower; it says nothing about its edibles.
- brands_stocked: which tracked brands the door already stocks and how much of each it sold last full month. MEASURED, not inferred. If the rep's brand is absent from a door's list, that door is whitespace for them.
- market_decile: market-quality decile, 1 = best, 10 = weakest. Lower is better.
- store_rank: statewide performance rank (1 = best-performing store in NY). sales_window_usd / sales_30d_usd are estimated sell-through.
- momentum_vs_market_pct (MOST ACTIONABLE): the store's momentum minus the market median. The whole NY market grows, so judge relative: positive = accelerating faster than the typical store (push, secure shelf space); negative = cooling relative to the market (defend, investigate).
- cod_only_list ("2x", "3x"): NY OCM publishes a list of retail licensees that other licensees may sell to on a CASH-ON-DELIVERY basis only — no credit terms. The number is how many of the last 3 published editions the door appeared on. This is a terms-and-collections fact, NOT a reason to skip the door: plenty of high-volume stores are on it. Say it plainly whenever you recommend a COD door ("sell it COD, no terms"), and treat 3x — on every edition — as a real AR risk worth raising with the rep before they extend anything. A door with no value here simply was not on the published list.
- days_since_order / hist_rev_usd: BLANK for every door in the table below. A trading
  relationship comes ONLY from the rep's own uploaded sales, given separately above.
  NEVER state, imply or estimate a relationship or a revenue figure that is not literally in
  the row. A BLANK hist_rev_usd means the door has NEVER ordered from us -- it is not an
  unknown to be filled in, and a "New Prospect"/"Priority T1-T3" role means exactly that, no
  matter how big the door is in market terms. Getting this wrong sends a rep into a prospect
  talking like it is an established account. If you are about to describe how a door buys
  from us, re-read its role and hist_rev_usd first and quote them as they are.
- region/county/city/neighborhood: geography for routing.

TARGETING WITHOUT A BRAND (the common case)
Read the request as a filter over the table and apply it literally:
- a category word ("flower", "prerolls", "vapes", "edibles") -> the door's top_categories entry for it
- "prem" / "premium" / "high-end" -> price_tier Prem in THAT category; "value" / "cheap" -> Value
- "near <place>" / "around <place>" -> city, county, region, or lat/lng proximity to it
- "best" / "top" with nothing else -> customer_quality H, then store_rank, then momentum
Rank the matches by dollars in the category asked about -- not by the door's overall size --
and name the number you ranked on. A door that is Prem on flower but moves $3k of it is a
price accident, not a premium flower door; say so rather than listing it high.

MATCHING A SPECIFIC BRAND TO A DOOR (only when a brand is in play)
1. Start from what the rep's brand actually sells — its categories, its share of each, and its average price point (given in the "THE REP IS SELLING" block).
2. Find doors that move volume in those SAME categories. A tincture brand belongs in doors that sell tinctures, not in the biggest flower doors.
3. Match the price tier. A brand whose average menu price is high belongs in Prem-tier doors for that category; a value brand belongs in Value-tier doors. Say which tier you are matching and why.
4. Prefer high customer_quality and positive momentum_vs_market_pct.
5. Whitespace (door does not stock the brand) is a pitch; existing carriage is a grow-or-defend play — and a door that stocks it while cooling is at risk of losing the shelf.

IF YOU DO NOT KNOW THE BRAND
If the rep names a brand with no product data in the block below, do NOT guess what it sells. Use web_search to find its actual product line and price points, say what you found and that it came from the web, and if the search is inconclusive ASK the rep directly: what categories do you sell, and at what retail price? Never infer a brand's products from its name.

WEB SEARCH
- Use it to learn an unfamiliar brand's product line, positioning, or retail pricing, and for genuinely current facts (a new NY licensee, a recent launch).
- Do NOT use it for anything the account data already answers — store performance, carriage, rank, and momentum all come from the data below, which is more accurate than anything on the web.
- Always distinguish what came from the data from what came from the web.

HOW TO ANSWER
- Be concise and specific. Lead with the answer, then the supporting accounts. Prefer tight tables or short bulleted lists over prose.
- Always name real accounts from the data, with city/neighborhood so the rep knows where it is.
- Cite real numbers when you reference money, rank, or recency.

KEEP IT FOCUSED (size control)
- If a good answer needs more than ~15 accounts, do NOT dump a long list. Show the ~10-15 most relevant, state the total, and prompt the rep to narrow it — e.g. "That's 60+ matches. Want to narrow by region, quality tier, category, or carriage?"
- If a routing request is very complex, ask the rep to constrain it first — a region, a single day, or a stop cap (8-10) — and suggest how.

ROUTING
You both BUILD routes and EDIT the one already on the rep's map. The live route is given
to you above under CURRENT ROUTE. When the rep says "drop the Utica stop", "add two
premium flower doors near Albany", "start me from Long Beach", "flip the order", "make it
a round trip" — act on that route. Do not silently rebuild it from scratch; that throws
away stops they chose.

- A start helps but is not a gate. If one is already set, use it. If not, and the request
  implies one ("from Brooklyn", "leaving Albany"), set it yourself with a start op. Only
  ask when there is genuinely no way to tell where they begin.
- An END IS OPTIONAL — an open-ended day is a normal thing to plan. Never refuse to build
  a route because no end was given.
- Order stops geographically from start toward end: group by county/neighbourhood and do
  not backtrack. Say in one or two lines why the order is what it is.
- Default to 6-10 stops for a full day. Say plainly when a day is too long to be real.

TO CHANGE THE ROUTE, emit a fenced block tagged "route-ops" holding a JSON array of
operations, applied in order. Stops are named by exact license (preferred) or exact name:
\`\`\`route-ops
[{"op":"remove","ids":["OCM-CAURD-24-000076"]},
 {"op":"add","ids":["OCM-CAURD-23-000033"]},
 {"op":"start","address":"Long Beach, NY"},
 {"op":"optimize"}]
\`\`\`
The operations available:
- {"op":"add","ids":[...]}                 append stops to the end
- {"op":"insert","after":"<id>","ids":[…]} insert straight after an existing stop
- {"op":"remove","ids":[...]}              drop stops
- {"op":"move","id":"<id>","to":3}         move a stop to position 3 (1-based)
- {"op":"set","ids":[...]}                 replace every stop, in this order
- {"op":"start"|"end","address":"…"}       set start/end by address (geocoded), or pass
                                           "lat"/"lng"/"label" if you have coordinates
- {"op":"end","same_as_start":true}        make it a round trip
- {"op":"mode","mode":"driving|transit|bicycling|walking"}
- {"op":"optimize"}                        re-order the stops for the shortest path
- {"op":"clear"}                           empty the route
Emit route-ops ONLY for an actual change to the route, and put every change for one
request in a SINGLE block. The map applies it immediately and shows the rep an Undo.

For a brand-new route where nothing is worth preserving you may still use the older
"route" block, which replaces the stop list wholesale:
\`\`\`route
OCM-RETL-25-000306
OCM-CAURD-24-000177
\`\`\`
Prefer route-ops whenever a route already exists.`;

const anthropic = new Anthropic();

// A web-search turn runs a server-side loop. If it hits the server's iteration cap
// the turn comes back with stop_reason "pause_turn" and a PARTIAL answer — resend the
// assistant turn to let it finish. Without this the rep silently gets a truncated
// reply mid-sentence, with no error anywhere.
const MAX_RESUMES = 3;
async function runWithSearch(params) {
  let response = await anthropic.messages.create(params);
  const messages = [...params.messages];
  for (let i = 0; i < MAX_RESUMES && response.stop_reason === 'pause_turn'; i++) {
    messages.push({ role: 'assistant', content: response.content });
    response = await anthropic.messages.create({ ...params, messages });
  }
  if (response.stop_reason === 'pause_turn') {
    console.warn('web search still paused after', MAX_RESUMES, 'resumes — returning partial answer');
  }
  return response;
}

// The response interleaves text with server_tool_use / web_search_tool_result blocks;
// only the text blocks are for the rep.
function extractText(response) {
  return response.content.filter((b) => b.type === 'text').map((b) => b.text).join('').trim();
}

// A turn can come back HTTP 200 and still be useless to the rep: it can hit the
// max_tokens ceiling (answer cut off, or — when the cap lands inside a tool-use
// block — no text at all), or be refused. Both used to render as a blank/half
// bubble with a clean 200 in the logs, which is why this was invisible for weeks.
// Fail loud instead of handing back a silent stub.
function checkAnswer(response, text) {
  if (response.stop_reason === 'refusal') {
    return 'The model declined that request' +
      (response.stop_details?.explanation ? ': ' + response.stop_details.explanation : '.');
  }
  if (response.stop_reason === 'max_tokens') {
    return text
      ? null // truncated but usable — flagged to the rep via `truncated` below
      : 'The answer hit the length limit before any text came back. Narrow the question (one borough, or fewer stores) and try again.';
  }
  if (!text) {
    return 'The model returned no text (stop_reason: ' + response.stop_reason + '). Try rephrasing the question.';
  }
  return null;
}

// Web search runs on Anthropic's side and reports failure as a 200 with an error
// object inside the result block — nothing throws. Surface it so a silently
// un-grounded answer doesn't read as a researched one.
function searchErrors(response) {
  return response.content
    .filter((b) => b.type === 'web_search_tool_result' && b.content && !Array.isArray(b.content))
    .map((b) => b.content.error_code)
    .filter(Boolean);
}

const app = express();
// 20mb, not 2: a sales export is posted as text in the body and a year of line
// items from a real wholesaler runs to several megabytes.
app.use(express.json({ limit: '20mb' }));
app.set('trust proxy', true);

app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Access-Control-Max-Age', '86400');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.get('/', (_req, res) => res.json({ ok: true, model: MODEL, accounts: ACCOUNTS.length, order_accounts: ORDERS ? Object.keys(ORDERS.accounts).length : 0, order_rev: ORDERS ? ORDERS.overall.rev : 0 }));

app.post('/chat', async (req, res) => {
  try {
    const ip = (req.headers['x-forwarded-for'] || req.ip || 'x').toString().split(',')[0].trim();
    const limited = rateLimited(ip);
    if (limited) return res.status(429).json({ error: limited });

    let messages = req.body?.messages;
    if (!Array.isArray(messages) || !messages.length) {
      return res.status(400).json({ error: 'Expected { messages: [{role, content}] }.' });
    }
    // Sanitize + cap history
    messages = messages
      .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
      .map((m) => ({ role: m.role, content: m.content.slice(0, 6000) }))
      .slice(-16);
    if (!messages.length || messages[messages.length - 1].role !== 'user') {
      return res.status(400).json({ error: 'Last message must be from the user.' });
    }

    // Whose data is this? A signed-in caller gets their own company's figures; anyone
    // else gets the market baseline and no relationship layer at all.
    req.__orgSales = null;
    if (req.body?.email && req.body?.code) {
      try { req.__orgSales = await orgs.getSales({ email: req.body.email, code: req.body.code }); }
      catch (e) { console.warn('sales lookup failed for chat:', e.message); }
    }

    // Which brand is the rep selling? Sent by the map's "What you're selling" selector.
    const rawBrand = typeof req.body?.brand === 'string' ? req.body.brand.slice(0, 80).trim() : '';
    // Only honour a brand we actually hold data for, so a junk value can't smuggle
    // arbitrary text into the prompt.
    const brand = (PROFILES && PROFILES[rawBrand]) || ACCOUNTS.some((d) => d.br && d.br[rawBrand])
      ? rawBrand : '';

    const params = {
      model: MODEL,
      // 2000 was far too low: a "top 15 doors + route" answer hit the ceiling and
      // came back with ZERO text (the cap landed inside a tool-use block), so the
      // rep got a blank bubble and the logs showed a clean 200. Reproduced
      // 2026-08-26. Keep this well clear of a full table + route block.
      max_tokens: 16000,
      // Web search lets the bot learn an unfamiliar brand's product line and pricing.
      // Everything about store performance still comes from the cached data below.
      tools: [{ type: 'web_search_20260209', name: 'web_search', max_uses: 4 }],
      system: [
        { type: 'text', text: INSTRUCTIONS },
        // Cache the big data block so follow-up questions are cheap. Caching is a
        // prefix match, so the per-request brand block MUST come after this one —
        // putting it before would invalidate the cache on every brand switch.
        // 1h TTL, not the default 5m: reps ask a question every few minutes, so at
        // 5m nearly every question paid a fresh ~178k-token cache WRITE (1.25x)
        // instead of a read (0.1x) — ~$0.67 vs ~$0.05 per cold question.
        { type: 'text', text: 'ACCOUNT DATA (' + ACCOUNTS.length + ' doors):\n' + TABLE + (BRAND_TEXT ? '\n\n' + BRAND_TEXT : ''), cache_control: { type: 'ephemeral', ttl: '1h' } },
        { type: 'text', text: brandContext(brand) },
        { type: 'text', text: routeContext(req.body?.route) },
        { type: 'text', text: salesContext(req.__orgSales) },
      ],
      messages,
    };

    const response = await runWithSearch(params);
    const text = extractText(response);

    const failure = checkAnswer(response, text);
    if (failure) {
      console.error('unusable answer:', response.stop_reason, '| output_tokens:', response.usage?.output_tokens, '|', failure);
      return res.status(502).json({ error: failure });
    }
    const searchFailed = searchErrors(response);
    if (searchFailed.length) console.warn('web search failed:', searchFailed.join(', '));

    res.json({
      reply: text,
      brand: brand || null,
      // The rep needs to know the answer stopped early rather than finished.
      truncated: response.stop_reason === 'max_tokens' || undefined,
      search_error: searchFailed.length ? searchFailed.join(', ') : undefined,
      usage: response.usage,
    });
  } catch (err) {
    console.error('chat error:', err);
    if (err instanceof Anthropic.APIError) {
      return res.status(err.status || 502).json({ error: err.message });
    }
    res.status(500).json({ error: String(err.message || err) });
  }
});

// ===== Route + visit logging =====
// Central log of every route a rep sends to Google Maps / exports, and a visit
// record per customer on export. Routes are emitted to Cloud Logging (searchable
// in GCP) and kept in a best-effort in-memory ring buffer for quick review via
// GET /logs. If HUBSPOT_TOKEN is set, exported visits are also written to HubSpot
// as a "last contacted"-style note on the matching company (by license number).
const RING_MAX = 500;
const routeRing = [];
const visitRing = [];
const HUBSPOT_TOKEN = process.env.HUBSPOT_TOKEN || '';

function pushRing(ring, item) { ring.unshift(item); if (ring.length > RING_MAX) ring.length = RING_MAX; }

app.post('/route-log', (req, res) => {
  const p = req.body || {};
  if (!Array.isArray(p.stops) || !p.stops.length) return res.status(400).json({ error: 'no stops' });
  const rec = {
    rep: String(p.rep || '').slice(0, 80),
    channel: String(p.channel || 'gmaps').slice(0, 40),
    ts: typeof p.ts === 'string' ? p.ts.slice(0, 40) : new Date().toISOString(),
    start: p.start ? String(p.start).slice(0, 200) : null,
    end: p.end ? String(p.end).slice(0, 200) : null,
    miles: Number(p.miles) || 0,
    stops: p.stops.slice(0, 50).map((s) => ({
      order: s.order, name: String(s.name || '').slice(0, 120), lic: String(s.lic || '').slice(0, 40),
      city: String(s.city || '').slice(0, 80), role: String(s.role || '').slice(0, 40),
    })),
    mapsUrl: String(p.mapsUrl || '').slice(0, 2000),
  };
  pushRing(routeRing, rec);
  console.log('ROUTE_LOG ' + JSON.stringify(rec));
  // Also persist against the company when the caller is signed in, so an admin can see
  // usage after this instance is gone.
  if (p.email && p.code) {
    orgs.logRoute({ email: p.email, code: p.code }, {
      channel: rec.channel, miles: rec.miles, stops: rec.stops.length,
      start: rec.start, end: rec.end,
      names: rec.stops.slice(0, 12).map((s) => s.name),
    }).catch((e) => console.warn('org route log failed:', e.message));
  }
  res.json({ ok: true });
});

app.post('/visit-log', async (req, res) => {
  const p = req.body || {};
  if (!Array.isArray(p.visits) || !p.visits.length) return res.status(400).json({ error: 'no visits' });
  const rec = {
    rep: String(p.rep || '').slice(0, 80),
    date: String(p.date || '').slice(0, 10),
    ts: typeof p.ts === 'string' ? p.ts.slice(0, 40) : new Date().toISOString(),
    source: String(p.source || 'route_export').slice(0, 40),
    visits: p.visits.slice(0, 50).map((v) => ({
      lic: String(v.lic || '').slice(0, 40), name: String(v.name || '').slice(0, 120),
      city: String(v.city || '').slice(0, 80), order: v.order,
    })),
  };
  pushRing(visitRing, rec);
  console.log('VISIT_LOG ' + JSON.stringify(rec));
  let hubspot = 'skipped (no HUBSPOT_TOKEN)';
  if (HUBSPOT_TOKEN) {
    try { hubspot = await logVisitsToHubspot(rec); }
    catch (e) { hubspot = 'error: ' + (e.message || e); console.error('hubspot visit-log error:', e); }
  }
  res.json({ ok: true, hubspot });
});

/* ----- Companies, their people, and their own sales data -----
   Thin HTTP over orgs.js. Every route reports the store's own error status so a
   misconfigured bucket says so instead of looking like a bad password. */
function orgErr(res, e) {
  const status = e.status || 500;
  if (status >= 500) console.error('org error:', e);
  res.status(status).json({ error: e.message || 'the company store failed' });
}
const creds = (b) => ({ email: b.email, code: b.code });

app.get('/auth/status', (req, res) => res.json({
  configured: orgs.configured(), email: mailer.canSend(),
}));

// Step one for a new customer: the company, and the admin who runs it.
app.post('/org/create', async (req, res) => {
  const b = req.body || {};
  try {
    const out = await orgs.createOrg(b);
    res.json({ org: { id: out.org.id, name: out.org.name }, ...out.user });
  } catch (e) { orgErr(res, e); }
});

app.post('/auth/login', async (req, res) => {
  try { res.json(await orgs.login(req.body || {})); } catch (e) { orgErr(res, e); }
});

// The link out of an invite email. Signs them in; no code to type.
app.post('/auth/redeem', async (req, res) => {
  try { res.json(await orgs.redeem(req.body || {})); } catch (e) { orgErr(res, e); }
});

app.get('/org/users', async (req, res) => {
  try { res.json(await orgs.listUsers({ email: req.query.email, code: req.query.code })); }
  catch (e) { orgErr(res, e); }
});

app.post('/org/users/add', async (req, res) => {
  const b = req.body || {};
  try {
    const out = await orgs.addUser(b);
    let mailed = false, mailError = null;
    if (mailer.canSend()) {
      // Best effort: a mail outage must not cost the admin the invite itself.
      try {
        await mailer.sendInvite({
          to: out.user.email, name: out.user.name, orgName: out.org.name,
          invitedBy: out.invitedBy, token: out.invite, isAdmin: out.user.role === 'admin',
        });
        mailed = true;
      } catch (err) { mailError = err.message; console.error('invite email failed:', err); }
    }
    res.json({ user: out.user, reinvited: out.reinvited, mailed, mailError,
               link: (process.env.APP_URL || 'https://zelidav.github.io/jbd-sales-map/') + '#invite=' + out.invite });
  } catch (e) { orgErr(res, e); }
});

app.post('/me/save', async (req, res) => {
  try { res.json(await orgs.save(req.body || {})); } catch (e) { orgErr(res, e); }
});

app.get('/org/stats', async (req, res) => {
  try { res.json(await orgs.stats({ email: req.query.email, code: req.query.code })); }
  catch (e) { orgErr(res, e); }
});

app.post('/org/brands', async (req, res) => {
  try { res.json(await orgs.setBrands(req.body || {})); } catch (e) { orgErr(res, e); }
});

app.post('/me/tutorial', async (req, res) => {
  try { res.json(await orgs.setTutorial(req.body || {})); } catch (e) { orgErr(res, e); }
});

/* ----- A company's own sales export -----
   Claude reads the header to work out the columns and matches the leftover store
   names to licences; the arithmetic is done here in code. Admin only: this decides
   what every rep in the company sees as "ours". */
app.post('/org/sales', async (req, res) => {
  const b = req.body || {};
  try {
    // Admin BEFORE the ingest, not after: this runs two Claude calls and rewrites what
    // every rep in the company sees, so a member must not be able to start it and only
    // be refused at the save.
    await orgs.requireAdmin(creds(b));
    // Text for CSV/TSV, base64 for a workbook. Most exports are workbooks, and telling
    // an admin to re-save as CSV first is the step that stops them uploading at all.
    const isBook = typeof b.xlsx === 'string' && b.xlsx.length > 0;
    if (!isBook && (typeof b.csv !== 'string' || !b.csv.trim())) {
      return res.status(400).json({ error: 'send the file as text in "csv" or base64 in "xlsx"' });
    }
    if ((isBook ? b.xlsx.length : b.csv.length) > 16e6) {
      return res.status(413).json({ error: 'that file is too big — export a narrower date range' });
    }
    const doors = ACCOUNTS.map((d) => ({ n: d.n, c: d.c, lic: d.lic }));
    const out = await ingest(isBook ? { workbook: Buffer.from(b.xlsx, 'base64') } : b.csv, doors);
    const saved = await orgs.putSales(creds(b), { ...out, filename: String(b.filename || '').slice(0, 200) });
    res.json({
      ok: true, filename: saved.filename, uploadedAt: saved.uploadedAt,
      rows: out.rows, matched: out.matched, unmatchedRows: out.unmatchedRows, sheet: out.sheet,
      unmatched: out.unmatched, columns: out.columns, overall: out.overall,
      accounts: out.accounts,
    });
  } catch (e) { orgErr(res, e); }
});

app.get('/org/sales', async (req, res) => {
  try { res.json(await orgs.getSales({ email: req.query.email, code: req.query.code }) || {}); }
  catch (e) { orgErr(res, e); }
});

app.delete('/org/sales', async (req, res) => {
  try { res.json(await orgs.clearSales(req.body || {})); } catch (e) { orgErr(res, e); }
});

// Best-effort review endpoint (this instance only — Cloud Logging is the source of truth).
app.get('/logs', (req, res) => {
  const n = Math.min(Number(req.query.n) || 100, RING_MAX);
  res.json({ routes: routeRing.slice(0, n), visits: visitRing.slice(0, n), note: 'In-memory per-instance buffer; full history is in Cloud Logging (filter ROUTE_LOG / VISIT_LOG).' });
});

async function hs(path, method, body) {
  const r = await fetch('https://api.hubapi.com' + path, {
    method,
    headers: { Authorization: 'Bearer ' + HUBSPOT_TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`HubSpot ${method} ${path} -> ${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// For each visited company, stamp a "Last sales visit" note (matched by license number).
async function logVisitsToHubspot(rec) {
  let matched = 0, missed = 0;
  for (const v of rec.visits) {
    if (!v.lic) { missed++; continue; }
    const found = await hs('/crm/v3/objects/companies/search', 'POST', {
      filterGroups: [{ filters: [{ propertyName: 'license_number', operator: 'EQ', value: v.lic }] }],
      properties: ['name'], limit: 1,
    }).catch(() => null);
    const company = found?.results?.[0];
    if (!company) { missed++; continue; }
    const note = await hs('/crm/v3/objects/notes', 'POST', {
      properties: {
        hs_timestamp: rec.ts,
        hs_note_body: `Field sales visit${rec.rep ? ' by ' + rec.rep : ''} on ${rec.date} (route stop #${v.order}). Logged from the field map.`,
      },
    });
    await hs(`/crm/v3/objects/notes/${note.id}/associations/companies/${company.id}/note_to_company`, 'PUT');
    matched++;
  }
  return `companies updated: ${matched}, unmatched: ${missed}`;
}

app.listen(PORT, () => {
  console.log(`Retail Intel bot on :${PORT} (model=${MODEL}, accounts=${ACCOUNTS.length}, brand_profiles=${PROFILES ? Object.keys(PROFILES).length : 0}, hubspot=${HUBSPOT_TOKEN ? 'on' : 'off'})`);
});

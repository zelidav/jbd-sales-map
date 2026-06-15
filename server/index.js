import express from 'express';
import { readFileSync } from 'node:fs';
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
function accountTable() {
  const head = 'name | role | dragonfly_status | jb_tier | pistil_decile | days_since_order | hist_rev_usd | city | neighborhood | county | region | rep | poc | phone | license | lat | lng';
  const lines = ACCOUNTS.map((d) => [
    d.n, d.role, num(d.ds), num(d.tier), num(d.dec), num(d.days),
    num(d.rev), d.c, d.nb, d.co, d.rg, num(d.rep), num(d.poc), num(d.ph),
    num(d.lic), (d.lat != null ? d.lat.toFixed(4) : ''), (d.lng != null ? d.lng.toFixed(4) : ''),
  ].join(' | '));
  return head + '\n' + lines.join('\n');
}
const TABLE = accountTable();

// ----- Order / product-mix knowledge (aggregated from the Dragonfly export) -----
let ORDERS = null;
try { ORDERS = JSON.parse(readFileSync(new URL('./orders_summary.json', import.meta.url))); }
catch { console.warn('orders_summary.json not found — product-mix knowledge disabled'); }

function pct(fam) {
  const tot = Object.values(fam).reduce((a, b) => a + b, 0) || 1;
  return Object.entries(fam).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${Math.round(v / tot * 100)}%`).join(', ');
}
function moStr(mo) { return mo.map(([m, v]) => `${m}=$${v.toLocaleString()}`).join(' '); }

function ordersText() {
  if (!ORDERS) return '';
  const o = ORDERS.overall;
  let s = `DRAGONFLY ORDER / PRODUCT-MIX DATA — actual wholesale orders ${ORDERS.date_range[0]} to ${ORDERS.date_range[1]}.\n`;
  s += `Use this for product-mix and change-over-time questions. Dollars are wholesale line-item subtotals; cancelled/rejected orders excluded. Match accounts by license.\n\n`;
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

const INSTRUCTIONS = `You are the field-sales strategist for Dragonfly Kitchen × Jerome Baker (JB) in New York — a cannabis distribution and glass brand. You advise reps planning real visits to licensed dispensaries. Today is ${TODAY}.

You have the full live account list below (the same data shown on the field map). Use ONLY this data — never invent stores, numbers, or contacts. If something isn't in the data, say so.

FIELD MEANINGS
- role: the account's status in our pipeline.
  - "Dragonfly Active" — currently ordering from Dragonfly.
  - "Dragonfly Slipping" — was ordering, order cadence is dropping (at-risk).
  - "Dragonfly Fallow" — went dark, no recent orders (needs reactivation).
  - "New Prospect" — licensed dispensary we don't yet sell to.
  - "JB Tier 1/2/3" — priority targets for the Jerome Baker glass program (Tier 1 = highest).
- pistil_decile: market-quality ranking from external Pistil data. 1 = TOP decile (best opportunity); 10 = weakest. Lower is better. Blank = unranked.
- days_since_order: days since last Dragonfly order (Dragonfly accounts only).
- hist_rev_usd: historical Dragonfly revenue with this account.
- region/county/city/neighborhood: geography for routing.

PRODUCT MIX & TRENDS
- A separate block below has actual Dragonfly wholesale order data (by account, by product category, by month). Product families: Flower, Vape, Preroll, Infused PR, Edibles, Other.
- Use it for any question about what an account buys, product mix, reorder cadence, month-over-month trends, what's growing/declining, or what to upsell/reintroduce. Cite real $ and percentages.
- When an account in the order data isn't in the pipeline list (or vice-versa), reconcile by license and say what you see.

HOW TO ANSWER
- Be concise and specific. Lead with the answer, then the supporting accounts. Prefer tight tables or short bulleted lists over prose.
- When ranking or prioritizing, weigh: at-risk revenue (Slipping/Fallow with high hist_rev or recent days), top deciles (1-3), JB tiers, and geographic density.
- Always name real accounts from the data. Include city/neighborhood so the rep knows where it is.
- When you reference money or recency, cite the actual numbers from the data.

ROUTING
- When the user asks for a route, a day plan, or "what should I hit", build an efficient geographic order (group by neighborhood/county, minimize backtracking) and explain the logic in 1-2 lines.
- THEN emit the stops as a fenced code block tagged "route", one account per line using its exact license (preferred) or exact name, in visit order. The map will plot it and build a Google Maps directions link. Example:
\`\`\`route
OCM-RETL-25-000306
OCM-CAURD-24-000177
\`\`\`
- Only emit a route block when a route/day-plan is actually requested. Keep routes to a sensible number of stops (default 6-10 unless asked otherwise).`;

const anthropic = new Anthropic();
const app = express();
app.use(express.json({ limit: '2mb' }));
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

    const response = await anthropic.messages.create({
      model: MODEL,
      max_tokens: 2000,
      system: [
        { type: 'text', text: INSTRUCTIONS },
        // Cache the big data block so follow-up questions are cheap.
        { type: 'text', text: 'ACCOUNT DATA (' + ACCOUNTS.length + ' doors):\n' + TABLE + (ORDERS_TEXT ? '\n\n' + ORDERS_TEXT : ''), cache_control: { type: 'ephemeral' } },
      ],
      messages,
    });

    const text = response.content.filter((b) => b.type === 'text').map((b) => b.text).join('').trim();
    res.json({
      reply: text,
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

app.listen(PORT, () => {
  console.log(`JBD sales bot on :${PORT} (model=${MODEL}, accounts=${ACCOUNTS.length})`);
});

/* The two cuts the map's per-door layers need, on top of the plain rankings.
 *
 *   1. stores x category   -> what each door moves in Flower / Prerolls / Vapes
 *   2. stores x brand      -> which doors stock each tracked brand (carriage)
 *
 * Only the brands the new subscription still reaches are pulled; the rest keep the
 * carriage from the previous pull, because a brand missing from a Flower/Preroll/Vape
 * subscription has not lost its shelf space, we have just lost sight of it.
 *
 * Usage: node next_pull_extra.mjs [outputDir]
 */
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || path.join(process.env.USERPROFILE || '.', 'Downloads', 'pistil-next');
const API = 'https://api.pistildata.com';
const RANKINGS = '77593c14-e360-409d-8f52-9e9e89fa8385';
const STATE = 'NY';
const PAGE = 500;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getToken() {
  const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null, protocolTimeout: 180000 });
  const p = await b.newPage();
  let token = null;
  p.on('request', (r) => { const a = r.headers()['authorization']; if (a && /pistildata/.test(r.url()) && !token) token = a; });
  await p.goto('https://app.pistildata.com/market-intelligence/rankings', { waitUntil: 'domcontentloaded', timeout: 90000 });
  for (let i = 0; i < 30 && !token; i++) await sleep(1000);
  await p.close(); b.disconnect();
  if (!token) throw new Error('no Authorization header seen — is the browser still signed in?');
  return token;
}

async function query(token, body) {
  const r = await fetch(`${API}/api/dashboards/${RANKINGS}/widgets/widget-rankings-table/query`, {
    method: 'POST',
    headers: { authorization: token, 'content-type': 'application/json',
               origin: 'https://app.pistildata.com', referer: 'https://app.pistildata.com/' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`);
  return r.json();
}

async function stores(token, filters) {
  const rows = [];
  for (let page = 1; page <= 40; page++) {
    const j = await query(token, {
      filters: { 'sales_estimates.state': [STATE], ...filters },
      dateRange: 'last 30 days',
      selectedDimension: 'sales_estimates.store_name',
      compareDateRange: 'from 60 days ago to 31 days ago',
      pagination: { pageNumber: page, pageSize: PAGE },
      timeZone: 'America/New_York',
    });
    const d = j.data || [];
    rows.push(...d);
    if (d.length < PAGE) break;
    await sleep(200);
  }
  return rows;
}

function save(name, rows) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(rows, null, 1));
  return rows.length;
}

async function main() {
  const token = await getToken();
  console.log('token ok\n');

  console.log('stores x category:');
  for (const cat of ['Flower', 'Prerolls', 'Vapes']) {
    try {
      const rows = await stores(token, { 'sales_estimates.category': [cat] });
      console.log(`  ${cat.padEnd(9)} ${save(`ny_stores_cat_${cat}.json`, rows)} doors`);
    } catch (e) { console.log(`  ${cat} FAILED: ${e.message.slice(0, 100)}`); }
  }

  // Only brands the new subscription still returns.
  const all = JSON.parse(fs.readFileSync(path.join(OUT, 'ny_brands_30d.json'), 'utf8'))
    .map((r) => r['sales_estimates.brand']);
  const want = JSON.parse(fs.readFileSync(process.argv[3] || path.join(OUT, '_wanted_brands.json'), 'utf8'));
  const live = want.filter((b) => all.includes(b));
  console.log(`\nstores x brand (carriage) — ${live.length} of ${want.length} tracked brands are still in scope:`);
  const out = {};
  for (const brand of live) {
    try {
      const rows = await stores(token, { 'sales_estimates.brand': [brand] });
      out[brand] = rows;
      console.log(`  ${brand.padEnd(28)} ${rows.length} doors`);
    } catch (e) { console.log(`  ${brand} FAILED: ${e.message.slice(0, 90)}`); }
    await sleep(150);
  }
  fs.writeFileSync(path.join(OUT, 'ny_carriage_all.json'), JSON.stringify(out, null, 1));
  console.log(`\nsaved carriage for ${Object.keys(out).length} brands -> ${OUT}`);
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });

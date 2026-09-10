/* Pistil Next puller.
 *
 * Pistil replaced the Sigma embed at insights.pistildata.com with a real app at
 * app.pistildata.com backed by a JSON API. That retires the whole canvas/blob export
 * hack: no 75-second render waits, no stale-render traps, no download gate. We ask for
 * data and get data.
 *
 *   POST https://api.pistildata.com/api/dashboards/{dashboardGuid}/widgets/{widgetId}/query
 *   { filters, dateRange, selectedDimension, compareDateRange, pagination, timeZone }
 *
 * Auth is a bearer token, lifted out of the logged-in browser over CDP -- the old
 * lesson still holds, there is no way in without a real session.
 *
 * ONE TRAP CARRIED OVER: the default state filter is MI. Every query here sets
 * sales_estimates.state explicitly, and the caller checks the row counts.
 *
 * Usage: node next_pull.mjs [outputDir]
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

/** Lift a live bearer token out of the signed-in browser. */
async function getToken() {
  const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null, protocolTimeout: 180000 });
  const p = await b.newPage();
  let token = null;
  p.on('request', (r) => {
    const a = r.headers()['authorization'];
    if (a && /pistildata/.test(r.url()) && !token) token = a;
  });
  await p.goto('https://app.pistildata.com/market-intelligence/rankings', { waitUntil: 'domcontentloaded', timeout: 90000 });
  for (let i = 0; i < 30 && !token; i++) await sleep(1000);
  await p.close();
  b.disconnect();
  if (!token) throw new Error('no Authorization header seen — is the browser still signed in to Pistil?');
  return token;
}

function headers(token) {
  return {
    authorization: token,
    'content-type': 'application/json',
    origin: 'https://app.pistildata.com',
    referer: 'https://app.pistildata.com/',
  };
}

async function api(token, pathname, body) {
  const res = await fetch(API + pathname, {
    method: body ? 'POST' : 'GET',
    headers: headers(token),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${pathname} :: ${(await res.text()).slice(0, 180)}`);
  return res.json();
}

/** One ranking cut, paged to the end. */
async function ranking(token, { dimension, dateRange, compare, filters = {}, widget = 'widget-rankings-table' }) {
  const rows = [];
  for (let page = 1; page <= 40; page++) {
    const j = await api(token, `/api/dashboards/${RANKINGS}/widgets/${widget}/query`, {
      filters: { 'sales_estimates.state': [STATE], ...filters },
      dateRange,
      selectedDimension: dimension,
      compareDateRange: compare,
      pagination: { pageNumber: page, pageSize: PAGE },
      timeZone: 'America/New_York',
    });
    const data = j.data || [];
    rows.push(...data);
    if (data.length < PAGE) break;
    await sleep(250);
  }
  return rows;
}

function save(name, obj) {
  fs.mkdirSync(OUT, { recursive: true });
  const f = path.join(OUT, name);
  fs.writeFileSync(f, JSON.stringify(obj, null, 1));
  const n = Array.isArray(obj) ? obj.length : Object.keys(obj).length;
  console.log(`  saved ${name}  (${n} ${Array.isArray(obj) ? 'rows' : 'keys'})`);
  return f;
}

// Names taken from the Rankings dashboard config, not guessed -- the UI tab labels
// ("Stores", "Products") are not the member ids.
const DIMENSIONS = {
  stores: 'sales_estimates.store_name',
  brands: 'sales_estimates.brand',
  products: 'sales_estimates.product_name',
  categories: 'sales_estimates.category',
};

// Nested windows so momentum can be differenced, the way the old pipeline needed.
const WINDOWS = {
  '30d': { dateRange: 'last 30 days', compare: 'from 60 days ago to 31 days ago' },
  '90d': { dateRange: 'last 90 days', compare: 'from 180 days ago to 91 days ago' },
};

async function main() {
  console.log('lifting a token from the signed-in browser...');
  const token = await getToken();
  console.log('  got one\n');

  console.log('discovery:');
  const meta = {};
  for (const [name, p] of [['dashboards', '/api/dashboards'], ['menu', '/api/menu'],
                           ['filters_main', '/api/filters/main'], ['filters_secondary', '/api/filters/secondary'],
                           ['saved_filters', '/api/saved-filters'],
                           ['dashboard_rankings', `/api/dashboards/${RANKINGS}`]]) {
    try { meta[name] = await api(token, p); console.log(`  ${name} ok`); }
    catch (e) { console.log(`  ${name} FAILED: ${e.message.slice(0, 90)}`); }
  }
  save('_meta.json', meta);

  console.log('\nrankings (NY):');
  for (const [wname, w] of Object.entries(WINDOWS)) {
    for (const [dname, dim] of Object.entries(DIMENSIONS)) {
      try {
        const rows = await ranking(token, { dimension: dim, dateRange: w.dateRange, compare: w.compare });
        save(`ny_${dname}_${wname}.json`, rows);
      } catch (e) { console.log(`  ny_${dname}_${wname} FAILED: ${e.message.slice(0, 110)}`); }
    }
  }

  // Per-brand store cuts: the carriage layer -- which doors stock a given brand.
  const brands = process.env.BRANDS ? process.env.BRANDS.split(',') : ['Dragonfly', 'Jerome Baker', 'Green Revolution'];
  console.log('\nper-brand carriage (NY, stores stocking each brand):');
  for (const brand of brands) {
    try {
      const rows = await ranking(token, {
        dimension: DIMENSIONS.stores, ...WINDOWS['30d'],
        dateRange: WINDOWS['30d'].dateRange, compare: WINDOWS['30d'].compare,
        filters: { 'sales_estimates.brand': [brand] },
      });
      save(`ny_carriage_${brand.replace(/\W+/g, '_')}.json`, rows);
    } catch (e) { console.log(`  ${brand} FAILED: ${e.message.slice(0, 110)}`); }
  }

  console.log(`\ndone -> ${OUT}`);
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });

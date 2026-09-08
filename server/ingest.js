/* Turn a company's sales export into per-dispensary figures.
 *
 * Every company exports differently -- QuickBooks, a POS, a spreadsheet somebody
 * maintains by hand. Rather than make an admin map columns by hand or force one
 * template, Claude reads the header row plus a handful of sample rows and says
 * which column is what. The mapping is applied to the rows in plain code: the
 * model sees a sample, never the whole file, so a 40,000-row export costs the same
 * as a 40-row one and the arithmetic is not left to a language model.
 *
 * Matching to dispensaries is two passes. Licence numbers and exact names are done
 * deterministically, which handles most rows for nothing. Only the leftovers -- the
 * "Happy Days Disp. (Farmingdale)" spellings -- go to Claude, once, with the store
 * list to choose from, and it must return a licence from that list or null.
 */
import Anthropic from '@anthropic-ai/sdk';

const MODEL = process.env.INGEST_MODEL || 'claude-opus-5';
const MAX_ROWS = 200000;
const SAMPLE_ROWS = 12;

const anthropic = new Anthropic();

/* ---------- CSV ---------------------------------------------------------- */

/** RFC4180-ish: quoted fields, doubled quotes, CR/LF inside quotes. */
export function parseDelimited(text) {
  const src = String(text || '').replace(/^﻿/, '');
  // Sniff the delimiter on the header line rather than assuming a comma; plenty of
  // exports are tab- or semicolon-separated and look fine until every row is one cell.
  const firstLine = src.slice(0, src.indexOf('\n') < 0 ? src.length : src.indexOf('\n'));
  const counts = [[',', 0], ['\t', 0], [';', 0], ['|', 0]].map(([d]) => [d, firstLine.split(d).length - 1]);
  counts.sort((a, b) => b[1] - a[1]);
  const D = counts[0][1] > 0 ? counts[0][0] : ',';

  const rows = [];
  let row = [], cell = '', q = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (q) {
      if (c === '"') { if (src[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === D) { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; if (rows.length > MAX_ROWS) break; }
    else if (c !== '\r') cell += c;
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  while (rows.length && rows[rows.length - 1].every((x) => !String(x).trim())) rows.pop();
  if (!rows.length) throw Object.assign(new Error('that file has no rows in it'), { status: 400 });

  // Report exporters (Distru, QuickBooks, most BI tools) put a title and a filter
  // summary above the real header. Taking row 0 blindly reads "Date | Apr 1 2026 to
  // Sep 8 2026" as the column names and everything after it falls apart. The header is
  // the first wide row that starts a block of rows shaped like it.
  const width = (r) => r.filter((x) => String(x).trim()).length;
  let head = 0;
  for (let i = 0; i < Math.min(rows.length - 1, 15); i++) {
    if (width(rows[i]) < 3) continue;
    const n = rows[i].length;
    let agree = 0;
    for (let k = i + 1; k < Math.min(rows.length, i + 4); k++) {
      if (width(rows[k]) && rows[k].length === n) agree++;
    }
    if (agree >= 2 || (agree >= 1 && rows.length - i <= 3)) { head = i; break; }
  }
  const header = rows[head].map((h) => String(h).trim());
  return { header, rows: rows.slice(head + 1).filter((r) => r.some((x) => String(x).trim())), delimiter: D, headerRow: head };
}

const num = (v) => {
  const n = parseFloat(String(v == null ? '' : v).replace(/[^0-9.\-]/g, ''));
  return Number.isFinite(n) ? n : 0;
};

/** Dates come in every shape; keep the ones we can read, ignore the rest. */
function ym(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  let m = /^(\d{4})[-/](\d{1,2})/.exec(s);
  if (m) return `${m[1]}-${String(+m[2]).padStart(2, '0')}`;
  m = /^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})/.exec(s);           // US m/d/y
  if (m) { const y = m[3].length === 2 ? '20' + m[3] : m[3]; return `${y}-${String(+m[1]).padStart(2, '0')}`; }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 7);
}
function iso(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  let m = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/.exec(s);
  if (m) return `${m[1]}-${String(+m[2]).padStart(2, '0')}-${String(+m[3]).padStart(2, '0')}`;
  m = /^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})/.exec(s);
  if (m) { const y = m[3].length === 2 ? '20' + m[3] : m[3];
    return `${y}-${String(+m[1]).padStart(2, '0')}-${String(+m[2]).padStart(2, '0')}`; }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

/* ---------- Claude reads the header ------------------------------------- */

const MAP_TOOL = {
  name: 'report_columns',
  description: 'Report which column of the sales export holds which field.',
  strict: true,
  input_schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      store: { type: ['string', 'null'], description: 'Header of the column naming the dispensary/customer. null if absent.' },
      license: { type: ['string', 'null'], description: 'Header of the column holding a state licence number (e.g. OCM-...). null if absent.' },
      date: { type: ['string', 'null'], description: 'Header of the order/invoice date column. null if absent.' },
      amount: { type: ['string', 'null'], description: 'Header of the dollar value SOLD. When a file has both a sold/invoiced total and an amount paid or received, choose the SOLD one - money collected is an AR question, not a sales one. null if absent.' },
      product: { type: ['string', 'null'], description: 'Header of the product/SKU name column. null if absent.' },
      quantity: { type: ['string', 'null'], description: 'Header of the units/quantity column. null if absent.' },
      order_id: { type: ['string', 'null'], description: 'Header of the invoice/order NUMBER (an identifier), used to count distinct orders in a line-item file. null if absent.' },
      order_count: { type: ['string', 'null'], description: 'Header of a column that already holds a COUNT of orders, in a file summarised one row per customer. Never put an identifier here. null if absent.' },
      notes: { type: 'string', description: 'One short sentence on anything odd about this file.' },
    },
    required: ['store', 'license', 'date', 'amount', 'product', 'quantity', 'order_id', 'order_count', 'notes'],
  },
};

export async function mapColumns(header, rows) {
  const sample = rows.slice(0, SAMPLE_ROWS)
    .map((r) => header.map((h, i) => `${h}=${String(r[i] ?? '').slice(0, 40)}`).join(' | '))
    .join('\n');
  const res = await anthropic.messages.create({
    model: MODEL,
    max_tokens: 2000,
    tools: [MAP_TOOL],
    tool_choice: { type: 'tool', name: 'report_columns' },
    messages: [{
      role: 'user',
      content: `A wholesale seller exported their own sales. Say which column is which.
Return header names EXACTLY as given, or null when the file has no such column.

HEADERS: ${header.join(' | ')}

SAMPLE ROWS:
${sample}`,
    }],
  });
  const block = res.content.find((b) => b.type === 'tool_use');
  if (!block) throw Object.assign(new Error('could not read that file\'s columns'), { status: 502 });
  return block.input;
}

/* ---------- matching rows to dispensaries -------------------------------- */

const squash = (s) => String(s || '').toLowerCase()
  .replace(/[^a-z0-9 ]+/g, ' ')
  .replace(/\b(the|llc|inc|corp|co|ltd|dispensary|dispensaries|cannabis|weed|nyc|ny|adult|use|store|shop)\b/g, ' ')
  .replace(/\s+/g, ' ').trim();

const MATCH_TOOL = {
  name: 'report_matches',
  description: 'Match each sales-export store name to a dispensary licence, or null.',
  strict: true,
  input_schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      matches: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            name: { type: 'string', description: 'The store name exactly as it appeared in the export.' },
            license: { type: ['string', 'null'], description: 'Licence of the matching dispensary, copied exactly from the list, or null when there is no confident match.' },
          },
          required: ['name', 'license'],
        },
      },
    },
    required: ['matches'],
  },
};

/**
 * @param names   distinct store names the deterministic pass could not place
 * @param doors   [{n, c, lic}] every dispensary on the map
 */
export async function matchNames(names, doors) {
  if (!names.length) return {};
  const list = doors.filter((d) => d.lic)
    .map((d) => `${d.lic} | ${d.n} | ${d.c || ''}`).join('\n');
  const res = await anthropic.messages.create({
    model: MODEL,
    max_tokens: 8000,
    tools: [MATCH_TOOL],
    tool_choice: { type: 'tool', name: 'report_matches' },
    messages: [{
      role: 'user',
      content: `Match each store name from a seller's sales export to a licensed dispensary.

Rules:
- The licence you return MUST be copied from the list below. Never invent one.
- Abbreviations, missing suffixes, a city in brackets, and misspellings are normal - match through them.
- Two different locations of the same chain are DIFFERENT stores; use the city to choose, and return null if the export does not say which one.
- Return null rather than guessing. A wrong match puts a company's revenue against the wrong door.
- Return one entry for every name given, in the same order.

NAMES FROM THE EXPORT:
${names.map((n) => '- ' + n).join('\n')}

LICENSED DISPENSARIES (licence | name | city):
${list}`,
    }],
  });
  const block = res.content.find((b) => b.type === 'tool_use');
  if (!block) return {};
  const valid = new Set(doors.map((d) => d.lic).filter(Boolean));
  const out = {};
  for (const m of block.input.matches || []) {
    if (m.license && valid.has(m.license)) out[m.name] = m.license;
  }
  return out;
}

/* ---------- putting it together ------------------------------------------ */

/** Aggregate mapped rows into the per-door shape the map and the bot read. */
export function aggregate(header, rows, cols, licFor) {
  const idx = {};
  for (const k of ['store', 'license', 'date', 'amount', 'product', 'quantity', 'order_id', 'order_count']) {
    idx[k] = cols[k] ? header.indexOf(cols[k]) : -1;
  }
  const accounts = {};
  // Two different shapes: a line-item file where orders are counted by distinct id, and
  // a summary file that already states the count. Adding an id to a total, or counting a
  // count as one order, both silently under-report -- so they are kept apart.
  const counted = idx.order_count >= 0;
  const overall = { rev: 0, orders: counted ? 0 : new Set(), mo: {}, top: {} };
  let unmatchedRows = 0;

  for (const r of rows) {
    const rawName = idx.store >= 0 ? String(r[idx.store] || '').trim() : '';
    const rawLic = idx.license >= 0 ? String(r[idx.license] || '').trim() : '';
    const lic = licFor(rawLic, rawName);
    const amt = idx.amount >= 0 ? num(r[idx.amount]) : 0;
    const when = idx.date >= 0 ? iso(r[idx.date]) : null;
    const month = idx.date >= 0 ? ym(r[idx.date]) : null;
    const prod = idx.product >= 0 ? String(r[idx.product] || '').trim() : '';
    const ord = idx.order_id >= 0 ? String(r[idx.order_id] || '').trim() : (when || '');

    const cnt = counted ? Math.round(num(r[idx.order_count])) : 0;
    overall.rev += amt;
    if (counted) overall.orders += cnt; else if (ord) overall.orders.add(ord);
    if (month) overall.mo[month] = (overall.mo[month] || 0) + amt;
    if (prod) overall.top[prod] = (overall.top[prod] || 0) + amt;

    if (!lic) { unmatchedRows++; continue; }
    const a = accounts[lic] || (accounts[lic] = { name: rawName, rev: 0, orders: counted ? 0 : new Set(), last: null, mo: {}, top: {} });
    a.rev += amt;
    if (counted) a.orders += cnt; else if (ord) a.orders.add(ord);
    if (when && (!a.last || when > a.last)) a.last = when;
    if (month) a.mo[month] = (a.mo[month] || 0) + amt;
    if (prod) a.top[prod] = (a.top[prod] || 0) + amt;
  }

  const topList = (o) => Object.entries(o).sort((x, y) => y[1] - x[1]).slice(0, 5)
    .map(([k, v]) => [k, Math.round(v)]);
  const moList = (o) => Object.entries(o).sort((x, y) => x[0].localeCompare(y[0])).slice(-12)
    .map(([k, v]) => [k, Math.round(v)]);

  const out = {};
  for (const [lic, a] of Object.entries(accounts)) {
    out[lic] = { name: a.name, rev: Math.round(a.rev), orders: (counted ? a.orders : a.orders.size) || null,
                 last: a.last, mo: moList(a.mo), top: topList(a.top) };
  }
  return {
    accounts: out,
    overall: { rev: Math.round(overall.rev), orders: (counted ? overall.orders : overall.orders.size) || null,
               mo: moList(overall.mo), top: topList(overall.top) },
    unmatchedRows,
  };
}

/** Full pipeline: text in, per-door figures out. */
export async function ingest(text, doors) {
  const { header, rows } = parseDelimited(text);
  const cols = await mapColumns(header, rows);
  if (!cols.store && !cols.license) {
    throw Object.assign(new Error('I could not find a store or licence column in that file — is it a sales export?'), { status: 400 });
  }
  if (!cols.amount) {
    throw Object.assign(new Error('I could not find a dollar-amount column in that file.'), { status: 400 });
  }

  // Pass 1, free: licence numbers, then exact and squashed name equality.
  const byLic = new Map(doors.filter((d) => d.lic).map((d) => [d.lic.toUpperCase(), d.lic]));
  const bySquash = new Map();
  for (const d of doors) {
    const k = squash(d.n);
    if (k && !bySquash.has(k)) bySquash.set(k, d.lic || null);
  }
  const resolved = new Map();
  const unresolved = new Set();
  const si = cols.store ? header.indexOf(cols.store) : -1;
  const li = cols.license ? header.indexOf(cols.license) : -1;
  for (const r of rows) {
    const rawLic = li >= 0 ? String(r[li] || '').trim().toUpperCase() : '';
    const rawName = si >= 0 ? String(r[si] || '').trim() : '';
    const key = rawLic + ' ' + rawName;
    if (resolved.has(key) || !rawName && !rawLic) continue;
    if (rawLic && byLic.has(rawLic)) { resolved.set(key, byLic.get(rawLic)); continue; }
    const sq = squash(rawName);
    if (sq && bySquash.has(sq) && bySquash.get(sq)) { resolved.set(key, bySquash.get(sq)); continue; }
    if (rawName) unresolved.add(rawName);
  }

  // Pass 2: whatever is left, once, to the model.
  const names = [...unresolved].slice(0, 300);
  const guessed = await matchNames(names, doors);

  const licFor = (rawLic, rawName) => {
    const key = String(rawLic || '').toUpperCase() + ' ' + String(rawName || '');
    if (resolved.has(key)) return resolved.get(key);
    return guessed[rawName] || null;
  };

  const agg = aggregate(header, rows, cols, licFor);
  // Names the model was asked about and still could not place. Surfaced to the admin
  // rather than swallowed: an unmatched store is revenue missing from their view.
  const unmatchedNames = [...unresolved].filter((n) => !guessed[n]);

  return {
    columns: cols,
    rows: rows.length,
    matched: Object.keys(agg.accounts).length,
    unmatched: unmatchedNames.slice(0, 60),
    unmatchedRows: agg.unmatchedRows,
    accounts: agg.accounts,
    overall: agg.overall,
  };
}

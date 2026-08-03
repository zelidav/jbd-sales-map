// Call services.pistildata.com with auth lifted from the debug Chrome.
// Usage: node svc.js GET  /api/path [outfile]
//        node svc.js POST /api/path '<json>' [outfile]
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const method = (process.argv[2] || 'GET').toUpperCase();
const path = process.argv[3];
const bodyArg = method === 'GET' ? null : process.argv[4];
const out = method === 'GET' ? process.argv[4] : process.argv[5];

async function auth() {
  const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null, protocolTimeout: 240000 });
  const all = await browser.cookies();
  browser.disconnect();
  const ck = all.filter(c => c.domain.endsWith('pistildata.com'));
  const jar = [...new Map(ck.map(c => [c.name, c.value])).entries()].map(([k, v]) => `${k}=${v}`).join('; ');
  const raw = (ck.find(c => c.name === 'user_accessToken') || {}).value || '';
  let bearer = '';
  try { bearer = Buffer.from(decodeURIComponent(raw), 'base64').toString('utf8'); } catch (e) {}
  if (!/^ey/.test(bearer)) bearer = decodeURIComponent(raw);
  return { jar, bearer };
}

(async () => {
  const { jar, bearer } = await auth();
  const headers = {
    Cookie: jar,
    Authorization: 'Bearer ' + bearer,
    Accept: 'application/json, text/plain, */*',
    'app-origin': 'insights',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
  };
  if (bodyArg) headers['Content-Type'] = 'application/json';
  const r = await fetch('https://services.pistildata.com' + path, { method, headers, body: bodyArg || undefined });
  const text = await r.text();
  console.log('STATUS', r.status, 'LEN', text.length);
  if (out) { fs.writeFileSync(out, text); console.log('saved ->', out); }
  let pretty = text;
  try { pretty = JSON.stringify(JSON.parse(text), null, 1); } catch (e) {}
  console.log(pretty.slice(0, 6000));
})().catch(e => { console.error('ERR', e.message, e.cause && e.cause.message); process.exit(1); });

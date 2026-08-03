// Set Pistil filters server-side, load the report, download XLSX, rename deterministically.
// Usage: node pull.js <slug> <reportPath> <wbGuid> <savedGuid> '<controlOverridesJson>'
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const [, , slug, report, wbGuid, savedGuid, overridesJson] = process.argv;
const overrides = JSON.parse(overridesJson || '{}');
const DL = 'C:\\Users\\zelid\\Downloads';
const SVC = 'https://services.pistildata.com';

async function authHeaders(browser) {
  const all = await browser.cookies();
  const ck = all.filter(c => c.domain.endsWith('pistildata.com'));
  const jar = [...new Map(ck.map(c => [c.name, c.value])).entries()].map(([k, v]) => `${k}=${v}`).join('; ');
  const raw = (ck.find(c => c.name === 'user_accessToken') || {}).value || '';
  let bearer = '';
  try { bearer = Buffer.from(decodeURIComponent(raw), 'base64').toString('utf8'); } catch (e) {}
  return { Cookie: jar, Authorization: 'Bearer ' + bearer, 'app-origin': 'insights', Accept: 'application/json, text/plain, */*' };
}

async function ensureLoggedIn(page) {
  for (let i = 0; i < 3; i++) {
    if (!/identity\.pistildata\.com/.test(page.url())) return true;
    console.log('  ! login page detected, signing in');
    await page.mouse.click(800, 702);
    await sleep(15000);
  }
  return !/identity\.pistildata\.com/.test(page.url());
}

// click an element in the top-level Pistil chrome by exact text
async function clickText(page, text) {
  const box = await page.evaluate((t) => {
    const els = [...document.querySelectorAll('a,button,div,span,li')];
    const hit = els.filter(e => (e.innerText || '').trim() === t && e.offsetParent !== null)
                   .sort((x, y) => x.innerText.length - y.innerText.length)[0];
    if (!hit) return null;
    const r = hit.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, text);
  if (!box) return false;
  await page.mouse.click(box.x, box.y);
  return true;
}

const snapshot = () => new Set(fs.readdirSync(DL));

(async () => {
  const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null, protocolTimeout: 300000 });
  const page = (await browser.pages()).find(p => p.url().includes('pistildata.com'));
  await page.bringToFront();
  await page.setViewport({ width: 1600, height: 1200 });
  await ensureLoggedIn(page);

  const headers = await authHeaders(browser);
  const ctrlUrl = `${SVC}/api/v2/Controls/workbook/${wbGuid}/saved/${savedGuid}`;
  const cur = await (await fetch(ctrlUrl, { headers })).json();
  const ctrls = cur.sigmaControls.map(c => ({ sigmaControlId: c.sigmaControlId, values: c.values }));
  for (const [k, v] of Object.entries(overrides)) {
    const hit = ctrls.find(c => c.sigmaControlId === k);
    if (hit) hit.values = v; else ctrls.push({ sigmaControlId: k, values: v });
  }
  const put = await fetch(ctrlUrl, {
    method: 'PUT', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ isDefault: false, isAutoSaving: true, savedFilterName: '', sigmaControls: ctrls }),
  });
  console.log('PUT controls ->', put.status);

  const client = await page.createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DL });

  await page.goto(`https://insights.pistildata.com/${report}/${wbGuid}`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  await sleep(6000);
  if (!(await ensureLoggedIn(page))) { console.log('LOGIN FAILED'); browser.disconnect(); process.exit(3); }
  if (!page.url().includes(wbGuid)) {
    await page.goto(`https://insights.pistildata.com/${report}/${wbGuid}`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  }
  await sleep(75000); // Sigma render (longer: partial renders produced short exports)

  await page.screenshot({ path: `pull_${slug}_page.png` });

  const before = snapshot();
  if (!(await clickText(page, 'Download'))) { console.log('no Download button'); browser.disconnect(); process.exit(4); }
  await sleep(5000);
  await page.screenshot({ path: `pull_${slug}_modal.png` });
  if (!(await clickText(page, 'XLSX'))) {
    await page.mouse.click(1148, 635); // fallback to known coords
    console.log('  (XLSX text not found, used coords)');
  }
  console.log('clicked XLSX');

  let added = null;
  for (let i = 0; i < 45; i++) {
    await sleep(2000);
    const now = [...snapshot()].filter(f => !before.has(f) && /\.xlsx$/i.test(f));
    if (now.length) { added = now[0]; break; }
  }
  if (!added) { console.log('NO FILE DOWNLOADED'); browser.disconnect(); process.exit(2); }
  const dest = path.join(DL, `PULL_${slug}.xlsx`);
  if (fs.existsSync(dest)) fs.unlinkSync(dest);
  fs.renameSync(path.join(DL, added), dest);
  console.log('saved ->', dest);
  browser.disconnect();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });

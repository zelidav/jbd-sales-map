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
  const writeControls = async () => {
    const r = await fetch(ctrlUrl, {
      method: 'PUT', headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ isDefault: false, isAutoSaving: true, savedFilterName: '', sigmaControls: ctrls }),
    });
    return r.status;
  };
  const readControl = async (id) => {
    const j = await (await fetch(ctrlUrl, { headers })).json();
    return (j.sigmaControls.find(c => c.sigmaControlId === id) || {}).values;
  };
  console.log('PUT controls ->', await writeControls());

  // Chrome cancels repeated automatic downloads, and the export is a blob: URL built
  // in page memory (not fetchable from Node). So capture the blob URL from the CDP
  // event and read the bytes out of the page itself.
  const client = await page.createCDPSession();
  await client.send('Browser.setDownloadBehavior',
    { behavior: 'allow', downloadPath: DL, eventsEnabled: true });
  let blobUrl = null;
  client.on('Browser.downloadWillBegin', e => { blobUrl = e.url; });

  await page.goto(`https://insights.pistildata.com/${report}/${wbGuid}`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  await sleep(6000);
  if (!(await ensureLoggedIn(page))) { console.log('LOGIN FAILED'); browser.disconnect(); process.exit(3); }
  if (!page.url().includes(wbGuid)) {
    await page.goto(`https://insights.pistildata.com/${report}/${wbGuid}`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  }
  await sleep(+(process.env.RENDER_MS || 75000)); // Sigma render (RENDER_MS to tune)

  await page.screenshot({ path: `pull_${slug}_page.png` });

  // Keep a reference to the Blob itself: Chrome cancels the download and revokes the
  // blob: URL before we can fetch it, but the Blob object stays alive if we hold it.
  await page.evaluate(() => {
    if (window.__origCOU) return;
    window.__origCOU = URL.createObjectURL.bind(URL);
    window.__lastBlob = null;
    URL.createObjectURL = function (b) { window.__lastBlob = b; return window.__origCOU(b); };
  });

  await page.keyboard.press('Escape');   // ensure no modal is already open
  await sleep(1500);
  if (!(await clickText(page, 'Download'))) { console.log('no Download button'); browser.disconnect(); process.exit(4); }
  await sleep(6000);
  if (!(await clickText(page, 'XLSX'))) {
    await page.mouse.click(1148, 635);
    console.log('  (XLSX text not found, used coords)');
  }

  for (let i = 0; i < 60 && !blobUrl; i++) await sleep(1000);
  if (!blobUrl) { console.log('NO DOWNLOAD STARTED'); browser.disconnect(); process.exit(2); }

  const b64 = await page.evaluate(async () => {
    const b = window.__lastBlob;
    if (!b) return null;
    const buf = new Uint8Array(await b.arrayBuffer());
    let s = '';
    for (let i = 0; i < buf.length; i += 8192)
      s += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
    return btoa(s);
  }).catch(e => { console.log('blob read failed:', e.message); return null; });

  if (!b64) { console.log('BLOB UNREADABLE'); browser.disconnect(); process.exit(5); }
  const buf = Buffer.from(b64, 'base64');
  // A too-small file means an empty render; a too-large one means the state filter did
  // not apply (Michigan rows leaked in at 729 stores vs New York's ~640).
  if (buf.length < 8000) {
    console.log(`REJECTED: ${buf.length} bytes - empty/partial render, not saved`);
    browser.disconnect(); process.exit(7);
  }
  const dest = path.join(DL, `PULL_${slug}.xlsx`);
  fs.writeFileSync(dest, buf);
  console.log('saved ->', dest, `(${buf.length} bytes)`);
  browser.disconnect();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });

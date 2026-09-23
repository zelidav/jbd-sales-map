/* Record a functional walkthrough of the live app for the BDSA demo.
 *
 * Drives the real site signed in as the demo org and captures a CDP screencast, so what
 * lands in the video is the product actually running -- not a mock, not a slideshow. The
 * beats follow the pitch: the rep's question, where the reasons come from, the half the
 * market layer cannot see, and the doors nobody measures.
 *
 * Frames come out as JPEGs and ffmpeg assembles them at a fixed frame rate. The screencast
 * only emits a frame when the page changes, so gaps are held by repeating the last frame
 * -- otherwise a still moment plays back as a jump cut.
 *
 * Usage: node tools/record_demo.mjs [outdir]
 */
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const FFMPEG = process.env.FFMPEG || String.raw`C:\Users\zelid\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe`;
const APP = 'https://zelidav.github.io/jbd-sales-map/';
const EMAIL = 'david+bdsademo@canismajorpartners.com';
const CODE = 'MAHTT3BV';

const OUT = process.argv[2] || path.join(process.env.TEMP || '.', 'beeline-demo');
const FRAMES = path.join(OUT, 'frames');
const FPS = 12;

fs.rmSync(FRAMES, { recursive: true, force: true });
fs.mkdirSync(FRAMES, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--window-size=1440,900', '--hide-scrollbars'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

// ── capture ──────────────────────────────────────────────────────────────────
let n = 0;
let last = null;
const cdp = await page.createCDPSession();
cdp.on('Page.screencastFrame', async (f) => {
  last = f.data;
  fs.writeFileSync(path.join(FRAMES, String(n++).padStart(5, '0') + '.jpg'),
                   Buffer.from(f.data, 'base64'));
  try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) { /* closing */ }
});

/** Hold the frame so a pause reads as a pause, not a cut. */
async function hold(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await sleep(1000 / FPS);
    if (last) {
      fs.writeFileSync(path.join(FRAMES, String(n++).padStart(5, '0') + '.jpg'),
                       Buffer.from(last, 'base64'));
    }
  }
}

/** A caption burned into the page, so the video explains itself without narration. */
async function caption(text, sub) {
  await page.evaluate((t, s) => {
    let el = document.getElementById('__cap');
    if (!el) {
      el = document.createElement('div');
      el.id = '__cap';
      el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;' +
        'background:linear-gradient(transparent,rgba(12,10,6,.93) 38%);' +
        'padding:52px 44px 26px;pointer-events:none;' +
        'font-family:Inter,system-ui,Segoe UI,sans-serif';
      document.body.appendChild(el);
    }
    el.innerHTML = '<div style="color:#FFB000;font:700 12px/1 ui-monospace,monospace;' +
      'letter-spacing:.18em;text-transform:uppercase;margin-bottom:9px">' + (s || '') + '</div>' +
      '<div style="color:#fff;font:600 27px/1.25 Inter,system-ui,sans-serif;' +
      'letter-spacing:-.02em;max-width:60ch;text-shadow:0 2px 18px rgba(0,0,0,.6)">' + t + '</div>';
  }, text, sub);
}
const clearCaption = () => page.evaluate(() => {
  const el = document.getElementById('__cap'); if (el) el.remove();
});

// ── set up signed in, before recording starts ────────────────────────────────
await page.goto(APP, { waitUntil: 'networkidle2' });
await page.evaluate((e, c) => localStorage.setItem('jbd_profile_v1',
  JSON.stringify({ email: e, code: c })), EMAIL, CODE);
await page.reload({ waitUntil: 'networkidle2' });
await page.waitForFunction(() => window.PROFILE && !document.getElementById('gate'),
                           { timeout: 60000 });
// A brand-new org gets the first-run tutorial, which would sit over the whole recording.
await sleep(2000);
for (let i = 0; i < 8; i++) {
  const gone = await page.evaluate(() => {
    const skip = document.getElementById('tutskip');
    const wrap = document.getElementById('tutwrap');
    if (skip && wrap && wrap.offsetParent !== null) { skip.click(); return false; }
    return !wrap || wrap.offsetParent === null;
  });
  if (gone) break;
  await sleep(600);
}
await page.evaluate(() => {
  const w = document.getElementById('tutwrap'); if (w) w.remove();
});
await sleep(3000);

await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 82, everyNthFrame: 1 });

// ── 1. the book, as the app sees it ──────────────────────────────────────────
await caption('Every licensed door in New York. 751 of them.',
              'Dragonfly Brands \u00b7 live');
await hold(2600);

await caption('Your own accounts are coloured in: 192 buying, 13 slipping, 9 gone quiet.',
              'First-party sales, matched on licence number');
await page.evaluate(() => {
  document.querySelectorAll('#roleChips *').forEach((c) => {
    const t = (c.textContent || '').trim();
    if (/^(Active|Slipping|Lapsed|Customer|Prospect)/i.test(t) &&
        !c.classList.contains('on')) c.click();
  });
});
await sleep(1200);
await hold(3200);

// ── 2. the doors worth saving ────────────────────────────────────────────────
await caption('The ones that stopped ordering are the whole point.',
              'Save it');
await hold(1800);

await page.evaluate(async () => {
  const LIC = 'OCM-CAURD-24-000183';                 // NugHub NY, quiet 63 days
  const d = (window.DATA || []).find((x) => x.lic === LIC);
  if (!d) return;
  window.map.setView([d.lat, d.lng], 15);
  await new Promise((r) => setTimeout(r, 900));
  const mk = (window.markers || {})[d.lic];
  if (mk && window.cluster && window.cluster.zoomToShowLayer) {
    window.cluster.zoomToShowLayer(mk, () => mk.openPopup());
  } else if (mk) { mk.openPopup(); }
});
await hold(3600);
await caption('$57k of business, silent for 63 days. Nobody was counting.',
              'NugHub NY');
await hold(3200);

// ── 3. plan the day ──────────────────────────────────────────────────────────
await page.evaluate(() => {
  const b = document.getElementById('planbtn'); if (b) b.click();
});
await caption('Five fields. Where you start, what you sell, how many stops.',
              'Plan the day');
await hold(2400);

await page.evaluate(() => {
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  set('pstart', 'Poughkeepsie, NY');
  set('ptime', '07:30');
  set('pend', 'Syracuse, NY');
  set('pmax', '4');
});
await hold(2200);

await page.evaluate(() => { const g = document.getElementById('plango'); if (g) g.click(); });
await caption('A routed day, with a reason under every stop.', 'Built in one click');
await hold(6000);

// ── 4. the doors nobody measures ─────────────────────────────────────────────
await page.evaluate(() => {
  const b = document.getElementById('planx'); if (b) b.click();
  const nd = (window.DATA || []).find((d) => d.nd && d.area && d.area.vol && d.site);
  if (!nd) return;
  window.map.setView([nd.lat, nd.lng], 15);
  const mk = (window.markers || {})[nd.lic || nd.n];
  if (mk && window.cluster && window.cluster.zoomToShowLayer) {
    window.cluster.zoomToShowLayer(mk, () => mk.openPopup());
  } else if (mk) { mk.openPopup(); }
});
await caption('135 licensed doors nobody measures. The app says so, and estimates from the neighbours.',
              'Where coverage ends');
await hold(5200);

// ── 5. ask it in words ───────────────────────────────────────────────────────
await page.evaluate(() => {
  const b = document.getElementById('botbtn'); if (b) b.click();
});
await caption('Or just ask.', 'The assistant holds the same day the rep is looking at');
await hold(1800);
await page.click('#botq').catch(() => {});
await page.type('#botq', 'Which of my accounts stopped ordering and are worth a visit?',
                { delay: 18 });
await page.click('#botsend').catch(() => {});
// wait for a real reply to appear rather than guessing at a duration
await page.waitForFunction(
  () => document.getElementById('botmsgs') &&
        document.getElementById('botmsgs').children.length >= 2,
  { timeout: 45000 }).catch(() => {});
await hold(9000);

await caption('Market data in one half. Your order book in the other. One day, four stops, a reason each.',
              'Beeline');
await hold(4200);

await clearCaption();
await cdp.send('Page.stopScreencast');
await browser.close();

// ── encode ───────────────────────────────────────────────────────────────────
const count = fs.readdirSync(FRAMES).filter((f) => f.endsWith('.jpg')).length;
const mp4 = path.join(OUT, 'beeline-demo.mp4');
execFileSync(FFMPEG, ['-y', '-framerate', String(FPS), '-i', path.join(FRAMES, '%05d.jpg'),
  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
  '-vf', 'scale=1440:-2', '-movflags', '+faststart', mp4], { stdio: 'inherit' });
const mb = fs.statSync(mp4).size / 1e6;
console.log(`\n${count} frames -> ${mp4}  (${mb.toFixed(1)} MB, ~${(count / FPS).toFixed(0)}s)`);

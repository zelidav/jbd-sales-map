/* Rep profiles + their saved routes and filters.
 *
 * Cloud Run holds nothing between requests, so a rep who built a good filter set or a
 * two-day route lost it the moment the instance recycled or they picked up a different
 * phone. Profiles live in a GCS bucket -- one small JSON object per rep, keyed by a
 * hash of their email so the object names carry no addresses.
 *
 * On auth, honestly: this is a shared-secret scheme for an internal field tool that
 * already sat behind one password for everybody. A profile means a rep's saved work
 * follows them and route logs carry a real name -- it is NOT a security boundary, and
 * nothing here should hold anything you would not put behind the old shared password.
 */
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { Storage } from '@google-cloud/storage';

const BUCKET = process.env.PROFILE_BUCKET || 'jbd-sales-map-profiles';
const INVITE = process.env.INVITE_CODE || '';
const MAX_SAVED = 40;

let bucket = null;
try { bucket = new Storage().bucket(BUCKET); }
catch (e) { console.warn('profile store unavailable:', e.message); }

const norm = (e) => String(e || '').trim().toLowerCase();
const keyFor = (email) => 'u/' + createHash('sha256').update(norm(email)).digest('hex') + '.json';

// Ambiguity-free alphabet: no O/0, I/1, so a code read off a phone screen still works.
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
function newCode(n = 8) {
  const b = randomBytes(n);
  let s = '';
  for (let i = 0; i < n; i++) s += ALPHABET[b[i] % ALPHABET.length];
  return s;
}

function sameCode(a, b) {
  const x = Buffer.from(String(a || ''), 'utf8');
  const y = Buffer.from(String(b || ''), 'utf8');
  return x.length === y.length && timingSafeEqual(x, y);
}

async function read(email) {
  if (!bucket) throw new Error('profile store not configured');
  const f = bucket.file(keyFor(email));
  const [exists] = await f.exists();
  if (!exists) return null;
  const [buf] = await f.download();
  return JSON.parse(buf.toString('utf8'));
}

async function write(p) {
  if (!bucket) throw new Error('profile store not configured');
  await bucket.file(keyFor(p.email)).save(JSON.stringify(p), {
    contentType: 'application/json',
    resumable: false,
  });
  return p;
}

/** Create a profile, or hand back the existing one's code when `reissue` is set. */
export async function register({ email, name, invite, reissue }) {
  const e = norm(email);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) throw Object.assign(new Error('a real email address is required'), { status: 400 });
  if (!INVITE || String(invite || '').trim().toUpperCase() !== INVITE.toUpperCase()) {
    throw Object.assign(new Error('that invite code is not right'), { status: 403 });
  }
  const existing = await read(e);
  if (existing && !reissue) {
    throw Object.assign(new Error('a profile already exists for that email — sign in with your code, or ask for a new one'), { status: 409 });
  }
  const p = existing || { email: e, created: new Date().toISOString(), filters: [], routes: [] };
  p.name = String(name || existing?.name || '').trim().slice(0, 80) || e.split('@')[0];
  p.code = newCode();
  p.updated = new Date().toISOString();
  await write(p);
  return { email: p.email, name: p.name, code: p.code, created: p.created };
}

/** Verify email + code and return the profile with everything it has saved. */
export async function login({ email, code }) {
  const p = await read(email);
  if (!p || !sameCode(p.code, code)) {
    throw Object.assign(new Error('that email and code do not match'), { status: 401 });
  }
  p.seen = new Date().toISOString();
  write(p).catch((e) => console.warn('seen-stamp failed:', e.message));
  return { email: p.email, name: p.name, filters: p.filters || [], routes: p.routes || [] };
}

/** Replace one of the two saved lists. Callers send the whole list, so a delete is
 *  just a shorter list -- no separate delete endpoint to keep in sync. */
export async function save({ email, code, kind, items }) {
  if (kind !== 'filters' && kind !== 'routes') {
    throw Object.assign(new Error('kind must be "filters" or "routes"'), { status: 400 });
  }
  const p = await read(email);
  if (!p || !sameCode(p.code, code)) {
    throw Object.assign(new Error('that email and code do not match'), { status: 401 });
  }
  if (!Array.isArray(items)) throw Object.assign(new Error('items must be a list'), { status: 400 });
  p[kind] = items.slice(0, MAX_SAVED).map((it) => ({
    name: String(it.name || 'Untitled').slice(0, 80),
    ts: typeof it.ts === 'string' ? it.ts.slice(0, 40) : new Date().toISOString(),
    data: it.data,
  }));
  p.updated = new Date().toISOString();
  await write(p);
  return { ok: true, kind, count: p[kind].length };
}

export const configured = () => !!bucket && !!INVITE;

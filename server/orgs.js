/* Companies, their people, and their own sales data.
 *
 * The map used to be one company's tool with one shared password. To sell it, the
 * unit of everything is a company: a company is created first, its admin invites
 * people, and the sales figures a company uploads are visible only inside it. The
 * store rank underneath is the shared baseline every company sees.
 *
 * Storage is one small JSON object per record in GCS, because Cloud Run keeps
 * nothing between requests:
 *   orgs/<orgId>.json          the company
 *   orgs/<orgId>/sales.json    what that company sold, matched to dispensaries
 *   u/<sha256(email)>.json     a person, and the company they belong to
 *
 * On auth, honestly: an emailed code over HTTPS. It scopes a company's sales data
 * to that company and puts a real name on a route log. It is not a security
 * boundary and nothing here should hold what you would not put behind a password
 * the whole team shares.
 */
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { Storage } from '@google-cloud/storage';

const BUCKET = process.env.PROFILE_BUCKET || 'jbd-sales-map-profiles';
const SIGNUP_CODE = process.env.SIGNUP_CODE || '';
const MAX_SAVED = 40;
const INVITE_DAYS = 14;

let bucket = null;
try { bucket = new Storage().bucket(BUCKET); }
catch (e) { console.warn('org store unavailable:', e.message); }

const norm = (e) => String(e || '').trim().toLowerCase();
const userKey = (email) => 'u/' + createHash('sha256').update(norm(email)).digest('hex') + '.json';
const orgKey = (id) => `orgs/${id}.json`;
const salesKey = (id) => `orgs/${id}/sales.json`;

// No O/0 or I/1: these get read off a phone screen and typed on another device.
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
function token(n) {
  const b = randomBytes(n);
  let s = '';
  for (let i = 0; i < n; i++) s += ALPHABET[b[i] % ALPHABET.length];
  return s;
}

function same(a, b) {
  const x = Buffer.from(String(a || ''), 'utf8');
  const y = Buffer.from(String(b || ''), 'utf8');
  return x.length === y.length && timingSafeEqual(x, y);
}

function fail(msg, status) { return Object.assign(new Error(msg), { status }); }

async function readJson(key) {
  if (!bucket) throw fail('the company store is not configured on this server', 500);
  const f = bucket.file(key);
  const [exists] = await f.exists();
  if (!exists) return null;
  const [buf] = await f.download();
  return JSON.parse(buf.toString('utf8'));
}

async function writeJson(key, obj) {
  if (!bucket) throw fail('the company store is not configured on this server', 500);
  await bucket.file(key).save(JSON.stringify(obj), {
    contentType: 'application/json', resumable: false,
  });
  return obj;
}

const readUser = (email) => readJson(userKey(email));
const writeUser = (u) => writeJson(userKey(u.email), u);

/** Verify a caller and hand back their record. Every authed route starts here. */
export async function auth({ email, code }) {
  const u = await readUser(email);
  if (!u || !same(u.code, code)) throw fail('that email and code do not match', 401);
  return u;
}

export async function requireAdmin(creds) {
  const u = await auth(creds);
  if (u.role !== 'admin') throw fail('only an admin can do that', 403);
  return u;
}

/* ---------- creating a company ------------------------------------------- */

/** Creates the company and its first admin in one step. */
export async function createOrg({ company, name, email, signupCode }) {
  const e = norm(email);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) throw fail('a real email address is required', 400);
  if (!String(company || '').trim()) throw fail('what is the company called?', 400);
  if (!SIGNUP_CODE || String(signupCode || '').trim().toUpperCase() !== SIGNUP_CODE.toUpperCase()) {
    throw fail('that signup code is not right — ask for one to set your company up', 403);
  }
  if (await readUser(e)) throw fail('there is already an account for that email — sign in instead', 409);

  const org = {
    id: token(10).toLowerCase(),
    name: String(company).trim().slice(0, 120),
    created: new Date().toISOString(),
    createdBy: e,
  };
  await writeJson(orgKey(org.id), org);

  const user = {
    email: e,
    name: String(name || '').trim().slice(0, 80) || e.split('@')[0],
    code: token(8),
    orgId: org.id,
    role: 'admin',
    tutorialDone: false,
    created: org.created,
    filters: [],
    routes: [],
  };
  await writeUser(user);
  return { org, user: { email: user.email, name: user.name, code: user.code, role: 'admin' } };
}

/* ---------- people ------------------------------------------------------- */

/** Admin adds someone. Returns a one-time link token for the invite email. */
export async function addUser({ email, code, newEmail, newName, role }) {
  const admin = await requireAdmin({ email, code });
  const e = norm(newEmail);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) throw fail('a real email address is required', 400);
  const existing = await readUser(e);
  if (existing && existing.orgId !== admin.orgId) {
    throw fail('that email already belongs to another company', 409);
  }
  const u = existing || { email: e, created: new Date().toISOString(), filters: [], routes: [] };
  u.name = String(newName || u.name || '').trim().slice(0, 80) || e.split('@')[0];
  u.orgId = admin.orgId;
  u.role = role === 'admin' ? 'admin' : 'member';
  if (!u.code) u.code = token(8);
  if (u.tutorialDone === undefined) u.tutorialDone = false;
  u.invite = { token: token(24), exp: Date.now() + INVITE_DAYS * 864e5 };
  await writeUser(u);
  const org = await readJson(orgKey(admin.orgId));
  return {
    invite: u.invite.token,
    user: { email: u.email, name: u.name, role: u.role },
    org: { id: org?.id, name: org?.name },
    invitedBy: admin.name,
    reinvited: !!existing,
  };
}

/** An invite link signs someone in without them ever typing a code. */
export async function redeem({ token: t }) {
  if (!bucket) throw fail('the company store is not configured on this server', 500);
  const raw = String(t || '');
  if (!/^[A-Z0-9]{24}$/.test(raw)) throw fail('that link is not valid', 400);
  // Invite tokens are not derivable from the email, so the only way to find the
  // owner is to look. The member list is small and this runs once per invite.
  const [files] = await bucket.getFiles({ prefix: 'u/' });
  for (const f of files) {
    const [buf] = await f.download();
    const u = JSON.parse(buf.toString('utf8'));
    if (u.invite && u.invite.token === raw) {
      if (u.invite.exp < Date.now()) throw fail('that invite link has expired — ask for a new one', 410);
      delete u.invite;
      await writeUser(u);
      return login({ email: u.email, code: u.code });
    }
  }
  throw fail('that link is not valid, or it has already been used', 404);
}

export async function login({ email, code }) {
  const u = await auth({ email, code });
  u.seen = new Date().toISOString();
  writeUser(u).catch((e) => console.warn('seen-stamp failed:', e.message));
  const org = u.orgId ? await readJson(orgKey(u.orgId)) : null;
  const sales = u.orgId ? await readJson(salesKey(u.orgId)).catch(() => null) : null;
  return {
    email: u.email,
    name: u.name,
    code: u.code,
    role: u.role || 'member',
    tutorialDone: !!u.tutorialDone,
    org: org ? { id: org.id, name: org.name, brands: org.brands || [] } : null,
    filters: u.filters || [],
    routes: u.routes || [],
    sales: sales ? { uploadedAt: sales.uploadedAt, filename: sales.filename,
                     matched: sales.matched, unmatched: sales.unmatched,
                     accounts: sales.accounts, overall: sales.overall } : null,
  };
}

export async function listUsers({ email, code }) {
  const admin = await requireAdmin({ email, code });
  if (!bucket) throw fail('the company store is not configured on this server', 500);
  const [files] = await bucket.getFiles({ prefix: 'u/' });
  const out = [];
  for (const f of files) {
    const [buf] = await f.download();
    const u = JSON.parse(buf.toString('utf8'));
    if (u.orgId === admin.orgId) {
      out.push({ email: u.email, name: u.name, role: u.role || 'member',
                 seen: u.seen || null, pending: !!u.invite });
    }
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return { users: out };
}

/* ---------- a person's own saved work ------------------------------------ */

export async function save({ email, code, kind, items }) {
  if (kind !== 'filters' && kind !== 'routes') throw fail('kind must be "filters" or "routes"', 400);
  const u = await auth({ email, code });
  if (!Array.isArray(items)) throw fail('items must be a list', 400);
  u[kind] = items.slice(0, MAX_SAVED).map((it) => ({
    name: String(it.name || 'Untitled').slice(0, 80),
    ts: typeof it.ts === 'string' ? it.ts.slice(0, 40) : new Date().toISOString(),
    data: it.data,
  }));
  await writeUser(u);
  return { ok: true, kind, count: u[kind].length };
}

export async function setTutorial({ email, code, done }) {
  const u = await auth({ email, code });
  u.tutorialDone = !!done;
  await writeUser(u);
  return { ok: true, tutorialDone: u.tutorialDone };
}

/* ---------- the company's own sales data --------------------------------- */

/** The brands this company sells. Drives the one-tap brand filters on the map. */
export async function setBrands({ email, code, brands }) {
  const admin = await requireAdmin({ email, code });
  const org = await readJson(orgKey(admin.orgId));
  if (!org) throw fail('company not found', 404);
  org.brands = (Array.isArray(brands) ? brands : []).slice(0, 8)
    .map((b) => String(b || '').trim().slice(0, 80)).filter(Boolean);
  await writeJson(orgKey(org.id), org);
  return { ok: true, brands: org.brands };
}

export async function putSales({ email, code }, payload) {
  const admin = await requireAdmin({ email, code });
  const rec = { ...payload, uploadedAt: new Date().toISOString(), uploadedBy: admin.email };
  await writeJson(salesKey(admin.orgId), rec);
  return rec;
}

export async function getSales({ email, code }) {
  const u = await auth({ email, code });
  if (!u.orgId) return null;
  return readJson(salesKey(u.orgId));
}

export async function clearSales({ email, code }) {
  const admin = await requireAdmin({ email, code });
  if (bucket) await bucket.file(salesKey(admin.orgId)).delete({ ignoreNotFound: true });
  return { ok: true };
}

/* ---------- what the company is actually doing with it -------------------- */

const routesKey = (id) => `orgs/${id}/routelog.json`;
const ROUTELOG_MAX = 300;

/** A route a rep handed to Google Maps or exported. Kept per company so an admin can
 *  see the tool being used; the in-memory ring it replaces died with the instance. */
export async function logRoute({ email, code }, rec) {
  const u = await auth({ email, code });
  if (!u.orgId) return { ok: false };
  const key = routesKey(u.orgId);
  const log = (await readJson(key)) || { routes: [] };
  log.routes.unshift({ ...rec, by: u.name || u.email, ts: new Date().toISOString() });
  log.routes = log.routes.slice(0, ROUTELOG_MAX);
  await writeJson(key, log);
  return { ok: true, count: log.routes.length };
}

/** Everything the admin view shows: the team, what they have saved, what they have
 *  built, and the state of the company's uploaded sales. */
export async function stats({ email, code }) {
  const admin = await requireAdmin({ email, code });
  const org = await readJson(orgKey(admin.orgId));
  const sales = await readJson(salesKey(admin.orgId)).catch(() => null);
  const log = (await readJson(routesKey(admin.orgId)).catch(() => null)) || { routes: [] };

  const [files] = await bucket.getFiles({ prefix: 'u/' });
  const users = [];
  for (const f of files) {
    const [buf] = await f.download();
    const u = JSON.parse(buf.toString('utf8'));
    if (u.orgId !== admin.orgId) continue;
    users.push({
      email: u.email, name: u.name, role: u.role || 'member',
      seen: u.seen || null, created: u.created || null, pending: !!u.invite,
      tutorialDone: !!u.tutorialDone,
      savedFilters: (u.filters || []).length, savedRoutes: (u.routes || []).length,
    });
  }
  users.sort((a, b) => (b.seen || '').localeCompare(a.seen || ''));

  const routes = log.routes.slice(0, 40);
  const byRep = {};
  for (const r of log.routes) byRep[r.by] = (byRep[r.by] || 0) + 1;

  return {
    org: { id: org?.id, name: org?.name, brands: org?.brands || [], created: org?.created },
    users,
    routes,
    routeCount: log.routes.length,
    routesByRep: byRep,
    sales: sales ? {
      uploadedAt: sales.uploadedAt, uploadedBy: sales.uploadedBy, filename: sales.filename,
      rows: sales.rows, sheet: sales.sheet || null,
      matched: Object.keys(sales.accounts || {}).length,
      unmatched: sales.unmatched || [],
      columns: sales.columns || null,
      overall: sales.overall || null,
      top: Object.entries(sales.accounts || {})
        .map(([lic, a]) => ({ lic, name: a.name, rev: a.rev, orders: a.orders, last: a.last }))
        .sort((x, y) => (y.rev || 0) - (x.rev || 0)).slice(0, 25),
    } : null,
  };
}

export const configured = () => !!bucket && !!SIGNUP_CODE;

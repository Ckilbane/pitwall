// Data loading layer: manifest, season, as-of, trajectory, event, models and history shards.
//
//   getManifest({force}?)  getSeason(y)  getAsof(y)  getTraj(y)  getEvent(y, key)  getModels()  getHistory()
//   fileUrl({path, hash})  tbl(table)  derivePre(eventData, model)  isStale(response)
//   configure({base, fetch})  isAnyStale()  onStale(fn)  decode(arrayBuffer)
//
// Shards are gzipped JSON. Servers may or may not add Content-Encoding, so we check the gzip magic
// bytes (1f 8b) ourselves: DecompressionStream, else the vendored inflate (window.pako).

let cfg = { base: './', fetch: (...a) => globalThis.fetch(...a) };
const memo = new Map();
const staleUrls = new Set();
const staleSubs = new Set();

export function configure(o = {}) {
  cfg = { ...cfg, ...o };
  if (o.reset) memo.clear();
}

export function _reset() { memo.clear(); staleUrls.clear(); }

export function fileUrl(ref) { return `${cfg.base}data/${ref.path}${ref.hash ? `?v=${ref.hash}` : ''}`; }

export function isStale(response) {
  try { return !!response && !!response.headers && response.headers.get('X-PitWall-Stale') === '1'; } catch (e) { return false; }
}
export function isAnyStale() { return staleUrls.size > 0; }
export function onStale(fn) { staleSubs.add(fn); return () => staleSubs.delete(fn); }

let pakoLoading = null;
function loadPako() {
  if (globalThis.pako) return Promise.resolve(globalThis.pako);
  if (typeof document === 'undefined') return Promise.reject(new Error('No gunzip available'));
  if (!pakoLoading) {
    pakoLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = `${cfg.base}vendor/pako/pako_inflate.min.js`;
      s.onload = () => resolve(globalThis.pako);
      s.onerror = () => reject(new Error('Could not load the gunzip fallback'));
      document.head.append(s);
    });
  }
  return pakoLoading;
}

/** Decode an ArrayBuffer of gzip or plain JSON into an object. */
export async function decode(buf) {
  const u8 = new Uint8Array(buf);
  if (u8.length > 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
    if (typeof DecompressionStream !== 'undefined') {
      return new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'))).json();
    }
    const pako = await loadPako();
    return JSON.parse(pako.ungzip(u8, { to: 'string' }));
  }
  return JSON.parse(new TextDecoder().decode(u8));
}

// Set by the service worker's 503 answer (sw.js, WP6) and by a failed fetch while the browser is offline.
export const OFFLINE_MSG = 'You are offline and this data is not saved on this device. Seasons downloaded on the Offline tab, and pages you have already viewed, work without a connection.';

function load(url, opts) {
  if (!memo.has(url)) {
    const p = (async () => {
      let res;
      try {
        res = await cfg.fetch(url, opts);
      } catch (err) {
        if (typeof navigator !== 'undefined' && navigator.onLine === false) throw new Error(OFFLINE_MSG);
        throw err;
      }
      if (res.status === 503 && res.headers && res.headers.get('X-PitWall-Offline') === '1') throw new Error(OFFLINE_MSG);
      if (!res.ok) throw new Error(`Could not load ${url.replace(/\?.*$/, '')} (HTTP ${res.status})`);
      if (isStale(res)) { staleUrls.add(url); for (const fn of staleSubs) { try { fn(); } catch (e) { /* ignore */ } } }
      return decode(await res.arrayBuffer());
    })();
    p.catch(() => memo.delete(url)); // a failed load can be retried
    memo.set(url, p);
  }
  return memo.get(url);
}

export function getManifest({ force } = {}) {
  const url = `${cfg.base}data/manifest.json`;
  if (force) memo.delete(url);
  return load(url, { cache: 'no-cache' });
}

function seasonEntry(m, season) {
  const e = m.seasons.find((s) => s.season === Number(season));
  if (!e) throw new Error(`Season ${season} is not published`);
  return e;
}

export async function getSeason(season) {
  const m = await getManifest();
  return load(fileUrl(seasonEntry(m, season).files.season));
}
export async function getAsof(season) {
  const m = await getManifest();
  return load(fileUrl(seasonEntry(m, season).files.asof));
}
export async function getTraj(season) {
  const m = await getManifest();
  return load(fileUrl(seasonEntry(m, season).files.traj));
}
export async function getModels() {
  const m = await getManifest();
  return load(fileUrl(m.files.models));
}
export async function getHistory() {
  const m = await getManifest();
  return load(fileUrl(m.files.history));
}

const EVENT_KEY = /^[0-9]{4}[a-z0-9]+$/;
export async function getEvent(season, eventKey) {
  if (!EVENT_KEY.test(String(eventKey))) throw new Error(`Bad event key "${eventKey}"`);
  const s = await getSeason(season);
  const t = tbl(s.events);
  const i = t.index('key').get(eventKey);
  if (i === undefined) throw new Error(`No event ${eventKey} in ${season}`);
  const hash = t.get(i, 'file_hash');
  if (!hash) throw new Error(`Event ${eventKey} has no data file`);
  return load(fileUrl({ path: `${season}/events/${eventKey}.json.gz`, hash }));
}

// ---- columnar tables ----
const tblCache = new WeakMap();

/** Wrap a columnar table {n, cols, models} with accessors. Memoized per table object. */
export function tbl(t) {
  if (tblCache.has(t)) return tblCache.get(t);
  const rowsCache = new Map();
  const idxCache = new Map();
  const api = {
    n: t.n,
    raw: t,
    has(name, model) { return !!(model && t.models && t.models[model] && name in t.models[model]) || name in t.cols; },
    col(name, model) {
      if (model && t.models && t.models[model] && t.models[model][name]) return t.models[model][name];
      return t.cols[name];
    },
    get(i, name, model) { const c = api.col(name, model); return c ? c[i] : undefined; },
    /** Row objects merging cols and models[model] (model columns win on name clashes). */
    rows(model) {
      const key = model || '';
      if (!rowsCache.has(key)) {
        const names = Object.keys(t.cols);
        const mcols = model && t.models && t.models[model] ? Object.entries(t.models[model]) : [];
        const out = new Array(t.n);
        for (let i = 0; i < t.n; i++) {
          const r = { _i: i };
          for (const k of names) r[k] = t.cols[k][i];
          for (const [k, c] of mcols) r[k] = c[i];
          out[i] = r;
        }
        rowsCache.set(key, out);
      }
      return rowsCache.get(key);
    },
    /** Map value -> row index for a cols column (first occurrence wins). */
    index(name) {
      if (!idxCache.has(name)) {
        const m = new Map();
        const c = t.cols[name] || [];
        for (let i = 0; i < c.length; i++) if (!m.has(c[i])) m.set(c[i], i);
        idxCache.set(name, m);
      }
      return idxCache.get(name);
    },
  };
  tblCache.set(t, api);
  return api;
}

/** Pre-match vectors for each team_matches row (SPEC 3.5.4): the model's post vector from the
 *  team's nearest earlier row with a non-null post, else the team_events pre_* vector.
 *  Returns an array of {vk: value} objects (null when the team has no team_events row). */
export function derivePre(ev, model) {
  const tm = ev.team_matches;
  const post = tm.models[model];
  const vks = Object.keys(post).filter((k) => !k.startsWith('attr_'));
  const te = ev.team_events;
  const teIdx = tbl(te).index('team');
  const preCols = te.models[model];
  const last = new Map(); // team -> row index of latest non-null post
  const out = new Array(tm.n);
  for (let r = 0; r < tm.n; r++) {
    const team = tm.cols.team[r];
    const li = last.get(team);
    let pre = null;
    if (li !== undefined) {
      pre = {};
      for (const k of vks) pre[k] = post[k][li];
    } else {
      const ti = teIdx.get(team);
      if (ti !== undefined) {
        pre = {};
        for (const k of vks) { const c = preCols[`pre_${k}`]; pre[k] = c ? c[ti] : null; }
      }
    }
    out[r] = pre;
    if (post.epa[r] !== null && post.epa[r] !== undefined) last.set(team, r);
  }
  return out;
}

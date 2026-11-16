// Offline support (SPEC 6, WP6): service-worker registration and update prompt, the offline
// indicator, and "download season for offline".
//
//   registerWorker()                      register ./sw.js, show "A new version ... Reload" when one waits
//   initIndicator()                       keep #pw-net ("Offline: showing saved data") in step with the network
//   seasonFiles(manifest, seasonData)     -> [{url, path, bytes}] every file a season's views read
//   summarize(expected, cachedUrls)       -> {have, total, haveBytes, totalBytes}   (pure)
//   pool(items, n, fn, signal)            run fn over items, n at a time
//   seasonStatus(manifest, season)        -> what is cached for a season (reads the cache only)
//   download(season, {onProgress, signal}) / remove(season) / record() / storage()
//
// Data files go to the cache "pw-data", the same one the worker uses. The page writes entries itself
// when no worker controls it yet (first visit), so a download never depends on worker timing.
import * as prefs from './prefs.js';
import { fileUrl, getManifest, getSeason, decode } from './data.js';

export const DATA_CACHE = 'pw-data';
const OFFLINE_KEY = prefs.KEYS.offline; // pw.v1.offline = {season: data_version}

const hasCaches = () => typeof caches !== 'undefined';

/** Absolute URL for a site-relative one (keys in CacheStorage are absolute). */
export function absUrl(u, base) {
  return new URL(u, base || (typeof document !== 'undefined' ? document.baseURI : 'http://localhost/')).href;
}

// ---------------------------------------------------------------- pure helpers

/** Every file the views of one season read: season, asof, traj, each event file with a hash, plus
 *  the global models and history files. bytes from the manifest and season.events.file_bytes. */
export function seasonFiles(manifest, seasonData) {
  const y = Number(seasonData && seasonData.season);
  const entry = manifest.seasons.find((s) => s.season === y);
  if (!entry) return [];
  const out = [];
  const add = (ref) => { if (ref && ref.path) out.push({ url: fileUrl(ref), path: ref.path, bytes: ref.bytes || 0 }); };
  add(entry.files.season); add(entry.files.asof); add(entry.files.traj);
  const ev = seasonData.events;
  if (ev && ev.cols && ev.cols.key) {
    const keys = ev.cols.key; const hashes = ev.cols.file_hash || []; const bytes = ev.cols.file_bytes || [];
    for (let i = 0; i < keys.length; i++) {
      if (hashes[i]) add({ path: `${y}/events/${keys[i]}.json.gz`, hash: hashes[i], bytes: bytes[i] || 0 });
    }
  }
  if (manifest.files) { add(manifest.files.models); add(manifest.files.history); }
  return out;
}

/** Expected size of a season without reading season.json: its three files plus events_bytes. */
export function seasonBytes(entry) {
  const f = entry.files || {};
  return ['season', 'asof', 'traj'].reduce((a, k) => a + ((f[k] && f[k].bytes) || 0), 0) + (entry.events_bytes || 0);
}

/** How much of `expected` ([{url, bytes}]) is in cachedUrls (Set of absolute or relative urls). */
export function summarize(expected, cachedUrls, base) {
  let have = 0; let haveBytes = 0; let totalBytes = 0;
  for (const f of expected) {
    totalBytes += f.bytes || 0;
    if (cachedUrls.has(f.url) || cachedUrls.has(absUrl(f.url, base))) { have++; haveBytes += f.bytes || 0; }
  }
  return { have, total: expected.length, haveBytes, totalBytes };
}

/** Run fn(item, i) over items with at most n running at once. Stops starting new ones when the
 *  signal aborts; rejects with the first error. */
export async function pool(items, n, fn, signal) {
  let next = 0;
  let failed = null;
  async function worker() {
    while (next < items.length && !failed) {
      if (signal && signal.aborted) return;
      const i = next++;
      try { await fn(items[i], i); } catch (e) { failed = failed || e; }
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(n, items.length)) }, worker));
  if (failed) throw failed;
}

/** "21.0 MB" (one decimal; at least 0.1 MB for any non-empty size). */
export function mb(bytes) {
  if (!bytes) return '0 MB';
  return `${Math.max(0.1, bytes / 1e6).toFixed(1)} MB`;
}

// ---------------------------------------------------------------- record (localStorage)

export function record() {
  const r = prefs.read(OFFLINE_KEY, {});
  return r && typeof r === 'object' && !Array.isArray(r) ? r : {};
}
function setRecord(season, value) {
  const r = { ...record() };
  if (value === null) delete r[season]; else r[season] = value;
  prefs.write(OFFLINE_KEY, r);
}

// ---------------------------------------------------------------- cache access

async function cachedUrlSet() {
  if (!hasCaches()) return new Set();
  const c = await caches.open(DATA_CACHE);
  return new Set((await c.keys()).map((r) => r.url));
}

/** What is saved for one season. Reads season.json from the cache (not the network) when its
 *  current version is there, to check every event file exactly. */
export async function seasonStatus(manifest, season, base) {
  const entry = manifest.seasons.find((s) => s.season === Number(season));
  const totalBytes = seasonBytes(entry);
  const totalFiles = 3 + (entry.events_count || 0);
  const urls = await cachedUrlSet();
  const prefix = absUrl(`./data/${season}/`, base);
  const anyCached = [...urls].filter((u) => u.startsWith(prefix));
  const seasonUrl = absUrl(fileUrl(entry.files.season), base);
  let s = null;
  if (urls.has(seasonUrl) && hasCaches()) {
    try {
      const res = await (await caches.open(DATA_CACHE)).match(seasonUrl, { ignoreVary: true });
      if (res) {
        const sd = await decode(await res.arrayBuffer());
        const exp = seasonFiles(manifest, sd).filter((f) => f.path.startsWith(`${season}/`));
        s = summarize(exp, urls, base);
      }
    } catch (e) { s = null; }
  }
  if (!s) {
    const cur = [entry.files.season, entry.files.asof, entry.files.traj].filter((r) => urls.has(absUrl(fileUrl(r), base)));
    s = { have: cur.length, total: totalFiles, haveBytes: cur.reduce((a, r) => a + (r.bytes || 0), 0), totalBytes };
  }
  return { season: entry.season, game: entry.game, ...s, older: Math.max(0, anyCached.length - s.have), recorded: record()[season] || null };
}

/** Store a fetched response under url unless the worker already did; evict other versions. */
async function ensureCached(cache, url, res) {
  if (await cache.match(url, { ignoreVary: true })) return;
  if (!res || res.status !== 200) throw new Error(`Could not save ${url.replace(/\?.*$/, '')} (HTTP ${res ? res.status : 'none'})`);
  if (res.headers.get('X-PitWall-Stale') === '1') throw new Error('You are offline; the latest version of this file could not be fetched.');
  await cache.put(url, res);
  const u = new URL(url);
  const keys = await cache.keys(u.origin + u.pathname, { ignoreSearch: true, ignoreVary: true });
  await Promise.all(keys.filter((k) => k.url !== url).map((k) => cache.delete(k)));
}

const jobs = new Map(); // season -> {done, total, bytesDone, bytesTotal, error, finished, controller}
const jobSubs = new Set();
export function job(season) { return jobs.get(Number(season)) || null; }
export function onJob(fn) { jobSubs.add(fn); return () => jobSubs.delete(fn); }
function emit(season) { for (const fn of jobSubs) { try { fn(season, jobs.get(season)); } catch (e) { /* ignore */ } } }

/** Download every file of a season into the cache, 4 at a time. Resolves with the job. */
export async function download(season, { base } = {}) {
  season = Number(season);
  if (!hasCaches()) throw new Error('This browser cannot save files for offline use.');
  const running = jobs.get(season);
  if (running && !running.finished) return running.promise;
  const controller = new AbortController();
  const j = { done: 0, total: 0, bytesDone: 0, bytesTotal: 0, error: null, finished: false, cancelled: false, controller };
  jobs.set(season, j);
  j.promise = (async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.storage && navigator.storage.persist) {
        try { await navigator.storage.persist(); } catch (e) { /* best effort */ }
      }
      const manifest = await getManifest();
      const sd = await getSeason(season);
      const files = seasonFiles(manifest, sd);
      j.total = files.length;
      j.bytesTotal = files.reduce((a, f) => a + f.bytes, 0);
      emit(season);
      const cache = await caches.open(DATA_CACHE);
      const man = absUrl('./data/manifest.json', base);
      if (!(await cache.match(man, { ignoreVary: true }))) {
        try { await ensureCached(cache, man, await fetch(man, { cache: 'no-cache' })); } catch (e) { /* the worker keeps it */ }
      }
      await pool(files, 4, async (f) => {
        const url = absUrl(f.url, base);
        if (!(await cache.match(url, { ignoreVary: true }))) {
          const res = await fetch(url, { signal: controller.signal });
          await ensureCached(cache, url, res);
        }
        j.done++; j.bytesDone += f.bytes;
        emit(season);
      }, controller.signal);
      if (controller.signal.aborted) { j.cancelled = true; } else { setRecord(season, manifest.data_version); }
    } catch (err) {
      if (controller.signal.aborted) j.cancelled = true; else j.error = err;
    } finally {
      j.finished = true;
      emit(season);
    }
    return j;
  })();
  return j.promise;
}

export function cancel(season) {
  const j = jobs.get(Number(season));
  if (j && !j.finished) j.controller.abort();
}

/** Delete every cached file of a season (the global models/history files stay). */
export async function remove(season, { base } = {}) {
  season = Number(season);
  setRecord(season, null);
  jobs.delete(season);
  if (!hasCaches()) return 0;
  const cache = await caches.open(DATA_CACHE);
  const prefix = absUrl(`./data/${season}/`, base);
  const keys = (await cache.keys()).filter((r) => r.url.startsWith(prefix));
  await Promise.all(keys.map((k) => cache.delete(k)));
  emit(season);
  return keys.length;
}

/** {usage, quota, persisted} or nulls where the browser does not say. */
export async function storage() {
  const out = { usage: null, quota: null, persisted: null };
  try {
    if (navigator.storage && navigator.storage.estimate) { const e = await navigator.storage.estimate(); out.usage = e.usage; out.quota = e.quota; }
    if (navigator.storage && navigator.storage.persisted) out.persisted = await navigator.storage.persisted();
  } catch (e) { /* unknown */ }
  return out;
}

export function support() {
  return {
    worker: typeof navigator !== 'undefined' && 'serviceWorker' in navigator,
    caches: hasCaches(),
    controlled: typeof navigator !== 'undefined' && !!(navigator.serviceWorker && navigator.serviceWorker.controller),
  };
}

// ---------------------------------------------------------------- indicator and worker

/** Text for the header indicator (the stale-data notice under the header covers the online case). */
export function indicatorText(online) {
  return online ? '' : 'Offline: showing saved data';
}

export function initIndicator() {
  const el = document.getElementById('pw-net');
  if (!el) return;
  const update = () => {
    const t = indicatorText(navigator.onLine !== false);
    if (el.textContent !== t) el.textContent = t;
  };
  window.addEventListener('online', update);
  window.addEventListener('offline', update);
  update();
}

function showUpdate(worker) {
  const el = document.getElementById('pw-update');
  if (!el || el.dataset.shown) return;
  el.dataset.shown = '1';
  el.innerHTML = '<div class="pw-notice pw-update"><span>A new version of PitWall is ready.</span> <button type="button" class="pw-btn" data-act="sw-reload">Reload</button></div>';
  const live = document.getElementById('pw-live');
  if (live) live.textContent = 'A new version of PitWall is ready. Use the Reload button under the header to switch to it.';
  el.querySelector('button').addEventListener('click', () => {
    let reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (!reloaded) { reloaded = true; location.reload(); } });
    worker.postMessage({ type: 'SKIP_WAITING' });
    setTimeout(() => { if (!reloaded) { reloaded = true; location.reload(); } }, 3000);
  });
}

export async function registerWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null;
  let reg;
  try {
    reg = await navigator.serviceWorker.register('./sw.js', { scope: './' });
  } catch (e) { return null; } // offline support is optional (http://, private mode, no sw.js)
  const hadController = !!navigator.serviceWorker.controller;
  const watch = (w) => {
    if (!w) return;
    w.addEventListener('statechange', () => { if (w.state === 'installed' && navigator.serviceWorker.controller) showUpdate(w); });
  };
  if (reg.waiting && hadController) showUpdate(reg.waiting);
  watch(reg.installing);
  reg.addEventListener('updatefound', () => watch(reg.installing));
  return reg;
}

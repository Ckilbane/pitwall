/* PitWall service worker (SPEC 6). Classic script, scope "./".
 *
 *   App shell   cache-first from pw-shell-{VERSION}; install precaches every file in sw-precache.json
 *               (written by tools/build_precache.py at stage time, which also writes VERSION below),
 *               checking each body's SHA-256 against the list. Navigations get the cached index.html.
 *   Manifest    data/manifest.json network-first with a 4 s timeout, fallback to pw-data.
 *   Shards      every other data/ file cache-first by exact URL (?v=hash makes them immutable); a miss
 *               is fetched and stored, then other cached versions of the same path are deleted. Offline
 *               (or the server fails) and that version is not cached: any cached version of the path is
 *               served with header X-PitWall-Stale: 1.
 *   Errors      only 200 same-origin responses are ever stored.
 *   Clean-up    activate deletes every pw-* cache except this version's shell and pw-data.
 *   Update      a waiting worker activates when the page posts {type: 'SKIP_WAITING'}.
 *
 * With VERSION 'dev' (the unstaged source tree) nothing is precached and the shell is network-first.
 */
'use strict';

const VERSION = 'dev';
const SHELL_PREFIX = 'pw-shell-';
const SHELL = SHELL_PREFIX + VERSION;
const DATA = 'pw-data';
const MANIFEST_TIMEOUT_MS = 4000;
const PRECACHE_CONCURRENCY = 6;
const MATCH = { ignoreVary: true };
const SCOPE = (self.registration && self.registration.scope) || new URL('./', self.location.href).href;
const SCOPE_PATH = new URL(SCOPE).pathname;

function rel(url) {
  return url.pathname.startsWith(SCOPE_PATH) ? url.pathname.slice(SCOPE_PATH.length) : null;
}

function cacheable(res) {
  return !!res && res.status === 200 && (res.type === 'basic' || res.type === 'default');
}

async function sha256hex12(buf) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
  let s = '';
  for (let i = 0; i < 6; i++) s += d[i].toString(16).padStart(2, '0');
  return s;
}

/** A copy of res with extra headers (body re-read; used for stale marks and hash tags). */
async function withHeaders(res, extra) {
  const h = new Headers(res.headers);
  for (const [k, v] of Object.entries(extra)) h.set(k, v);
  return new Response(await res.arrayBuffer(), { status: res.status, statusText: res.statusText, headers: h });
}

function offlineResponse(what) {
  return new Response(`PitWall is offline and ${what} is not saved on this device.`, {
    status: 503, statusText: 'Offline', headers: { 'Content-Type': 'text/plain; charset=utf-8', 'X-PitWall-Offline': '1' },
  });
}

// ---------------------------------------------------------------- install: precache the shell

async function precache() {
  if (VERSION === 'dev') return;
  const res = await fetch(new Request(`${SCOPE}sw-precache.json?v=${VERSION}`, { cache: 'reload' }));
  if (!res.ok) throw new Error(`sw-precache.json: HTTP ${res.status}`);
  const list = await res.json();
  if (list.version !== VERSION) throw new Error(`sw-precache.json is version ${list.version}, worker is ${VERSION}`);
  const cache = await caches.open(SHELL);
  const older = (await caches.keys()).filter((k) => k.startsWith(SHELL_PREFIX) && k !== SHELL);
  const files = list.files.slice();
  async function one(f) {
    const key = SCOPE + f.url;
    if (await cache.match(key)) return;
    for (const name of older) { // unchanged file: copy from the previous shell instead of downloading
      const prev = await (await caches.open(name)).match(key);
      if (prev && prev.headers.get('X-PitWall-Hash') === f.hash) { await cache.put(key, prev); return; }
    }
    const r = await fetch(new Request(`${key}?v=${f.hash}`, { cache: 'no-cache' }));
    if (!cacheable(r)) throw new Error(`${f.url}: HTTP ${r.status}`);
    const buf = await r.arrayBuffer();
    const got = await sha256hex12(buf);
    if (got !== f.hash) throw new Error(`${f.url}: hash ${got}, expected ${f.hash} (deploy still propagating?)`);
    const h = new Headers(r.headers);
    h.set('X-PitWall-Hash', f.hash);
    await cache.put(key, new Response(buf, { status: 200, statusText: 'OK', headers: h }));
  }
  const workers = [];
  for (let w = 0; w < PRECACHE_CONCURRENCY; w++) {
    workers.push((async () => { while (files.length) await one(files.shift()); })());
  }
  await Promise.all(workers);
}

self.addEventListener('install', (event) => {
  event.waitUntil(precache().catch(async (err) => {
    await caches.delete(SHELL); // never leave a half-filled shell behind
    throw err;
  }));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith('pw-') && k !== SHELL && k !== DATA).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  const d = event.data;
  if (d && d.type === 'SKIP_WAITING') self.skipWaiting();
  else if (d && d.type === 'VERSION' && event.ports && event.ports[0]) event.ports[0].postMessage({ version: VERSION });
});

// ---------------------------------------------------------------- fetch

async function shellResponse(req, path) {
  const cache = await caches.open(SHELL);
  const key = SCOPE + path;
  if (VERSION !== 'dev') {
    const hit = await cache.match(key, MATCH);
    if (hit) return hit;
  }
  try {
    const res = await fetch(req);
    if (VERSION === 'dev' && cacheable(res)) await cache.put(key, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(key, MATCH);
    if (hit) return hit;
    return offlineResponse(path);
  }
}

async function navigation(req) {
  const res = await shellResponse(req, 'index.html');
  // A response that came from the cache under another URL must not be "redirected"; rebuild if so.
  return res.redirected ? new Response(await res.arrayBuffer(), { status: res.status, headers: res.headers }) : res;
}

async function manifestResponse(event, req) {
  const cache = await caches.open(DATA);
  const key = `${SCOPE}data/manifest.json`;
  const net = fetch(req).then(async (res) => {
    if (cacheable(res)) await cache.put(key, res.clone());
    return res;
  });
  event.waitUntil(net.catch(() => {})); // a late answer still refreshes the cache
  let timer;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), MANIFEST_TIMEOUT_MS); });
  let res = null;
  try { res = await Promise.race([net, timeout]); } catch (err) { res = null; } finally { clearTimeout(timer); }
  if (res && res.ok) return res;
  const old = await cache.match(key, MATCH);
  if (old) return withHeaders(old, { 'X-PitWall-Stale': '1' });
  return res || offlineResponse('the data index');
}

/** Delete cached entries for the same path as url with a different query (older versions). */
async function evictOtherVersions(cache, url) {
  const keys = await cache.keys(url.origin + url.pathname, { ignoreSearch: true, ignoreVary: true });
  await Promise.all(keys.filter((k) => k.url !== url.href).map((k) => cache.delete(k)));
}

async function shardResponse(req, url) {
  const cache = await caches.open(DATA);
  const hit = await cache.match(url.href, MATCH);
  if (hit) return hit;
  let res = null;
  try { res = await fetch(req); } catch (err) { res = null; }
  if (cacheable(res)) {
    await cache.put(url.href, res.clone());
    await evictOtherVersions(cache, url);
    return res;
  }
  const old = await cache.match(url.origin + url.pathname, { ignoreSearch: true, ignoreVary: true });
  if (old) return withHeaders(old, { 'X-PitWall-Stale': '1' });
  return res || offlineResponse(url.pathname.slice(SCOPE_PATH.length));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const path = rel(url);
  if (path === null) return;
  if (req.mode === 'navigate') { event.respondWith(navigation(req)); return; }
  if (path === 'sw.js' || path === 'sw-precache.json') return;
  if (path === 'data/manifest.json') { event.respondWith(manifestResponse(event, req)); return; }
  if (path.startsWith('data/')) { event.respondWith(shardResponse(req, url)); return; }
  event.respondWith(shellResponse(req, path === '' ? 'index.html' : path));
});

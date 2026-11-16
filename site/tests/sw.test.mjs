// Service worker, precache list generator and offline helpers (WP6, SPEC 6).
//  - tools/build_precache.py on a temp tree: what is listed, hashes, version injection, determinism
//  - sw.js run in a vm with fake CacheStorage/fetch: install, activate clean-up, shell cache-first,
//    navigation fallback, manifest network-first with timeout, shard cache-first + eviction + stale
//    fallback, never caching errors
//  - pure helpers of js/core/offline.js and js/views/offline.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import vm from 'node:vm';

import { seasonFiles, summarize, pool, mb, seasonBytes, indicatorText } from '../js/core/offline.js';
import { statusText, progressText, ledeText } from '../js/views/offline.js';

const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const SW_SRC = readFileSync(join(SITE, 'sw.js'), 'utf8');
const PRE = join(SITE, 'tools', 'build_precache.py');
const h12 = (s) => createHash('sha256').update(s).digest('hex').slice(0, 12);

// ---------------------------------------------------------------- build_precache.py

function tree(files) {
  const root = mkdtempSync(join(tmpdir(), 'pw-pre-'));
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true });
    writeFileSync(join(root, rel), body);
  }
  return root;
}
const runPre = (root) => spawnSync('python3', [PRE, root], { encoding: 'utf8' });
const BASE_TREE = {
  'index.html': '<!doctype html>', 'manifest.webmanifest': '{}', 'sw.js': SW_SRC,
  'css/a.css': 'a{}', 'js/app.js': 'x', 'js/views/v.js': 'v', 'assets/fonts/F-400.woff2': 'font',
  'brand/icon-192.png': 'png', 'vendor/pako/pako_inflate.min.js': 'p', 'vendor/pako/LICENSE': 'MIT',
  'data/manifest.json': '{}', 'data/2025/season.json.gz': 'gz', 'tests/x.test.mjs': 't', 'tools/t.py': 'py',
  'js/.hidden': 'h', 'README.md': 'r', 'other.txt': 'o',
};

test('build_precache lists exactly the shell files, sorted, with hash12 of their bytes', () => {
  const root = tree(BASE_TREE);
  const r = runPre(root);
  assert.equal(r.status, 0, r.stderr);
  const pre = JSON.parse(readFileSync(join(root, 'sw-precache.json'), 'utf8'));
  assert.deepEqual(pre.files.map((f) => f.url), [
    'assets/fonts/F-400.woff2', 'brand/icon-192.png', 'css/a.css', 'index.html', 'js/app.js', 'js/views/v.js',
    'manifest.webmanifest', 'vendor/pako/LICENSE', 'vendor/pako/pako_inflate.min.js',
  ]);
  for (const f of pre.files) assert.equal(f.hash, h12(readFileSync(join(root, f.url))), f.url);
  assert.match(pre.version, /^[0-9a-f]{12}$/);
  const sw = readFileSync(join(root, 'sw.js'), 'utf8');
  assert.ok(sw.includes(`const VERSION = '${pre.version}';`), 'version written into sw.js');
  assert.equal(sw.split('\n').length, SW_SRC.split('\n').length, 'only the VERSION line changes');
});

test('build_precache is deterministic and idempotent; version follows shell files and sw code only', () => {
  const a = tree(BASE_TREE); const b = tree(BASE_TREE);
  runPre(a); runPre(b);
  const va = JSON.parse(readFileSync(join(a, 'sw-precache.json'), 'utf8')).version;
  assert.equal(JSON.parse(readFileSync(join(b, 'sw-precache.json'), 'utf8')).version, va, 'same tree, same version');
  assert.equal(runPre(a).status, 0);
  assert.equal(JSON.parse(readFileSync(join(a, 'sw-precache.json'), 'utf8')).version, va, 're-run on a staged tree keeps the version');
  const c = tree({ ...BASE_TREE, 'data/2025/season.json.gz': 'other data' });
  runPre(c);
  assert.equal(JSON.parse(readFileSync(join(c, 'sw-precache.json'), 'utf8')).version, va, 'data does not change the shell version');
  const d = tree({ ...BASE_TREE, 'css/a.css': 'a{color:inherit}' });
  runPre(d);
  assert.notEqual(JSON.parse(readFileSync(join(d, 'sw-precache.json'), 'utf8')).version, va, 'a shell file change changes the version');
  const e = tree({ ...BASE_TREE, 'sw.js': SW_SRC.replace('const MANIFEST_TIMEOUT_MS = 4000;', 'const MANIFEST_TIMEOUT_MS = 3000;') });
  runPre(e);
  assert.notEqual(JSON.parse(readFileSync(join(e, 'sw-precache.json'), 'utf8')).version, va, 'worker code changes the version');
});

test('build_precache refuses a tree without sw.js or without exactly one VERSION line', () => {
  const { ['sw.js']: _drop, ...noSw } = BASE_TREE;
  assert.equal(runPre(tree(noSw)).status, 1);
  assert.equal(runPre(tree({ ...BASE_TREE, 'sw.js': '// nothing' })).status, 1);
  const two = SW_SRC.replace("const VERSION = 'dev';", "const VERSION = 'dev';\nconst VERSION = 'x';");
  assert.equal(runPre(tree({ ...BASE_TREE, 'sw.js': two })).status, 1);
  assert.equal(existsSync(join(tree(noSw), 'sw-precache.json')), false);
});

test('the source sw.js ships as version dev', () => {
  assert.equal((SW_SRC.match(/^const VERSION = '[^']*';$/gm) || []).length, 1);
  assert.ok(SW_SRC.includes("const VERSION = 'dev';"));
});

// ---------------------------------------------------------------- sw.js in a vm

const ORIGIN = 'https://pw.test';
const SCOPE = `${ORIGIN}/pitwall/`;

class FakeCache {
  constructor() { this.m = new Map(); }
  static u(r) { return typeof r === 'string' ? r : r.url; }
  async match(r, o = {}) {
    const u = FakeCache.u(r);
    if (o.ignoreSearch) { for (const [k, v] of this.m) if (k.split('?')[0] === u.split('?')[0]) return v.clone(); return undefined; }
    const v = this.m.get(u); return v ? v.clone() : undefined;
  }
  async put(r, res) { this.m.set(FakeCache.u(r), res.clone()); }
  async delete(r) { return this.m.delete(FakeCache.u(r)); }
  async keys(r, o = {}) {
    let ks = [...this.m.keys()];
    if (r) { const u = FakeCache.u(r); ks = ks.filter((k) => (o.ignoreSearch ? k.split('?')[0] === u.split('?')[0] : k === u)); }
    return ks.map((k) => new Request(k));
  }
}
class FakeCaches {
  constructor() { this.c = new Map(); }
  async open(n) { if (!this.c.has(n)) this.c.set(n, new FakeCache()); return this.c.get(n); }
  async keys() { return [...this.c.keys()]; }
  async delete(n) { return this.c.delete(n); }
  async has(n) { return this.c.has(n); }
  async match(r) { for (const c of this.c.values()) { const x = await c.match(r); if (x) return x; } return undefined; }
}

/** Load sw.js (optionally with a VERSION) into a fresh vm. net: url -> body | {status, body} | 'down' | 'hang'. */
function loadSW({ version = 'dev', net = {} } = {}) {
  const handlers = {};
  const calls = [];
  const caches = new FakeCaches();
  const fetchFn = async (req) => {
    const url = typeof req === 'string' ? req : req.url;
    calls.push(url);
    let v = net[url];
    if (v === undefined) v = net[url.split('?')[0]];
    if (v === undefined || v === 'down') throw new TypeError('Failed to fetch');
    if (v === 'hang') return new Promise(() => {});
    const o = typeof v === 'string' ? { status: 200, body: v } : v;
    return new Response(o.body, { status: o.status, headers: { 'Content-Type': 'text/plain' } });
  };
  const self = {
    registration: { scope: SCOPE },
    location: { href: `${SCOPE}sw.js`, origin: ORIGIN },
    addEventListener: (t, fn) => { handlers[t] = fn; },
    clients: { claim: async () => { self.claimed = true; } },
    skipWaiting: () => { self.skipped = true; },
  };
  const src = SW_SRC.replace("const VERSION = 'dev';", `const VERSION = '${version}';`);
  vm.runInNewContext(src, {
    self, caches, fetch: fetchFn, Request, Response, Headers, URL, crypto: globalThis.crypto, console,
    setTimeout: (fn, ms) => setTimeout(fn, ms >= 1000 ? 20 : ms), clearTimeout,
  });
  const fire = async (type, extra = {}) => {
    let p = null; const bg = [];
    const ev = { ...extra, waitUntil: (x) => { if (type === 'fetch') bg.push(x); else p = x; }, respondWith: (x) => { p = x; } };
    handlers[type](ev);
    const out = p === null ? undefined : await p;
    await Promise.race([Promise.allSettled(bg), new Promise((r) => setTimeout(r, 50))]);
    return out;
  };
  const get = (path, o = {}) => fire('fetch', { request: { method: o.method || 'GET', url: path.startsWith('http') ? path : SCOPE + path, mode: o.mode || 'cors' } });
  return { self, caches, calls, fire, get, handlers };
}

function precacheWorld(files, version = 'v1') {
  const list = { version, files: Object.entries(files).map(([url, body]) => ({ url, hash: h12(body) })) };
  const net = { [`${SCOPE}sw-precache.json`]: JSON.stringify(list) };
  for (const [url, body] of Object.entries(files)) net[SCOPE + url] = body;
  return { list, net };
}

test('install precaches every listed file into pw-shell-{version}, checking hashes', async () => {
  const { net } = precacheWorld({ 'index.html': '<html>', 'js/app.js': 'app', 'css/a.css': 'css' });
  const sw = loadSW({ version: 'v1', net });
  await sw.fire('install');
  const shell = await sw.caches.open('pw-shell-v1');
  assert.deepEqual((await shell.keys()).map((r) => r.url).sort(), ['css/a.css', 'index.html', 'js/app.js'].map((u) => SCOPE + u));
  assert.equal(await (await shell.match(`${SCOPE}js/app.js`)).text(), 'app');
  assert.ok(sw.calls.some((u) => u.startsWith(`${SCOPE}js/app.js?v=`)), 'fetched with ?v=hash');
});

test('install fails and leaves no half-filled shell when a body does not match its hash', async () => {
  const { net } = precacheWorld({ 'index.html': '<html>', 'js/app.js': 'app' });
  net[`${SCOPE}js/app.js`] = 'old app from a stale CDN';
  const sw = loadSW({ version: 'v1', net });
  await assert.rejects(sw.fire('install'), /hash/);
  assert.equal(await sw.caches.has('pw-shell-v1'), false);
});

test('install refuses a precache list for another version, and an HTTP error', async () => {
  const { net } = precacheWorld({ 'index.html': '<html>' }, 'v0');
  await assert.rejects(loadSW({ version: 'v1', net }).fire('install'), /version/);
  const { net: n2 } = precacheWorld({ 'index.html': '<html>', 'js/app.js': 'app' });
  n2[`${SCOPE}js/app.js`] = { status: 404, body: 'nope' };
  await assert.rejects(loadSW({ version: 'v1', net: n2 }).fire('install'), /404/);
});

test('install copies unchanged files from the previous shell instead of downloading them', async () => {
  const w1 = precacheWorld({ 'index.html': '<html>', 'js/app.js': 'app' }, 'v1');
  const sw = loadSW({ version: 'v1', net: w1.net });
  await sw.fire('install');
  const w2 = precacheWorld({ 'index.html': '<html>', 'js/app.js': 'app 2' }, 'v2');
  const sw2 = loadSW({ version: 'v2', net: w2.net });
  sw2.caches.c = sw.caches.c; // same origin storage
  await sw2.fire('install');
  assert.ok(!sw2.calls.some((u) => u.startsWith(`${SCOPE}index.html`)), 'index.html reused');
  assert.ok(sw2.calls.some((u) => u.startsWith(`${SCOPE}js/app.js`)), 'changed file fetched');
  assert.equal(await (await (await sw2.caches.open('pw-shell-v2')).match(`${SCOPE}js/app.js`)).text(), 'app 2');
});

test('activate deletes other pw- caches, keeps this shell, pw-data and foreign caches, and claims clients', async () => {
  const sw = loadSW({ version: 'v2' });
  for (const n of ['pw-shell-v1', 'pw-shell-v2', 'pw-data', 'pw-old-thing', 'someone-else']) await sw.caches.open(n);
  await sw.fire('activate');
  assert.deepEqual((await sw.caches.keys()).sort(), ['pw-data', 'pw-shell-v2', 'someone-else']);
  assert.equal(sw.self.claimed, true);
});

test('SKIP_WAITING message activates a waiting worker', async () => {
  const sw = loadSW();
  sw.handlers.message({ data: { type: 'SKIP_WAITING' } });
  assert.equal(sw.self.skipped, true);
});

test('shell is cache-first; navigations get the cached index.html; network is a fallback only', async () => {
  const { net } = precacheWorld({ 'index.html': '<html>v1', 'js/app.js': 'app' });
  const sw = loadSW({ version: 'v1', net });
  await sw.fire('install');
  for (const k of Object.keys(net)) net[k] = 'down';
  sw.calls.length = 0;
  assert.equal(await (await sw.get('js/app.js')).text(), 'app');
  assert.equal(await (await sw.get('', { mode: 'navigate' })).text(), '<html>v1');
  assert.equal(await (await sw.get('index.html?utm=x', { mode: 'navigate' })).text(), '<html>v1');
  assert.equal(sw.calls.length, 0, 'no network for cached shell');
  const miss = await sw.get('js/not-listed.js');
  assert.equal(miss.status, 503);
});

test('requests outside the worker\'s job are not answered', async () => {
  const sw = loadSW({ version: 'v1' });
  assert.equal(await sw.get('data/2025/season.json.gz', { method: 'POST' }), undefined);
  assert.equal(await sw.get('https://fonts.example.com/x.woff2'), undefined);
  assert.equal(await sw.get(`${ORIGIN}/other-app/index.html`), undefined);
  assert.equal(await sw.get('sw.js'), undefined);
  assert.equal(await sw.get('sw-precache.json'), undefined);
});

test('manifest: network-first, cached on success, cached copy marked stale when offline or slow', async () => {
  const net = { [`${SCOPE}data/manifest.json`]: '{"v":1}' };
  const sw = loadSW({ version: 'v1', net });
  const r1 = await sw.get('data/manifest.json');
  assert.equal(await r1.text(), '{"v":1}');
  assert.equal(r1.headers.get('X-PitWall-Stale'), null);
  net[`${SCOPE}data/manifest.json`] = '{"v":2}';
  assert.equal(await (await sw.get('data/manifest.json')).text(), '{"v":2}', 'network wins when online');
  net[`${SCOPE}data/manifest.json`] = 'down';
  const r3 = await sw.get('data/manifest.json');
  assert.equal(await r3.text(), '{"v":2}');
  assert.equal(r3.headers.get('X-PitWall-Stale'), '1');
  net[`${SCOPE}data/manifest.json`] = 'hang';
  const r4 = await sw.get('data/manifest.json');
  assert.equal(r4.headers.get('X-PitWall-Stale'), '1', 'timeout falls back to the cache');
  net[`${SCOPE}data/manifest.json`] = { status: 500, body: 'oops' };
  const r5 = await sw.get('data/manifest.json');
  assert.equal(await r5.text(), '{"v":2}', 'a server error is never cached and the cache answers');
});

test('manifest with nothing cached and no network gives a 503 the app can explain', async () => {
  const sw = loadSW({ version: 'v1', net: {} });
  const r = await sw.get('data/manifest.json');
  assert.equal(r.status, 503);
  assert.equal(r.headers.get('X-PitWall-Offline'), '1');
});

test('shards: cache-first by exact URL, new version evicts old ones, stale copy served offline', async () => {
  const p = 'data/2025/traj.json.gz';
  const net = { [`${SCOPE}${p}`]: 'traj A' };
  const sw = loadSW({ version: 'v1', net });
  assert.equal(await (await sw.get(`${p}?v=aaa`)).text(), 'traj A');
  sw.calls.length = 0;
  assert.equal(await (await sw.get(`${p}?v=aaa`)).text(), 'traj A');
  assert.equal(sw.calls.length, 0, 'immutable: no network on a hit');
  net[`${SCOPE}${p}`] = 'traj B';
  assert.equal(await (await sw.get(`${p}?v=bbb`)).text(), 'traj B');
  const data = await sw.caches.open('pw-data');
  assert.deepEqual((await data.keys()).map((r) => r.url), [`${SCOPE}${p}?v=bbb`], 'old version evicted');
  net[`${SCOPE}${p}`] = 'down';
  const r = await sw.get(`${p}?v=ccc`);
  assert.equal(await r.text(), 'traj B');
  assert.equal(r.headers.get('X-PitWall-Stale'), '1');
  const other = await sw.get('data/2025/events/2025xyz.json.gz?v=1');
  assert.equal(other.status, 503, 'never seen and offline');
});

test('shards: errors are never cached', async () => {
  const p = 'data/2025/events/2025abc.json.gz';
  const net = { [`${SCOPE}${p}`]: { status: 404, body: 'Not Found' } };
  const sw = loadSW({ version: 'v1', net });
  assert.equal((await sw.get(`${p}?v=1`)).status, 404);
  net[`${SCOPE}${p}`] = { status: 500, body: 'err' };
  assert.equal((await sw.get(`${p}?v=1`)).status, 500);
  assert.equal((await (await sw.caches.open('pw-data')).keys()).length, 0);
  net[`${SCOPE}${p}`] = 'ok now';
  assert.equal(await (await sw.get(`${p}?v=1`)).text(), 'ok now');
});

test('dev worker (unstaged source) precaches nothing and serves the shell network-first', async () => {
  const net = { [`${SCOPE}js/app.js`]: 'app 1' };
  const sw = loadSW({ version: 'dev', net });
  await sw.fire('install');
  assert.deepEqual(await sw.caches.keys(), []);
  assert.equal(await (await sw.get('js/app.js')).text(), 'app 1');
  net[`${SCOPE}js/app.js`] = 'app 2';
  assert.equal(await (await sw.get('js/app.js')).text(), 'app 2', 'network first');
  net[`${SCOPE}js/app.js`] = 'down';
  assert.equal(await (await sw.get('js/app.js')).text(), 'app 2', 'cache when offline');
});

// ---------------------------------------------------------------- offline helpers

const MAN = {
  data_version: 'dv1',
  seasons: [{ season: 2025, game: 'Reefscape', events_count: 3, events_bytes: 300,
    files: { season: { path: '2025/season.json.gz', hash: 's1', bytes: 50 }, asof: { path: '2025/asof.json.gz', hash: 'a1', bytes: 20 }, traj: { path: '2025/traj.json.gz', hash: 't1', bytes: 30 } } }],
  files: { models: { path: 'models.json', hash: 'm1', bytes: 5 }, history: { path: 'history.json.gz', hash: 'h1', bytes: 7 } },
};
const SD = { season: 2025, events: { cols: { key: ['2025a', '2025b', '2025c'], file_hash: ['ea', null, 'ec'], file_bytes: [100, null, 200] } } };

test('seasonFiles lists season, asof, traj, every event with a file, models and history', () => {
  const f = seasonFiles(MAN, SD);
  assert.deepEqual(f.map((x) => x.url), [
    './data/2025/season.json.gz?v=s1', './data/2025/asof.json.gz?v=a1', './data/2025/traj.json.gz?v=t1',
    './data/2025/events/2025a.json.gz?v=ea', './data/2025/events/2025c.json.gz?v=ec',
    './data/models.json?v=m1', './data/history.json.gz?v=h1',
  ]);
  assert.equal(f.reduce((a, x) => a + x.bytes, 0), 50 + 20 + 30 + 100 + 200 + 5 + 7);
  assert.deepEqual(seasonFiles(MAN, { season: 2019, events: SD.events }), []);
  assert.equal(seasonBytes(MAN.seasons[0]), 400);
});

test('summarize counts exact versions only (absolute or relative urls)', () => {
  const f = seasonFiles(MAN, SD);
  const base = 'https://pw.test/pitwall/';
  const cached = new Set([`${base}data/2025/season.json.gz?v=s1`, './data/2025/asof.json.gz?v=a1', `${base}data/2025/traj.json.gz?v=OLD`]);
  assert.deepEqual(summarize(f, cached, base), { have: 2, total: 7, haveBytes: 70, totalBytes: 412 });
});

test('pool runs at most n at once, visits every item, and rethrows the first error', async () => {
  let live = 0; let peak = 0; const seen = [];
  await pool([...Array(20).keys()], 4, async (x) => { live++; peak = Math.max(peak, live); await new Promise((r) => setTimeout(r, 2)); seen.push(x); live--; });
  assert.equal(peak, 4);
  assert.deepEqual(seen.sort((a, b) => a - b), [...Array(20).keys()]);
  await assert.rejects(pool([1, 2, 3], 2, async (x) => { if (x === 2) throw new Error('boom'); }), /boom/);
  const ac = new AbortController(); let n = 0;
  await pool([...Array(10).keys()], 1, async () => { n++; if (n === 3) ac.abort(); }, ac.signal);
  assert.equal(n, 3, 'abort stops new work');
  await pool([], 4, async () => { throw new Error('never'); });
});

test('mb, indicator and Offline tab wording', () => {
  assert.equal(mb(0), '0 MB');
  assert.equal(mb(21_100_000), '21.1 MB');
  assert.equal(mb(1200), '0.1 MB');
  assert.equal(indicatorText(true), '');
  assert.equal(indicatorText(false), 'Offline: showing saved data');
  const s = { season: 2025, have: 0, total: 206, haveBytes: 0, totalBytes: 21e6, older: 0, recorded: null };
  assert.equal(statusText(s, null), 'Not saved');
  assert.equal(statusText({ ...s, have: 206 }, null), 'Saved, up to date');
  assert.match(statusText({ ...s, have: 4 }, null), /4 of 206 files/);
  assert.match(statusText({ ...s, have: 200, recorded: 'dv0' }, null), /Newer data/);
  assert.match(statusText({ ...s, older: 12 }, null), /older copy/);
  assert.equal(statusText(s, { finished: false, done: 5, total: 206 }), 'Downloading: 5 of 206 files');
  assert.match(statusText(s, { finished: true, error: new Error('x') }), /stopped: x/);
  assert.equal(progressText(2025, { done: 54, total: 206, bytesDone: 4.1e6, bytesTotal: 21e6 }), '2025: 54 of 206 files, 4.1 MB of 21.0 MB');
  const rows = [{ ...s, have: 206 }, { ...s, season: 2024, totalBytes: 17e6 }];
  assert.match(ledeText(rows, { caches: true }), /^1 season of 2 is saved on this device \(21\.0 MB\): 2025\./);
  assert.match(ledeText([rows[1]], { caches: true }), /^No season is saved/);
  assert.match(ledeText(rows, { caches: false }), /cannot save/);
});

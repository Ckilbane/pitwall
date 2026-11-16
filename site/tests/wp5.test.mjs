// WP5 tests: watchlist, csv, asof. Run: node --test site/tests/
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = path.resolve(here, '../js');

// asof.js imports core/html.js (WP4a). If it does not exist yet, test against a copy with a stub.
async function loadAsof() {
  if (fs.existsSync(path.join(jsDir, 'core/html.js'))) return import(pathToFileURL(path.join(jsDir, 'components/asof.js')).href);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pw5-'));
  fs.mkdirSync(path.join(tmp, 'core'), { recursive: true });
  fs.mkdirSync(path.join(tmp, 'components'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'core/html.js'), 'export const raw = (s) => ({ toString: () => s, __safe: true });');
  fs.copyFileSync(path.join(jsDir, 'components/asof.js'), path.join(tmp, 'components/asof.js'));
  return import(pathToFileURL(path.join(tmp, 'components/asof.js')).href);
}

function fakeStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
}

test('watchlist defaults to 1218, 1219 and persists', async () => {
  globalThis.localStorage = fakeStorage();
  const w = await import('../js/core/watchlist.js');
  w._reset();
  assert.deepEqual(w.get(), [1218, 1219]);
  assert.equal(w.has(1218), true);
  let seen = null;
  const off = w.subscribe((l) => { seen = l; });
  assert.equal(w.toggle(254), true);
  assert.deepEqual(seen, [1218, 1219, 254]);
  assert.equal(w.toggle(1219), false);
  assert.deepEqual(w.get(), [1218, 254]);
  assert.equal(globalThis.localStorage.getItem('pw.v1.watchlist'), '[1218,254]');
  off();
  // a new page load reads storage, not the default
  w._reset();
  assert.deepEqual(w.get(), [1218, 254]);
  // emptied list stays empty (not the default)
  w.set([]);
  w._reset();
  assert.deepEqual(w.get(), []);
});

test('watchlist survives broken storage and bad data', async () => {
  const w = await import('../js/core/watchlist.js');
  globalThis.localStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); } };
  w._reset();
  assert.deepEqual(w.get(), [1218, 1219]);
  w.toggle(9999);
  assert.deepEqual(w.get(), [1218, 1219, 9999]); // memory fallback
  globalThis.localStorage = fakeStorage();
  globalThis.localStorage.setItem('pw.v1.watchlist', '{not json');
  w._reset();
  assert.deepEqual(w.get(), [1218, 1219]);
  globalThis.localStorage.setItem('pw.v1.watchlist', '[1218,"x",-4,1218,2.5,"254"]');
  assert.deepEqual(w.get(), [1218, 254]);
  w.setDefault([1, 2]);
  globalThis.localStorage = fakeStorage();
  w._reset();
  w.setDefault([5, 6]);
  assert.deepEqual(w.get(), [5, 6]);
});

test('csv is RFC 4180', async () => {
  const { toCSV, csvFilename } = await import('../js/core/csv.js');
  const cols = [{ key: 'team', label: 'Team' }, { key: 'name', label: 'Name, full' }, { key: 'epa', label: 'EPA' }];
  const rows = [
    { team: 1218, name: 'Springside "Chestnut Hill", Robotics', epa: 74.6 },
    { team: 1, name: 'line\nbreak', epa: null },
    { team: 2, name: '=SUM(A1)', epa: NaN },
    { team: 3, name: '-x is text', epa: -3.5 },
  ];
  assert.equal(toCSV(cols, rows),
    'Team,"Name, full",EPA\n'
    + '1218,"Springside ""Chestnut Hill"", Robotics",74.6\n'
    + '1,"line\nbreak",\n'
    + "2,'=SUM(A1),\n"
    + "3,'-x is text,-3.5\n");
  assert.equal(toCSV([{ key: 'a', label: 'A', value: (r) => r.x * 2 }], [{ x: 2 }]), 'A\n4\n');
  assert.equal(csvFilename({ view: 'teams', season: 2025, region: 'fma', asof: 'w3' }), 'pitwall-teams-2025-fma-w3.csv');
  assert.equal(csvFilename({ view: 'teams', season: 2025, region: 'state:PA' }), 'pitwall-teams-2025-state-pa.csv');
  assert.equal(csvFilename({ view: 'events', season: 2025, region: 'all' }), 'pitwall-events-2025.csv');
});

test('asof options and helpers', async () => {
  const a = await loadAsof();
  const season = { events: { cols: { week: [1, 2, 7, 8, null, 3] } } };
  const o = a.asofOptions(season);
  assert.deepEqual(o.map((x) => x.id), ['w0', 'w1', 'w2', 'w3', 'w4', 'w5', 'w6', 'w7', 'end']);
  assert.equal(o[0].label, 'Preseason');
  assert.equal(o[7].label, 'After week 7 (before Champs)');
  assert.equal(o[8].label, 'End of season');
  assert.equal(a.normalizeSeasonAsof('prechamps'), 'w7');
  assert.equal(a.normalizeSeasonAsof('w8'), 'end');
  assert.equal(a.normalizeSeasonAsof('zzz'), 'end');
  assert.equal(a.seasonAsofIndex('w0'), 0);
  assert.equal(a.seasonAsofIndex('end'), 8);
  assert.equal(a.matchLabel('sf', 2, 1), 'SF2-1');
  assert.equal(a.matchLabel('f', 1, 2), 'F2');
  assert.equal(a.matchLabel('qm', 1, 32), 'Q32');

  const ev = { meta: {}, matches: { n: 4, cols: {
    comp_level: ['qm', 'qm', 'qm', 'f'], set_number: [1, 1, 1, 1], match_number: [1, 2, 3, 1],
    red_score: [10, 20, null, null] } } };
  const eo = a.asofOptions(ev);
  assert.deepEqual(eo.map((x) => x.id), ['start', 'Q1', 'end']);
  assert.equal(a.eventAsofMatchIndex(ev, 'start'), 0);
  assert.equal(a.eventAsofMatchIndex(ev, 'Q2'), 2);
  assert.equal(a.eventAsofMatchIndex(ev, 'end'), 4);
  const s = String(a.asofControl({ scope: 'event', value: 'Q1', options: eo }));
  assert.match(s, /<option value="Q1" selected>/);
  assert.match(s, /type="range"[^>]*max="2"[^>]*value="1"/);
  const el = { dataset: { act: 'asof-range', ids: 'start,Q1,end' }, value: '2' };
  assert.equal(a.readAsof(el), 'end');
  assert.equal(a.readAsof({ dataset: { act: 'asof' }, value: 'w3' }), 'w3');
  const ss = String(a.asofControl({ scope: 'season', value: 'prechamps', options: o }));
  assert.match(ss, /<option value="w7" selected>/);
  assert.doesNotMatch(ss, /type="range"/);
});

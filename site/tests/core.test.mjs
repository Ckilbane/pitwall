// WP4a core modules: html escaping, router round trip, formatting, regions, columnar tables,
// derived pre-match vectors (SPEC 3.5.4) on a real event file, data decoding.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { html, raw, esc, SafeHTML } from '../js/core/html.js';
import { parse, link } from '../js/core/router.js';
import * as fmt from '../js/core/fmt.js';
import * as regions from '../js/core/regions.js';
import { tbl, derivePre, decode, fileUrl } from '../js/core/data.js';

const FIX = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const ev = JSON.parse(gunzipSync(readFileSync(join(FIX, '2025mrcmp.json.gz'))).toString('utf8'));
const season = JSON.parse(readFileSync(join(FIX, 'season_2025_mini.json'), 'utf8'));

test('html escapes every interpolated value unless it is SafeHTML', () => {
  const evil = '<img src=x onerror="alert(1)">';
  const out = html`<p title="${evil}">${evil}${raw('<b>ok</b>')}${[1, null, false, 'a&b']}</p>`;
  assert.ok(out instanceof SafeHTML);
  assert.equal(String(out), '<p title="&lt;img src=x onerror=&quot;alert(1)&quot;&gt;">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;<b>ok</b>1a&amp;b</p>');
  assert.equal(esc("'"), '&#39;');
});

test('router: parse and link round trip, region syntax survives', () => {
  const h = link('teams', [], { season: 2025, model: 'fast', region: 'state:PA', q: 'sch robotics', cols: 'rp' });
  assert.equal(h, '#/teams?season=2025&model=fast&region=state:PA&q=sch%20robotics&cols=rp');
  const r = parse(h);
  assert.deepEqual(r, { view: 'teams', path: [], query: { season: '2025', model: 'fast', region: 'state:PA', q: 'sch robotics', cols: 'rp' } });
  assert.equal(link(r.view, r.path, r.query), h);
  assert.deepEqual(parse('#/team/1218?season=2025'), { view: 'team', path: ['1218'], query: { season: '2025' } });
  assert.deepEqual(parse(''), { view: '', path: [], query: {} });
  assert.equal(link('compare', [], { teams: '1218,254', empty: '' }), '#/compare?teams=1218,254');
});

test('fmt: match keys, signs, ranks, labels, dates', () => {
  assert.equal(fmt.matchLabel('qm', 1, 32), 'Q32');
  assert.equal(fmt.matchLabel('sf', 2, 1), 'SF2-1');
  assert.equal(fmt.matchLabel('f', 1, 1), 'F1');
  assert.equal(fmt.matchLabel('qf', 2, 1), 'QF2-1');
  assert.equal(fmt.delta(3.33), '+3.3');
  assert.equal(fmt.delta(-3.33), '−3.3');
  assert.equal(fmt.delta(0.01), '0.0');
  assert.equal(fmt.rating(-1.25), '−1.3');
  assert.equal(fmt.rating(null), '–');
  assert.equal(fmt.rank(6, 139), '6th of 139');
  assert.equal(fmt.rank(11, 20), '11th of 20');
  assert.equal(fmt.rank(22, 30), '22nd of 30');
  assert.equal(fmt.pct(0.92), '92%');
  assert.equal(fmt.record(35, 22, 1), '35-22-1');
  assert.equal(fmt.compLabel('auto_coral_points'), 'Auto coral points');
  assert.equal(fmt.dateTime('2026-11-11T23:34:00Z', 'UTC'), 'Nov 11, 2026, 23:34');
  assert.equal(fmt.dateShort('2025-04-02'), 'Apr 2');
  assert.equal(fmt.vectorLabel('comp_0_epa', season.keys), fmt.compLabel(season.keys.names.comp_0));
  assert.equal(fmt.unit('rp_1_epa', season.keys), 'RP score');
  assert.equal(fmt.median([3, 1, 2, null]), 2);
});

test('regions: district, state, country, all', () => {
  const t = { district: 'fma', state: 'PA', country: 'USA' };
  assert.equal(regions.matchRegion(t, 'fma'), true);
  assert.equal(regions.matchRegion(t, 'fim'), false);
  assert.equal(regions.matchRegion(t, 'state:PA'), true);
  assert.equal(regions.matchRegion(t, 'country:USA'), true);
  assert.equal(regions.matchRegion(t, 'all'), true);
  assert.equal(regions.regionLabel('fma'), 'FIRST Mid-Atlantic');
  assert.equal(regions.regionShort('fma'), 'FMA');
  assert.equal(regions.teamRegion(t), 'fma');
  const opts = regions.regionOptions(season);
  assert.ok(opts.some((o) => o.id === 'fma'));
  assert.ok(opts.some((o) => o.id === 'all'));
});

test('tbl: column access, model columns, memoized rows, index', () => {
  const T = tbl(season.teams);
  assert.equal(T.n, season.teams.n);
  const i = T.index('team').get(1218);
  assert.ok(i !== undefined);
  assert.equal(T.get(i, 'epa', 'epa'), season.teams.models.epa.epa[i]);
  const rows = T.rows('fast');
  assert.equal(rows, T.rows('fast'));
  assert.equal(rows[i].epa, season.teams.models.fast.epa[i]);
  assert.equal(rows[i].name, season.teams.cols.name[i]);
  assert.equal(tbl(season.teams), T);
});

test('derivePre: first row of each team is its team_events pre vector; later rows chain post vectors', () => {
  for (const model of ['epa', 'fast']) {
    const pre = derivePre(ev, model);
    const tm = ev.team_matches;
    const TE = tbl(ev.team_events);
    const seen = new Map();
    for (let r = 0; r < tm.n; r++) {
      const t = tm.cols.team[r];
      const p = pre[r];
      assert.ok(p, `row ${r} has a pre vector`);
      if (!seen.has(t)) {
        assert.equal(p.epa, TE.get(TE.index('team').get(t), 'pre_epa', model));
      } else {
        assert.equal(p.epa, tm.models[model].epa[seen.get(t)]);
      }
      if (tm.models[model].epa[r] !== null) seen.set(t, r);
    }
  }
});

test('decode handles gzip and plain JSON; fileUrl adds the hash', async () => {
  const gz = readFileSync(join(FIX, '2025mrcmp.json.gz'));
  const a = await decode(gz.buffer.slice(gz.byteOffset, gz.byteOffset + gz.byteLength));
  assert.equal(a.event, '2025mrcmp');
  const b = await decode(new TextEncoder().encode('{"x":1}').buffer);
  assert.deepEqual(b, { x: 1 });
  assert.equal(fileUrl({ path: '2025/season.json.gz', hash: 'abc123def456' }), './data/2025/season.json.gz?v=abc123def456');
});

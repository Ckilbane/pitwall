// WP4b acceptance on a real event file (2025 FMA District Championship): the as-of replay at start
// and end, both models' predictions, robot-attributed points + alliance remainder = no_foul,
// the qualification standings replay, and the Hypothetical reproduction of recorded predictions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tbl } from '../js/core/data.js';
import { asofOptions, eventAsofMatchIndex } from '../js/components/asof.js';
import { ratingsAsOf, standingsAsOf, robotSplit, isPlayed, accuracy, componentRows, parseMatchKey } from '../js/views/event_lib.js';
import { teamsData, matchesData } from '../js/views/event.js';
import { hypVectors, parseList } from '../js/views/hypothetical.js';
import { predictAlliances } from '../js/model/predict.js';

const FIX = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const ev = JSON.parse(gunzipSync(readFileSync(join(FIX, '2025mrcmp.json.gz'))).toString('utf8'));
const season = JSON.parse(readFileSync(join(FIX, 'season_2025_mini.json'), 'utf8'));
const MODELS = ['epa', 'fast'];
const TE = tbl(ev.team_events);

test('as-of start shows pre-event ratings; end shows team_events end-of-event ratings', () => {
  const opts = asofOptions(ev);
  assert.equal(opts[0].id, 'start');
  assert.equal(opts[opts.length - 1].id, 'end');
  assert.equal(opts[1].id, 'Q1');
  assert.equal(eventAsofMatchIndex(ev, 'start'), 0);
  assert.equal(eventAsofMatchIndex(ev, 'end'), ev.matches.n);
  assert.equal(eventAsofMatchIndex(ev, 'Q1'), 1);
  for (const model of MODELS) {
    const start = ratingsAsOf(ev, model, 0);
    const end = ratingsAsOf(ev, model, ev.matches.n);
    for (let i = 0; i < TE.n; i++) {
      const t = TE.get(i, 'team');
      for (const k of ['epa', 'auto_epa', 'teleop_epa', 'endgame_epa']) {
        assert.equal(start.get(t).v[k], TE.get(i, `pre_${k}`, model), `${model} ${t} start ${k}`);
        // team_matches post vectors carry 3 decimals, team_events 2 (SPEC 3.1 says 2): equal after rounding
        assert.ok(Math.abs(end.get(t).v[k] - TE.get(i, k, model)) <= 0.0051, `${model} ${t} end ${k}`);
      }
    }
    const td = teamsData(ev, model, ev.matches.n);
    assert.equal(td.rows.length, TE.n);
    assert.equal(td.official, true);
    assert.deepEqual(td.rows.map((r) => r.epaRank), td.rows.map((_, j) => j + 1));
  }
});

test('both models are shown for every match; recorded predictions before the as-of point, forecasts after', () => {
  const half = 60;
  const rows = matchesData(ev, season, MODELS, half, 'all');
  assert.equal(rows.length, ev.matches.n);
  for (const r of rows) {
    for (const m of MODELS) {
      assert.ok(typeof r.preds[m].p === 'number', `${r.key} ${m}`);
      assert.equal(r.preds[m].forecast, r.mi >= half);
      if (r.mi < half) assert.equal(r.preds[m].p, ev.matches.models[m].epa_win_prob[r.mi]);
    }
    assert.match(r.label, /^(Q\d+|SF\d+-\d+|F\d+)$/);
  }
  const acc = accuracy(ev, 'epa', ev.matches.n);
  assert.ok(acc.all.decided > 100 && acc.all.calledShare > 0.5);
});

test('robot-attributed points plus the alliance remainder equal the no-foul score', () => {
  let checked = 0;
  for (let mi = 0; mi < ev.matches.n; mi++) {
    if (!isPlayed(ev, mi)) continue;
    for (const side of ['red', 'blue']) {
      const sp = robotSplit(ev, mi, side);
      if (!sp) continue;
      const sum = sp.scored.reduce((a, b) => a + (b || 0), 0);
      assert.equal(sum, sp.sum);
      assert.equal(sp.sum + sp.remainder, ev.matches.cols[`${side}_no_foul`][mi]);
      assert.ok(sp.sum <= sp.noFoul);
      checked++;
    }
  }
  assert.ok(checked > 200, `checked ${checked} alliances`);
  const rows = componentRows(season, ev, 0, 'red', MODELS);
  assert.equal(rows[0].id, 'score');
  assert.ok(MODELS.every((m) => typeof rows[0].pred[m] === 'number'));
});

test('replayed qualification standings match the official ranks closely', () => {
  const quals = ev.matches.cols.comp_level.filter((l) => l === 'qm').length;
  const st = standingsAsOf(ev, quals);
  let same = 0;
  for (const r of st) { const i = TE.index('team').get(r.team); if (TE.get(i, 'rps') === r.rp) same++; }
  assert.equal(same, st.length, 'every team’s replayed RP total equals the official one');
  assert.deepEqual(parseMatchKey('2025gal_qm94'), { event: '2025gal', season: 2025, part: 'qm94' });
});

test('Hypothetical with a real match and ratings=event:{key} reproduces the recorded prediction', () => {
  const M = tbl(ev.matches);
  let n = 0;
  for (const model of MODELS) {
    for (let mi = 0; mi < M.n; mi++) {
      const key = M.get(mi, 'key');
      const red = parseList(M.get(mi, 'red').join(','));
      const blue = parseList(M.get(mi, 'blue').join(','));
      const v = hypVectors(season, model, red, blue, ev, key);
      assert.equal(v.source, 'match');
      const res = predictAlliances(season, v.red, v.blue, { model, elim: v.elim });
      const rec = M.get(mi, 'epa_win_prob', model);
      assert.ok(Math.abs(res.winProbRed - rec) <= 0.002, `${model} ${key}: ${res.winProbRed} vs ${rec}`);
      assert.ok(Math.abs(res.red.score - M.get(mi, 'epa_red_score_pred', model)) <= 0.05);
      assert.ok(Math.abs(res.blue.score - M.get(mi, 'epa_blue_score_pred', model)) <= 0.05);
      n++;
    }
  }
  assert.equal(n, 2 * M.n);
  // pre-event vectors (no match key) reproduce the event's first match exactly
  const v0 = hypVectors(season, 'epa', M.get(0, 'red'), M.get(0, 'blue'), ev, null);
  assert.equal(v0.source, 'event');
  const r0 = predictAlliances(season, v0.red, v0.blue, { model: 'epa' });
  assert.ok(Math.abs(r0.winProbRed - M.get(0, 'epa_win_prob', 'epa')) <= 0.002);
});

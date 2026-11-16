// Tests for site/js/model/predict.js (WP3). Run: node --test site/tests/
//
// Fixtures come from pipeline/tests/make_predict_fixtures.py, which replays the vendored EPA
// model and records each match's pre-match team vectors and predictions:
//   predict_{2022..2025}.json.gz  2,000 matches each (1,000 playoff + 1,000 qual).
//     - exact subset (100/season): full-precision vectors -> must reproduce the pipeline's
//       unrounded predictions to 1e-6.
//     - all 2,000: vectors rounded as exported -> SPEC 7 WP3 tolerances vs exported values.
//   predict_synthetic.json  2022-2026 random and edge-case vectors, expectations from the
//     vendored EPA.predict_match called directly (covers 2026 and every 2023 grid branch).
// Optional full-season check (every match, not committed, ~80 MB):
//   python3 -I pipeline/tests/make_predict_fixtures.py ... --full /tmp/pw_full
//   PW_PREDICT_FULL=/tmp/pw_full node --test site/tests/predict.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  predictAlliances, predictMatch18, allKeys, COMP_NAMES, SLOT_INDEX, toSlots, unitSigmoid,
} from '../js/model/predict.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = join(HERE, '..', '..', 'pipeline', 'tests', 'fixtures');

function load(path) {
  const buf = readFileSync(path);
  const text = buf[0] === 0x1f && buf[1] === 0x8b ? gunzipSync(buf).toString('utf8') : buf.toString('utf8');
  return JSON.parse(text);
}

const SEASONS = [2022, 2023, 2024, 2025].filter((y) => existsSync(join(FIX, `predict_${y}.json.gz`)));

function seasonOf(fx) {
  return { season: fx.season, keys: fx.keys, predict: fx.predict };
}

// max |a - b| over the comparable outputs of one match; null expectations must be null
function errors(pred, exp) {
  const e = { win: 0, score: 0, rp: 0, bd: 0 };
  const upd = (k, a, b) => {
    if (b === null || b === undefined) {
      assert.equal(a, null, `${k}: expected null, got ${a}`);
      return;
    }
    e[k] = Math.max(e[k], Math.abs(a - b));
  };
  upd('win', pred.winProbRed, exp.win_prob);
  upd('score', pred.red.score, exp.red_score);
  upd('score', pred.blue.score, exp.blue_score);
  for (let i = 0; i < 3; i++) {
    upd('rp', pred.red.rp[i], exp.red_rp[i]);
    upd('rp', pred.blue.rp[i], exp.blue_rp[i]);
  }
  pred.red.vector.forEach((v, i) => upd('bd', v, exp.red_pred[i]));
  pred.blue.vector.forEach((v, i) => upd('bd', v, exp.blue_pred[i]));
  return e;
}

function maxInto(acc, e) {
  for (const k of Object.keys(e)) acc[k] = Math.max(acc[k] || 0, e[k]);
  return acc;
}

// RP expectations: recorded rp_3 is 0 before 2025 (upstream), exported as null
function rpNullBefore2025(year, rp) {
  return year >= 2025 ? rp : [rp[0], rp[1], null];
}

test('comp names match vendored key_to_name', () => {
  const synth = load(join(FIX, 'predict_synthetic.json'));
  const tables = synth.seasons.map((s) => [s.season, s.keys.names]);
  for (const y of SEASONS) tables.push([y, load(join(FIX, `predict_${y}.json.gz`)).keys.names]);
  for (const [year, names] of tables) {
    const comps = Object.keys(names).filter((k) => k.startsWith('comp_'))
      .sort((a, b) => Number(a.slice(5)) - Number(b.slice(5)))
      .map((k) => names[k]);
    assert.deepEqual(COMP_NAMES[year], comps, `comp names ${year}`);
    assert.equal(allKeys(year).length, 18);
  }
});

test('synthetic 2022-2026: equals vendored EPA.predict_match (incl. 2023 grid, 2025 processor, 2026 rp_3)', () => {
  const synth = load(join(FIX, 'predict_synthetic.json'));
  assert.deepEqual(synth.seasons.map((s) => s.season), [2022, 2023, 2024, 2025, 2026]);
  for (const s of synth.seasons) {
    const season = seasonOf(s);
    const acc = {};
    for (const c of s.cases) {
      const pred = predictAlliances(season, c.red_vecs, c.blue_vecs, { model: 'epa', elim: c.elim });
      const exp = { ...c, red_rp: rpNullBefore2025(s.season, c.red_rp), blue_rp: rpNullBefore2025(s.season, c.blue_rp) };
      maxInto(acc, errors(pred, exp));
    }
    for (const [k, v] of Object.entries(acc)) assert.ok(v <= 1e-9, `${s.season} ${k} max err ${v}`);
  }
});

test('2023 grid overflow branches are exercised by the synthetic cases', () => {
  const synth = load(join(FIX, 'predict_synthetic.json'));
  const s = synth.seasons.find((x) => x.season === 2023);
  const vk = s.keys.vector;
  const seen = { top: 0, mid: 0, bot: 0, gridZero: 0 };
  for (const c of s.cases) {
    for (const vecs of [c.red_vecs, c.blue_vecs]) {
      const sum = (key) => vecs.reduce((a, v) => a + v[vk.indexOf(key)], 0);
      const top = sum('comp_5_epa'); const mid = sum('comp_4_epa'); const bot = sum('comp_3_epa');
      if (top > 9) seen.top++;
      if (mid + Math.max(0, top - 9) > 9) seen.mid++;
      if (bot > 9 || bot + Math.max(0, mid + Math.max(0, top - 9) - 9) > 9) seen.bot++;
      if (sum('comp_6_epa') + sum('comp_7_epa') <= 0) seen.gridZero++;
    }
  }
  for (const [k, v] of Object.entries(seen)) assert.ok(v > 0, `branch ${k} not exercised`);
});

// SPEC 7 WP3 tolerances for predictions from exported (rounded) pre vectors.
const SPEC_TOL = { win: 0.002, score: 0.05, rp: 0.002, bd: 0.02 };
// SPEC CONFLICT (reported): with SPEC 3.1 rounding (ratings 2 decimals, rp 4) the SPEC 7 score and
// win tolerances cannot hold for every match in 2023 and 2025, whatever the predictor does.
// Measured on every match (exact vectors rounded to 2 dp): 2023 max |win| 0.0025 (27 matches
// > 0.002), max |score| 0.154 (6,985 > 0.05); 2025 max |score| 0.088 (631 > 0.05). With 3 decimals
// all seasons pass (2023: win 0.00024, score 0.017). Analytic bounds used here instead:
//   2023 score = cs + 2 bottom + 3 mid + 5 top + auto pieces + 5 links + endgame cs: coefficient
//     sum 18, 3 teams x 0.005 per slot -> |d score| <= 18 * 0.015 * (1 + foul) ~ 0.29;
//     |d win| <= ln10 |k| / 4 / score_sd * 2 * 0.29 ~ 0.0066.
//   2025 score = no_foul + 3 (own + opp processor algae): (1 + 3 + 3) * 0.015 * (1 + foul) ~ 0.12.
//   breakdown (red_pred, itself exported at 2 dp: + 0.005): 2023 teleop grid / cube / cone points
//     take the overflow total (up to 4 piece-slot sums) -> 0.015 + 4 * 0.015 + 0.005 = 0.08;
//     2025 teleop and total take 3 (own + opp processor) -> 0.015 + 6 * 0.015 + 0.005 = 0.11.
const ROUNDING_BOUND = {
  2023: { win: 0.0066, score: 0.29, bd: 0.08 },
  2025: { score: 0.12, bd: 0.11 },
};

for (const year of SEASONS) {
  test(`${year}: exact pre vectors reproduce pipeline predictions to 1e-6`, () => {
    const fx = load(join(FIX, `predict_${year}.json.gz`));
    const season = seasonOf(fx);
    const acc = {};
    let n = 0;
    for (const m of fx.matches) {
      if (!m.exact) continue;
      const x = m.exact;
      const pred = predictAlliances(season, x.red_vecs, x.blue_vecs, { model: 'epa', elim: m.elim });
      maxInto(acc, errors(pred, { ...x, red_rp: rpNullBefore2025(year, x.red_rp), blue_rp: rpNullBefore2025(year, x.blue_rp) }));
      // rounding the exact prediction gives the exported value
      assert.ok(Math.abs(pred.winProbRed - m.export.win_prob) <= 5e-5 + 1e-12, `${m.key} win prob rounding`);
      assert.ok(Math.abs(pred.red.score - m.export.red_score) <= 5e-3 + 1e-9, `${m.key} red score rounding`);
      n++;
    }
    assert.ok(n >= 100, `exact matches ${n}`);
    for (const [k, v] of Object.entries(acc)) assert.ok(v <= 1e-6, `${year} ${k} max err ${v}`);
    console.log(`# ${year} exact (n=${n}) max abs err: win ${acc.win.toExponential(2)}, score ${acc.score.toExponential(2)}, rp ${acc.rp.toExponential(2)}, breakdown ${acc.bd.toExponential(2)}`);
  });

  test(`${year}: exported rounded pre vectors within SPEC tolerances or the 2-dp rounding bound (2,000 matches)`, () => {
    const fx = load(join(FIX, `predict_${year}.json.gz`));
    const season = seasonOf(fx);
    const acc = {};
    assert.equal(fx.matches.length, 2000);
    assert.equal(fx.matches.filter((m) => m.elim).length, 1000);
    for (const m of fx.matches) {
      const pred = predictAlliances(season, m.red_vecs, m.blue_vecs, { model: 'epa', elim: m.elim });
      maxInto(acc, errors(pred, m.export));
    }
    const tol = { ...SPEC_TOL, ...(ROUNDING_BOUND[year] || {}) };
    for (const k of Object.keys(tol)) assert.ok(acc[k] <= tol[k], `${year} ${k} max err ${acc[k]} > ${tol[k]}`);
    console.log(`# ${year} rounded (n=2000) max abs err: win ${acc.win.toFixed(5)}, score ${acc.score.toFixed(4)}, rp ${acc.rp.toFixed(5)}, breakdown ${acc.bd.toFixed(4)}`);
  });
}

test('input forms: VK arrays, VK objects and pre_<vk> objects agree; model k is used', () => {
  const synth = load(join(FIX, 'predict_synthetic.json'));
  const s = synth.seasons.find((x) => x.season === 2025);
  const season = seasonOf(s);
  const vk = s.keys.vector;
  const c = s.cases[5];
  const asObj = (v, prefix = '') => Object.fromEntries(vk.map((k, i) => [prefix + k, v[i]]));
  const a = predictAlliances(season, c.red_vecs, c.blue_vecs);
  const b = predictAlliances(season, c.red_vecs.map((v) => asObj(v)), c.blue_vecs.map((v) => asObj(v)));
  const d = predictAlliances(season, c.red_vecs.map((v) => asObj(v, 'pre_')), c.blue_vecs.map((v) => asObj(v, 'pre_')));
  assert.deepEqual(a, b);
  assert.deepEqual(a, d);
  assert.equal(a.red.breakdown.epa, a.red.vector[0]);
  assert.equal(a.red.scoreNoFouls * (1 + season.predict.foul_rate), a.red.score);

  const fast = { ...season, predict: { ...season.predict, models: { ...season.predict.models, fast: { k: -1 } } } };
  const f = predictAlliances(fast, c.red_vecs, c.blue_vecs, { model: 'fast' });
  const diff = (f.red.scoreNoFouls - f.blue.scoreNoFouls) / season.predict.score_sd;
  assert.equal(f.winProbRed, 1 / (1 + 10 ** (-1 * diff)));
  assert.equal(f.red.score, a.red.score);
  assert.equal(f.winner, f.winProbRed >= 0.5 ? 'red' : 'blue');
});

test('errors: missing rating value, unknown model, unsupported season', () => {
  const synth = load(join(FIX, 'predict_synthetic.json'));
  const s = synth.seasons.find((x) => x.season === 2024);
  const season = seasonOf(s);
  const c = s.cases[1];
  const broken = c.red_vecs.map((v) => v.slice());
  broken[1][3] = null;
  assert.throws(() => predictAlliances(season, broken, c.blue_vecs), /no number for endgame_epa/);
  assert.throws(() => predictAlliances(season, c.red_vecs, c.blue_vecs, { model: 'nope' }), /no k/);
  assert.throws(() => predictMatch18(2019, [], [], { k: -0.625, scoreSd: 1, foulRate: 0 }), /2022-2026/);
});

test('helpers: SLOT_INDEX order, unit sigmoid, 4th alliance member ignored', () => {
  assert.equal(SLOT_INDEX.comp_9_epa, 17);
  assert.equal(unitSigmoid(0.5), 0.5);
  const synth = load(join(FIX, 'predict_synthetic.json'));
  const s = synth.seasons.find((x) => x.season === 2026);
  const season = seasonOf(s);
  const c = s.cases[2];
  const a = predictAlliances(season, c.red_vecs, c.blue_vecs);
  const b = predictAlliances(season, [...c.red_vecs, c.blue_vecs[0]], [...c.blue_vecs, c.red_vecs[0]]);
  assert.deepEqual(a, b);
  assert.equal(toSlots(c.red_vecs[0], s.keys.vector).length, 18);
});

const FULL = process.env.PW_PREDICT_FULL;
if (FULL && existsSync(FULL)) {
  const files = readdirSync(FULL).filter((f) => /^predict_full_\d{4}\.json(\.gz)?$/.test(f)).sort();
  for (const f of files) {
    test(`full season ${f}: every match within 1e-6`, () => {
      const fx = load(join(FULL, f));
      const season = seasonOf(fx);
      const year = fx.season;
      const acc = {};
      let winnerFlips = 0;
      for (const m of fx.matches) {
        const x = m.exact;
        const pred = predictAlliances(season, x.red_vecs, x.blue_vecs, { model: 'epa', elim: m.elim });
        maxInto(acc, errors(pred, { ...x, red_rp: rpNullBefore2025(year, x.red_rp), blue_rp: rpNullBefore2025(year, x.blue_rp) }));
        if ((pred.winProbRed >= 0.5) !== (x.win_prob >= 0.5)) winnerFlips++;
      }
      console.log(`# FULL ${year} (n=${fx.matches.length}) max abs err: win ${acc.win.toExponential(2)}, score ${acc.score.toExponential(2)}, rp ${acc.rp.toExponential(2)}, breakdown ${acc.bd.toExponential(2)}, winner flips ${winnerFlips}`);
      for (const [k, v] of Object.entries(acc)) assert.ok(v <= 1e-6, `${year} ${k} max err ${v}`);
      assert.equal(winnerFlips, 0);
    });
  }
}

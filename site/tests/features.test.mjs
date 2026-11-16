// Feature rules (change detector, consistency, opponent effect) and data-built Models sentences.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as F from '../js/core/features.js';
import { findings, holdoutMatches, lookaheadText } from '../js/views/models.js';

test('change mark uses |z| > 3 from change_z, ignores the shard flag', () => {
  assert.equal(F.CHANGE_Z, 3);
  assert.equal(F.changeDir(2.5), 0);
  assert.equal(F.changeDir(3.2), 1);
  assert.equal(F.changeDir(-3.01), -1);
  assert.equal(F.changeDir(null), null);
  assert.equal(String(F.changeCell(2.4)), '');
  const s = String(F.changeCell(3.4, { k: 3, event: 'Hatboro-Horsham', first: true }));
  assert.match(s, /▲ \+3\.4 SD/);
  assert.match(s, /k 3/);
  assert.match(s, /preseason prior/);
  assert.doesNotMatch(s, /[Rr]ebuilt/);
  assert.match(F.changeCaption(), /more than 3 SD/);
  F.setThreshold({ features: { change_z_threshold: 2.5 } });
  assert.equal(F.changeDir(2.6), 1);
  F.setThreshold({ features: { change_z_threshold: 3 } });
});

test('consistency always prints n and is blank below 4', () => {
  assert.equal(F.consText(12.34, 9), '±12.3 · n = 9');
  assert.equal(F.consText(12.34, 3), '– · n = 3');
  assert.equal(F.consText(null, 0), '– · n = 0');
});

test('opponent effect subtracts the season mean before flagging', () => {
  const mean = F.seasonMean([-4, -2, null, 0]);
  assert.equal(mean, -2);
  const o = F.oppEffect({ opp_effect: -5, opp_effect_se: 1, opp_n: 20 }, mean);
  assert.equal(o.adj, -3);
  assert.equal(o.show, true);
  assert.equal(F.oppEffect({ opp_effect: -3, opp_effect_se: 1, opp_n: 20 }, mean).show, false);
  assert.equal(F.oppEffect({ opp_effect: -9, opp_effect_se: 1, opp_n: 11 }, mean), null);
});

const ov = (acc, brier, mae, conf) => ({ overall: { n: 100, acc, brier, log_loss: 0.4, score_mae: mae, conf } });

test('Models sentences are computed from the numbers, either way round', () => {
  const tuning = { tuned_on: [2022, 2023, 2024], search: { holdout: [2025] } };
  const sbA = { 2024: { epa: ov(0.76, 0.16, 11, 0.7), fast: ov(0.77, 0.15, 12, 0.75) }, 2025: { epa: ov(0.784, 0.1538, 19.11, 0.70), fast: ov(0.781, 0.1471, 20.01, 0.75) } };
  const a = findings(sbA, tuning, -0.625, -0.75);
  assert.equal(a.year, 2025);
  assert.match(a.sentences[0], /held out from tuning/);
  assert.match(a.sentences[0], /Fast EPA called 78\.1% of winners and EPA 78\.4%/);
  assert.match(a.sentences[0], /−0\.30 percentage points/);
  assert.match(a.sentences[1], /Fast EPA's is lower/);
  assert.match(a.sentences[2], /Fast EPA's error is higher/);
  const sbB = { 2025: { epa: ov(0.784, 0.1538, 19.11, 0.70), fast: ov(0.789, 0.1446, 18.8, 0.75) } };
  const b = findings(sbB, tuning, -0.625, -0.75);
  assert.match(b.sentences[0], /\+0\.50 percentage points/);
  assert.match(b.sentences[2], /Fast EPA's error is lower/);
  assert.equal(b.mismatch, null);
});

test('holdout intervals are used only when they describe the published run', () => {
  const e = ov(0.784, 0.1538, 19.11); const f = ov(0.781, 0.1471, 20.01);
  const h = { epa: { acc: 0.7838, brier: 0.1538, score_mae: 19.1 }, fast: { acc: 0.7806, brier: 0.1471, score_mae: 20.0 }, fast_minus_epa_ci95: { acc: { diff: -0.003, lo: -0.0077, hi: 0.0014 } } };
  assert.equal(holdoutMatches(h, e, f), true);
  const tuning = { tuned_on: [2022], holdout_check: { 2025: h } };
  const v = findings({ 2025: { epa: e, fast: f } }, tuning, -0.625, -0.75);
  assert.match(v.sentences[0], /95% interval −0\.77 to \+0\.14, which includes 0/);
  const other = { 2025: { epa: e, fast: ov(0.789, 0.1446, 18.8) } };
  const w = findings(other, tuning, -0.625, -0.75);
  assert.doesNotMatch(w.sentences[0], /interval/);
  assert.match(w.mismatch, /reports different numbers/);
});

test('week-1 look-ahead is quoted only from data', () => {
  assert.match(lookaheadText(undefined, 2025), /not in the published data/);
  const la = { 2025: { epa: { leakfree_minus_standard: { acc: { diff: 0 }, brier: { diff: 0.00001 } } } } };
  assert.match(lookaheadText(la, 2025), /Measured for 2025/);
});

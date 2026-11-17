import test from 'node:test';
import assert from 'node:assert/strict';
import { phi, rangeOf, pTrueAbove, pMatchAbove, tiers, verdict, outOf10 } from '../js/core/ranges.js';

test('phi matches known values', () => {
  assert.ok(Math.abs(phi(0) - 0.5) < 1e-7);
  assert.ok(Math.abs(phi(1.96) - 0.975) < 1e-4);
  assert.ok(Math.abs(phi(-1.2816) - 0.1) < 1e-4);
});

test('rangeOf is EPA +- 1.96 se, null without se', () => {
  const g = rangeOf(70, 5);
  assert.ok(Math.abs(g.lo - 60.2) < 1e-9 && Math.abs(g.hi - 79.8) < 1e-9);
  assert.equal(rangeOf(70, null), null);
});

test('equal teams are a coin flip; symmetric', () => {
  const a = { team: 1, epa: 70, epa_se: 5, match_sd: 7 };
  const b = { team: 2, epa: 70, epa_se: 5, match_sd: 7 };
  assert.ok(Math.abs(pMatchAbove(a, b) - 0.5) < 1e-6);
  const c = { ...b, epa: 80 };
  assert.ok(Math.abs(pMatchAbove(a, c) + pMatchAbove(c, a) - 1) < 1e-9);
  assert.ok(pTrueAbove(c, a) > pMatchAbove(c, a)); // a rating gap is surer than one match
});

test('tiers split only when the next team is 90% likely below the tier leader', () => {
  const r = (team, epa) => ({ team, epa, epa_se: 5, match_sd: 7 });
  // threshold gap = 1.2816 * sqrt(50) = 9.06
  const t = tiers([r(1, 90), r(2, 82), r(3, 80.5), r(4, 72), r(5, 60)]);
  assert.deepEqual(t.map((x) => x.rows.map((y) => y.team)), [[1, 2], [3, 4], [5]]);
});

test('verdict wording', () => {
  const a = { team: 1218 }; const b = { team: 2539 };
  assert.equal(verdict(0.5, a, b).head, 'Coin flip');
  assert.match(verdict(0.6, a, b).head, /Slight edge: 1218/);
  assert.match(verdict(0.3, a, b).head, /2539 usually adds more/);
  assert.match(verdict(0.9, a, b).text, /about 9 of 10/);
  assert.equal(outOf10(0.64), 6);
});

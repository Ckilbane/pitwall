// Chart modules (WP4a/WP5): round ticks, label spacing, figure rules (role=img, aria-label, axis
// titles), zero-based bars, and the one diverging win-probability scale.
import test from 'node:test';
import assert from 'node:assert/strict';
import { niceTicks, nudge, lineChart, lineDataTable } from '../js/charts/line.js';
import { smallMultiples } from '../js/charts/multiples.js';
import { barWidth, groupedBars } from '../js/charts/bars.js';
import { spark } from '../js/charts/spark.js';
import { calibrationChart } from '../js/charts/calibration.js';
import { diffPlot } from '../js/charts/dotplot.js';
import { rangeStrip } from '../js/charts/strip.js';
import { wpBand, wpChip, pctP, WP_BANDS, favoured } from '../js/components/wp.js';

test('niceTicks gives 3-6 round ticks covering the range', () => {
  for (const [lo, hi] of [[20, 100], [0, 7], [-0.015, 0], [76.4, 80.7], [0, 0.75], [1, 8]]) {
    const t = niceTicks(lo, hi, 6);
    assert.ok(t.ticks.length >= 3 && t.ticks.length <= 6, `${lo}-${hi}: ${t.ticks}`);
    assert.ok(t.min <= lo + 1e-9 && t.max >= hi - 1e-9);
    const steps = t.ticks.slice(1).map((v, i) => +(v - t.ticks[i]).toFixed(9));
    assert.ok(steps.every((s) => Math.abs(s - steps[0]) < 1e-9));
  }
});

test('nudge keeps labels at least gap apart and inside the band', () => {
  const out = nudge([{ y: 50 }, { y: 52 }, { y: 51 }, { y: 300 }], 15, 10, 200);
  const ys = out.map((o) => o.y).sort((a, b) => a - b);
  for (let i = 1; i < ys.length; i++) assert.ok(ys[i] - ys[i - 1] >= 15 - 1e-9);
  assert.ok(ys[ys.length - 1] <= 200);
});

const pts = (n, f) => Array.from({ length: n }, (_, i) => ({ x: i + 1, y: f(i) }));

test('lineChart is a labelled figure: role img, aria-label, both axis titles, direct end labels', () => {
  const svg = lineChart({
    series: [{ id: 'a', label: '341 · 90.6', role: 'context', points: pts(10, (i) => 50 + i) }, { id: 'b', label: '1218 · 74.6', role: 'you', points: pts(8, (i) => 30 + 5 * i) }],
    x: { title: 'Matches (count)' }, y: { title: 'EPA (points per match)' }, refLines: [{ y: 26, label: 'FMA median 26.0' }],
    annotations: [{ x: 4.5, label: 'SCH' }], ariaLabel: '1218 rose', width: 780,
  });
  assert.match(svg, /^<svg[^>]*role="img"[^>]*aria-label="1218 rose"/);
  assert.match(svg, />EPA \(points per match\)</);
  assert.match(svg, />Matches \(count\)</);
  assert.match(svg, /pw-line--you/);
  assert.match(svg, />1218 · 74\.6</);
  assert.match(svg, />FMA median 26\.0</);
  assert.doesNotMatch(svg, /legend/i);
  const tbl = lineDataTable([{ id: 'a', label: 'A', points: pts(2, () => 1) }], 'Match');
  assert.match(tbl, /<th scope="col" class="n">Match<\/th>/);
});

test('small multiples share axes and grey out the other series', () => {
  const out = smallMultiples({ series: [{ id: 'a', label: 'A', points: pts(5, (i) => i) }, { id: 'b', label: 'B', points: pts(9, (i) => 3 * i) }], context: true, cols: 2, panelWidth: 400, x: { title: 'x (count)' }, y: { title: 'y (points)' } });
  const panels = out.split('pw-multiple__title').length - 1;
  assert.equal(panels, 2);
  const yTicks = [...out.matchAll(/text-anchor="end">(-?[\d.]+)<\/text>/g)].map((m) => m[1]);
  const half = yTicks.length / 2;
  assert.deepEqual(yTicks.slice(0, half), yTicks.slice(half));
  assert.match(out, /pw-line--context/);
  assert.match(out, /repeat\(2, /);
});

test('bars start at zero and scale together', () => {
  assert.equal(barWidth(0, 10, 120), 0);
  assert.equal(barWidth(-3, 10, 120), 0);
  assert.equal(barWidth(5, 10, 120), 60);
  assert.equal(barWidth(20, 10, 120), 120);
  const g = groupedBars({ groups: [{ label: 'Auto', values: [{ label: '1218', value: 20.2, you: true }, { label: '254', value: 31.6 }] }], axisTitle: 'EPA (points per match)', ariaLabel: 'x' });
  assert.match(g, />0<\/text>/);
  assert.match(g, /pw-hbar--you/);
  assert.match(g, />1218 · 20\.2</);
});

test('other chart modules are labelled figures', () => {
  assert.match(spark([1, -2, null, 3]), /role="img" aria-label="Last 4 match residuals/);
  const cal = calibrationChart([{ label: 'EPA', pts: [[10, 12, 100]] }, { label: 'Fast EPA', hollow: true, pts: [[10, 9, 100]] }], { x: 'Predicted (%)', y: 'Won (%)', ariaLabel: 'cal' });
  assert.match(cal, /pw-mark--hollow/);
  assert.match(cal, />Predicted \(%\)</);
  const d = diffPlot([{ label: '2025', value: -0.3, lo: -0.77, hi: 0.14, good: null }, { label: '2026', value: null }], { title: 'Difference (points)', fmt: (v) => v.toFixed(2), ariaLabel: 'd' });
  assert.match(d, /not scored/);
  assert.match(d, /pw-ci/);
  assert.match(d, />0\.25|>0\.5|>−0\.5|>0</);
  assert.match(rangeStrip({ value: 5, p25: 1, p75: 3, p90: 4, median: 2, top: 8 }), /aria-hidden="true"/);
});

test('win probability: one set of diverging bands, values always printed', () => {
  assert.deepEqual(WP_BANDS.map((b) => b.id), ['r3', 'r2', 'r1', 'mid', 'b1', 'b2', 'b3']);
  assert.equal(wpBand(0.92), 'r3');
  assert.equal(wpBand(0.7), 'r2');
  assert.equal(wpBand(0.6), 'r1');
  assert.equal(wpBand(0.5), 'mid');
  assert.equal(wpBand(0.4), 'b1');
  assert.equal(wpBand(0.3), 'b2');
  assert.equal(wpBand(0.08), 'b3');
  assert.equal(wpBand(null), null);
  assert.equal(String(wpChip(0.08)), '<span class="pw-wp-chip pw-wp-chip--b3">Blue 92%</span>');
  assert.equal(pctP(0.495), '49.5%');
  assert.deepEqual(favoured(0.3), { side: 'blue', lead: 0.7 });
  for (let p = 0; p <= 1; p += 0.01) {
    const b = WP_BANDS.find((x) => x.id === wpBand(p));
    assert.ok(p >= b.lo - 1e-9 && p <= b.hi + 1e-9, `p ${p} in ${b.id}`);
  }
});

// Contrast of every foreground/background token pair the stylesheets use (SPEC 5.8, WP6), in both
// themes, from docs/design/tokens.json. Text >= 4.5:1; large text (>= 24px, or >= 18.66px bold)
// >= 3:1; non-text marks and control borders >= 3:1. Pairs come from the rules in css/*.css; the
// browser check (pw_tools/a11y.py) measures the rendered pages as well.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const C = JSON.parse(readFileSync(join(ROOT, 'docs/design/tokens.json'), 'utf8')).color;

function lum(hex) {
  const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
export function ratio(a, b) {
  const x = lum(a); const y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const SURFACES = ['paper', 'surface', 'surface-sunk']; // page, cards/tables, hover rows, seg track

// [foreground, backgrounds, minimum, where]
const PAIRS = [
  // body text, labels, captions, ticks
  ['ink', [...SURFACES, 'highlight-tint'], 4.5, 'text, your-team rows and chips'],
  ['ink-muted', [...SURFACES, 'highlight-tint'], 4.5, 'secondary text, table cells, eyebrows'],
  ['ink-subtle', SURFACES, 4.5, 'pw-small, captions, ticks, event scores in hover rows'],
  // alliances
  ['alliance-red', ['surface', 'paper', 'alliance-red-tint'], 4.5, 'red chips, win-prob labels'],
  ['alliance-blue', ['surface', 'paper', 'alliance-blue-tint'], 4.5, 'blue chips, win-prob labels'],
  ['on-alliance', ['div-red-3', 'div-blue-3'], 4.5, 'strongest win-prob chips'],
  ['ink', ['div-red-1', 'div-red-2', 'div-mid', 'div-blue-1', 'div-blue-2'], 4.5, 'other win-prob chips'],
  // change, highlight, inverse
  ['delta-up', SURFACES, 4.5, 'deltas'],
  ['delta-down', SURFACES, 4.5, 'deltas'],
  ['highlight', ['surface', 'paper'], 4.5, 'your-team chart labels (13px)'],
  ['on-inverse', ['inverse'], 4.5, 'skip link'],
  // non-text: data marks, control borders, focus ring
  ['highlight', ['surface', 'paper', 'highlight-tint'], 3, 'your-team lines, bars, row stripe'],
  ['context', ['surface', 'paper'], 3, 'context lines, bars, sparklines'],
  ['alliance-red', ['surface'], 3, 'alliance bars'],
  ['alliance-blue', ['surface'], 3, 'alliance bars'],
  ['rule-strong', SURFACES, 3, 'input, select and button borders'],
  ['focus-ring', SURFACES, 3, 'focus outline'],
  ['ink', ['surface-sunk'], 3, 'download progress bar'],
];

for (const theme of ['light', 'dark']) {
  test(`token contrast, ${theme} theme`, () => {
    const fails = [];
    for (const [fg, bgs, min, where] of PAIRS) {
      for (const bg of bgs) {
        const r = ratio(C[fg][theme], C[bg][theme]);
        if (r < min) fails.push(`${fg} on ${bg}: ${r.toFixed(2)} < ${min} (${where})`);
      }
    }
    assert.deepEqual(fails, []);
  });
}

test('large-text only pairs reach 3:1 (match scores 32px in ink-subtle)', () => {
  for (const theme of ['light', 'dark']) assert.ok(ratio(C['ink-subtle'][theme], C.surface[theme]) >= 3);
});

test('ratio() matches known WCAG values', () => {
  assert.equal(ratio('#000000', '#ffffff').toFixed(2), '21.00');
  assert.equal(ratio('#777777', '#ffffff').toFixed(2), '4.48');
});

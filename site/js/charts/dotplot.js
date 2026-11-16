// Difference dot plot: one row per item, a square at the value with an optional interval, a zero line.
// Used for "Fast EPA minus EPA" by season. Direction is encoded by delta-up/delta-down (better/worse),
// grey when the interval includes zero; the value is always printed.
//   diffPlot(rows, {title, ariaLabel, fmt, width}) -> SVG string
//     rows: [{label, sub, value, lo, hi, good: true|false|null}]
import { esc } from '../core/html.js';
import { niceTicks } from './line.js';

const f1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

export function diffPlot(rows, o) {
  const w = o.width || 380; const rowH = 36; const m = { l: 96, r: 64, t: 8, b: 46 };
  const h = m.t + m.b + rows.length * rowH;
  let lo = 0; let hi = 0;
  for (const r of rows) for (const v of [r.value, r.lo, r.hi]) if (typeof v === 'number') { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const sc = niceTicks(lo, hi, 4);
  const X = (v) => m.l + ((v - sc.min) / (sc.max - sc.min)) * (w - m.l - m.r);
  const nd = Math.min(4, (String(Number(sc.step.toPrecision(6))).split('.')[1] || '').length);
  const tl = (t) => (Math.abs(t) < 1e-12 ? '0' : t.toFixed(nd).replace('-', '−'));
  const s = [`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel)}" xmlns="http://www.w3.org/2000/svg">`];
  for (const t of sc.ticks) {
    s.push(`<line class="pw-grid" x1="${f1(X(t))}" x2="${f1(X(t))}" y1="${m.t}" y2="${h - m.b}"/>`);
    s.push(`<text class="pw-tick" x="${f1(X(t))}" y="${h - m.b + 16}" text-anchor="middle">${tl(t)}</text>`);
  }
  s.push(`<line class="pw-axis" x1="${f1(X(0))}" x2="${f1(X(0))}" y1="${m.t}" y2="${h - m.b}"/>`);
  s.push(`<text class="pw-axis-title" x="${f1((m.l + w - m.r) / 2)}" y="${h - 8}" text-anchor="middle">${esc(o.title)}</text>`);
  rows.forEach((r, i) => {
    const cy = m.t + i * rowH + rowH / 2;
    s.push(`<text class="pw-label pw-label--context" x="${m.l - 10}" y="${f1(cy - 1)}" text-anchor="end">${esc(r.label)}</text>`);
    if (r.sub) s.push(`<text class="pw-tick" x="${m.l - 10}" y="${f1(cy + 12)}" text-anchor="end">${esc(r.sub)}</text>`);
    if (typeof r.value !== 'number') { s.push(`<text class="pw-note" x="${m.l + 8}" y="${f1(cy + 4)}">not scored</text>`); return; }
    if (typeof r.lo === 'number' && typeof r.hi === 'number') {
      s.push(`<line class="pw-ci" x1="${f1(X(r.lo))}" x2="${f1(X(r.hi))}" y1="${f1(cy)}" y2="${f1(cy)}"/>`);
      for (const v of [r.lo, r.hi]) s.push(`<line class="pw-ci" x1="${f1(X(v))}" x2="${f1(X(v))}" y1="${f1(cy - 5)}" y2="${f1(cy + 5)}"/>`);
    }
    const cls = r.good === true ? 'pw-mark--up' : r.good === false ? 'pw-mark--down' : 'pw-mark--solid';
    s.push(`<rect class="pw-mark ${cls}" x="${f1(X(r.value) - 5)}" y="${f1(cy - 5)}" width="10" height="10"/>`);
    const right = Math.max(X(r.value), typeof r.hi === 'number' ? X(r.hi) : -Infinity);
    s.push(`<text class="pw-tick pw-tick--strong" x="${f1(Math.min(right + 8, w - 2))}" y="${f1(cy + 4)}"${right + 8 > w - m.r + 40 ? ' text-anchor="end"' : ''}>${esc(o.fmt(r.value))}</text>`);
  });
  s.push('</svg>');
  return s.join('');
}

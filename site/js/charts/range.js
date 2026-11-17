// EPA range marks (SPEC 5.10): a dot for the best estimate inside a band that fades toward the edges
// of the likely range. Solid = likely, faint = possible. One shared scale per view.
//   rangeScale(rows)                    -> {min, max, ticks}
//   rangeCell(row, scale, {you, width}) -> html string (aria-hidden; the numbers sit in their own column)
//   rangeAxis(scale, width)             -> html string of tick labels for a column header
//   distCurves({teams, width, unitLabel}) -> SVG of each team's one-match spread (normal curves)
import { esc } from '../core/html.js';
import { niceTicks } from './line.js';
import { rangeOf, matchSpread } from '../core/ranges.js';

const f1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

export function rangeScale(rows) {
  let lo = Infinity; let hi = -Infinity;
  for (const r of rows) {
    const g = rangeOf(r.epa, r.epa_se);
    if (g) { lo = Math.min(lo, g.lo); hi = Math.max(hi, g.hi); } else if (typeof r.epa === 'number') { lo = Math.min(lo, r.epa); hi = Math.max(hi, r.epa); }
  }
  if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
  const t = niceTicks(Math.max(lo, Math.min(0, hi - 1)), hi, 7);
  return { min: t.min, max: t.max, ticks: t.ticks };
}

const pos = (v, s) => Math.max(0, Math.min(100, ((v - s.min) / (s.max - s.min)) * 100));

export function rangeCell(r, s, o = {}) {
  const w = o.width || 200;
  const g = rangeOf(r.epa, r.epa_se);
  const band = g ? `<span class="pw-range__band" style="left:${f1(pos(g.lo, s))}%;width:${f1(pos(g.hi, s) - pos(g.lo, s))}%"></span>` : '';
  const dot = typeof r.epa === 'number' ? `<span class="pw-range__dot" style="left:${f1(pos(r.epa, s))}%"></span>` : '';
  return `<span class="pw-range${o.you ? ' pw-range--you' : ''}" aria-hidden="true" style="width:${w}px">${band}${dot}</span>`;
}

export function rangeAxis(s, w = 200) {
  return `<span class="pw-range-axis" aria-hidden="true" style="width:${w}px">${s.ticks.map((t) => `<span style="left:${f1(pos(t, s))}%">${t}</span>`).join('')}</span>`;
}

/** Normal curves of one-match points for 2-4 teams on a shared axis. teams: [{row, label, role: 'you'|'focus'|'context'}]. */
export function distCurves(o) {
  const w = o.width || 640; const h = 170; const l = 16; const r0 = 16; const top = 12; const base = 128;
  const ms = o.teams.map((t) => ({ ...t, sd: matchSpread(t.row) })).filter((t) => t.sd && typeof t.row.epa === 'number');
  if (!ms.length) return '';
  const lo = Math.min(...ms.map((t) => t.row.epa - 3 * t.sd)); const hi = Math.max(...ms.map((t) => t.row.epa + 3 * t.sd));
  const sc = niceTicks(lo, hi, 7);
  const X = (v) => l + ((v - sc.min) / (sc.max - sc.min)) * (w - l - r0);
  const peak = Math.max(...ms.map((t) => 1 / t.sd));
  const s = [`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel || 'One-match points for each team')}" xmlns="http://www.w3.org/2000/svg">`];
  for (const t of sc.ticks) s.push(`<line class="pw-grid" x1="${f1(X(t))}" x2="${f1(X(t))}" y1="${top}" y2="${base}"/><text class="pw-tick" x="${f1(X(t))}" y="${base + 16}" text-anchor="middle">${t}</text>`);
  s.push(`<line class="pw-axis" x1="${l}" x2="${w - r0}" y1="${base}" y2="${base}"/>`);
  if (o.unitLabel) s.push(`<text class="pw-axis-title" x="${f1(w / 2)}" y="${h - 4}" text-anchor="middle">${esc(o.unitLabel)}</text>`);
  const order = ms.slice().sort((a, b) => (a.role === 'you') - (b.role === 'you'));
  for (const t of order) {
    const pts = [];
    for (let i = 0; i <= 120; i++) {
      const v = sc.min + ((sc.max - sc.min) * i) / 120;
      const d = Math.exp(-0.5 * ((v - t.row.epa) / t.sd) ** 2) / t.sd;
      pts.push(`${f1(X(v))},${f1(base - (d / peak) * (base - top))}`);
    }
    s.push(`<polygon class="pw-curve pw-curve--${t.role}" points="${f1(X(sc.min))},${base} ${pts.join(' ')} ${f1(X(sc.max))},${base}"/>`);
    s.push(`<line class="pw-curve-mid pw-curve-mid--${t.role}" x1="${f1(X(t.row.epa))}" x2="${f1(X(t.row.epa))}" y1="${base}" y2="${f1(base - ((1 / t.sd) / peak) * (base - top))}"/>`);
  }
  s.push('</svg>');
  return s.join('');
}

// Bars start at zero and share one scale (SPEC 5.5). Used inline in tables.
//   barWidth(value, max, maxPx) -> integer px (0 for null/negative)
//   barCell(value, max, maxPx, text) -> html string "<span class=pw-cell-bar><span class=pw-bar style=width:..></span>text</span>"
import { esc } from '../core/html.js';
import { niceTicks } from './line.js';

export function barWidth(value, max, maxPx = 120) {
  if (typeof value !== 'number' || !Number.isFinite(value) || !(max > 0) || value <= 0) return 0;
  return Math.round(Math.min(1, value / max) * maxPx);
}

export function barCell(value, max, maxPx, text) {
  return `<span class="pw-cell-bar"><span class="pw-bar" aria-hidden="true" style="width:${barWidth(value, max, maxPx)}px"></span>${esc(text)}</span>`;
}

/**
 * Grouped horizontal bars on one zero-based axis: groups (e.g. auto/teleop/endgame) x items (teams).
 *   groupedBars({groups: [{label, values: [{label, value, you}]}], axisTitle, ariaLabel, width}) -> SVG string
 * Every bar is labelled with its item and value; watchlist items use the highlight colour.
 */
export function groupedBars(o) {
  const w = o.width || 640; const l = 84; const r0 = 96; const rowH = 22; const gap = 16;
  const n = Math.max(1, ...o.groups.map((g) => g.values.length));
  const gh = n * rowH;
  const plotH = o.groups.length * (gh + gap) - gap;
  const h = 8 + plotH + 44;
  const maxV = Math.max(1, ...o.groups.flatMap((g) => g.values.map((v) => v.value || 0)));
  const sc = niceTicks(0, maxV, 5);
  const X = (v) => l + (Math.max(0, v) / sc.max) * (w - l - r0);
  const f = (v) => (Math.round(v * 10) / 10).toFixed(1);
  const s = [`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel)}" xmlns="http://www.w3.org/2000/svg">`];
  const baseY = 8 + plotH + 4;
  for (const t of sc.ticks) {
    s.push(`<line class="pw-grid" x1="${f(X(t))}" x2="${f(X(t))}" y1="4" y2="${baseY}"/><text class="pw-tick" x="${f(X(t))}" y="${baseY + 16}" text-anchor="middle">${t}</text>`);
  }
  s.push(`<line class="pw-axis" x1="${l}" x2="${l}" y1="4" y2="${baseY}"/><text class="pw-axis-title" x="${f((l + w - r0) / 2)}" y="${h - 6}" text-anchor="middle">${esc(o.axisTitle)}</text>`);
  o.groups.forEach((g, gi) => {
    const y0 = 8 + gi * (gh + gap);
    s.push(`<text class="pw-label pw-label--context" x="${l - 8}" y="${y0 + 15}" text-anchor="end">${esc(g.label)}</text>`);
    g.values.forEach((v, vi) => {
      const y = y0 + vi * rowH;
      s.push(`<rect class="pw-hbar${v.you ? ' pw-hbar--you' : ''}" x="${l}" y="${y + 3}" width="${f(Math.max(1, X(v.value || 0) - l))}" height="${rowH - 7}"/>`);
      s.push(`<text class="pw-tick pw-tick--strong" x="${f(X(v.value || 0) + 6)}" y="${y + 15}">${esc(v.label)} · ${f(v.value || 0)}</text>`);
    });
  });
  s.push('</svg>');
  return s.join('');
}

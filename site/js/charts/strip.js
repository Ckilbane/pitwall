// Range strip for a table cell: the team's value as a bar from zero, the world 25-75th percentile
// band, a tick at the world 90th percentile and a line at the region median. Scale 0 .. top (world
// 99th percentile, or the value if larger). Decorative: the row prints every number.
//   rangeStrip({value, p25, p75, p90, median, top, you}) -> SVG string (aria-hidden)
const f1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

export function rangeStrip({ value, p25, p75, p90, median, top, you }) {
  const w = 180; const h = 22; const m = 2;
  const t = Math.max(top || 0, value || 0, p90 || 0, 1e-6);
  const X = (v) => m + (Math.max(0, Math.min(t, v)) / t) * (w - 2 * m);
  const s = [`<svg class="pw-strip" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true" focusable="false">`];
  if (typeof p25 === 'number' && typeof p75 === 'number') s.push(`<rect class="pw-strip__band" x="${f1(X(p25))}" y="3" width="${f1(Math.max(1, X(p75) - X(p25)))}" height="${h - 6}"/>`);
  if (typeof value === 'number') s.push(`<rect class="pw-strip__bar${you ? ' pw-strip__bar--you' : ''}" x="${m}" y="${h / 2 - 3}" width="${f1(Math.max(1, X(value) - m))}" height="6"/>`);
  if (typeof p90 === 'number') s.push(`<line class="pw-strip__p90" x1="${f1(X(p90))}" x2="${f1(X(p90))}" y1="1" y2="${h - 1}"/>`);
  if (typeof median === 'number') s.push(`<line class="pw-strip__med" x1="${f1(X(median))}" x2="${f1(X(median))}" y1="4" y2="${h - 4}"/>`);
  s.push('</svg>');
  return s.join('');
}

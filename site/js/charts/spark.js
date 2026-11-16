// Residual sparkline: one square bar per match around a hairline zero (SPEC 5.5).
//   spark(values, {n = 12, w = 72, h = 18, label}) -> SVG string. null values leave a gap.
import { esc } from '../core/html.js';

export function spark(values, o = {}) {
  const n = o.n || 12;
  const w = o.w || 72;
  const h = o.h || 18;
  const v = (values || []).slice(-n);
  if (!v.length) return '';
  const nums = v.filter((x) => typeof x === 'number');
  const max = Math.max(1, ...nums.map((x) => Math.abs(x)));
  const mid = h / 2;
  const slot = w / n;
  const bw = Math.max(1, slot - 1);
  const bars = v.map((x, i) => {
    if (typeof x !== 'number') return '';
    const bh = Math.max(1, (Math.abs(x) / max) * (mid - 1));
    const x0 = (n - v.length + i) * slot;
    return `<rect x="${x0.toFixed(1)}" y="${(x >= 0 ? mid - bh : mid).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}"></rect>`;
  }).join('');
  const label = o.label || `Last ${v.length} match residuals, actual minus predicted, points: ${nums.map((x) => x.toFixed(1)).join(', ')}`;
  return `<svg class="pw-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}"><line x1="0" x2="${w}" y1="${mid}" y2="${mid}"></line>${bars}</svg>`;
}

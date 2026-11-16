// Small multiples: one panel per series on shared axes (SPEC 5.5, used when a chart would carry more
// than three series). With context: true each panel also draws the other series as thin grey lines.
//   smallMultiples({series, x, y, ariaLabel, panelWidth, cols, context}) -> html string
//     series: [{id, label, title?, role: 'you'|'focus', points: [{x, y}]}]
import { lineChart, niceTicks } from './line.js';
import { esc } from '../core/html.js';

export function smallMultiples(o) {
  const series = o.series.filter((s) => s.points && s.points.length);
  if (!series.length) return '';
  const ys = series.flatMap((s) => s.points.map((p) => p.y));
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  const yt = niceTicks(Math.min(...ys, o.y && o.y.min !== undefined ? o.y.min : Infinity), Math.max(...ys), 5);
  const xmax = Math.max(...xs, 1);
  const w = o.panelWidth || 420;
  const panels = series.map((s) => {
    const others = o.context ? series.filter((t) => t !== s).map((t) => ({ id: t.id, role: 'context', points: t.points })) : [];
    const svg = lineChart({
      series: [...others, { ...s, role: s.role || 'focus' }],
      x: { ...(o.x || {}), min: 0, max: xmax },
      y: { ...(o.y || {}), min: yt.min, max: yt.max },
      width: w, height: 240,
      ariaLabel: `${s.title || s.label || s.id}. ${o.ariaLabel || ''}`,
    });
    return `<div class="pw-multiple"><h3 class="pw-multiple__title">${esc(s.title || s.label || s.id)}</h3><div class="pw-chart">${svg}</div></div>`;
  });
  const style = o.cols ? ` style="grid-template-columns: repeat(${o.cols}, minmax(0, 1fr))"` : '';
  return `<div class="pw-multiples"${style}>${panels.join('')}</div>`;
}

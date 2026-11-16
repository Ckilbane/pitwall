// Hand-written SVG line chart in the design-system classes (SPEC 5.5). Returns an SVG string.
//
//   lineChart({series, x, y, refLines, annotations, ariaLabel, width, height})
//     series: [{id, label, role: 'you'|'focus'|'context', points: [{x, y}], dot?: true}]
//     x, y:   {title, min?, max?, ticks?: number[], format?: (v) => string}
//     refLines:    [{y, label}] horizontal dashed reference lines (labelled directly)
//     annotations: [{x, label, line = true}] vertical dotted lines with a short note (event boundaries)
//   niceTicks(lo, hi, maxTicks) -> {ticks, min, max, step};  nudge(items, gap, lo, hi)
//   lineDataTable(series, xTitle) -> html string of the same numbers (the "Show data" table)
import { esc } from '../core/html.js';

const f1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

export function niceTicks(lo, hi, maxTicks = 6) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { ticks: [0, 1], min: 0, max: 1, step: 1 };
  if (hi === lo) { hi = lo + 1; }
  const span = hi - lo;
  let best = null;
  for (let pow = Math.floor(Math.log10(span)) - 1; pow <= Math.floor(Math.log10(span)) + 1; pow++) {
    for (const m of [1, 2, 2.5, 5, 10]) {
      const step = m * 10 ** pow;
      const a = Math.floor(lo / step + 1e-9) * step;
      const b = Math.ceil(hi / step - 1e-9) * step;
      const count = Math.round((b - a) / step) + 1;
      if (count >= 3 && count <= maxTicks && (!best || count > best.count || (count === best.count && step < best.step))) {
        best = { step, a, b, count };
      }
    }
  }
  if (!best) { const step = span / 4; best = { step, a: lo, b: hi, count: 5 }; }
  const ticks = [];
  for (let i = 0; i < best.count; i++) ticks.push(Math.round((best.a + i * best.step) / best.step * 1e6) / 1e6 * best.step);
  return { ticks: ticks.map((t) => Number(t.toPrecision(10))), min: ticks[0], max: ticks[ticks.length - 1], step: best.step };
}

/** Vertical collision nudge: items [{y}] -> same items with y spread by at least gap inside [lo, hi]. */
export function nudge(items, gap, lo, hi) {
  const s = items.map((it, i) => ({ it, i, y: it.y })).sort((a, b) => a.y - b.y);
  for (let k = 0; k < s.length; k++) { s[k].y = Math.max(s[k].y, k ? s[k - 1].y + gap : lo); }
  const over = s.length ? s[s.length - 1].y - hi : 0;
  if (over > 0) {
    for (let k = s.length - 1; k >= 0; k--) {
      s[k].y = Math.min(s[k].y, k === s.length - 1 ? hi : s[k + 1].y - gap);
    }
  }
  const out = items.map((it) => ({ ...it }));
  for (const e of s) out[e.i].y = e.y;
  return out;
}

const roleClass = { you: 'you', focus: 'focus', context: 'context' };

export function lineChart(o) {
  const W = o.width || 780;
  const wide = W >= 560;
  const H = o.height || (wide ? 340 : 300);
  const L = wide ? 60 : 50;
  const R = wide ? 124 : 88;
  const T = 20;
  const B = H - 50;
  const pr = W - R; // plot right
  const series = o.series.filter((s) => s.points && s.points.length);
  const allY = series.flatMap((s) => s.points.map((p) => p.y)).concat((o.refLines || []).map((r) => r.y));
  const allX = series.flatMap((s) => s.points.map((p) => p.x));
  const yt = niceTicks(o.y && o.y.min !== undefined ? o.y.min : Math.min(...allY), o.y && o.y.max !== undefined ? o.y.max : Math.max(...allY), 6);
  const xhi = o.x && o.x.max !== undefined ? o.x.max : Math.max(...allX, 1);
  const xt = niceTicks(o.x && o.x.min !== undefined ? o.x.min : 0, xhi, wide ? 8 : 6);
  const xmin = o.x && o.x.min !== undefined ? o.x.min : xt.min;
  const xmax = Math.max(xhi, xmin + 1);
  const sx = (v) => L + ((v - xmin) / (xmax - xmin)) * (pr - L);
  const sy = (v) => B - ((v - yt.min) / (yt.max - yt.min)) * (B - T);
  const yfmt = (o.y && o.y.format) || ((v) => String(v));
  const xfmt = (o.x && o.x.format) || ((v) => String(v));
  const out = [];
  out.push(`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.ariaLabel || '')}" xmlns="http://www.w3.org/2000/svg">`);
  for (const t of yt.ticks) {
    out.push(`<line class="pw-grid" x1="${L}" x2="${pr}" y1="${f1(sy(t))}" y2="${f1(sy(t))}"></line>`);
    out.push(`<text class="pw-tick" x="${L - 8}" y="${f1(sy(t) + 4)}" text-anchor="end">${esc(yfmt(t))}</text>`);
  }
  out.push(`<line class="pw-axis" x1="${L}" x2="${L}" y1="${T}" y2="${B}"></line>`);
  out.push(`<line class="pw-axis" x1="${L}" x2="${pr}" y1="${B + 6}" y2="${B + 6}"></line>`);
  for (const t of xt.ticks) {
    if (t < xmin || t > xmax) continue;
    out.push(`<line class="pw-axis" x1="${f1(sx(t))}" x2="${f1(sx(t))}" y1="${B + 6}" y2="${B + 10}"></line>`);
    out.push(`<text class="pw-tick" x="${f1(sx(t))}" y="${B + 23}" text-anchor="middle">${esc(xfmt(t))}</text>`);
  }
  if (o.y && o.y.title) out.push(`<text class="pw-axis-title" transform="translate(14 ${f1((T + B) / 2)}) rotate(-90)" text-anchor="middle">${esc(o.y.title)}</text>`);
  if (o.x && o.x.title) out.push(`<text class="pw-axis-title" x="${f1((L + pr) / 2)}" y="${H - 6}" text-anchor="middle">${esc(o.x.title)}</text>`);

  // event boundaries with short notes along the bottom of the plot
  let lastEnd = -Infinity;
  for (const a of o.annotations || []) {
    const x = sx(a.x);
    if (a.line !== false) out.push(`<line class="pw-grid pw-grid--event" x1="${f1(x)}" x2="${f1(x)}" y1="${T}" y2="${B}"></line>`);
    if (a.label) {
      const w = a.label.length * 6.2;
      const right = x + 6 + w > pr;
      const x0 = right ? x - 6 - w : x + 6;
      if (x0 >= lastEnd + 4 && x0 >= L) {
        out.push(`<text class="pw-note" x="${f1(right ? x - 6 : x + 6)}" y="${B - 6}"${right ? ' text-anchor="end"' : ''}>${esc(a.label)}</text>`);
        lastEnd = x0 + w;
      }
    }
  }

  // end labels: lines' labels and reference labels share one collision pass
  const labels = [];
  for (const r of o.refLines || []) {
    out.push(`<line class="pw-line--ref" x1="${L}" x2="${pr}" y1="${f1(sy(r.y))}" y2="${f1(sy(r.y))}"></line>`);
    if (r.label) {
      if (wide) labels.push({ y: sy(r.y) + 4, text: r.label, cls: 'pw-label pw-label--context', x: pr + 6 });
      else out.push(`<text class="pw-label pw-label--context" x="${pr - 2}" y="${f1(sy(r.y) - 5)}" text-anchor="end" style="font-size:12px">${esc(r.label)}</text>`);
    }
  }
  for (const s of series) {
    const cls = roleClass[s.role] || 'context';
    const d = s.points.map((p, i) => `${i ? 'L' : 'M'}${f1(sx(p.x))},${f1(sy(p.y))}`).join(' ');
    out.push(`<path class="pw-line--${cls}" d="${d}"></path>`);
  }
  for (const s of series) {
    const cls = roleClass[s.role] || 'context';
    const last = s.points[s.points.length - 1];
    if (s.dot || cls !== 'context') out.push(`<circle class="pw-dot--${cls === 'context' ? 'focus' : cls}" cx="${f1(sx(last.x))}" cy="${f1(sy(last.y))}" r="3.5"></circle>`);
    if (s.label) {
      labels.push({ y: sy(last.y) + 4, text: s.label, cls: `pw-label pw-label--${cls}`, x: sx(last.x) + 8 });
    }
  }
  const placed = nudge(labels, 15, T + 8, B);
  for (const l of placed) {
    // keep labels inside the viewBox: anchor to the end when they would run off the right edge
    const w = l.text.length * 6.6;
    const over = l.x + w > W;
    out.push(`<text class="${l.cls}" x="${f1(over ? Math.min(l.x - 12, W - 2) : l.x)}" y="${f1(l.y)}"${over ? ' text-anchor="end"' : ''}>${esc(l.text)}</text>`);
  }
  out.push('</svg>');
  return out.join('');
}

/** The same numbers as a table, for the "Show data" toggle. series[].points x are 1-based counts or any numbers. */
export function lineDataTable(series, xTitle, yFmt = f1, caption) {
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort((a, b) => a - b);
  const maps = series.map((s) => new Map(s.points.map((p) => [p.x, p.y])));
  const head = `<tr><th scope="col" class="n">${esc(xTitle)}</th>${series.map((s) => `<th scope="col" class="n">${esc(s.label || s.id)}</th>`).join('')}</tr>`;
  const rows = xs.map((x) => `<tr><th scope="row" class="n">${x}</th>${maps.map((m) => `<td class="n">${m.has(x) ? esc(yFmt(m.get(x))) : ''}</td>`).join('')}</tr>`);
  const cap = caption || `${series.map((s) => s.label || s.id).join(', ')} by ${xTitle}`;
  return `<table class="pw-table"><caption class="pw-sr">${esc(cap)}</caption><thead>${head}</thead><tbody>${rows.join('')}</tbody></table>`;
}

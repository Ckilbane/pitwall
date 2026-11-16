// Calibration chart: predicted probability (x) against observed frequency (y), both 0-100%, with the
// y = x reference. Square marks; series differ by fill (solid / hollow), never by colour alone, and a
// two-entry key names them (direct labels would collide on the diagonal).
//   calibrationChart(series, {x, y, ariaLabel}) -> SVG string
//     series: [{label, hollow, pts: [[xPct, yPct, n]]}]
import { esc } from '../core/html.js';

const f1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

export function calibrationChart(series, o) {
  const w = 460; const h = 420; const m = { l: 56, r: 16, t: 34, b: 48 };
  const ticks = [0, 25, 50, 75, 100];
  const X = (v) => m.l + (v / 100) * (w - m.l - m.r);
  const Y = (v) => h - m.b - (v / 100) * (h - m.t - m.b);
  const s = [`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel)}" xmlns="http://www.w3.org/2000/svg">`];
  for (const t of ticks) {
    s.push(`<line class="pw-grid" x1="${m.l}" x2="${w - m.r}" y1="${f1(Y(t))}" y2="${f1(Y(t))}"/>`);
    s.push(`<text class="pw-tick" x="${m.l - 8}" y="${f1(Y(t) + 4)}" text-anchor="end">${t}</text>`);
    s.push(`<text class="pw-tick" x="${f1(X(t))}" y="${h - m.b + 18}" text-anchor="middle">${t}</text>`);
  }
  s.push(`<line class="pw-axis" x1="${m.l}" x2="${w - m.r}" y1="${h - m.b}" y2="${h - m.b}"/><line class="pw-axis" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${h - m.b}"/>`);
  s.push(`<line class="pw-line--ref" x1="${f1(X(0))}" y1="${f1(Y(0))}" x2="${f1(X(100))}" y2="${f1(Y(100))}"/>`);
  s.push(`<text class="pw-note" x="${f1(X(100))}" y="${f1(Y(4))}" text-anchor="end">dashed line: perfectly calibrated</text>`);
  s.push(`<text class="pw-axis-title" x="${f1((m.l + w - m.r) / 2)}" y="${h - 8}" text-anchor="middle">${esc(o.x)}</text>`);
  s.push(`<text class="pw-axis-title" transform="translate(14 ${f1((m.t + h - m.b) / 2)}) rotate(-90)" text-anchor="middle">${esc(o.y)}</text>`);
  series.forEach((se) => {
    for (const p of se.pts) s.push(`<rect class="pw-mark ${se.hollow ? 'pw-mark--hollow' : 'pw-mark--solid'}" x="${f1(X(p[0]) - 4)}" y="${f1(Y(p[1]) - 4)}" width="8" height="8"/>`);
  });
  let kx = m.l;
  series.forEach((se) => {
    s.push(`<rect class="pw-mark ${se.hollow ? 'pw-mark--hollow' : 'pw-mark--solid'}" x="${kx}" y="10" width="8" height="8"/>`);
    s.push(`<text class="pw-label pw-label--focus" x="${kx + 13}" y="18">${esc(se.label)}</text>`);
    kx += 26 + se.label.length * 7.5;
  });
  s.push('</svg>');
  return s.join('');
}

// Win probability on the design system's diverging scale (div-red-3 .. div-blue-3). One set of
// cut-offs everywhere; the value is always printed, colour never carries it alone.
//
//   WP_BANDS                       [{id, lo, hi, label}] red-certain to blue-certain
//   wpBand(p) -> 'r3'|'r2'|'r1'|'mid'|'b1'|'b2'|'b3'|null      p = red win probability
//   favoured(p) -> {side: 'red'|'blue'|null, lead}
//   pctP(p)                        "62%", one decimal within 3 points of 50%
//   wpChip(p) -> SafeHTML          "Red 62%" on the band colour
//   wpKey() -> SafeHTML            the seven bands with their ranges
//   wpBar(p) -> SafeHTML           "Red 62% · 38% Blue" labels over a red/blue track (match cards)
//   wpScale(p, name, note) -> SafeHTML   the diverging scale with a marker at p (Match tab)
import { html } from '../core/html.js';
import { pct, DASH } from '../core/fmt.js';

export const WP_BANDS = [
  { id: 'r3', lo: 0.80, hi: 1.00, label: 'Red 80% or more' },
  { id: 'r2', lo: 0.65, hi: 0.80, label: 'Red 65-80%' },
  { id: 'r1', lo: 0.55, hi: 0.65, label: 'Red 55-65%' },
  { id: 'mid', lo: 0.45, hi: 0.55, label: 'Even, 45-55%' },
  { id: 'b1', lo: 0.35, hi: 0.45, label: 'Blue 55-65%' },
  { id: 'b2', lo: 0.20, hi: 0.35, label: 'Blue 65-80%' },
  { id: 'b3', lo: 0.00, hi: 0.20, label: 'Blue 80% or more' },
];

export function wpBand(p) {
  if (p === null || p === undefined || Number.isNaN(p)) return null;
  if (p >= 0.80) return 'r3';
  if (p >= 0.65) return 'r2';
  if (p >= 0.55) return 'r1';
  if (p > 0.45) return 'mid';
  if (p > 0.35) return 'b1';
  if (p > 0.20) return 'b2';
  return 'b3';
}

export function favoured(p) {
  if (p === null || p === undefined) return { side: null, lead: null };
  if (p > 0.5) return { side: 'red', lead: p };
  if (p < 0.5) return { side: 'blue', lead: 1 - p };
  return { side: null, lead: 0.5 };
}

export function pctP(p) {
  if (p === null || p === undefined) return DASH;
  return Math.abs(p - 0.5) < 0.03 ? pct(p, 1) : pct(p);
}

export function wpChip(p) {
  const band = wpBand(p);
  if (band === null) return html`<span class="pw-wp-chip pw-wp-chip--none">${DASH}</span>`;
  const f = favoured(p);
  const text = f.side ? `${f.side === 'red' ? 'Red' : 'Blue'} ${pctP(f.lead)}` : 'Even 50%';
  return html`<span class="pw-wp-chip pw-wp-chip--${band}">${text}</span>`;
}

export function wpKey() {
  return html`<ul class="pw-wp-key" aria-label="Win probability colour scale">${WP_BANDS.map((b) => html`<li class="pw-wp-chip pw-wp-chip--${b.id}">${b.label}</li>`)}</ul>`;
}

export function wpBar(p) {
  if (p === null || p === undefined) return '';
  return html`<div class="pw-wp__labels"><span class="r">Red ${pctP(p)}</span><span class="b">${pctP(1 - p)} Blue</span></div>
<div class="pw-wp__track" aria-hidden="true"><span class="r" style="width: ${(p * 100).toFixed(1)}%"></span><span class="gap"></span><span class="b" style="width: ${((1 - p) * 100).toFixed(1)}%"></span></div>`;
}

export function wpScale(p, name, note) {
  const text = p === null || p === undefined ? 'No prediction' : `Red ${pctP(p)} · Blue ${pctP(1 - p)}`;
  return html`<div class="pw-wpscale">
<div class="pw-wpscale__head"><span class="pw-wpscale__model">${name}</span><span class="pw-wpscale__val">${text}</span>${note ? html`<span class="pw-small">${note}</span>` : ''}</div>
${p === null || p === undefined ? '' : html`<div class="pw-wpscale__bar" role="img" aria-label="${name} gives red ${pctP(p)} and blue ${pctP(1 - p)}">${WP_BANDS.map((b) => html`<span class="pw-wpscale__stop pw-wp-chip--${b.id}" style="flex: ${Math.round((b.hi - b.lo) * 100)}"></span>`)}<span class="pw-wpscale__mark" style="left: ${((1 - p) * 100).toFixed(1)}%"></span></div>
<div class="pw-wpscale__ends" aria-hidden="true"><span>Red certain</span><span>Even</span><span>Blue certain</span></div>`}
</div>`;
}

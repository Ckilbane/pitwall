// Markup helpers shared by the Events, Event and Match views (WP4b). Uses only core modules and the
// design-system classes (pw-*); view-specific classes are in css/views/{events,event,match}.css.
import { html, raw } from '../core/html.js';
import * as fmt from '../core/fmt.js';
import * as router from '../core/router.js';
import * as watchlist from '../core/watchlist.js';
import { called } from './event_lib.js';
import { teamChip } from '../components/team.js';
import { wpChip, pctP } from '../components/wp.js';
import { pageHead } from '../components/page.js';

export { html, raw, fmt, router, watchlist, teamChip, wpChip, pctP, pageHead };

/** Join already-safe fragments (html`` results) into one SafeHTML. */
export function list(items) {
  return raw(items.map((x) => String(x)).join(''));
}

// ---------- text ----------

export const DASH = fmt.DASH;

export function modelLabel(models, id) {
  const m = (models || []).find((x) => x.id === id);
  return m ? m.label : id;
}

/** "Q32 · Galileo Division" style short names. */
export function label(ev, mi) {
  const c = ev.matches.cols;
  return fmt.matchLabel(c.comp_level[mi], c.set_number[mi], c.match_number[mi]);
}

/** Oxford-free list: "a, b and c". */
export function andList(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

export function fieldLabel(key) {
  const s = String(key).replace(/Robot$/, '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------- components ----------

/** "called" / "missed" / "" for a model's call of a match. */
export function verdict(p, winner) {
  const c = called(p, winner);
  return c === null ? '' : c ? 'called' : 'missed';
}

/** ▲/▼ change with the design-system delta colours (never red/blue). */
export function deltaCell(x, { nd = 1 } = {}) {
  if (x == null || Number.isNaN(x)) return html`<span class="pw-muted">${DASH}</span>`;
  const s = fmt.delta(x, nd);
  if (/^[0.]+$/.test(s.replace(/[+−]/g, ''))) return html`<span class="pw-delta">${s}</span>`;
  const up = x > 0;
  return html`<span class="pw-delta ${up ? 'pw-delta--up' : 'pw-delta--down'}">${up ? '▲' : '▼'} ${s}</span>`;
}

/** Table scroll box that is keyboard-scrollable. */
export function scrollBox(inner, labelText) {
  return html`<div class="pw-scroll pwe-box" tabindex="0" role="region" aria-label="${labelText}">${inner}</div>`;
}

/** Event key as a readable label in small text. */
export function eventTitle(ev) {
  return ev.meta.name || ev.meta.key;
}

export function errorNote(text) {
  return html`<p class="pw-lede">${text}</p>`;
}

/** Generic text for how fresh the data is. */
export function updatedText(season, manifest) {
  const t = (season && season.generated_at) || (manifest && manifest.generated_at);
  return t ? `Data updated ${fmt.dateTime(t)}.` : '';
}

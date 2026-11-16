// "As of" control (WP5). A select (every option reachable by keyboard) and, for events, a range
// input that steps through the same options.
//
//   asofOptions(seasonOrEvent) -> [{id, label}]
//       season.json object: Preseason (w0), After week 1..N, End of season (end). N = the last
//       in-season week (1-7) that has events. Week 7 reads "After week 7 (before Champs)" when the
//       season has Champs events (week 8).
//       event file object ({meta, matches}): start, After Q1 .. After <last completed match - 1>,
//       end. Ids for matches are the match labels (Q32, SF2-1, F1) so URLs read well.
//   asofControl({scope: 'season'|'event', value, options, id?, label?}) -> SafeHTML
//       options defaults to []; pass asofOptions(...). The select carries data-act="asof" and
//       data-asof-scope; event scope adds <input type="range" data-act="asof-range" data-ids=...>.
//   readAsof(el) -> id | null     read the chosen id from a change/input event target
//   normalizeSeasonAsof(v) -> 'end' | 'w0'..'w7'     ('prechamps' -> 'w7', 'w8' -> 'end')
//   seasonAsofIndex(v) -> 0..8    index into asof.json points (w0..w8); 'end' = 8
//   eventAsofMatchIndex(eventData, v) -> number   how many rows of `matches` (in order) count as
//       played: 0 for 'start', all for 'end', else index of the labelled match + 1
//   matchLabel(comp_level, set, number) -> 'Q32' | 'QF2-1' | 'SF2-1' | 'F1'
// Styles: css/views/asof.css (pw-asof, pw-range), linked from index.html.

import { raw, esc } from '../core/html.js';
import { matchLabel } from '../core/fmt.js';

export { matchLabel };

export function normalizeSeasonAsof(v) {
  if (v === 'prechamps') return 'w7';
  if (typeof v === 'string' && /^w[0-7]$/.test(v)) return v;
  return 'end';
}

export function seasonAsofIndex(v) {
  const n = normalizeSeasonAsof(v);
  return n === 'end' ? 8 : Number(n.slice(1));
}

function isEventData(x) {
  return !!(x && x.matches && x.matches.cols && x.meta);
}

export function asofOptions(obj) {
  if (isEventData(obj)) {
    const c = obj.matches.cols;
    const out = [{ id: 'start', label: 'Before the first match' }];
    let last = -1;
    for (let i = 0; i < obj.matches.n; i++) if (c.red_score && c.red_score[i] !== null && c.red_score[i] !== undefined) last = i;
    for (let i = 0; i < last; i++) {
      if (c.red_score[i] === null || c.red_score[i] === undefined) continue;
      const id = matchLabel(c.comp_level[i], c.set_number[i], c.match_number[i]);
      out.push({ id, label: `After ${id}` });
    }
    out.push({ id: 'end', label: 'End of event' });
    return out;
  }
  const weeks = (obj && obj.events && obj.events.cols && obj.events.cols.week) || [];
  let maxWeek = 0;
  let champs = false;
  for (const w of weeks) {
    if (typeof w !== 'number') continue;
    if (w >= 8) champs = true;
    else if (w > maxWeek) maxWeek = w;
  }
  const out = [{ id: 'w0', label: 'Preseason' }];
  for (let w = 1; w <= maxWeek; w++) {
    out.push({ id: `w${w}`, label: w === 7 && champs ? 'After week 7 (before Champs)' : `After week ${w}` });
  }
  out.push({ id: 'end', label: 'End of season' });
  return out;
}

export function eventAsofMatchIndex(eventData, v) {
  const m = eventData.matches;
  if (v === undefined || v === null || v === 'end') return m.n;
  if (v === 'start') return 0;
  const c = m.cols;
  for (let i = 0; i < m.n; i++) {
    if (matchLabel(c.comp_level[i], c.set_number[i], c.match_number[i]) === v) return i + 1;
  }
  return m.n; // unknown label: show the end
}

export function asofControl({ scope = 'season', value = 'end', options = [], id = 'pw-asof', label = 'As of' } = {}) {
  const cur = scope === 'season' ? normalizeSeasonAsof(value) : value;
  const opts = options.map((o) => `<option value="${esc(o.id)}"${o.id === cur ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
  let range = '';
  let idx = options.findIndex((o) => o.id === cur);
  if (idx < 0) idx = options.length - 1;
  if (scope === 'event' && options.length > 1) {
    const ids = options.map((o) => o.id).join(',');
    range = `<input class="pw-range" type="range" min="0" max="${options.length - 1}" step="1" value="${idx}" `
      + `data-act="asof-range" data-ids="${esc(ids)}" aria-label="Step through the event, match by match" `
      + `aria-valuetext="${esc((options[idx] || {}).label || '')}">`;
  }
  return raw(`<div class="pw-asof pw-asof--${esc(scope)}" data-asof-scope="${esc(scope)}">`
    + `<label class="pw-eyebrow pw-asof__label" for="${esc(id)}">${esc(label)}</label>`
    + `<select class="pw-select" id="${esc(id)}" data-act="asof" data-asof-scope="${esc(scope)}">${opts}</select>`
    + range
    + '</div>');
}

export function readAsof(el) {
  if (!el || !el.dataset) return null;
  if (el.dataset.act === 'asof') return el.value;
  if (el.dataset.act === 'asof-range') {
    const ids = (el.dataset.ids || '').split(',');
    return ids[Number(el.value)] ?? null;
  }
  return null;
}

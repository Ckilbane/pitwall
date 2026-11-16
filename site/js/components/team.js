// Team chip (SPEC 5.5): links to #/team/{n}; watchlist teams get --you (amber), others the alliance
// tint for their side or the plain chip.
//   teamChip(num, {you?, side?: 'red'|'blue', focus?, season?, model?, label?})   you defaults to the watchlist
//   watchButton(num, watching, {btn?})   text toggle "Watch"/"Watching" with aria-pressed
import { html } from '../core/html.js';
import { link } from '../core/router.js';
import * as watchlist from '../core/watchlist.js';

export function teamChip(num, o = {}) {
  const you = o.you === undefined ? watchlist.has(num) : o.you;
  const cls = ['pw-team'];
  if (you) cls.push('pw-team--you');
  else if (o.side === 'red') cls.push('pw-team--red-soft');
  else if (o.side === 'blue') cls.push('pw-team--blue-soft');
  if (o.focus && !you) cls.push('pw-team--focus');
  const q = {};
  if (o.season) q.season = o.season;
  if (o.model) q.model = o.model;
  return html`<a class="${cls.join(' ')}" href="${link('team', [num], q)}">${o.label || num}</a>`;
}

export function watchButton(num, watching, o = {}) {
  const cls = o.btn ? 'pw-btn' : 'pw-watch';
  return html`<button type="button" class="${cls}" data-act="watch" data-team="${num}" aria-pressed="${watching ? 'true' : 'false'}" aria-label="${watching ? 'Watching' : 'Watch'} team ${num}">${watching ? 'Watching' : 'Watch'}</button>`;
}

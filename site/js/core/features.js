// Model features shown across views (SPEC 4.6), one place for their rules and wording.
//
//   CHANGE_Z                       |z| above which a team-event is marked (3). The shards' own
//                                  change_flag used 2 when they were built, so views never read
//                                  change_flag: they recompute the mark from change_z.
//   setThreshold(models)           use models.json features.change_z_threshold when it is published
//   changeDir(z) -> 1 | -1 | 0 | null
//   changeCell(z, {k, event, first}) -> SafeHTML  "▲ +3.4 SD" in delta colours (never red/blue)
//   changeCaption({first})         the caption sentence that defines the mark
//   consText(sd, n) -> "±12.3 · n = 9" | "– · n = 3"   (null SD when n < 4; n always printed)
//   oppEffect(row, seasonMean)     {adj, se, n, show} with the season mean subtracted
//   seasonMean(values)             mean of the non-null values
import { html } from './html.js';
import { rating, delta } from './fmt.js';

export let CHANGE_Z = 3;
export const CONS_MIN_N = 4;

export function setThreshold(models) {
  const t = models && models.features && models.features.change_z_threshold;
  if (typeof t === 'number' && t > 0) CHANGE_Z = t;
}

const num = (x) => typeof x === 'number' && Number.isFinite(x);

export function changeDir(z) {
  if (!num(z)) return null;
  if (z > CHANGE_Z) return 1;
  if (z < -CHANGE_Z) return -1;
  return 0;
}

/** z in SD of match noise; k = matches used; event = where; first = first event of the team's season. */
export function changeCell(z, o = {}) {
  const d = changeDir(z);
  if (!d) return '';
  const where = o.event ? ` at ${o.event}` : '';
  const kTxt = num(o.k) ? `, first ${o.k} matches` : '';
  const vs = o.first ? 'its preseason prior' : 'its pre-event rating';
  return html`<span class="pw-delta pw-delta--${d > 0 ? 'up' : 'down'}" title="First matches${where} differ from ${vs} by ${delta(z)} SD of match noise${kTxt}">${d > 0 ? '▲' : '▼'} ${delta(z)} SD${num(o.k) ? html` <span class="pw-small">k ${o.k}</span>` : ''}<span class="pw-sr"> ${d > 0 ? 'above' : 'below'} ${vs}${where}</span></span>`;
}

export function changeCaption() {
  return `▲▼ SD: a team's first 2 to 3 matches at an event (k) differ from its pre-event rating by more than ${CHANGE_Z} SD of match noise (z shown). At a team's first event of the season the comparison is against its preseason prior, which is itself an estimate. A mark to check, not a verdict.`;
}

export function consText(sd, n) {
  const nn = num(n) ? n : 0;
  const v = num(sd) && nn >= CONS_MIN_N ? `±${rating(sd)}` : '–';
  return `${v} · n = ${nn}`;
}

export function seasonMean(values) {
  let s = 0; let c = 0;
  for (const v of values) if (num(v)) { s += v; c++; }
  return c ? s / c : null;
}

/** Opponent effect relative to the season mean. show = |adj| > 2 SE and n >= 12 (null otherwise). */
export function oppEffect(row, mean) {
  const e = row.opp_effect; const se = row.opp_effect_se; const n = row.opp_n;
  if (!num(e) || !num(se) || !num(n) || n < 12) return null;
  const adj = e - (num(mean) ? mean : 0);
  return { adj, raw: e, se, n, show: Math.abs(adj) > 2 * se };
}

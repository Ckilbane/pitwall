// Match card (SPEC 5.5): red on top, blue below, win-probability bar, and a prediction line that
// names the model.
//   matchCard({ev, i, model, modelLabel, watch: Set, focusTeam, season, subject})
//     ev = event file, i = row index in ev.matches. Returns SafeHTML.
//   matchFacts(ev, i, model) -> {key, label, red, blue, redScore, blueScore, winner, wp, predRed, predBlue, played, called, tossup, lean}
import { html } from '../core/html.js';
import { link } from '../core/router.js';
import { tbl } from '../core/data.js';
import { matchLabel, pct } from '../core/fmt.js';
import { teamChip } from './team.js';
import { wpBar } from './wp.js';

export function matchFacts(ev, i, model) {
  const m = tbl(ev.matches);
  const g = (name) => m.get(i, name, model);
  const winner = g('winner');
  const wp = g('epa_win_prob');
  const played = winner !== null && winner !== undefined;
  const predWinner = typeof wp === 'number' ? (wp > 0.5 ? 'red' : wp < 0.5 ? 'blue' : null) : null;
  const tossup = typeof wp === 'number' && Math.abs(wp - 0.5) < 0.03;
  let called = null;
  if (played && winner !== 'tie' && predWinner) called = predWinner === winner;
  return {
    key: g('key'),
    label: matchLabel(g('comp_level'), g('set_number'), g('match_number')),
    red: g('red') || [], blue: g('blue') || [],
    redScore: g('red_score'), blueScore: g('blue_score'),
    winner, wp, played, called, tossup, lean: predWinner,
    predRed: g('epa_red_score_pred'), predBlue: g('epa_blue_score_pred'),
  };
}

export function predictionLine(f, modelLabel) {
  if (typeof f.predRed !== 'number') return `${modelLabel} made no prediction`;
  let s = `${modelLabel} prediction ${Math.round(f.predRed)}–${Math.round(f.predBlue)}`;
  if (f.tossup && f.lean) s += ` · toss-up, leaned ${f.lean}`;
  if (f.played) {
    if (f.winner === 'tie') s += ' · tie, not scored as a call';
    else if (f.called !== null) s += f.called ? ' · called correctly' : ' · missed';
  }
  return s;
}

export function matchCard(o) {
  const { ev, i } = o;
  const f = matchFacts(ev, i, o.model);
  const watch = o.watch || new Set();
  const chips = (teams, side) => teams.map((t) => teamChip(t, {
    you: watch.has(t), side, focus: t === o.focusTeam && !watch.has(t), season: o.season, model: o.model,
  }));
  let result = '';
  if (o.focusTeam !== undefined && f.played) {
    const onRed = f.red.includes(o.focusTeam);
    result = f.winner === 'tie' ? 'tie' : (f.winner === (onRed ? 'red' : 'blue') ? 'win' : 'loss');
  } else if (!f.played) result = 'not played';
  const where = o.eventLabel ? `${o.eventLabel}${result ? ' · ' : ''}${result}` : result;
  const wpR = typeof f.wp === 'number' ? f.wp : null;
  const sc = (v) => (typeof v === 'number' ? v : '–');
  return html`<article class="pw-match">
<div class="pw-match__head"><a class="pw-match__key" href="${link('match', [f.key], { model: o.model })}">${f.label}</a><span class="pw-small">${where}</span></div>
<div class="pw-match__side pw-match__side--red${f.winner === 'red' ? ' pw-match__side--win' : ''}"><span class="pw-match__bar"></span><div class="pw-match__teams">${chips(f.red, 'red')}</div><span class="pw-match__score"><span class="pw-sr">Red </span>${sc(f.redScore)}</span></div>
<div class="pw-match__side pw-match__side--blue${f.winner === 'blue' ? ' pw-match__side--win' : ''}"><span class="pw-match__bar"></span><div class="pw-match__teams">${chips(f.blue, 'blue')}</div><span class="pw-match__score"><span class="pw-sr">Blue </span>${sc(f.blueScore)}</span></div>
<div class="pw-match__foot">
${wpBar(wpR)}
<span class="pw-small">${predictionLine(f, o.modelLabel || 'EPA')}</span>
</div>
</article>`;
}

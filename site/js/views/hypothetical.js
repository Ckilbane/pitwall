// Hypothetical (WP5, SPEC 5.6): pick 3 v 3 and see the model's prediction from js/model/predict.js
// with the season's constants: predicted score, win probability on the diverging scale (printed),
// component prediction and RP odds.
// Route: #/hypothetical?season=&model=&red=a,b,c&blue=d,e,f&ratings=end|event:{key}&match={key}
//   ratings=event:{key}  each team's vector entering that event (team_events pre_*)
//   match={key}          (with ratings=event:{key}) each team's vector entering that match, derived by
//                        the SPEC 3.5.4 rule, which reproduces the recorded prediction
import { html, mount } from '../core/html.js';
import * as state from '../core/state.js';
import * as watchlist from '../core/watchlist.js';
import { getSeason, getEvent, tbl } from '../core/data.js';
import { rating, vectorLabel, unit, eventShort, matchLabel, dateTime } from '../core/fmt.js';
import { predictAlliances } from '../model/predict.js';
import { pageHead, note } from '../components/page.js';
import { figure } from '../components/figure.js';
import { dataTable } from '../components/table.js';
import { teamChip } from '../components/team.js';
import { wpChip, wpBar, wpKey, pctP } from '../components/wp.js';
import { preMatchVectors } from './event_lib.js';

export const parseList = (s) => String(s || '').split(',').map((x) => parseInt(x, 10)).filter((n) => Number.isInteger(n) && n > 0).slice(0, 3);
const slots = (list) => { const a = list.slice(0, 3); while (a.length < 3) a.push(null); return a; };

/**
 * Rating vectors for the six teams (exported for tests).
 * source: 'end' (end of season data), 'event' (pre-event), 'match' (entering the match).
 * Returns {red, blue, fallback: [teams that fell back to end-of-data], elim, source}.
 */
export function hypVectors(S, model, red, blue, ev, matchKey) {
  const T = tbl(S.teams);
  const idx = T.index('team');
  const vk = S.keys.vector;
  const endVec = (t) => { const r = T.rows(model)[idx.get(t)]; return vk.map((k) => r[k]); };
  const fallback = [];
  if (ev && matchKey) {
    const mi = tbl(ev.matches).index('key').get(matchKey);
    if (mi !== undefined) {
      const pm = preMatchVectors(ev, model, mi, vk);
      const pick = (side, teams) => teams.map((t) => { const e = pm[side].find((x) => x.team === t); return e ? e.vec : null; });
      const r = pick('red', red); const b = pick('blue', blue);
      if (r.every(Boolean) && b.every(Boolean)) return { red: r, blue: b, fallback, elim: !!ev.matches.cols.elim[mi], source: 'match', mi };
    }
  }
  if (ev) {
    const TE = tbl(ev.team_events);
    const ti = TE.index('team');
    const pre = (t) => {
      const i = ti.get(t);
      if (i === undefined) { fallback.push(t); return endVec(t); }
      return vk.map((k) => TE.get(i, `pre_${k}`, model));
    };
    return { red: red.map(pre), blue: blue.map(pre), fallback, elim: false, source: 'event' };
  }
  return { red: red.map(endVec), blue: blue.map(endVec), fallback, elim: false, source: 'end' };
}

function teamInput(side, n, val, name) {
  const id = `hyp-${side}-${n}`;
  return html`<label class="pw-hyp__slot" for="${id}"><span class="pw-eyebrow">${side === 'red' ? 'Red' : 'Blue'} ${n + 1}</span>
<input id="${id}" class="pw-input pw-input--team" inputmode="numeric" autocomplete="off" data-side="${side}" data-n="${n}" value="${val || ''}">
<span class="pw-small pw-hyp__name">${name}</span></label>`;
}

export async function render(ctx) {
  const { root, route } = ctx;
  const st = state.get();
  const { season, model } = st;
  const q = route.query;
  ctx.loading(`Loading ${season} teams`);
  const S = await getSeason(season);
  let ev = null; let evErr = '';
  const em = /^event:([0-9]{4}[a-z0-9]+)$/.exec(q.ratings || '');
  if (em) { try { ev = await getEvent(season, em[1]); } catch (e) { evErr = `Could not load event ${em[1]}: ${e.message}.`; } }
  if (!ctx.isCurrent()) return undefined;
  const T = tbl(S.teams);
  const idx = T.index('team');
  const modelName = (st.manifest.models.find((m) => m.id === model) || { label: 'EPA' }).label;
  const red = slots(parseList(q.red)); const blue = slots(parseList(q.blue));
  const all = red.concat(blue);
  const missing = all.filter((t) => t && !idx.has(t));
  const full = all.every((t) => t && idx.has(t));
  const nameOf = (t) => (t && idx.has(t) ? T.get(idx.get(t), 'name') || '' : t ? 'no rating this season' : '');
  let res = null; let vecs = null;
  if (full) {
    vecs = hypVectors(S, model, red, blue, ev, q.match);
    res = predictAlliances(S, vecs.red, vecs.blue, { model, elim: vecs.elim });
  }
  const p = res ? res.winProbRed : null;
  let check = '';
  if (res && ev && q.match) {
    const M = tbl(ev.matches); const mi = M.index('key').get(q.match);
    const rec = mi === undefined ? null : M.get(mi, 'epa_win_prob', model);
    if (typeof rec === 'number') check = `Recorded ${modelName} prediction for ${matchLabel(M.get(mi, 'comp_level'), M.get(mi, 'set_number'), M.get(mi, 'match_number'))}: red ${rec.toFixed(3)}; recomputed here: red ${p.toFixed(3)}.`;
  }
  const scope = !vecs ? '' : vecs.source === 'match' ? `ratings entering ${q.match}` : vecs.source === 'event' ? `ratings entering ${eventShort(ev.meta)}` : `ratings at the end of the ${season} data`;
  const lede = res
    ? `${p >= 0.5 ? 'Red' : 'Blue'} is the ${modelName} favourite at ${pctP(p >= 0.5 ? p : 1 - p)}, predicted ${Math.round(res.red.score)}–${Math.round(res.blue.score)} (red–blue) on ${scope}. This is a prediction, not a result.`
    : 'Pick three teams for each alliance to see a predicted score, win probability and ranking-point odds.';

  const E = tbl(S.events);
  const evOpts = [];
  for (let i = 0; i < E.n; i++) if (E.get(i, 'file_hash')) evOpts.push({ key: E.get(i, 'key'), name: `${eventShort(E.rows()[i])} (${E.get(i, 'key')})` });
  evOpts.sort((a, b) => a.name.localeCompare(b.name));
  const sel = q.ratings || 'end';
  const vk = S.keys.vector;

  const allianceTable = (side, teams, v) => dataTable({
    label: `${side === 'red' ? 'Red' : 'Blue'} alliance ratings`,
    columns: [
      { id: 't', label: side === 'red' ? 'Red' : 'Blue', td: 'team', render: (r) => teamChip(r.t, { side, season }) },
      { id: 'e', label: `${modelName}`, n: true, render: (r) => rating(r.v[vk.indexOf('epa')]) },
      { id: 'a', label: 'Auto', n: true, render: (r) => rating(r.v[vk.indexOf('auto_epa')]) },
      { id: 'tp', label: 'Teleop', n: true, render: (r) => rating(r.v[vk.indexOf('teleop_epa')]) },
      { id: 'g', label: 'Endgame', n: true, render: (r) => rating(r.v[vk.indexOf('endgame_epa')]) },
    ],
    rows: teams.map((t, i) => ({ t, v: v[i] })),
  });

  let results = '';
  if (res) {
    const comp = vk.filter((k) => !/^rp_\d_epa$/.test(k) && k !== 'tiebreaker_epa').map((k) => ({ k, label: vectorLabel(k, S.keys), unit: unit(k, S.keys), r: res.red.breakdown[k], b: res.blue.breakdown[k] }));
    const rpk = vk.filter((k) => /^rp_\d_epa$/.test(k));
    const rp = rpk.map((k, i) => ({ label: vectorLabel(k, S.keys), r: res.red.rp[i], b: res.blue.rp[i] }));
    results = html`
${figure({
    id: 'hyp-wp',
    title: `${p >= 0.5 ? 'Red' : 'Blue'} wins ${pctP(p >= 0.5 ? p : 1 - p)} of the time on these ratings`,
    sub: `${modelName} prediction on ${scope}. A prediction, not a result.`,
    body: html`<div class="pw-hyp__wp">${wpBar(p)}<p class="pw-small">${wpChip(p)} Predicted score: red ${res.red.score.toFixed(1)}, blue ${res.blue.score.toFixed(1)} points, including the season's average foul points.</p>${wpKey()}</div>`,
    caption: html`<b>Bar:</b> red's chance on the left, blue's on the right. <b>Chip:</b> the favourite on the diverging scale (red favoured to blue favoured); the value is always printed. <b>Model:</b> ${modelName}, win probability = 1 / (1 + 10^(k × (red − blue) / SD)) with k = ${S.predict.models[model].k} and score SD ${S.predict.score_sd} points. Source: The Blue Alliance, fetched ${dateTime(S.generated_at)}.`,
  })}
<div class="pw-two">${allianceTable('red', red, vecs.red)}${allianceTable('blue', blue, vecs.blue)}</div>
${figure({
    id: 'hyp-comp', cls: 'pw-figure--flush',
    title: `Predicted alliance scores before fouls: red ${res.red.scoreNoFouls.toFixed(1)}, blue ${res.blue.scoreNoFouls.toFixed(1)}`,
    sub: 'Sum of the three teams’ ratings for each component (count rows are game pieces per match).',
    body: dataTable({ label: 'Predicted breakdown by component', columns: [
      { id: 'label', label: 'Component', render: (r) => r.label }, { id: 'unit', label: 'Unit', render: (r) => r.unit },
      { id: 'r', label: 'Red', n: true, render: (r) => rating(r.r) }, { id: 'b', label: 'Blue', n: true, render: (r) => rating(r.b) }], rows: comp }),
    caption: html`Source: ${modelName} component ratings of the six teams, summed per alliance. Fouls are added only to the total score above.`,
  })}
${rp.length ? figure({
    id: 'hyp-rp', cls: 'pw-figure--flush',
    title: 'Bonus ranking-point odds if this were a qualification match',
    sub: 'Chance that each alliance earns each bonus RP (a prediction). Win and tie RPs are not included.',
    body: dataTable({ label: 'Ranking point probabilities', columns: [
      { id: 'label', label: 'Ranking point', render: (r) => r.label }, { id: 'r', label: 'Red', n: true, render: (r) => pctP(r.r) }, { id: 'b', label: 'Blue', n: true, render: (r) => pctP(r.b) }], rows: rp }),
    caption: 'Source: sigmoid of the summed RP ratings, as in the Statbotics model.',
  }) : ''}`;
  }

  const notices = [evErr, missing.length ? `No ${season} rating for ${missing.join(', ')}.` : '', vecs && vecs.fallback.length ? `${vecs.fallback.join(', ')} did not play ${em ? em[1] : ''}; their end-of-data rating is used.` : '', check].filter(Boolean).join(' ');
  mount(root, html`
${pageHead({ eyebrow: `Hypothetical · ${season} season · ${modelName} prediction`, title: 'Hypothetical match', lede })}
<form class="pw-hyp" data-act="hyp-form" aria-label="Alliances">
<fieldset class="pw-hyp__side pw-hyp__side--red"><legend>Red alliance</legend>${red.map((t, n) => teamInput('red', n, t, nameOf(t)))}</fieldset>
<fieldset class="pw-hyp__side pw-hyp__side--blue"><legend>Blue alliance</legend>${blue.map((t, n) => teamInput('blue', n, t, nameOf(t)))}</fieldset>
</form>
<section class="pw-filters" aria-label="Ratings and match loader">
<div class="pw-filters__group">
<label class="pw-field pw-field--wide" for="hyp-ratings"><span class="pw-eyebrow">Ratings</span><select id="hyp-ratings" class="pw-select">
<option value="end"${sel === 'end' ? html` selected` : ''}>End of ${season} data</option>
${evOpts.map((o) => html`<option value="event:${o.key}"${sel === `event:${o.key}` ? html` selected` : ''}>Entering ${o.name}</option>`)}</select></label>
<form class="pw-field" data-act="hyp-load"><label class="pw-eyebrow" for="hyp-match">Load match</label><input id="hyp-match" class="pw-input" autocomplete="off" placeholder="${season}paca_qm12" value="${q.match || ''}" size="16"><button class="pw-btn" type="submit">Load</button></form>
</div>
${watchlist.get().length ? html`<button type="button" class="pw-btn" data-act="hyp-watch">Watchlist on red</button>` : ''}
</section>
${note(notices, { live: true })}
${results}`);
  ctx.setTitle(`Hypothetical · ${season}`);

  const onChange = (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.side) {
      const side = t.dataset.side; const list = slots(parseList(q[side]));
      const n = parseInt(t.value, 10);
      list[Number(t.dataset.n)] = Number.isInteger(n) && n > 0 ? n : null;
      state.setQuery({ [side]: list.filter(Boolean).join(',') || null, match: null }, { replace: true });
    } else if (t.id === 'hyp-ratings') state.setQuery({ ratings: t.value === 'end' ? null : t.value, match: null }, { replace: true });
  };
  const onSubmit = async (e) => {
    const f = e.target.closest('form');
    if (!f || !root.contains(f)) return;
    e.preventDefault();
    if (f.dataset.act !== 'hyp-load') return;
    const key = f.querySelector('input').value.trim();
    const m = /^(\d{4}[a-z0-9]+)_([a-z0-9]+)$/.exec(key);
    const say = (t) => { const n = root.querySelector('.pw-note-line'); if (n) n.textContent = t; };
    if (!m) { say('Enter a match key like 2025paca_qm12.'); return; }
    try {
      const evm = await getEvent(Number(key.slice(0, 4)), m[1]);
      const i = tbl(evm.matches).index('key').get(key);
      if (i === undefined) { say(`No match ${key} in that event.`); return; }
      const c = evm.matches.cols;
      state.setQuery({ season: key.slice(0, 4), red: c.red[i].slice(0, 3).join(','), blue: c.blue[i].slice(0, 3).join(','), ratings: `event:${m[1]}`, match: key });
    } catch (err) { say(`Could not load ${m[1]}: ${err.message}`); }
  };
  const onClick = (e) => {
    const t = e.target.closest('[data-act="hyp-watch"]');
    if (t && root.contains(t)) state.setQuery({ red: watchlist.get().slice(0, 3).join(','), match: null }, { replace: true });
  };
  root.addEventListener('change', onChange);
  root.addEventListener('submit', onSubmit);
  root.addEventListener('click', onClick);
  return () => { root.removeEventListener('change', onChange); root.removeEventListener('submit', onSubmit); root.removeEventListener('click', onClick); };
}

export default render;

// Match tab (WP4b): one match, predicted against actual for both models, component by component,
// with robot-attributed points marked apart from the alliance points the model splits.
// Route: #/match/{eventKey_matchKey}   e.g. #/match/2025gal_qm94  (red first, blue second)
import { getSeason, getEvent } from '../core/data.js';
import * as state from '../core/state.js';
import { mount } from '../core/html.js';
import { wpScale } from '../components/wp.js';
import {
  html, fmt, router, watchlist, list, scrollBox, modelLabel, andList, pageHead, teamChip, verdict, label, deltaCell,
  fieldLabel, pctP, DASH,
} from './event_ui.js';
import {
  col, parseMatchKey, context, isPlayed, componentRows, robotSplit, preMatchVectors, favoured, called,
  levelName,
} from './event_lib.js';

export function title(route) {
  const k = route && route.path && route.path[0];
  return k ? `Match ${k}` : 'Match';
}

function ledeText({ ev, mi, mm, models, season }) {
  const c = ev.matches.cols;
  const played = isPlayed(ev, mi);
  const winner = c.winner[mi];
  const rs = c.red_score[mi], bs = c.blue_score[mi];
  const parts = [];
  if (played) {
    if (winner === 'red') parts.push(`Red won ${rs}–${bs}.`);
    else if (winner === 'blue') parts.push(`Blue won ${bs}–${rs}.`);
    else parts.push(`Red and blue tied ${rs}–${bs}.`);
  } else parts.push('This match has not been played yet.');
  const calls = models.map((md) => {
    const p = col(ev.matches, 'epa_win_prob', md)[mi];
    const f = favoured(p);
    const name = modelLabel(mm, md);
    if (p == null) return `${name} has no prediction`;
    const ps = col(ev.matches, 'epa_red_score_pred', md)[mi], pb = col(ev.matches, 'epa_blue_score_pred', md)[mi];
    const score = ` (predicted ${fmt.rating(ps, 0)}–${fmt.rating(pb, 0)})`;
    if (!f.side) return `${name} had it even${score}`;
    const base = `${name} favoured ${f.side} at ${pctP(f.lead)}${score}`;
    if (!played) return base;
    const cl = called(p, winner);
    return cl === null ? base : `${base} and ${cl ? 'called it' : 'missed'}`;
  });
  parts.push(`${calls.join('; ')}.`);
  const wl = new Set(watchlist.get());
  const mine = [...c.red[mi].map((t) => [t, 'red']), ...c.blue[mi].map((t) => [t, 'blue'])].filter(([t]) => wl.has(t));
  if (mine.length) parts.push(`${andList(mine.map(([t, s]) => `${t} played on ${s}`))}.`);
  return parts.join(' ');
}

function cardSide(ev, mi, side, season) {
  const c = ev.matches.cols;
  const played = isPlayed(ev, mi);
  const win = c.winner[mi] === side;
  const score = played ? c[side + '_score'][mi] : DASH;
  const dq = new Set(c[side + '_dq'][mi] || []);
  const sur = new Set(c[side + '_surrogate'][mi] || []);
  return html`<div class="pw-match__side pw-match__side--${side}${win ? ' pw-match__side--win' : ''}">
    <span class="pw-match__bar"></span>
    <div class="pw-match__teams"><span class="pwe-sidename">${side === 'red' ? 'Red' : 'Blue'}</span>
      ${list(c[side][mi].map((t) => html`${teamChip(t, { side, season })}${dq.has(t) ? html`<span class="pw-small">DQ</span>` : ''}${sur.has(t) ? html`<span class="pw-small">surrogate</span>` : ''}`))}</div>
    <span class="pw-match__score">${score}</span></div>`;
}

function predRow(r, { models, lead }) {
  const fmtVal = (x) => (r.kind === 'rp' ? fmt.pct(x) : fmt.rating(x, r.kind === 'count' ? 2 : 1));
  const act = r.kind === 'rp' ? (r.actual == null ? DASH : r.actual ? 'Yes' : 'No') : r.actual == null ? DASH : r.actual;
  const miss = r.kind !== 'rp' && r.actual != null && r.pred[lead] != null ? r.actual - r.pred[lead] : null;
  const labelText = r.nameKey ? fmt.compLabel(r.nameKey) : r.label;
  const suffix = r.kind === 'count' ? ' (count)' : r.kind === 'rp' ? ' earned' : '';
  return html`<tr class="${r.strong ? 'pwe-strong' : ''}${r.start ? ' pwe-gstart' : ''}">
    <th scope="row">${labelText}${suffix}</th>
    ${list(models.map((md) => html`<td class="n ${md === lead ? 'pwe-lead' : ''}">${r.pred[md] == null ? DASH : fmtVal(r.pred[md])}</td>`))}
    <td class="n pwe-actual">${r.kind === 'rp' ? act : typeof act === 'number' ? act : act}</td>
    <td class="n">${miss == null ? DASH : fmt.delta(miss)}</td></tr>`;
}

function componentTable(season, ev, mi, side, { models, mm, lead }) {
  const rows = componentRows(season, ev, mi, side, models);
  let prev = null;
  for (const r of rows) { r.start = r.group !== prev && r.group !== undefined && prev !== null; prev = r.group; }
  const c = ev.matches.cols;
  const name = side === 'red' ? 'Red' : 'Blue';
  return html`<div class="pwe-alliance pwe-alliance--${side}">
    <h3 class="pwe-allhead"><span class="pwe-allbar pwe-allbar--${side}"></span>${name} alliance
      <span class="pwe-allteams">${list(c[side][mi].map((t) => teamChip(t, { side, season: season.season })))}</span></h3>
    ${scrollBox(html`<table class="pw-table pwe-table pwe-comp">
      <caption class="pwe-sr">${name} alliance: predicted against actual, by component</caption>
      <thead><tr><th scope="col" rowspan="2">Component</th><th scope="colgroup" colspan="${models.length}" class="pwe-grouphead">Predicted</th>
        <th scope="col" rowspan="2" class="n">Actual</th><th scope="col" rowspan="2" class="n">Miss</th></tr>
        <tr>${list(models.map((md) => html`<th scope="col" class="n ${md === lead ? 'pwe-lead' : ''}">${modelLabel(mm, md)}</th>`))}</tr></thead>
      <tbody>${list(rows.map((r) => predRow(r, { models, lead })))}</tbody></table>`, `${name} alliance components`)}
  </div>`;
}

function robotSection(season, ev, mi) {
  const red = robotSplit(ev, mi, 'red'), blue = robotSplit(ev, mi, 'blue');
  const c = ev.matches.cols;
  if (!isPlayed(ev, mi)) return '';
  if (!red && !blue) {
    return html`<section class="pw-section" aria-labelledby="m-robot-h"><h2 class="pw-h2" id="m-robot-h">Who scored</h2>
      <p class="pw-muted">${season.season} has no per-robot fields TBA reports for this match, so every point is an alliance point the model splits among the three teams.</p></section>`;
  }
  const maxScore = Math.max(red ? red.noFoul : 0, blue ? blue.noFoul : 0, 1);
  const bar = (sp, side) => {
    if (!sp) return '';
    const w = (sp.noFoul / maxScore) * 100;
    const teams = c[side][mi];
    return html`<div class="pwe-split__row">
      <span class="pwe-split__name">${side === 'red' ? 'Red' : 'Blue'}</span>
      <div class="pwe-split__lane"><div class="pwe-split__box"><div class="pwe-split__track" style="width:${w.toFixed(1)}%" role="img"
        aria-label="${side} alliance ${sp.noFoul} points without fouls: ${sp.sum} robot-attributed and ${sp.remainder} alliance points split by the model">
        ${list(sp.scored.map((x, i) => (x ? html`<span class="pwe-seg pwe-seg--${side} pwe-seg--robot" style="flex:${x}" title="${teams[i]}: ${x}"></span>` : '')))}
        ${sp.remainder > 0 ? html`<span class="pwe-seg pwe-seg--${side} pwe-seg--shared" style="flex:${sp.remainder}"></span>` : ''}
      </div></div><span class="pwe-split__num">${sp.sum} + ${sp.remainder} = ${sp.noFoul}</span></div></div>`;
  };
  const table = (sp, side) => {
    if (!sp) return '';
    const teams = c[side][mi];
    const names = season.keys.names || {};
    const rows = sp.scored.map((x, i) => {
      const facts = sp.facts[i].filter((f) => f.value != null).map((f) => `${fieldLabel(f.key)}: ${f.value}`).join(' · ');
      const pts = sp.slotPoints[i];
      const parts = [];
      if (pts.auto) parts.push(`auto ${pts.auto}`);
      if (pts.endgame) parts.push(`endgame ${pts.endgame}`);
      for (const [k, v] of Object.entries(pts)) {
        if (k === 'auto' || k === 'endgame' || v === pts.auto || v === pts.endgame) continue;
        parts.push(`${fmt.compLabel(names[k] || k).toLowerCase()} ${v}`);
      }
      return html`<tr><td class="team">${teamChip(teams[i], { side, season: season.season })}</td>
        <td>${facts || DASH}</td><td class="n pwe-robot">${x == null ? DASH : x}</td><td>${parts.join(' · ') || DASH}</td></tr>`;
    });
    return html`<div class="pwe-alliance pwe-alliance--${side}">
      <h3 class="pwe-allhead"><span class="pwe-allbar pwe-allbar--${side}"></span>${side === 'red' ? 'Red' : 'Blue'} alliance</h3>
      ${scrollBox(html`<table class="pw-table pwe-table pwe-robots">
        <caption class="pwe-sr">${side} alliance: points TBA attributes to each robot</caption>
        <thead><tr><th scope="col">Team</th><th scope="col">Robot fields from TBA</th><th scope="col" class="n">Robot-attributed points</th><th scope="col">Of which</th></tr></thead>
        <tbody>${list(rows)}</tbody>
        <tfoot><tr><th scope="row" colspan="2">Alliance points split by the model</th><td class="n pwe-shared">${sp.remainder}</td><td>Score without fouls ${sp.noFoul} minus ${sp.sum}</td></tr></tfoot></table>`, `${side} alliance robot points`)}
      ${sp.ok === false ? html`<p class="pw-small">The robot fields did not reproduce this alliance's reported totals exactly; treat the attribution as approximate.</p>` : ''}
    </div>`;
  };
  const rs = red ? `${red.sum} of red's ${red.noFoul}` : '';
  const bsx = blue ? `${blue.sum} of blue's ${blue.noFoul}` : '';
  return html`<section class="pw-section" aria-labelledby="m-robot-h">
    <figure class="pw-figure">
      <h2 class="pw-figure__title" id="m-robot-h">Robot fields account for ${andList([rs, bsx].filter(Boolean))} points; the model splits the rest</h2>
      <p class="pw-figure__sub">Score without fouls, split into points TBA attributes to one robot (solid) and alliance points no single robot owns (hatched). Same scale for both alliances.</p>
      <div class="pwe-split">${bar(red, 'red')}${bar(blue, 'blue')}</div>
      <div class="pwe-grid2">${table(red, 'red')}${table(blue, 'blue')}</div>
      <figcaption class="pw-caption"><b>Robot-attributed:</b> points from per-robot TBA fields such as auto line or endgame position, each point counted once.
        <b>Split by the model:</b> everything else the alliance scored (scoring done by game pieces, not by a robot's own status), which rating models can only share among the three teams.
        Fouls are not included. Source: The Blue Alliance score breakdown.</figcaption>
    </figure>
  </section>`;
}

function ratingsSection(season, ev, mi, { models, mm, lead }) {
  const c = ev.matches.cols;
  const ctx = context(ev);
  const tm = ev.team_matches;
  const rowsIdx = ctx.rowsByMatch.get(mi) || [];
  const vector = season.keys.vector;
  const pres = {};
  for (const md of models) pres[md] = preMatchVectors(ev, md, mi, vector);
  const played = isPlayed(ev, mi);
  const body = rowsIdx.map((r) => {
    const t = col(tm, 'team')[r];
    const side = col(tm, 'alliance')[r] === 0 ? 'red' : 'blue';
    const cells = models.map((md) => {
      const grp = pres[md][side].find((x) => x.team === t);
      const pre = grp ? grp.vec[0] : null;
      const post = col(tm, 'epa', md)[r];
      const attr = col(tm, 'attr_epa', md)[r];
      const ch = post != null && pre != null ? post - pre : null;
      return html`<td class="n ${md === lead ? 'pwe-lead' : ''}">${fmt.rating(pre)}</td>
        <td class="n ${md === lead ? 'pwe-lead' : ''}">${played ? fmt.rating(attr) : DASH}</td>
        <td class="n ${md === lead ? 'pwe-lead' : ''}">${played ? fmt.rating(post) : DASH}</td>
        <td class="n ${md === lead ? 'pwe-lead' : ''}">${played ? deltaCell(ch) : DASH}</td>`;
    });
    return html`<tr><td class="team pwe-stick">${teamChip(t, { side, season: season.season })}</td>
      <td class="pw-nowrap">${side === 'red' ? 'Red' : 'Blue'}</td>${list(cells)}</tr>`;
  });
  return html`<section class="pw-section" aria-labelledby="m-rate-h">
    <h2 class="pw-h2" id="m-rate-h">${played ? 'Ratings before and after' : 'Ratings going in'}</h2>
    ${scrollBox(html`<table class="pw-table pwe-table pwe-ratings">
      <caption class="pwe-sr">Each team's total rating before the match, the value the model attributed to it from this match, the rating after, and the change</caption>
      <thead>
        <tr><th scope="col" rowspan="2" class="pwe-stick">Team</th><th scope="col" rowspan="2">Side</th>
          ${list(models.map((md) => html`<th scope="colgroup" colspan="4" class="pwe-grouphead ${md === lead ? 'pwe-lead' : ''}">${modelLabel(mm, md)}</th>`))}</tr>
        <tr>${list(models.map(() => html`<th scope="col" class="n">Before</th><th scope="col" class="n">This match</th><th scope="col" class="n">After</th><th scope="col" class="n">Change</th>`))}</tr>
      </thead>
      <tbody>${list(body)}</tbody></table>`, 'Team ratings before and after the match')}
    <p class="pw-caption"><b>Before / After:</b> total rating in points per match. <b>This match:</b> the value the model credited to the team for this match (its target for the update),
      so the gap to Before is the surprise and the Change is how far the rating moved. Before is the rating entering the match; it never includes the match itself.</p>
  </section>`;
}

export async function render(ctx) {
  const { root, route } = ctx;
  const key = route.path[0];
  const parsed = parseMatchKey(key);
  if (!parsed) throw new Error(`"${key || ''}" is not a match key. Match keys look like 2025gal_qm94; open a match from an Event page.`);
  ctx.loading(`Loading ${parsed.event}`);
  const [season, ev] = await Promise.all([getSeason(parsed.season), getEvent(parsed.season, parsed.event)]);
  if (!ctx.isCurrent()) return undefined;
  const cx = context(ev);
  const mi = cx.keyIndex.get(key);
  if (mi === undefined) throw new Error(`Match ${key} is not in the ${parsed.event} data. It may not be scheduled yet.`);

  const st = state.get();
  const mm = (st.manifest && st.manifest.models) || [{ id: 'epa', label: 'EPA' }, { id: 'fast', label: 'Fast EPA' }];
  const models = mm.map((m) => m.id);
  const lead = models.includes(st.model) ? st.model : models[0];
  const c = ev.matches.cols;
  const played = isPlayed(ev, mi);
  const mlabel = label(ev, mi);
  const evName = ev.meta.name || parsed.event;
  const prev = mi > 0 ? mi - 1 : null, next = mi < ev.matches.n - 1 ? mi + 1 : null;
  const when = c.time[mi] || c.predicted_time[mi];

  const wp = models.map((md) => {
    const p = col(ev.matches, 'epa_win_prob', md)[mi];
    return wpScale(p, modelLabel(mm, md), played ? verdictText(verdict(p, c.winner[mi])) : '');
  });

  const page = html`
    <nav class="pwe-crumbs pw-crumbs pw-small" aria-label="Match navigation">
      <a href="${router.link('event', [parsed.event], { tab: 'matches' })}">All ${evName} matches</a>
      ${prev !== null ? html`<a href="${router.link('match', [c.key[prev]], {})}">${'←'} ${label(ev, prev)}</a>` : ''}
      ${next !== null ? html`<a href="${router.link('match', [c.key[next]], {})}">${label(ev, next)} ${'→'}</a>` : ''}
    </nav>
    ${pageHead({ eyebrow: `Match · ${parsed.season} · ${levelName(c.comp_level[mi])}${when ? ` · ${fmt.dateTime(when)}` : ''}`, title: `${mlabel} at ${evName}`, lede: ledeText({ ev, mi, mm, models, season }) })}
    <article class="pw-match pwe-card" aria-label="${mlabel}">
      <div class="pw-match__head"><span class="pw-match__key">${mlabel}</span>
        <span class="pw-small">${played ? 'Final' : 'Not played'}${c.video[mi] ? html` · <a href="https://www.youtube.com/watch?v=${c.video[mi]}" target="_blank" rel="noopener">Watch the match</a>` : ''}</span></div>
      ${cardSide(ev, mi, 'red', parsed.season)}
      ${cardSide(ev, mi, 'blue', parsed.season)}
      <div class="pw-match__foot pwe-wps"><span class="pw-eyebrow">Win probability, recorded before the match</span>${list(wp)}</div>
    </article>
    <section class="pw-section" aria-labelledby="m-comp-h">
      <h2 class="pw-h2" id="m-comp-h">Predicted against actual, by component</h2>
      <div class="pwe-grid2">${componentTable(season, ev, mi, 'red', { models, mm, lead })}${componentTable(season, ev, mi, 'blue', { models, mm, lead })}</div>
      <p class="pw-caption"><b>Predicted:</b> sum of the three teams' ratings entering the match for each model, in points per match (counts for count rows, chance for ranking-point rows).
        <b>Score with fouls</b> adds the season's average foul rate to the prediction. <b>Miss:</b> actual minus the lead model's prediction (${modelLabel(mm, lead)}); positive means the alliance did better than predicted.
        ${played ? '' : 'Actual values appear after the match is played. '}Both models are shown; the toggle in the header picks the lead model. Predictions are not outcomes.
        Source: The Blue Alliance; EPA from Statbotics (MIT).</p>
    </section>
    ${robotSection(season, ev, mi)}
    ${ratingsSection(season, ev, mi, { models, mm, lead })}`;
  mount(root, page);
  ctx.setTitle(`${mlabel} at ${evName} · ${parsed.season}`);
  return undefined;
}

function verdictText(v) {
  return v === 'called' ? 'Called' : v === 'missed' ? 'Missed' : '';
}

export default render;

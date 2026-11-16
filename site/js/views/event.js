// Event tab (WP4b): one event. Tabs: teams (ratings and change flags), matches (both models'
// predictions), rankings, alliances, accuracy. The as-of control replays the event from derived
// pre-match vectors; later matches then show forecasts recomputed from the ratings at that point.
// Route: #/event/{key}?tab=&asof=&show=   (model comes from the shell toggle)
import { getSeason, getEvent } from '../core/data.js';
import * as state from '../core/state.js';
import * as csv from '../core/csv.js';
import { mount } from '../core/html.js';
import { asofControl, asofOptions, readAsof } from '../components/asof.js';
import { changeCell, changeCaption } from '../core/features.js';
import { predictAlliances } from '../model/predict.js';
import {
  html, fmt, router, watchlist, list, scrollBox, modelLabel, andList, pageHead, teamChip, wpChip,
  verdict, deltaCell, label, DASH,
} from './event_ui.js';
import {
  col, seasonOfEvent, ratingsAsOf, standingsAsOf, accuracy, biggestMisses, asofIndex, lastPlayedLabel,
  isPlayed, typeLabel, weekLabel, dateRange, allianceResult,
} from './event_lib.js';

const TABS = [['teams', 'Teams'], ['matches', 'Matches'], ['rankings', 'Rankings'], ['alliances', 'Alliances'], ['accuracy', 'Accuracy']];
const SHOW = { all: 'All matches', qual: 'Qualification', playoff: 'Playoffs', watch: 'Watchlist teams' };
const PAGE = 100;

export function title(route) {
  const k = route && route.path && route.path[0];
  return k ? `Event ${k}` : 'Event';
}

// ---------- data builders (shared by the tables and the CSV export) ----------

/** One row per team at the event as of idx. */
export function teamsData(ev, model, idx) {
  const te = ev.team_events;
  const n = ev.matches.n;
  const rat = ratingsAsOf(ev, model, idx);
  const official = idx >= n && col(te, 'rank').some((r) => r != null);
  const stand = official ? null : new Map(standingsAsOf(ev, idx).map((s) => [s.team, s]));
  const teams = col(te, 'team');
  const rows = teams.map((t, i) => {
    const r = rat.get(t);
    const pre = col(te, 'pre_epa', model)[i];
    const s = stand ? stand.get(t) : null;
    const k = col(te, 'change_k', model)[i];
    const z = col(te, 'change_z', model)[i];
    const first = !!col(te, 'first_event')[i];
    return {
      team: t, name: col(te, 'team_name')[i],
      epa: r.v.epa, auto: r.v.auto_epa, teleop: r.v.teleop_epa, endgame: r.v.endgame_epa,
      pre, change: r.n > 0 && pre != null && r.v.epa != null ? r.v.epa - pre : null,
      n: r.n,
      z: z != null && k != null && r.n >= k ? z : null, k, first,
      rank: official ? col(te, 'rank')[i] : (s && s.n ? s.rank : null),
      w: official ? col(te, 'qual_wins')[i] : s && s.w, l: official ? col(te, 'qual_losses')[i] : s && s.l,
      tie: official ? col(te, 'qual_ties')[i] : s && s.t, qn: official ? col(te, 'qual_count')[i] : s && s.n,
      rp: official ? col(te, 'rps')[i] : s && s.rp, rpm: official ? col(te, 'rps_per_match')[i] : s && s.rpm,
      _i: i,
    };
  });
  const byEpa = rows.slice().sort((a, b) => (b.epa ?? -1e9) - (a.epa ?? -1e9) || a.team - b.team);
  byEpa.forEach((r, j) => { r.epaRank = j + 1; });
  return { rows: byEpa, official };
}

/** Matches as of idx, optionally filtered; each carries the recorded or recomputed predictions of every model. */
export function matchesData(ev, season, models, idx, show = 'all') {
  const m = ev.matches, c = m.cols;
  const n = m.n;
  const wl = new Set(watchlist.get());
  const vector = season.keys.vector;
  const rats = {};
  const out = [];
  for (let mi = 0; mi < n; mi++) {
    if (show === 'qual' && c.comp_level[mi] !== 'qm') continue;
    if (show === 'playoff' && c.comp_level[mi] === 'qm') continue;
    if (show === 'watch' && ![...c.red[mi], ...c.blue[mi]].some((t) => wl.has(t))) continue;
    const played = mi < idx && isPlayed(ev, mi);
    const preds = {};
    for (const md of models) {
      const forecast = idx < n && mi >= idx;
      let p = col(m, 'epa_win_prob', md)[mi], rs = col(m, 'epa_red_score_pred', md)[mi], bs = col(m, 'epa_blue_score_pred', md)[mi];
      if (forecast) {
        if (!rats[md]) rats[md] = ratingsAsOf(ev, md, idx, vector);
        try {
          const vec = (teams) => teams.slice(0, 3).map((t) => vector.map((k) => rats[md].get(t).v[k]));
          const r = predictAlliances(season, vec(c.red[mi]), vec(c.blue[mi]), { model: md, elim: !!c.elim[mi] });
          p = r.winProbRed; rs = r.red.score; bs = r.blue.score;
        } catch (e) { p = null; rs = null; bs = null; }
      }
      preds[md] = { p, rs, bs, forecast };
    }
    out.push({
      mi, key: c.key[mi], label: label(ev, mi), level: c.comp_level[mi], red: c.red[mi], blue: c.blue[mi],
      redScore: played ? c.red_score[mi] : null, blueScore: played ? c.blue_score[mi] : null,
      winner: played ? c.winner[mi] : null, played, preds,
    });
  }
  return out;
}

// ---------- lede ----------

function makeLede({ ev, model, models, td, idx, acc }) {
  const mname = modelLabel(models, model);
  const n = ev.matches.n;
  const short = fmt.eventShort(ev.meta);
  if (!td.rows.length) return `${short} has no team list yet. Ratings appear once TBA publishes the schedule or the first matches.`;
  const top = td.rows[0], second = td.rows[1];
  const parts = [];
  const gap = second ? `, ${fmt.rating(top.epa - second.epa)} ahead of ${second.team}` : '';
  const figs = `${fmt.rating(top.epa)} ${mname} points per match${gap}`;
  if (idx <= 0) parts.push(`${top.team} is the highest-rated team entering ${short} at ${figs}.`);
  else if (idx >= n && ev.meta.status === 'Completed') parts.push(`${top.team} finished ${short} as the highest-rated team at ${figs}.`);
  else parts.push(`${top.team} leads ${short} at ${figs}${idx >= n ? '' : ` (after ${lastPlayedLabel(ev, idx)})`}.`);
  const wl = watchlist.get();
  const mine = td.rows.filter((r) => wl.includes(r.team)).slice(0, 2);
  if (mine.length) {
    parts.push(`${andList(mine.map((r) => `${r.team} is ${fmt.rank(r.epaRank, td.rows.length)} at ${fmt.rating(r.epa)}${r.rank ? `, ranked ${fmt.ordinal(r.rank)} in quals` : ''}`))}.`);
  }
  if (acc && acc.all.decided >= 10) {
    parts.push(`${mname} called ${fmt.pct(acc.all.calledShare)} of ${acc.all.decided} decided matches${idx >= n ? '' : ' so far'}.`);
  } else if (idx > 0 && idx < 10) {
    parts.push('Fewer than 10 matches played, so ratings are mostly the pre-event numbers.');
  }
  return parts.join(' ');
}

// ---------- tab renderers ----------

function teamsTable(ev, td, { model, models, season, idx, n }) {
  const mname = modelLabel(models, model);
  const maxEpa = Math.max(1, ...td.rows.map((r) => r.epa ?? 0));
  const guess = td.rows.filter((r) => r.n > 0 && r.n < 6).length;
  const rows = td.rows.map((r) => {
    const you = watchlist.has(r.team);
    return html`<tr class="${you ? 'you' : ''}">
      <td class="n pw-sticky-a">${r.epaRank}</td>
      <td class="team pw-sticky-b">${teamChip(r.team, { season })}</td>
      <td class="pwe-name"><span>${r.name}</span></td>
      <td class="n"><span class="pw-cell-bar"><span class="pw-bar" style="width:${Math.max(1, Math.round(((r.epa ?? 0) / maxEpa) * 64))}px" aria-hidden="true"></span>${fmt.rating(r.epa)}</span></td>
      <td class="n">${fmt.rating(r.auto)}</td><td class="n">${fmt.rating(r.teleop)}</td><td class="n">${fmt.rating(r.endgame)}</td>
      <td class="n">${fmt.rating(r.pre)}</td>
      <td class="n">${deltaCell(r.change)}</td>
      <td class="pw-nowrap">${changeCell(r.z, { k: r.k, first: r.first })}</td>
      <td class="n">${r.n}${r.n > 0 && r.n < 6 ? html` <span class="pw-small">guess</span>` : ''}</td>
      <td class="n">${r.rank ?? DASH}</td>
      <td class="n">${r.qn ? fmt.record(r.w, r.l, r.tie) : DASH}</td>
    </tr>`;
  });
  return html`<section class="pw-section" aria-labelledby="ev-teams-h">
    <h2 class="pw-h2" id="ev-teams-h">${td.rows.length} teams by ${mname} rating</h2>
    ${scrollBox(html`<table class="pw-table pwe-table">
      <caption class="pwe-sr">Teams at the event ranked by ${mname} rating${idx <= 0 ? ' before the first match' : idx >= n ? ' at the end of the event' : ` after match ${idx}`}</caption>
      <thead><tr>
        <th scope="col" class="n pw-sticky-a">#</th><th scope="col" class="pw-sticky-b">Team</th><th scope="col">Name</th>
        <th scope="col" class="n">${mname}</th><th scope="col" class="n">Auto</th><th scope="col" class="n">Teleop</th><th scope="col" class="n">Endgame</th>
        <th scope="col" class="n">Pre-event</th><th scope="col" class="n">Change</th><th scope="col">Differs from pre-event</th>
        <th scope="col" class="n">Matches</th><th scope="col" class="n">Qual rank</th><th scope="col" class="n">Quals W-L-T</th>
      </tr></thead>
      <tbody>${list(rows)}</tbody></table>`, 'Teams at the event')}
    <p class="pw-caption"><b>${mname}:</b> total rating in points per match; auto, teleop and endgame are its parts. <b>Pre-event:</b> the rating entering the event;
      <b>Change:</b> rating now minus pre-event. <b>Differs from pre-event:</b> ${changeCaption()} <b>Matches:</b> matches that updated the rating so far${guess ? `; ${guess} ${guess === 1 ? 'team has' : 'teams have'} fewer than 6 and the rating is a guess` : ''}.
      ${idx > 0 && idx < n ? `Ratings are as of ${lastPlayedLabel(ev, idx)}.` : ''}
      Source: The Blue Alliance; ${mname} model.</p>
  </section>`;
}
function matchRow(r, { models, season, you }) {
  const win = (s) => (r.winner === s ? 'pwe-win' : '');
  const modelCells = models.map((md) => {
    const pr = r.preds[md];
    const v = r.played ? verdict(pr.p, r.winner) : '';
    return html`<td class="pwe-wpcell">
      <div>${wpChip(pr.p)}${v ? html` <span class="pw-small">${v}</span>` : ''}</div>
      <div class="pw-small pwe-predscore">${pr.rs != null ? `${fmt.rating(pr.rs, 0)}${'–'}${fmt.rating(pr.bs, 0)}` : DASH}${pr.forecast ? ' forecast' : ''}</div></td>`;
  });
  return html`<tr class="${you ? 'you' : ''}">
    <td class="pwe-stick team"><a href="${router.link('match', [r.key], {})}">${r.label}</a></td>
    <td><span class="pwe-chips">${list(r.red.map((t) => teamChip(t, { side: 'red', season })))}</span></td>
    <td class="n pwe-score pwe-score--red ${win('red')}">${r.played ? r.redScore : DASH}</td>
    <td class="n pwe-score pwe-score--blue ${win('blue')}">${r.played ? r.blueScore : DASH}</td>
    <td><span class="pwe-chips">${list(r.blue.map((t) => teamChip(t, { side: 'blue', season })))}</span></td>
    ${list(modelCells)}
  </tr>`;
}

function matchesTable(rows, { models, mm, season, idx, n, expanded, show }) {
  const wl = new Set(watchlist.get());
  const shown = expanded ? rows : rows.slice(0, PAGE);
  const body = shown.map((r) => matchRow(r, { models, season, you: [...r.red, ...r.blue].some((t) => wl.has(t)) }));
  const played = rows.filter((r) => r.played).length;
  const forecast = idx < n && rows.some((r) => !r.played);
  return html`<section class="pw-section" aria-labelledby="ev-matches-h">
    <h2 class="pw-h2" id="ev-matches-h">${rows.length} ${show === 'all' ? '' : `${SHOW[show].toLowerCase()} `}${rows.length === 1 ? 'match' : 'matches'}, ${played} played</h2>
    ${rows.length ? scrollBox(html`<table class="pw-table pwe-table pwe-matches">
      <caption class="pwe-sr">Matches with score and each model's win probability. Red alliance on the left, blue on the right.</caption>
      <thead><tr>
        <th scope="col" class="pwe-stick">Match</th><th scope="col">Red alliance</th><th scope="col" class="n">Red</th><th scope="col" class="n">Blue</th><th scope="col">Blue alliance</th>
        ${list(models.map((md) => html`<th scope="col">${modelLabel(mm, md)} prediction</th>`))}
      </tr></thead>
      <tbody>${list(body)}</tbody></table>`, 'Matches') : html`<p class="pw-muted">No matches to show.</p>`}
    ${rows.length > PAGE && !expanded ? html`<button type="button" class="pw-btn" data-act="more" id="ev-more">Show all ${rows.length} matches</button>` : ''}
    <p class="pw-caption"><b>Win probability:</b> the favoured alliance and its chance, on the red (left) to blue (right) scale; the small line is the predicted score.
      Each prediction was recorded before the match from ratings that did not include it. <b>Called</b> means the favoured alliance won.
      ${forecast ? '<b>Forecast</b> rows have not been played at this point; they are recomputed from the ratings as of now. ' : ''}Amber rows include a watchlist team.</p>
  </section>`;
}

function rankingsTable(td, { model, models, season, idx, n }) {
  const mname = modelLabel(models, model);
  const rows = td.rows.filter((r) => r.rank != null).sort((a, b) => a.rank - b.rank);
  const body = rows.map((r) => {
    const you = watchlist.has(r.team);
    const diff = r.epaRank - r.rank;
    return html`<tr class="${you ? 'you' : ''}">
      <td class="n pw-sticky-a">${r.rank}</td><td class="team pw-sticky-b">${teamChip(r.team, { season })}</td>
      <td class="pwe-name"><span>${r.name}</span></td>
      <td class="n">${r.rp ?? DASH}</td><td class="n">${r.rpm != null ? r.rpm.toFixed(2) : DASH}</td>
      <td class="n">${r.qn ? fmt.record(r.w, r.l, r.tie) : DASH}</td>
      <td class="n">${fmt.rating(r.epa)}</td><td class="n">${r.epaRank}</td>
      <td class="n">${deltaCell(diff, { nd: 0 })}</td></tr>`;
  });
  return html`<section class="pw-section" aria-labelledby="ev-rank-h">
    <h2 class="pw-h2" id="ev-rank-h">Qualification rankings</h2>
    ${rows.length ? scrollBox(html`<table class="pw-table pwe-table">
      <caption class="pwe-sr">Qualification rankings with ranking points and ${mname} rank</caption>
      <thead><tr><th scope="col" class="n pw-sticky-a">Rank</th><th scope="col" class="pw-sticky-b">Team</th><th scope="col">Name</th>
        <th scope="col" class="n">RP</th><th scope="col" class="n">RP per match</th><th scope="col" class="n">W-L-T</th>
        <th scope="col" class="n">${mname}</th><th scope="col" class="n">${mname} rank</th><th scope="col" class="n">Rank vs ${mname} rank</th></tr></thead>
      <tbody>${list(body)}</tbody></table>`, 'Qualification rankings') : html`<p class="pw-muted">No qualification matches have been played yet.</p>`}
    <p class="pw-caption"><b>RP:</b> ranking points. <b>Rank vs ${mname} rank:</b> ${mname} rank minus qualification rank, so ▲ means the team ranked higher than its rating alone suggests.
      ${td.official ? 'Ranks are the official end-of-qualification ranks from TBA.' : `Replayed from the matches so far and sorted by RP per match; the official tiebreakers are not applied, so close ranks can differ from the official list.`}</p>
  </section>`;
}

function alliancesTable(ev, td, { model, models, season }) {
  const al = ev.alliances;
  const mname = modelLabel(models, model);
  if (!al || !al.length) {
    return html`<section class="pw-section"><h2 class="pw-h2">Alliances</h2><p class="pw-muted">TBA has no alliance selection for this event yet. Qualification events without playoffs never will.</p></section>`;
  }
  const epa = new Map(td.rows.map((r) => [r.team, r.epa]));
  const body = al.map((a) => {
    const top3 = a.picks.slice(0, 3);
    const sum = top3.reduce((s, t) => s + (epa.get(t) ?? 0), 0);
    const rec = a.status && a.status.record ? fmt.record(a.status.record.wins, a.status.record.losses, a.status.record.ties) : DASH;
    return html`<tr>
      <th scope="row" class="pwe-alname">${a.name}</th>
      <td><span class="pwe-chips">${list(a.picks.map((t) => teamChip(t, { season })))}</span></td>
      <td class="n">${fmt.rating(sum)}</td>
      <td class="pw-nowrap">${allianceResult(a) || DASH}</td>
      <td class="n">${rec}</td></tr>`;
  });
  return html`<section class="pw-section" aria-labelledby="ev-al-h">
    <h2 class="pw-h2" id="ev-al-h">Alliances and playoff results</h2>
    ${scrollBox(html`<table class="pw-table pwe-table">
      <caption class="pwe-sr">Alliances in selection order with their playoff result</caption>
      <thead><tr><th scope="col">Alliance</th><th scope="col">Captain and picks</th><th scope="col" class="n">First 3 ${mname}</th><th scope="col">Result</th><th scope="col" class="n">Playoff W-L-T</th></tr></thead>
      <tbody>${list(body)}</tbody></table>`, 'Alliances')}
    <p class="pw-caption"><b>First 3 ${mname}:</b> sum of the three playing teams' ${mname} ratings (points per match) at this point in the replay; a backup or fourth team is listed but not counted.
      Source: The Blue Alliance alliance data. <a href="${router.link('event', [ev.event], { tab: 'matches', show: 'playoff', model })}">See the playoff matches</a>.</p>
  </section>`;
}

function accuracyView(ev, season, { models, model, idx, n }) {
  const mm = state.get().manifest.models;
  const accs = Object.fromEntries(models.map((md) => [md, accuracy(ev, md, idx)]));
  const lead = accs[model];
  const decided = lead.all.decided;
  const finding = decided
    ? models.map((md) => `${modelLabel(mm, md)} called ${fmt.pct(accs[md].all.calledShare)} of ${accs[md].all.decided}`).join('; ')
    : 'No decided matches yet';
  const seasonAcc = (md) => { const x = season.year && season.year.models && season.year.models[md]; return x && x.metrics ? x.metrics.epa_acc : null; };
  const bar = (v) => (v == null ? '' : html`<span class="pw-bar" style="width:${Math.round(v * 96)}px" aria-hidden="true"></span>`);
  const row = (md) => {
    const a = accs[md];
    const cell = (x) => (x.calledShare == null ? DASH : html`<span class="pw-cell-bar">${bar(x.calledShare)}${fmt.pct(x.calledShare)}</span> <span class="pw-small">n ${x.decided}</span>`);
    return html`<tr>
      <th scope="row">${modelLabel(mm, md)}</th>
      <td class="n">${cell(a.qual)}</td><td class="n">${cell(a.playoff)}</td><td class="n">${cell(a.all)}</td>
      <td class="n">${fmt.pct(seasonAcc(md))}</td>
      <td class="n">${a.all.brier == null ? DASH : a.all.brier.toFixed(3)}</td>
      <td class="n">${a.all.mae == null ? DASH : a.all.mae.toFixed(1)}</td>
      <td class="n">${a.all.bias == null ? DASH : fmt.delta(a.all.bias)}</td></tr>`;
  };
  const misses = biggestMisses(ev, model, idx, 6);
  const missRows = misses.map((x) => {
    const c = ev.matches.cols;
    const p = col(ev.matches, 'epa_win_prob', model)[x.mi];
    return html`<tr><td class="pwe-stick team"><a href="${router.link('match', [c.key[x.mi]], {})}">${label(ev, x.mi)}</a></td>
      <td>${x.winner === 'red' ? 'Red' : 'Blue'} won ${c.red_score[x.mi]}${'–'}${c.blue_score[x.mi]}</td>
      <td class="n">${fmt.pct(x.pWinner)}</td><td class="n">${wpChip(p)}</td></tr>`;
  });
  return html`<section class="pw-section" aria-labelledby="ev-acc-h">
    <figure class="pw-figure">
      <h2 class="pw-figure__title" id="ev-acc-h">${finding}${decided ? ` decided matches correctly${idx < n ? ' so far' : ''}` : ''}</h2>
      <p class="pw-figure__sub">Prediction accuracy at ${fmt.eventShort(ev.meta)}, ${idx <= 0 ? 'before the first match' : idx >= n ? 'whole event' : `through ${lastPlayedLabel(ev, idx)}`}. A coin flip would call 50%.</p>
      ${scrollBox(html`<table class="pw-table pwe-table">
        <caption class="pwe-sr">Prediction accuracy by model</caption>
        <thead><tr><th scope="col">Model</th><th scope="col" class="n">Quals called</th><th scope="col" class="n">Playoffs called</th><th scope="col" class="n">All called</th>
          <th scope="col" class="n">${season.season} season</th><th scope="col" class="n">Brier</th><th scope="col" class="n">Score MAE</th><th scope="col" class="n">Score bias</th></tr></thead>
        <tbody>${list(models.map(row))}</tbody></table>`, 'Prediction accuracy')}
      <figcaption class="pw-caption"><b>Called:</b> share of decided matches where the favoured alliance won (ties and exact 50% calls left out); n is the number of matches. Bars start at zero.
        <b>Season:</b> same measure over all ${season.season} matches, counting ties as misses. <b>Brier:</b> mean squared error of the red win probability, 0 is perfect and 0.250 is
        always saying 50%. <b>Score MAE:</b> mean absolute gap between predicted and actual alliance score with fouls, in points. <b>Score bias:</b> mean predicted minus actual (negative means the
        model undershot). Every prediction was made before its match. ${updated(season)}</figcaption>
    </figure>
    ${misses.length ? html`<h2 class="pw-h2" id="ev-miss-h">Biggest misses for ${modelLabel(mm, model)}</h2>
      ${scrollBox(html`<table class="pw-table pwe-table"><caption class="pwe-sr">Matches won by the alliance the model gave the lowest chance</caption>
        <thead><tr><th scope="col" class="pwe-stick">Match</th><th scope="col">Result</th><th scope="col" class="n">Winner's chance</th><th scope="col" class="n">Prediction</th></tr></thead>
        <tbody>${list(missRows)}</tbody></table>`, 'Biggest misses')}` : ''}
  </section>`;
}

function updated(season) {
  return season.generated_at ? `Data updated ${fmt.dateTime(season.generated_at)}. Source: The Blue Alliance.` : 'Source: The Blue Alliance.';
}

// ---------- csv ----------

function csvFor(tab, { td, md, rank, models, model, ev }) {
  const mname = modelLabel(state.get().manifest.models, model);
  const r1 = fmt.round;
  if (tab === 'teams') {
    return {
      cols: [['epaRank', '#'], ['team', 'Team'], ['name', 'Name'], ['epa', `${mname} (points)`], ['auto', 'Auto (points)'], ['teleop', 'Teleop (points)'], ['endgame', 'Endgame (points)'],
        ['pre', 'Pre-event (points)'], ['change', 'Change (points)'], ['zc', 'Differs from pre-event (z)'], ['k', 'Matches used (k)'], ['n', 'Matches'], ['rank', 'Qual rank'], ['rec', 'Quals W-L-T']],
      rows: td.rows.map((r) => ({ ...r, epa: r1(r.epa), auto: r1(r.auto), teleop: r1(r.teleop), endgame: r1(r.endgame), pre: r1(r.pre), change: r1(r.change),
        zc: r.z == null ? null : Number(r.z.toFixed(2)), rec: r.qn ? fmt.record(r.w, r.l, r.tie) : '' })),
    };
  }
  if (tab === 'rankings') {
    return {
      cols: [['rank', 'Rank'], ['team', 'Team'], ['name', 'Name'], ['rp', 'RP'], ['rpm', 'RP per match'], ['rec', 'W-L-T'], ['epa', `${mname} (points)`], ['epaRank', `${mname} rank`]],
      rows: td.rows.filter((r) => r.rank != null).sort((a, b) => a.rank - b.rank)
        .map((r) => ({ ...r, rpm: r.rpm == null ? null : Number(r.rpm.toFixed(2)), epa: r1(r.epa), rec: r.qn ? fmt.record(r.w, r.l, r.tie) : '' })),
    };
  }
  if (tab === 'matches') {
    const cols = [['label', 'Match'], ['red', 'Red alliance'], ['redScore', 'Red score'], ['blueScore', 'Blue score'], ['blue', 'Blue alliance']];
    for (const m of models) cols.push([`p_${m}`, `${modelLabel(state.get().manifest.models, m)} red win probability`], [`s_${m}`, `${modelLabel(state.get().manifest.models, m)} predicted score (red-blue)`]);
    return {
      cols,
      rows: rank.map((r) => {
        const o = { ...r, red: r.red.join(' '), blue: r.blue.join(' ') };
        for (const m of models) { o[`p_${m}`] = r.preds[m].p == null ? null : Number(r.preds[m].p.toFixed(3)); o[`s_${m}`] = r.preds[m].rs == null ? '' : `${r1(r.preds[m].rs, 0)}-${r1(r.preds[m].bs, 0)}`; }
        return o;
      }),
    };
  }
  return null;
}

// ---------- view ----------

export async function render(ctx) {
  const { root, route } = ctx;
  const key = route.path[0];
  if (!key) throw new Error('No event key in the address. Pick an event from the Events tab.');
  const seasonNum = seasonOfEvent(key) || state.get().season;
  ctx.loading(`Loading ${key}`);
  const [season, ev] = await Promise.all([getSeason(seasonNum), getEvent(seasonNum, key)]);
  if (!ctx.isCurrent()) return undefined;

  const st = state.get();
  const model = st.model;
  const mm = (st.manifest && st.manifest.models) || [{ id: 'epa', label: 'EPA' }, { id: 'fast', label: 'Fast EPA' }];
  const models = mm.map((m) => m.id);
  const n = ev.matches.n;
  const meta = ev.meta;
  const tab = TABS.some(([id]) => id === route.query.tab) ? route.query.tab : 'teams';
  let query = { ...route.query };
  let asof = query.asof || 'end';
  let show = SHOW[query.show] ? query.show : 'all';
  let expanded = false;

  const opts = asofOptions(ev);
  const idxOf = () => asofIndex(ev, asof);
  const wlSet = () => new Set(watchlist.get());
  const hasWatchTeam = [...col(ev.team_events, 'team')].some((t) => wlSet().has(t));
  const showOptions = Object.keys(SHOW).filter((k) => k !== 'watch' || hasWatchTeam);

  const regionTxt = meta.district ? `${meta.district.toUpperCase()}` : meta.state || '';
  const eyebrow = [`Event · ${seasonNum}`, weekLabel(meta.week), typeLabel(meta.type), dateRange(meta.start_date, meta.end_date), regionTxt].filter(Boolean).join(' · ');

  let current = null; // {tab data} for CSV
  function build() {
    const idx = idxOf();
    const td = teamsData(ev, model, idx);
    const acc = { all: accuracy(ev, model, idx).all };
    const lede = makeLede({ ev, model, models: mm, td, idx, acc });
    let content, csvInfo = null;
    const common = { model, models: mm, season: seasonNum, idx, n };
    if (tab === 'teams') {
      content = td.rows.length ? teamsTable(ev, td, common) : html`<p class="pw-muted">No team list for this event yet.</p>`;
      csvInfo = csvFor('teams', { td, models });
    } else if (tab === 'matches') {
      const rows = matchesData(ev, season, models, idx, show);
      content = matchesTable(rows, { ...common, models, mm, expanded, show });
      csvInfo = csvFor('matches', { rank: rows, models, model });
    } else if (tab === 'rankings') {
      content = rankingsTable(td, common);
      csvInfo = csvFor('rankings', { td, model });
    } else if (tab === 'alliances') {
      content = alliancesTable(ev, td, common);
    } else {
      content = accuracyView(ev, season, { models, model, idx, n });
    }
    current = csvInfo;
    return { lede, content };
  }

  const tabHref = (id) => router.link('event', [key], { ...query, tab: id === 'teams' ? undefined : id, show: id === 'matches' ? query.show : undefined });
  const asofValueForSelect = () => (opts.some((o) => o.id === asof) ? asof : idxOf() >= n ? 'end' : asof);
  const built = build();

  const page = html`
    ${pageHead({ eyebrow, title: meta.name || key, lede: built.lede, ledeId: 'ev-lede' })}
    <section class="pw-filters" aria-label="Event controls">
      <div class="pw-filters__group">
        ${tab !== 'alliances' ? asofControl({ scope: 'event', value: asofValueForSelect(), options: opts, id: 'ev-asof', label: 'As of' }) : ''}
        ${tab === 'matches' ? html`<label class="pw-field"><span class="pw-eyebrow">Show</span>
          <select class="pw-select" id="ev-show" data-act="show">${list(showOptions.map((k) => html`<option value="${k}" ${k === show ? 'selected' : ''}>${SHOW[k]}</option>`))}</select></label>` : ''}
      </div>
      ${['teams', 'matches', 'rankings'].includes(tab) ? html`<button type="button" class="pw-btn" data-act="csv" id="ev-csv">Download CSV</button>` : ''}
    </section>
    <nav class="pwe-tabs" aria-label="Event sections">
      ${list(TABS.map(([id, lab]) => html`<a href="${tabHref(id)}" ${id === tab ? 'aria-current="page"' : ''}>${lab}</a>`))}
      ${meta.video ? html`<a class="pwe-tabs__ext" href="${meta.video}" target="_blank" rel="noopener">Watch the webcast</a>` : ''}
    </nav>
    <div id="ev-content" class="pwe-content">${built.content}</div>`;
  mount(root, page);
  ctx.setTitle(`${meta.name || key} · ${seasonNum}`);

  const lede = root.querySelector('#ev-lede');
  const content = root.querySelector('#ev-content');
  function repaint() {
    const b = build();
    if (lede) lede.textContent = b.lede;
    mount(content, b.content);
  }
  let raf = 0;
  function setAsof(id) {
    if (!id || id === asof) return;
    asof = id;
    query = { ...query, asof: id === 'end' ? undefined : id };
    router.navigate('event', [key], query, { silent: true });
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(repaint);
  }
  const onInput = (e) => {
    const el = e.target.closest('[data-act="asof"], [data-act="asof-range"]');
    if (!el) return;
    const id = readAsof(el);
    // keep the select and the slider in step
    const sel = root.querySelector('#ev-asof');
    const rng = root.querySelector('[data-act="asof-range"]');
    if (sel && el !== sel) sel.value = id;
    if (rng && el !== rng) { rng.value = String(opts.findIndex((o) => o.id === id)); }
    if (rng) rng.setAttribute('aria-valuetext', (opts.find((o) => o.id === id) || {}).label || '');
    setAsof(id);
  };
  const onChange = (e) => {
    const el = e.target.closest('[data-act="show"]');
    if (el) {
      show = el.value;
      query = { ...query, show: show === 'all' ? undefined : show };
      router.navigate('event', [key], query, { silent: true });
      expanded = false;
      repaint();
      return;
    }
    onInput(e);
  };
  const onClick = (e) => {
    const more = e.target.closest('[data-act="more"]');
    if (more) { expanded = true; repaint(); const box = content.querySelector('.pwe-box'); if (box) box.focus(); return; }
    const b = e.target.closest('[data-act="csv"]');
    if (b && current) {
      const cols = current.cols.map(([k, l]) => ({ key: k, label: l }));
      csv.download(csv.csvFilename({ view: 'event', season: seasonNum, region: `${key}-${tab}`, asof: asof === 'end' ? '' : asof }), csv.toCSV(cols, current.rows));
    }
  };
  root.addEventListener('input', onInput);
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  return () => {
    cancelAnimationFrame(raf);
    root.removeEventListener('input', onInput);
    root.removeEventListener('change', onChange);
    root.removeEventListener('click', onClick);
  };
}

export default render;

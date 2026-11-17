// Compare (WP5, SPEC 5.6): 2-4 teams side by side: lede, stat table, trajectories as small multiples
// on shared axes, phase bars, every rating component, per-robot rates.
// Route: #/compare?season=&model=&teams=a,b,c   (default: watchlist, topped up with the season's best)
import { html, raw, mount } from '../core/html.js';
import * as state from '../core/state.js';
import * as watchlist from '../core/watchlist.js';
import { getSeason, getTraj, tbl } from '../core/data.js';
import { rating, delta, rank as fmtRank, record, int, vectorLabel, unit, compLabel, dateTime } from '../core/fmt.js';
import { toCSV, csvFilename, download } from '../core/csv.js';
import { consText, changeCell } from '../core/features.js';
import { pageHead, note } from '../components/page.js';
import { figure, fitChart } from '../components/figure.js';
import { dataTable } from '../components/table.js';
import { teamChip, watchButton } from '../components/team.js';
import { smallMultiples } from '../charts/multiples.js';
import { groupedBars } from '../charts/bars.js';
import { lineDataTable } from '../charts/line.js';
import { rangeOf, pMatchAbove, verdict, outOf10 } from '../core/ranges.js';
import { distCurves } from '../charts/range.js';

/** Head-to-head block for 2-4 rows (exported for tests): {title, text, pairs}. */
export function headToHead(rows) {
  if (rows.length < 2) return null;
  const s = rows.slice().sort((a, b) => (b.epa ?? -1e9) - (a.epa ?? -1e9));
  if (rows.length === 2) {
    const v = verdict(pMatchAbove(rows[0], rows[1]), rows[0], rows[1]);
    return { title: v.head, text: v.text, pairs: [] };
  }
  const pairs = [];
  for (let i = 0; i < s.length; i++) for (let j = i + 1; j < s.length; j++) pairs.push({ a: s[i], b: s[j], p: pMatchAbove(s[i], s[j]) });
  const close = pairs.filter((x) => x.p !== null && x.p < 0.56);
  const v = verdict(pMatchAbove(s[0], s[1]), s[0], s[1]);
  const title = `${v.head} at the top`;
  const text = close.length
    ? `${v.text} Too close to call: ${close.map((x) => `${x.a.team} vs ${x.b.team}`).join(', ')}.`
    : v.text;
  return { title, text, pairs };
}

const MAX = 4;

export function parseTeams(text) {
  return String(text || '').split(',').map((s) => parseInt(s, 10)).filter((n) => Number.isInteger(n) && n > 0)
    .filter((n, i, a) => a.indexOf(n) === i).slice(0, MAX);
}

/** Teams to show and a note when the list was topped up (exported for tests). */
export function chooseTeams(rows, q, wl) {
  const has = new Set(rows.map((r) => r.team));
  if (q.teams) return { teams: parseTeams(q.teams).filter((t) => has.has(t)), added: [] };
  let teams = wl.filter((t) => has.has(t)).slice(0, MAX);
  const added = [];
  if (teams.length < 2) {
    for (const r of rows.filter((x) => x.count > 0 && !teams.includes(x.team)).sort((a, b) => b.epa - a.epa)) {
      if (teams.length >= 2) break;
      teams = teams.concat(r.team); added.push(r.team);
    }
  }
  return { teams, added };
}

function lede(rows, modelName, season) {
  if (rows.length < 2) return 'Add a second team to compare. Ratings are predictions of points a team adds per match, not results.';
  const s = rows.slice().sort((a, b) => (b.epa ?? -1e9) - (a.epa ?? -1e9));
  const [top, second] = s; const last = s[s.length - 1];
  let t = `${top.team} leads this group at ${rating(top.epa)} points per match (${modelName}, ${season}), ${rating(top.epa - second.epa)} ahead of ${second.team}`;
  t += s.length > 2 ? `; ${last.team} is ${rating(top.epa - last.epa)} behind.` : '.';
  const few = s.filter((r) => r.count < 6).map((r) => r.team);
  if (few.length) t += ` ${few.join(' and ')} ${few.length > 1 ? 'have' : 'has'} fewer than 6 matches, so treat ${few.length > 1 ? 'those ratings' : 'that rating'} as a guess.`;
  return t;
}

export async function render(ctx) {
  const { root, route } = ctx;
  const st = state.get();
  const { season, model } = st;
  ctx.loading(`Loading ${season} teams`);
  const [S, traj] = await Promise.all([getSeason(season), getTraj(season)]);
  if (!ctx.isCurrent()) return undefined;
  const modelName = (st.manifest.models.find((m) => m.id === model) || { label: 'EPA' }).label;
  const T = tbl(S.teams);
  const all = T.rows(model);
  const q = route.query;
  const { teams, added } = chooseTeams(all, q, watchlist.get());
  const bad = parseTeams(q.teams).filter((t) => !T.index('team').has(t));
  const rows = teams.map((t) => all[T.index('team').get(t)]);
  const lead = rows.slice().sort((a, b) => b.epa - a.epa)[0];
  const notes = [added.length ? `Added ${added.join(' and ')}, the top-rated ${added.length > 1 ? 'teams' : 'team'} of the season, for contrast.` : '', bad.length ? `No ${season} rating for ${bad.join(', ')}; left out.` : ''].filter(Boolean).join(' ');

  let body = '';
  const draws = {};
  if (rows.length) {
    const statCols = [
      { id: 'team', label: 'Team', td: 'team', render: (r) => teamChip(r.team, { season, model }) },
      { id: 'name', label: 'Name', render: (r) => r.name || '' },
      { id: 'epa', label: modelName, n: true, render: (r) => rating(r.epa) },
      { id: 'range', label: 'Likely range', n: true, render: (r) => { const g = rangeOf(r.epa, r.epa_se); return g ? `${rating(g.lo, 0)}–${rating(g.hi, 0)}` : '–'; } },
      { id: 'gap', label: 'Vs leader', n: true, render: (r) => (r === lead ? 'leader' : delta(r.epa - lead.epa)) },
      { id: 'auto', label: 'Auto', n: true, render: (r) => rating(r.auto_epa) },
      { id: 'teleop', label: 'Teleop', n: true, render: (r) => rating(r.teleop_epa) },
      { id: 'endgame', label: 'Endgame', n: true, render: (r) => rating(r.endgame_epa) },
      { id: 'norm', label: 'Norm EPA', n: true, render: (r) => int(r.norm_epa) },
      { id: 'rank', label: 'World rank', n: true, render: (r) => fmtRank(r.total_epa_rank, r.total_team_count) },
      { id: 'rec', label: 'Record', n: true, render: (r) => (r.count ? record(r.wins, r.losses, r.ties) : '–') },
      { id: 'cons', label: 'Consistency', n: true, render: (r) => consText(r.cons_sd, r.cons_n) },
      { id: 'chg', label: 'Differs from pre-event', thClass: 'pw-th-wrap', render: (r) => changeCell(r.change_z, { event: r.change_event, first: (r.events || [])[0] === r.change_event }) },
      { id: 'watch', label: 'Watchlist', render: (r) => watchButton(r.team, watchlist.has(r.team)) },
    ];

    // trajectories: one panel per team, the others in grey, shared axes
    const tIdx = new Map(traj.team.map((t, i) => [t, i]));
    const series = rows.map((r) => {
      const i = tIdx.get(r.team);
      const ys = i === undefined ? [] : (traj.models[model].epa[i] || []);
      const pts = ys.map((y, k) => ({ x: k + 1, y })).filter((p) => typeof p.y === 'number');
      const end = pts.length ? pts[pts.length - 1].y : null;
      return { id: String(r.team), label: `${r.team} · ${rating(end)}`, title: `${r.team}${pts.length ? `: ${rating(end)} after ${pts.length} matches` : ': no matches yet'}`, role: watchlist.has(r.team) ? 'you' : 'focus', points: pts, r };
    });
    const rise = series.filter((s) => s.points.length > 1).map((s) => ({ t: s.r.team, d: s.points[s.points.length - 1].y - (typeof s.r.epa_start === 'number' ? s.r.epa_start : s.points[0].y) })).sort((a, b) => b.d - a.d);
    const trajTitle = rise.length ? `${rise[0].t} ${rise[0].d >= 0 ? 'gained' : 'lost'} the most over the season: ${delta(rise[0].d)} points per match` : 'Ratings over the season';

    const phases = [['auto_epa', 'Auto'], ['teleop_epa', 'Teleop'], ['endgame_epa', 'Endgame']];
    const leaders = phases.map(([k]) => rows.slice().sort((a, b) => (b[k] || 0) - (a[k] || 0))[0].team);
    const phaseTitle = rows.length < 2 ? 'Rating by phase' : new Set(leaders).size === 1 ? `${leaders[0]} has the highest auto, teleop and endgame ratings in this group` : `No team leads every phase: auto ${leaders[0]}, teleop ${leaders[1]}, endgame ${leaders[2]}`;

    const vks = S.keys.vector.filter((k) => k !== 'epa' && k !== 'tiebreaker_epa');
    const items = vks.map((k) => {
      const vals = rows.map((r) => r[k]);
      const max = Math.max(...vals.filter((v) => typeof v === 'number'));
      return { k, label: vectorLabel(k, S.keys), unit: unit(k, S.keys), vals, max, nd: unit(k, S.keys) === 'RP score' ? 2 : 1 };
    });
    const compCols = [
      { id: 'label', label: 'Component', render: (it) => it.label },
      { id: 'unit', label: 'Unit', render: (it) => it.unit },
      ...rows.map((r, ri) => ({ id: `v${ri}`, label: String(r.team), n: true, render: (it) => (typeof it.vals[ri] === 'number' ? html`<span class="${it.vals[ri] === it.max && rows.length > 1 ? 'best' : ''}">${it.vals[ri].toFixed(it.nd)}</span>` : '–') })),
    ];

    const rb = S.robot || {};
    const fields = (rb.fields || []).filter((f) => f.active);
    const robotCell = (f, r) => {
      const c = ((rb.counts || {})[f.key] || [])[r._i];
      const tot = c ? c.reduce((a, b) => a + b, 0) : 0;
      if (!tot) return '–';
      const parts = c.map((v, ci) => ({ v, code: f.codes[ci] })).filter((p) => p.v / tot >= 0.03).sort((a, b) => b.v - a.v).map((p) => `${p.code} ${Math.round((p.v / tot) * 100)}%`);
      return `${parts.join(' · ')} (n = ${tot})`;
    };

    draws.mult = (w) => smallMultiples({ series, context: true, panelWidth: w >= 640 ? Math.floor((w - 16) / 2) : w, cols: w >= 640 ? 2 : 1, x: { title: 'Matches played (count)' }, y: { title: `${modelName} (points per match)` }, ariaLabel: `${modelName} after each match; the other compared teams in grey.` });
    draws.phase = (w) => groupedBars({ width: Math.max(320, w), groups: phases.map(([k, label]) => ({ label, values: rows.map((r) => ({ label: String(r.team), value: r[k], you: watchlist.has(r.team) })) })), axisTitle: `${modelName} (points per match)`, ariaLabel: `${modelName} auto, teleop and endgame for ${rows.map((r) => r.team).join(', ')}` });
    const h2h = headToHead(rows);
    const curveTeams = rows.map((r) => ({ row: r, role: watchlist.has(r.team) ? 'you' : (rows.length === 2 ? 'focus' : 'context') }));
    if (rows.length > 2 && !curveTeams.some((t) => t.role === 'you')) curveTeams[0].role = 'focus';
    draws.curves = (w) => distCurves({ teams: curveTeams, width: Math.max(300, w), unitLabel: `Points added in one match (${modelName})`, ariaLabel: `How much each team's points swing from match to match: ${rows.map((r) => `${r.team} centered at ${rating(r.epa)}`).join(', ')}` });
    const oddsTable = h2h && h2h.pairs.length ? html`<table class="pw-odds"><caption class="pw-small" style="caption-side: bottom; text-align: left; padding-top: 6px">Matches out of 10 in which the row team would add more points than the column team.</caption>
<thead><tr><th scope="col"><span class="pw-sr">Team</span></th>${rows.map((c) => html`<th scope="col">${c.team}</th>`)}</tr></thead>
<tbody>${rows.map((r) => html`<tr><th scope="row">${r.team}</th>${rows.map((c) => { if (c === r) return html`<td>–</td>`; const p = pMatchAbove(r, c); return html`<td>${p === null ? '–' : outOf10(p)}</td>`; })}</tr>`)}</tbody></table>` : '';
    body = html`
${h2h ? figure({
    id: 'cmp-h2h', title: h2h.title,
    sub: h2h.text,
    body: html`<div class="pw-chart" data-chart="curves"></div><p class="pw-legend">${curveTeams.map((t) => html`<span class="pw-legend__item"><span class="pw-swatch pw-swatch--${t.role}" aria-hidden="true"></span>${t.row.team} · ${rating(t.row.epa)}</span>`)}</p>${oddsTable}`,
    caption: html`<b>Curves:</b> how many points each team adds in a single match: the peak is its ${modelName}, the width is how much its points swing from match to match. Where curves overlap, either team can come out ahead. ${rows.map((r) => html`<span class="pw-nowrap">${r.team}: ${r.count} matches</span>`).reduce((a, b) => html`${a}, ${b}`)}. Points a robot adds, not alliance wins. Source: The Blue Alliance.`,
  }) : ''}
<section class="pw-section" aria-labelledby="cmp-stats">
<h2 class="pw-h2" id="cmp-stats">Ratings side by side</h2>
${dataTable({ columns: statCols, rows, labelledby: 'cmp-stats', rowClass: (r) => (watchlist.has(r.team) ? 'you' : '') })}
<p class="pw-small">${modelName}: predicted points a team adds per match. World rank among all ${season} teams. Consistency: SD of match residuals (points) with n qual matches.</p>
</section>
${figure({
    id: 'cmp-traj', title: trajTitle,
    sub: `${modelName} after each counted match, ${season}. One panel per team on shared axes; the other teams are the grey lines.`,
    body: html`<div class="pw-chart" data-chart="mult"></div>`,
    data: raw(lineDataTable(series.map((s) => ({ id: s.id, label: s.id, points: s.points })), 'Match')),
    caption: html`<b>Lines:</b> amber = a watchlist team, dark = another team, grey = the other teams in this comparison. n = ${series.map((s) => `${s.r.team}: ${s.points.length} matches`).join(', ')}. Early points move fast because few matches back them. Source: The Blue Alliance, fetched ${dateTime(S.generated_at)}.`,
  })}
${figure({
    id: 'cmp-phase', title: phaseTitle, sub: `${modelName} by phase, points per match, ${season}.`,
    body: html`<div class="pw-chart" data-chart="phase"></div>`,
    caption: html`<b>Bars:</b> start at zero on one shared axis; amber = watchlist team, grey = other team; every bar carries its value. n = ${rows.map((r) => `${r.team}: ${r.count} matches`).join(', ')}. Source: The Blue Alliance.`,
  })}
${figure({
    id: 'cmp-comp', cls: 'pw-figure--flush', title: 'Every rating component',
    sub: 'Points components in points per match, counts in game pieces per match, RP in ranking-point score. Highest in each row is bold.',
    body: dataTable({ columns: compCols, rows: items, labelledby: 'cmp-comp-t' }),
    caption: html`Season component names come from TBA score breakdowns. Source: The Blue Alliance.`,
  })}
${fields.length ? figure({
    id: 'cmp-robot', cls: 'pw-figure--flush', title: 'What each robot did, as a share of its counted matches',
    sub: 'Per-robot fields TBA reports for the season, quals and playoffs. Outcomes under 3% are left out.',
    body: dataTable({ columns: [{ id: 'f', label: 'Robot field', render: (f) => compLabel(f.key.replace(/([a-z0-9])([A-Z])/g, '$1_$2')) }, ...rows.map((r) => ({ id: `t${r.team}`, label: String(r.team), render: (f) => html`<span class="wrap">${robotCell(f, r)}</span>` }))], rows: fields, labelledby: 'cmp-robot-t' }),
    caption: html`n = matches with that field reported for the team. Source: TBA per-robot score breakdown fields.`,
  }) : ''}`;
  }

  mount(root, html`
${pageHead({ eyebrow: `Compare · ${season} season · ${modelName}`, title: 'Compare teams', lede: rows.length ? lede(rows, modelName, season) : 'Add two to four teams to compare their ratings, trajectories and components.' })}
<section class="pw-filters" aria-label="Choose teams">
<div class="pw-filters__group">
<span class="pw-eyebrow" id="cmp-list-l">Teams (2 to 4)</span>
<ul class="pw-chiplist" aria-labelledby="cmp-list-l">${teams.map((t) => html`<li>${teamChip(t, { season, model })}<button type="button" class="pw-x" data-act="remove" data-team="${t}" aria-label="Remove team ${t}">×</button></li>`)}</ul>
${teams.length < MAX ? html`<form class="pw-field" data-act="add"><label class="pw-eyebrow" for="cmp-add">Add team</label><input id="cmp-add" class="pw-input pw-input--team" inputmode="numeric" autocomplete="off" placeholder="254"><button class="pw-btn" type="submit">Add</button></form>` : ''}
</div>
${rows.length ? html`<button type="button" class="pw-btn" data-act="csv">Download CSV</button>` : ''}
</section>
${note(notes, { live: true })}
${body}`);
  ctx.setTitle(`Compare · ${season}`);
  const stops = [...root.querySelectorAll('[data-chart]')].map((h) => fitChart(h, draws[h.dataset.chart]));

  const say = (t) => { const n = root.querySelector('.pw-note-line'); if (n) n.textContent = t; };
  const onClick = (e) => {
    const t = e.target.closest('[data-act]');
    if (!t || !root.contains(t)) return;
    const act = t.dataset.act;
    if (act === 'remove') state.setQuery({ teams: teams.filter((x) => x !== Number(t.dataset.team)).join(',') || 'none' });
    else if (act === 'watch') { watchlist.toggle(Number(t.dataset.team)); ctx.retry(); }
    else if (act === 'csv') {
      download(csvFilename({ view: 'compare', season }), toCSV([
        { key: 'team', label: 'Team' }, { key: 'name', label: 'Name' }, { key: 'epa', label: `${modelName} (points per match)` },
        { key: 'auto_epa', label: 'Auto (points)' }, { key: 'teleop_epa', label: 'Teleop (points)' }, { key: 'endgame_epa', label: 'Endgame (points)' },
        { key: 'norm_epa', label: 'Norm EPA' }, { key: 'total_epa_rank', label: 'World rank' }, { key: 'total_team_count', label: 'Teams ranked' },
        { key: 'wins', label: 'Wins' }, { key: 'losses', label: 'Losses' }, { key: 'ties', label: 'Ties' }, { key: 'count', label: 'Matches' },
        { key: 'cons', label: 'Consistency SD (points)', value: (r) => (r.cons_n >= 4 && typeof r.cons_sd === 'number' ? Number(r.cons_sd.toFixed(1)) : null) },
        { key: 'cons_n', label: 'Consistency n' },
      ], rows.map((r) => ({ ...r, epa: Number(r.epa.toFixed(1)), auto_epa: Number(r.auto_epa.toFixed(1)), teleop_epa: Number(r.teleop_epa.toFixed(1)), endgame_epa: Number(r.endgame_epa.toFixed(1)) }))));
    }
  };
  const onSubmit = (e) => {
    const f = e.target.closest('form[data-act="add"]');
    if (!f || !root.contains(f)) return;
    e.preventDefault();
    const n = parseInt(f.querySelector('input').value, 10);
    if (!Number.isInteger(n) || n <= 0) { say('Enter a team number, for example 254.'); return; }
    if (!T.index('team').has(n)) { say(`No ${season} rating for team ${n}.`); return; }
    state.setQuery({ teams: [...teams.filter((x) => x !== n), n].slice(0, MAX).join(',') });
  };
  root.addEventListener('click', onClick);
  root.addEventListener('submit', onSubmit);
  return () => { stops.forEach((f) => f()); root.removeEventListener('click', onClick); root.removeEventListener('submit', onSubmit); };
}

export default render;

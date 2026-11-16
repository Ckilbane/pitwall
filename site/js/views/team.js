// Team tab (SPEC 5.6): answer-first lede, stat tiles with region ranks, rating over time against the
// region's leaders, events table, recent matches, season over season, explain-this-number.
// Layout from docs/design/template-team-page.dc.html.
import { html, raw, mount } from '../core/html.js';
import * as router from '../core/router.js';
import * as state from '../core/state.js';
import { getSeason, getTraj, getHistory, getEvent, tbl } from '../core/data.js';
import { rating, delta, int, rank as fmtRank, record, eventShort, numberWord, plural, dateTime, matchLabel } from '../core/fmt.js';
import { matchRegion, regionLabel, regionShort, teamRegion } from '../core/regions.js';
import { rankOf, medianOf, topBy } from '../core/stats.js';
import * as watchlist from '../core/watchlist.js';
import { stat } from '../components/stat.js';
import { figure, showData, fitChart } from '../components/figure.js';
import { dataTable } from '../components/table.js';
import { watchButton } from '../components/team.js';
import { matchCard, matchFacts } from '../components/match.js';
import { explainRows, explainPanel } from '../components/explain.js';
import { lineChart, lineDataTable } from '../charts/line.js';
import { barCell } from '../charts/bars.js';
import { consText, changeDir, changeCell, oppEffect, seasonMean, CHANGE_Z } from '../core/features.js';

const TILES = [
  { key: 'epa', label: 'Total EPA' },
  { key: 'auto_epa', label: 'Auto EPA' },
  { key: 'teleop_epa', label: 'Teleop EPA' },
  { key: 'endgame_epa', label: 'Endgame EPA' },
];

export function title(route) { return `Team ${route.path[0]} · ${route.query.season || ''}`; }

// ---- pure helpers (exported for tests) ----

/** Per-event rows for the events table from loaded event files [{key, ev}], in the order given. */
export function eventRows(S, team, model, loaded) {
  const E = tbl(S.events);
  const out = [];
  for (const { key, ev } of loaded) {
    const ei = E.index('key').get(key);
    const meta = ei === undefined ? { key, name: key } : E.rows()[ei];
    let start = null; let end = null; let rk = null; let nt = null; let count = 0;
    if (ev) {
      const T = tbl(ev.team_events);
      const i = T.index('team').get(team);
      if (i !== undefined) {
        start = T.get(i, 'pre_epa', model);
        end = T.get(i, 'epa', model);
        rk = T.get(i, 'rank');
        nt = T.get(i, 'num_teams');
        count = T.get(i, 'count') || 0;
      }
    }
    out.push({
      key, meta, name: eventShort(meta), week: meta.week, start, end, count, rank: rk, numTeams: nt,
      change: count > 0 && typeof start === 'number' && typeof end === 'number' ? end - start : null,
    });
  }
  return out;
}

/** Finding for the events table title. */
export function eventsTitle(rows, season) {
  const played = rows.filter((r) => r.change !== null);
  if (!played.length) return `No completed events yet in ${season}`;
  if (played.length === 1) return `${played[0].name}: EPA went from ${rating(played[0].start)} to ${rating(played[0].end)}`;
  const best = played.slice().sort((a, b) => b.change - a.change)[0];
  const worst = played.slice().sort((a, b) => a.change - b.change)[0];
  if (best.change <= 0) return `EPA fell at every event, most at ${worst.name} (${delta(worst.change)})`;
  return `Biggest jump came at ${best.name} (${delta(best.change)})`;
}

/** Chart finding as the title. gaps: leaders' mean EPA minus the team's, at the first and last match. */
export function chartTitle({ num, region, season, evRows, gapStart, gapEnd, start, end }) {
  const played = evRows.filter((r) => r.change !== null);
  const reg = regionShort(region);
  const up = played.length > 1 && played.every((r) => r.change > 0);
  const closed = typeof gapStart === 'number' && typeof gapEnd === 'number' && gapStart > 0 ? (gapStart - gapEnd) / gapStart : null;
  if (up && closed !== null && closed >= 0.5) return `${num} climbed at every event and closed most of the gap to ${reg}'s top two`;
  if (up) return `${num} climbed at every event, from ${rating(start)} to ${rating(end)}`;
  if (closed !== null && closed >= 0.5) return `${num} closed most of the gap to ${reg}'s top two`;
  if (end > start * 1.1) return `${num} rose from ${rating(start)} to ${rating(end)} points per match in ${season}`;
  if (end < start * 0.9) return `${num} fell from ${rating(start)} to ${rating(end)} points per match in ${season}`;
  return `${num} stayed near ${rating(end)} points per match through ${season}`;
}

/** Lede: the sentence that drives a decision. */
export function teamLede({ num, watched, row, regionRows, region, complete, evCount, strongest }) {
  const who = watched ? 'Your' : `${num}'s`;
  const reg = regionShort(region);
  const rk = rankOf(regionRows, (r) => r.epa, row.epa);
  const n = regionRows.length;
  if (!row.count) {
    return `${num} has not played a match this season. ${watched ? 'Your' : 'Its'} rating is the preseason estimate, ${rating(row.epa)} points per match${rk ? `, ${fmtRank(rk, n)} among ${reg} teams` : ''}.`;
  }
  const a = row.epa_start;
  const b = row.epa;
  const verb = a > 0 && b / a >= 2 ? 'more than doubled' : b / a >= 1.25 ? 'rose' : b / a <= 0.8 ? 'fell' : 'held near its start';
  const span = evCount >= 2 ? ` across ${numberWord(evCount)} events` : ` over ${plural(row.count, 'match', 'matches')}`;
  const range = verb === 'held near its start' ? `, ${rating(a)} to ${rating(b)} points per match` : `, from ${rating(a)} to ${rating(b)} points per match`;
  let s = `${who} EPA ${verb}${span}${range}, ${complete ? 'finishing' : 'now'} ${fmtRank(rk, n)} ${reg === 'all teams' ? 'teams' : `${reg} teams`}.`;
  if (strongest) s += ` ${strongest}`;
  if (row.count < 6) s += ` n = ${row.count} matches, treat as a guess.`;
  return s;
}

/** Best phase by region rank, as a sentence, or '' when none is a standout. */
export function strongestPhase(row, regionRows, region) {
  const n = regionRows.length;
  let best = null;
  for (const [key, name] of [['auto_epa', 'Auto'], ['teleop_epa', 'Teleop'], ['endgame_epa', 'Endgame']]) {
    const r = rankOf(regionRows, (x) => x[key], row[key]);
    if (r !== null && (!best || r < best.r)) best = { key, name, r };
  }
  if (!best || !(best.r <= 5 || best.r <= n * 0.1)) return '';
  return `Strongest phase is ${best.name.toLowerCase()}: ${fmtRank(best.r, n)} in ${regionShort(region)} at ${rating(row[best.key])} points per match.`;
}

function notFound(root, num, season, hist) {
  const seasons = [];
  if (hist) {
    const H = tbl(hist.rows);
    const c = H.col('team');
    const s = H.col('season');
    for (let i = 0; i < H.n; i++) if (c[i] === num) seasons.push(s[i]);
  }
  seasons.sort((a, b) => b - a);
  mount(root, html`<section class="pw-head"><span class="pw-eyebrow">Team · ${season} season</span>
<h1 class="pw-h1" tabindex="-1">No ratings for team ${num} in ${season}</h1>
<p class="pw-lede">${seasons.length ? html`Team ${num} has ratings in ${seasons.map((y, i) => html`${i ? ', ' : ''}<a href="${router.link('team', [num], { season: y })}">${y}</a>`)}.` : `No published season has a team ${num}. Check the number, or use Find.`}</p></section>`);
}

export async function render(ctx) {
  const { root, route } = ctx;
  const num = Number(route.path[0]);
  if (!Number.isInteger(num) || num <= 0) {
    mount(root, html`<section class="pw-head"><h1 class="pw-h1" tabindex="-1">Pick a team</h1><p class="pw-lede">Team pages live at #/team/1218. Use Find in the header, or open the Teams tab.</p></section>`);
    ctx.setTitle('Team');
    return;
  }
  const st = state.get();
  const { season, model } = st;
  ctx.loading(`Loading team ${num}, ${season}`);
  const [S, trajF, hist] = await Promise.all([getSeason(season), getTraj(season), getHistory().catch(() => null)]);
  if (!ctx.isCurrent()) return;
  const T = tbl(S.teams);
  const ti = T.index('team').get(num);
  if (ti === undefined) { notFound(root, num, season, hist); ctx.setTitle(`Team ${num} · ${season}`); return; }
  const modelLabel = (st.manifest.models.find((m) => m.id === model) || { label: 'EPA' }).label;
  const allRows = T.rows(model);
  const row = allRows[ti];
  const region = teamRegion(row);
  const regionRows = allRows.filter((r) => matchRegion(r, region) && typeof r.epa === 'number');
  const regName = regionLabel(region);
  const complete = S.status === 'complete';
  let watchSet = new Set(watchlist.get());

  // event files for this team's events (all in parallel; a missing file just drops that row)
  const evKeys = row.events || [];
  const ctxLoad = ctx;
  ctxLoad.loading(`Loading team ${num} events`);
  const loaded = await Promise.all(evKeys.map(async (key) => {
    try { return { key, ev: await getEvent(season, key) }; } catch (e) { return { key, ev: null }; }
  }));
  if (!ctx.isCurrent()) return;
  const evRows = eventRows(S, num, model, loaded);
  const played = evRows.filter((r) => r.count > 0);

  // trajectory: subject and the region's top two others
  const tIndex = new Map(trajF.team.map((t, i) => [t, i]));
  const trajOf = (team) => {
    const i = tIndex.get(team);
    if (i === undefined) return null;
    const m = trajF.models[model];
    const ys = m.epa[i];
    const seq = trajF.seq[i];
    const ev = trajF.event[i];
    return { ys, seq, ev, n: ys.length };
  };
  const mine = trajOf(num);
  const leaders = topBy(regionRows.filter((r) => r.team !== num), (r) => r.epa, 2).filter((r) => trajOf(r.team));
  const med = medianOf(regionRows, (r) => r.epa);

  const tiles = TILES.map((t) => {
    const v = row[t.key];
    const rk = rankOf(regionRows, (r) => r[t.key], v);
    const md = medianOf(regionRows, (r) => r[t.key]);
    const d = t.key === 'epa' && row.count && typeof row.epa_start === 'number' ? row.epa - row.epa_start : null;
    return stat({
      label: t.label,
      value: rating(v),
      delta: d !== null ? { text: `${delta(d)} since season start`, dir: d < 0 ? 'down' : 'up' } : null,
      context: `${fmtRank(rk, regionRows.length)} in ${regionShort(region)} · ${regionShort(region)} median ${rating(md)}`,
      note: row.count && row.count < 6 ? `n = ${row.count} matches, treat as a guess` : '',
      act: { key: t.key },
    });
  });

  // model features (SPEC 4.6): consistency, pre-event comparison, opponent effect (experimental)
  const featBits = [];
  featBits.push(html`<span><b>Consistency:</b> ${consText(row.cons_sd, row.cons_n)} qual matches (SD of actual minus predicted contribution, points; blank below n = 4)</span>`);
  if (changeDir(row.change_z)) {
    const lev = loaded.find((l) => l.key === row.change_event);
    let k = null;
    if (lev && lev.ev) { const TE = tbl(lev.ev.team_events); const ii = TE.index('team').get(num); if (ii !== undefined) k = TE.get(ii, 'change_k', model); }
    const first = (row.events || [])[0] === row.change_event;
    const evName = (evRows.find((r) => r.key === row.change_event) || { name: row.change_event }).name;
    featBits.push(html`<span><b>Differs from pre-event:</b> ${changeCell(row.change_z, { k, event: evName, first })} at ${evName}${k ? `, first ${k} matches` : ''}, against ${first ? 'its preseason prior (first event of the season)' : 'its rating entering the event'}; threshold ${CHANGE_Z} SD of match noise.</span>`);
  }
  const oMean = seasonMean(allRows.map((r) => r.opp_effect));
  const oe = oppEffect(row, oMean);
  if (oe && oe.show) {
    featBits.push(html`<span><b>Opponent effect (experimental):</b> ${num}'s opponents scored ${rating(Math.abs(oe.adj))} points per match ${oe.adj < 0 ? 'less' : 'more'} than predicted, relative to the ${season} average (SE ${rating(oe.se)}, n = ${oe.n} quals). A harder schedule slightly inflates this number.</span>`);
  } else if (oe) {
    featBits.push(html`<span><b>Opponent effect (experimental):</b> no clear effect (${delta(oe.adj)} points against the ${season} average, SE ${rating(oe.se)}, n = ${oe.n}).</span>`);
  }
  const watched = () => watchSet.has(num);
  const lede = teamLede({ num, watched: watched(), row, regionRows, region, complete, evCount: played.length, strongest: strongestPhase(row, regionRows, region) });

  // chart series
  const subjectRole = watched() ? 'you' : 'focus';
  const series = [];
  const dataSeries = [];
  const toPts = (t) => t.ys.map((y, k) => ({ x: k + 1, y }));
  for (const l of leaders) {
    const t = trajOf(l.team);
    const pts = toPts(t);
    series.push({ id: String(l.team), label: `${l.team} · ${rating(pts[pts.length - 1].y)}`, role: 'context', points: pts });
    dataSeries.push({ id: String(l.team), label: String(l.team), points: pts });
  }
  if (mine && mine.n) {
    const pts = toPts(mine);
    series.push({ id: String(num), label: `${num} · ${rating(pts[pts.length - 1].y)}`, role: subjectRole, points: pts });
    dataSeries.unshift({ id: String(num), label: String(num), points: pts });
  }
  // event boundaries: x = matches played before the event + 0.5 (labels at each event's start)
  const annotations = [];
  if (mine && mine.n) {
    let prev = null;
    mine.ev.forEach((ei, k) => {
      if (ei !== prev) {
        const evm = S.events.cols.key[ei];
        const nm = eventShort({ key: evm, name: S.events.cols.name[ei], type: S.events.cols.type[ei], district: S.events.cols.district[ei] });
        annotations.push({ x: k === 0 ? 0.4 : k + 0.5, label: nm, line: k !== 0 });
        prev = ei;
      }
    });
  }
  const first = mine && mine.n ? (typeof row.epa_start === 'number' ? row.epa_start : mine.ys[0]) : null;
  const last = mine && mine.n ? mine.ys[mine.n - 1] : null;
  const lAvg = (f) => (leaders.length ? leaders.reduce((a, l) => a + f(trajOf(l.team)), 0) / leaders.length : null);
  const gapStart = leaders.length && first !== null ? lAvg((t) => t.ys[0]) - first : null;
  const gapEnd = leaders.length && last !== null ? lAvg((t) => t.ys[t.n - 1]) - last : null;
  const cTitle = mine && mine.n ? chartTitle({ num, region, season, evRows: played, gapStart, gapEnd, start: first, end: last }) : '';
  const ariaLabel = mine && mine.n
    ? `Line chart: ${num} ${modelLabel} went from ${rating(first)} to ${rating(last)} points per match over ${mine.n} matches in ${season}. ${leaders.length ? `${regionShort(region)} leaders ${leaders.map((l) => `${l.team} finished at ${rating(l.epa)}`).join(' and ')}. ` : ''}${regionShort(region)} median end-of-season ${modelLabel} was ${rating(med)}.`
    : '';
  const nCounts = [mine && mine.n ? `${mine.n} matches (${num})` : '', ...leaders.map((l) => `${trajOf(l.team).n} (${l.team})`)].filter(Boolean).join(', ');
  const chartHost = 'team-chart';

  const evCols = [
    { id: 'event', label: 'Event', render: (r) => html`<a class="pw-event-link" href="${router.link('event', [r.key], { model })}">${r.name}</a>` },
    { id: 'wk', label: 'Wk', n: true, render: (r) => (r.week === null || r.week === undefined ? '–' : r.meta.type === 'champs_div' || r.meta.type === 'einstein' ? '8' : r.week) },
    { id: 'start', label: 'Start', n: true, render: (r) => rating(r.start) },
    { id: 'end', label: 'End', n: true, render: (r) => rating(r.end) },
    { id: 'chg', label: 'Change', n: true, render: (r) => (r.change === null ? '–' : html`<span class="pw-delta ${r.change > 0 ? 'pw-delta--up' : r.change < 0 ? 'pw-delta--down' : ''}">${delta(r.change)}</span>`) },
    { id: 'rank', label: 'Rank', n: true, render: (r) => (typeof r.rank === 'number' ? `${r.rank}/${r.numTeams}` : '–') },
  ];

  // recent matches at the latest event with completed matches
  let recent = null;
  for (let k = loaded.length - 1; k >= 0 && !recent; k--) {
    const ev = loaded[k].ev;
    if (!ev) continue;
    const M = tbl(ev.matches);
    const idx = [];
    for (let i = 0; i < M.n; i++) {
      const winner = M.get(i, 'winner');
      if (winner === null || winner === undefined) continue;
      if ((M.get(i, 'red') || []).includes(num) || (M.get(i, 'blue') || []).includes(num)) idx.push(i);
    }
    if (idx.length) recent = { key: loaded[k].key, ev, idx: idx.slice(-4), total: idx.length };
  }
  let recentHtml = '';
  if (recent) {
    const evMeta = evRows.find((r) => r.key === recent.key);
    const facts = recent.idx.map((i) => matchFacts(recent.ev, i, model));
    const wins = facts.filter((f) => f.winner !== 'tie' && (f.winner === 'red') === f.red.includes(num)).length;
    const ties = facts.filter((f) => f.winner === 'tie').length;
    const judged = facts.filter((f) => f.called !== null);
    const called = judged.filter((f) => f.called).length;
    const allQuals = recent.idx.every((i) => tbl(recent.ev.matches).get(i, 'comp_level') === 'qm');
    const cnt = numberWord(facts.length);
    const ttl = `Last ${facts.length === 1 ? 'match' : `${cnt} ${allQuals ? 'quals' : 'matches'}`} at ${evMeta.name}: ${plural(wins, 'win')}${ties ? ` and ${plural(ties, 'tie')}` : ''}, ${modelLabel} called ${called} of ${judged.length}`;
    recentHtml = html`<section aria-labelledby="m-title" class="pw-section">
<div class="pw-section__head">
<h2 id="m-title" class="pw-h2">${ttl}</h2>
<a class="pw-btn" href="${router.link('event', [recent.key], { tab: 'matches', model })}">Matches at ${evMeta.name}</a>
</div>
<div class="pw-cards">${recent.idx.map((i) => matchCard({ ev: recent.ev, i, model, modelLabel, watch: watchSet, focusTeam: num, season, eventLabel: evMeta.name }))}</div>
<p class="pw-small">${modelLabel} predictions use ratings from before each match. Win probability is red's chance; the prediction line gives the model's score for each alliance.</p>
</section>`;
  }

  // season over season
  let seasonsHtml = '';
  if (hist) {
    const H = tbl(hist.rows);
    const hr = H.rows(model).filter((r) => r.team === num).sort((a, b) => b.season - a.season);
    if (hr.length) {
      const best = hr.slice().sort((a, b) => (b.norm_epa || 0) - (a.norm_epa || 0))[0];
      const maxEpa = Math.max(1, ...hr.map((r) => r.epa || 0));
      const sCols = [
        { id: 'season', label: 'Season', td: 'team', render: (r) => (r.season === season ? String(r.season) : html`<a href="${router.link('team', [num], { season: r.season, model })}">${r.season}</a>`) },
        { id: 'epa', label: 'EPA', n: true, render: (r) => raw(barCell(r.epa, maxEpa, 90, rating(r.epa))) },
        { id: 'auto', label: 'Auto', n: true, render: (r) => rating(r.auto_epa) },
        { id: 'teleop', label: 'Teleop', n: true, render: (r) => rating(r.teleop_epa) },
        { id: 'endgame', label: 'Endgame', n: true, render: (r) => rating(r.endgame_epa) },
        { id: 'norm', label: 'Norm EPA', n: true, render: (r) => int(r.norm_epa) },
        { id: 'rank', label: 'Rank', n: true, render: (r) => (r.district && r.district_epa_rank ? `${r.district_epa_rank}/${r.district_team_count} ${String(r.district).toUpperCase()}` : r.total_epa_rank ? `${r.total_epa_rank}/${r.total_team_count}` : '–') },
        { id: 'rec', label: 'Record', n: true, render: (r) => (r.count ? record(r.wins, r.losses, r.ties) : '–') },
      ];
      seasonsHtml = figure({
        id: 'sos', cls: 'pw-figure--flush',
        title: hr.length > 1 ? `${num}'s best season by norm EPA was ${best.season}, at ${int(best.norm_epa)}` : `${num} has one published season, ${best.season}`,
        sub: 'EPA scales differ by game, so compare norm EPA across seasons (1500 is average, 250 is one standard deviation).',
        body: html`${dataTable({ columns: sCols, rows: hr, labelledby: 'sos-t', rowClass: (r) => (r.season === season ? 'you' : '') })}`,
        caption: html`<span><b>EPA:</b> points per match at the end of each season (${modelLabel}). <b>Rank:</b> end-of-season rank among district teams, or among all teams without a district. Highlighted row is the season shown above. n = ${hr.length} seasons. Source: The Blue Alliance.</span>`,
      });
    }
  }

  const evTable = dataTable({ columns: evCols, rows: evRows, labelledby: 'ev-title' });
  const chartFig = mine && mine.n
    ? figure({
      id: 'rating-chart',
      cls: 'pw-grow',
      title: cTitle,
      sub: `${modelLabel} after each completed match, ${season} season. ${num} against ${leaders.length ? `${leaders.map((l) => l.team).join(' and ')}, the top two ${regionShort(region)} teams by final ${modelLabel}` : `the ${regionShort(region)} median`}.`,
      body: html`<div class="pw-chart" id="${chartHost}"></div>${showData(lineDataTable(dataSeries, `Match played by ${num}`))}`,
      caption: html`<b>Lines:</b> ${modelLabel} (Expected Points Added, recomputed by PitWall from TBA match data) after each match, quals and playoffs. <b>Dotted verticals:</b> ${num}'s event boundaries. <b>Dashed line:</b> median end-of-season ${modelLabel} of ${regionRows.length} ${regionShort(region)} teams. n = ${nCounts}. Source: The Blue Alliance, fetched ${dateTime(S.generated_at)}.`,
    })
    : '';

  mount(root, html`
<section class="pw-head">
<span class="pw-eyebrow">Team · ${season} season · ${regName}</span>
<div class="pw-h1-row">
<h1 class="pw-h1" tabindex="-1">${num} ${row.name || ''}</h1>
<span id="team-watch" class="pw-team-actions">${watched() ? html`<span class="pw-team pw-team--you">${num} · your team</span>` : ''}${watchButton(num, watched(), { btn: true })}</span>
</div>
<p class="pw-lede" id="team-lede">${lede}</p>
</section>
<section aria-label="Season ratings" class="pw-stats">${tiles}</section>
<p class="pw-small pw-features">${featBits}</p>
<div id="explain-host"></div>
${chartFig || row.count ? '' : ''}
<section class="pw-split">
${chartFig}
<section class="pw-figure pw-side" aria-labelledby="ev-title">
<h2 id="ev-title" class="pw-figure__title">${eventsTitle(evRows, season)}</h2>
<p class="pw-figure__sub">${modelLabel} before and after each ${season} event.</p>
${evRows.length ? evTable : html`<p class="pw-small">No events listed for ${num} in ${season}.</p>`}
<p class="pw-caption"><b>Start / End:</b> ${modelLabel} entering and leaving the event (points per match). <b>Rank:</b> qualification rank / teams at event. Season record ${record(row.wins, row.losses, row.ties)} across ${plural(row.count, 'match', 'matches')}.</p>
</section>
</section>
${recentHtml}
${seasonsHtml}`);

  // chart (redrawn when the container width changes, so tick labels stay 11px)
  let stopFit = () => {};
  const host = document.getElementById(chartHost);
  if (host) {
    stopFit = fitChart(host, (w) => lineChart({
      series,
      x: { title: `Matches played by the team in ${season} (count)`, min: 0 },
      y: { title: `${modelLabel} (points per match)` },
      refLines: [{ y: med, label: `${regionShort(region)} median ${rating(med)}` }],
      annotations,
      ariaLabel,
      width: w,
    }));
  }

  // explain-this-number
  const eventFiles = loaded.map((l) => l.ev).filter(Boolean);
  const host2 = document.getElementById('explain-host');
  let openKey = null;
  let opener = null;
  const closeExplain = (refocus) => {
    openKey = null;
    host2.innerHTML = '';
    root.querySelectorAll('[data-act="explain"]').forEach((b) => { b.setAttribute('aria-expanded', 'false'); b.removeAttribute('aria-controls'); });
    if (refocus && opener) opener.focus();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape' && openKey) { e.preventDefault(); closeExplain(true); } };
  const openExplain = (key, btn) => {
    openKey = key;
    opener = btn;
    const label = TILES.find((t) => t.key === key).label;
    mount(host2, explainPanel({ team: num, label, rows: explainRows(eventFiles, model, num, key), modelLabel }));
    root.querySelectorAll('[data-act="explain"]').forEach((b) => { const on = b.dataset.key === key; b.setAttribute('aria-expanded', String(on)); if (on) b.setAttribute('aria-controls', 'pw-explain'); else b.removeAttribute('aria-controls'); });
    const panel = host2.firstElementChild;
    if (panel) { panel.focus({ preventScroll: true }); if (matchMedia('(min-width: 641px)').matches) panel.scrollIntoView({ block: 'nearest' }); }
    document.addEventListener('keydown', onKey);
  };

  const onClick = (e) => {
    const t = e.target.closest('[data-act]');
    if (!t || !root.contains(t)) return;
    if (t.dataset.act === 'explain') { if (openKey === t.dataset.key) closeExplain(true); else openExplain(t.dataset.key, t); }
    else if (t.dataset.act === 'explain-close') closeExplain(true);
    else if (t.dataset.act === 'watch') watchlist.toggle(Number(t.dataset.team));
  };
  root.addEventListener('click', onClick);
  const unWatch = watchlist.subscribe((list) => {
    watchSet = new Set(list);
    const keep = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.act === 'watch';
    document.getElementById('team-watch').innerHTML = (watched() ? `<span class="pw-team pw-team--you">${num} · your team</span>` : '') + watchButton(num, watched(), { btn: true }).s;
    document.getElementById('team-lede').textContent = teamLede({ num, watched: watched(), row, regionRows, region, complete, evCount: played.length, strongest: strongestPhase(row, regionRows, region) });
    if (keep) { const b = root.querySelector('#team-watch [data-act="watch"]'); if (b) b.focus(); }
  });
  ctx.setTitle(`Team ${num} · ${season}`);
  return () => {
    stopFit();
    root.removeEventListener('click', onClick);
    document.removeEventListener('keydown', onKey);
    unWatch();
  };
}

export default render;

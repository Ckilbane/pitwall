// Events tab (WP4b): every event of a season by week, with status and the strength of its field.
// Route: #/events?season=&region=&week=&type=&sort=   (model comes from the shell toggle)
import { getSeason } from '../core/data.js';
import * as state from '../core/state.js';
import { mount } from '../core/html.js';
import * as regions from '../core/regions.js';
import * as csv from '../core/csv.js';
import {
  html, fmt, router, watchlist, list, scrollBox, modelLabel, andList, pageHead, DASH,
} from './event_ui.js';
import {
  tableRows, groupByWeek, weekLabel, typeLabel, dateRange, eventStatus,
} from './event_lib.js';

const SORTS = {
  week: 'By week',
  top8: 'Top 8 EPA, highest first',
  mean: 'Mean EPA, highest first',
  acc: 'Accuracy, highest first',
};

export function title(route) {
  const q = (route && route.query) || {};
  return `Events${q.season ? ` · ${q.season}` : ''}`;
}

/** Filter and sort the event rows for the given query (exported for tests). */
export function selectEvents(rows, { region = 'all', week = '', type = '', sort = 'week' } = {}) {
  let out = rows.filter((r) => regions.matchRegion(r, region));
  if (week !== '') out = out.filter((r) => (r.week == null ? 'other' : String(r.week)) === week);
  if (type) out = out.filter((r) => r.type === type);
  const cmp = {
    week: (a, b) => (a.week ?? 99) - (b.week ?? 99) || (a.start_date || '').localeCompare(b.start_date || '') || a.name.localeCompare(b.name),
    top8: (a, b) => (b.epa_top_8 ?? -1) - (a.epa_top_8 ?? -1),
    mean: (a, b) => (b.epa_mean ?? -1) - (a.epa_mean ?? -1),
    acc: (a, b) => (b.epa_acc ?? -1) - (a.epa_acc ?? -1),
  }[sort] || null;
  return cmp ? out.slice().sort(cmp) : out;
}

function statusText(r) {
  const s = eventStatus(r);
  if (s === 'complete') return 'Complete';
  if (s === 'live') return `Live, ${r.completed_count} of ${r.match_count} matches`;
  return 'Upcoming';
}

function lede({ rows, all, model, models, region, seasonNum, watchEvents }) {
  const mname = modelLabel(models, model);
  const regionName = region === 'all' ? '' : ` in ${regions.regionLabel(region)}`;
  if (!rows.length) return `No ${seasonNum} events match these filters.`;
  const withField = rows.filter((r) => r.epa_top_8 != null);
  const parts = [`${rows.length} ${seasonNum} ${rows.length === 1 ? 'event' : 'events'}${regionName}.`];
  if (withField.length) {
    const top = withField.reduce((a, b) => (b.epa_top_8 > a.epa_top_8 ? b : a));
    const med = fmt.median(withField.map((r) => r.epa_top_8));
    parts.push(`${fmt.eventShort(top)} had the strongest top 8 at ${fmt.rating(top.epa_top_8)} ${mname} points per match; the median event's top 8 is ${fmt.rating(med)}.`);
  }
  if (watchEvents.length) {
    const byTeam = new Map();
    for (const w of watchEvents) for (const t of w.teams) { if (!byTeam.has(t)) byTeam.set(t, []); byTeam.get(t).push(w.row); }
    const bits = [...byTeam.entries()].slice(0, 3).map(([t, evs]) => `${t} at ${andList(evs.slice(0, 4).map((r) => `${fmt.eventShort(r)} (${weekLabel(r.week).toLowerCase()})`))}${evs.length > 4 ? ` and ${evs.length - 4} more` : ''}`);
    parts.push(`Watchlist: ${bits.join('; ')}.`);
  }
  return parts.join(' ');
}

function eventRow(r, { model, season, watch, maxTop8, showWeek }) {
  const you = watch.has(r.key);
  const st = eventStatus(r);
  const bar = r.epa_top_8 != null && maxTop8 > 0
    ? html`<span class="pw-bar" style="width:${Math.max(1, Math.round((r.epa_top_8 / maxTop8) * 56))}px" aria-hidden="true"></span>` : '';
  const q = { model };
  return html`<tr class="${you ? 'you' : ''}">
    <td class="pwe-ev"><a href="${router.link('event', [r.key], q)}">${r.name}</a>
      <span class="pw-small pwe-ev__sub">${r.key}${you ? ` · ${andList(watch.get(r.key))}` : ''}</span></td>
    ${showWeek ? html`<td class="pw-nowrap">${weekLabel(r.week)}</td>` : ''}
    <td class="pw-nowrap">${dateRange(r.start_date, r.end_date)}</td>
    <td class="pw-nowrap">${typeLabel(r.type)}</td>
    <td class="n">${r.num_teams ?? DASH}</td>
    <td class="pw-nowrap ${st === 'live' ? 'pwe-live' : ''}">${statusText(r)}</td>
    <td class="n"><span class="pw-cell-bar">${bar}${fmt.rating(r.epa_top_8)}</span></td>
    <td class="n">${fmt.rating(r.epa_top_24)}</td>
    <td class="n">${fmt.rating(r.epa_mean)}</td>
    <td class="n">${r.epa_acc != null && r.count ? html`${fmt.pct(r.epa_acc)} <span class="pw-small">n ${r.count}</span>` : DASH}</td>
  </tr>`;
}

export async function render(ctx) {
  const { root, route } = ctx;
  const st = state.get();
  const seasonNum = st.season;
  const model = st.model;
  const models = (st.manifest && st.manifest.models) || [{ id: 'epa', label: 'EPA' }];
  ctx.loading(`Loading ${seasonNum} events`);
  const season = await getSeason(seasonNum);
  if (!ctx.isCurrent()) return undefined;

  const q = route.query;
  const region = q.region || 'all';
  const week = q.week || '';
  const type = q.type || '';
  const sort = SORTS[q.sort] ? q.sort : 'week';

  const all = tableRows(season.events, model);
  const filtered = selectEvents(all, { region, week, type, sort });

  // Events with a watchlist team (season.teams.cols.events lists each team's event keys).
  const watch = new Map();
  const tc = season.teams.cols;
  const wl = watchlist.get();
  for (let i = 0; i < season.teams.n; i++) {
    if (!wl.includes(tc.team[i])) continue;
    for (const k of tc.events[i] || []) {
      if (!watch.has(k)) watch.set(k, []);
      watch.get(k).push(tc.team[i]);
    }
  }
  const watchEvents = filtered.filter((r) => watch.has(r.key)).map((r) => ({ row: r, teams: watch.get(r.key) }));

  const weeks = [...new Set(all.map((r) => (r.week == null ? 'other' : String(r.week))))]
    .sort((a, b) => (a === 'other' ? 99 : +a) - (b === 'other' ? 99 : +b));
  const types = [...new Set(all.map((r) => r.type))].sort((a, b) => typeLabel(a).localeCompare(typeLabel(b)));
  const regionOpts = regions.regionOptions(season).map((o) => (o.id === 'all' ? { ...o, label: 'All events' } : o));
  const maxTop8 = Math.max(0, ...filtered.map((r) => r.epa_top_8 ?? 0));
  const grouped = sort === 'week';
  const colCount = grouped ? 9 : 10;
  const opt = (id, lab, cur) => html`<option value="${id}" ${String(id) === String(cur) ? 'selected' : ''}>${lab}</option>`;

  const head = (txt, n) => html`<th scope="col" class="${n ? 'n' : ''}">${txt}</th>`;
  const body = grouped
    ? list(groupByWeek(filtered).map((g) => html`<tbody>
        <tr class="pwe-weekrow"><th scope="colgroup" colspan="${colCount}">${weekLabel(g.week)}
          <span class="pw-small"> · ${dateRange(g.rows[0].start_date, g.rows[g.rows.length - 1].end_date)} · ${g.rows.length} ${g.rows.length === 1 ? 'event' : 'events'}</span></th></tr>
        ${list(g.rows.map((r) => eventRow(r, { model, season: seasonNum, watch, maxTop8, showWeek: false })))}
      </tbody>`))
    : html`<tbody>${list(filtered.map((r) => eventRow(r, { model, season: seasonNum, watch, maxTop8, showWeek: true })))}</tbody>`;

  const ledeText = lede({ rows: filtered, all, model, models, region, seasonNum, watchEvents });
  const mname = modelLabel(models, model);

  const out = html`
    ${pageHead({
      eyebrow: `Events · ${seasonNum} season · ${region === 'all' ? 'all regions' : regions.regionLabel(region)}`,
      title: `${seasonNum} events`,
      lede: ledeText,
    })}
    <section class="pw-filters" aria-label="Filters">
      <div class="pw-filters__group">
        <label class="pw-field"><span class="pw-eyebrow">Region</span>
          <select class="pw-select" id="ev-region" data-filter="region">${list(regionOpts.map((o) => opt(o.id, o.label, region)))}</select></label>
        <label class="pw-field"><span class="pw-eyebrow">Week</span>
          <select class="pw-select" id="ev-week" data-filter="week">${opt('', 'All weeks', week)}${list(weeks.map((w) => opt(w, w === 'other' ? 'Other events' : weekLabel(+w), week)))}</select></label>
        <label class="pw-field"><span class="pw-eyebrow">Type</span>
          <select class="pw-select" id="ev-type" data-filter="type">${opt('', 'All types', type)}${list(types.map((t) => opt(t, typeLabel(t), type)))}</select></label>
        <label class="pw-field"><span class="pw-eyebrow">Sort</span>
          <select class="pw-select" id="ev-sort" data-filter="sort">${list(Object.entries(SORTS).map(([id, lab]) => opt(id, lab, sort)))}</select></label>
      </div>
      <button type="button" class="pw-btn" data-act="csv" id="ev-csv">Download CSV</button>
    </section>
    ${filtered.length ? html`<section class="pw-section" aria-labelledby="ev-table-h">
      <h2 class="pw-h2" id="ev-table-h">Events by ${grouped ? 'week' : SORTS[sort].replace(/, highest first$/, '').toLowerCase()}</h2>
      ${scrollBox(html`<table class="pw-table pwe-table">
        <caption class="pwe-sr">${seasonNum} events with the ${mname} rating of each field</caption>
        <thead><tr>
          ${head('Event')}${grouped ? '' : head('Week')}${head('Dates')}${head('Type')}${head('Teams', 1)}${head('Status')}
          ${head(`Top 8 ${mname}`, 1)}${head('Top 24', 1)}${head('Mean', 1)}${head(`${mname} called`, 1)}
        </tr></thead>
        ${body}
      </table>`, `${seasonNum} events`)}
      <p class="pw-caption"><b>Top 8, top 24, mean:</b> ${mname} rating (points per match) of the event's highest-rated teams and of the whole field,
        from ratings at the end of the event so far. <b>Called:</b> share of the event's matches where the favoured alliance won, with the number of matches
        (n). Amber rows have a team from your watchlist. ${season.generated_at ? `Data updated ${fmt.dateTime(season.generated_at)}.` : ''}
        Source: The Blue Alliance; ${mname} model.</p>
    </section>` : ''}`;
  mount(root, out);
  ctx.setTitle(`Events · ${seasonNum}`);

  const onChange = (e) => {
    const el = e.target.closest('[data-filter]');
    if (!el) return;
    const next = { ...route.query, [el.dataset.filter]: el.value };
    if (el.dataset.filter === 'sort' && el.value === 'week') delete next.sort;
    for (const k of Object.keys(next)) if (next[k] === '') delete next[k];
    router.navigate('events', [], next);
  };
  const onClick = (e) => {
    const b = e.target.closest('[data-act="csv"]');
    if (!b) return;
    const cols = [
      { key: 'key', label: 'Event key' }, { key: 'name', label: 'Event' }, { key: 'weekName', label: 'Week' },
      { key: 'typeName', label: 'Type' }, { key: 'start_date', label: 'Start' }, { key: 'end_date', label: 'End' },
      { key: 'num_teams', label: 'Teams' }, { key: 'statusName', label: 'Status' },
      { key: 'epa_top_8', label: `Top 8 ${mname} (points)` }, { key: 'epa_top_24', label: `Top 24 ${mname} (points)` },
      { key: 'epa_mean', label: `Mean ${mname} (points)` }, { key: 'count', label: 'Matches scored' }, { key: 'epa_acc', label: `${mname} called (share)` },
    ];
    const data = filtered.map((r) => ({
      ...r, weekName: weekLabel(r.week), typeName: typeLabel(r.type), statusName: statusText(r),
      epa_top_8: fmt.round(r.epa_top_8), epa_top_24: fmt.round(r.epa_top_24), epa_mean: fmt.round(r.epa_mean),
      epa_acc: r.epa_acc == null ? null : Number(r.epa_acc.toFixed(3)),
    }));
    csv.download(csv.csvFilename({ view: 'events', season: seasonNum, region, asof: week ? `week-${week}` : '' }), csv.toCSV(cols, data));
  };
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  return () => { root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); };
}

export default render;

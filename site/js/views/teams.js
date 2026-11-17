// Teams tab (SPEC 5.6): lede, filters, ratings table with bars, CSV. Layout from
// docs/design/template-teams-table.dc.html.
import { html, raw, esc, mount } from '../core/html.js';
import * as router from '../core/router.js';
import * as state from '../core/state.js';
import { getSeason, getAsof, getTraj, tbl } from '../core/data.js';
import { rating, rp, pct, int, rank as fmtRank, record, compLabel, vectorLabel, unit, dateTime, delta } from '../core/fmt.js';
import { matchRegion, regionOptions, regionLabel, regionShort } from '../core/regions.js';
import { rankOf, medianOf } from '../core/stats.js';
import { dataTable, sortRows } from '../components/table.js';
import { seg } from '../components/seg.js';
import { watchButton } from '../components/team.js';
import { barCell } from '../charts/bars.js';
import { spark } from '../charts/spark.js';
import * as watchlist from '../core/watchlist.js';
import { toCSV, csvFilename, download } from '../core/csv.js';
import { changeCell, changeDir, changeCaption, consText, CONS_MIN_N } from '../core/features.js';
import { asofControl, asofOptions, readAsof, normalizeSeasonAsof, seasonAsofIndex } from '../components/asof.js';
import { rangeOf, tiers as makeTiers, tierBlurb } from '../core/ranges.js';
import { rangeScale, rangeCell, rangeAxis } from '../charts/range.js';

const PAGE = 100;
const SETS = [
  { id: 'ratings', label: 'Ratings' },
  { id: 'components', label: 'Components' },
  { id: 'rp', label: 'Ranking points' },
  { id: 'robot', label: 'Robot' },
];

// ---- data shaping (exported for tests) ----

/** Rows of the season teams table for a region, with as-of ratings applied and a rank by total EPA. */
export function buildRows(S, A, { model, region, asof }) {
  const T = tbl(S.teams);
  const base = T.rows(model);
  const idx = A ? seasonAsofIndex(asof) : 8;
  const asofMode = !!A && normalizeSeasonAsof(asof) !== 'end';
  const am = A && A.models && A.models[model];
  let rows = [];
  for (const r of base) {
    if (!matchRegion(r, region)) continue;
    let row = r;
    if (asofMode && am) {
      row = { ...r };
      for (const k of ['epa', 'auto_epa', 'teleop_epa', 'endgame_epa', 'epa_se']) {
        const c = am[k] && am[k][r._i];
        row[k] = c ? c[idx] : null;
      }
    }
    rows.push(row);
  }
  rows = rows.filter((r) => typeof r.epa === 'number');
  const order = rows.slice().sort((a, b) => b.epa - a.epa || a.team - b.team);
  const out = order.map((r, i) => ({ ...r, rank: i + 1 }));
  // ties share a rank
  for (let i = 1; i < out.length; i++) if (out[i].epa === out[i - 1].epa) out[i].rank = out[i - 1].rank;
  return { rows: out, asofMode };
}

const ordinalList = (nums) => (nums.length === 1 ? String(nums[0]) : nums.length === 2 ? `${nums[0]} and ${nums[1]}` : `${nums.slice(0, -1).join(', ')} and ${nums[nums.length - 1]}`);

/** The decision sentence: who leads, where the watchlist teams stand (SPEC 5.6). */
/** "1218 is in tier 2 with 2607, 2539 and 365, too close to separate." (null when not useful) */
export function tierSentence(tierList, watch) {
  for (const t of tierList) {
    const mine = t.rows.find((r) => watch.includes(r.team));
    if (!mine) continue;
    const others = t.rows.filter((r) => r !== mine).map((r) => r.team);
    if (!others.length) return `${mine.team} is alone in tier ${t.n}: no team is too close to call against it.`;
    if (others.length > 6) return `${mine.team} is in tier ${t.n} with ${others.length} other teams that EPA can't separate.`;
    return `${mine.team} is in tier ${t.n} with ${ordinalList(others)}: EPA can't separate them, so scout these first.`;
  }
  return null;
}

export function teamsLede({ rows, region, status, asof, asofLabel, watch, modelLabel }) {
  const reg = regionShort(region);
  if (!rows.length) return `No teams in ${reg === 'all teams' ? 'this season' : reg} have a rating for this view.`;
  const complete = status === 'complete' && normalizeSeasonAsof(asof) === 'end';
  const leader = rows[0];
  const when = normalizeSeasonAsof(asof) === 'end' ? '' : normalizeSeasonAsof(asof) === 'w0' ? ' before the first match' : ` ${String(asofLabel || '').toLowerCase()}`;
  const tail = modelLabel !== 'EPA' ? ` by ${modelLabel}` : '';
  let s = `${leader.team} ${complete ? 'led' : 'leads'} ${reg}${when} at ${rating(leader.epa)} points per match${tail}.`;
  const verb = complete ? 'finished' : (normalizeSeasonAsof(asof) === 'end' ? 'is' : 'was');
  const mine = rows.filter((r) => watch.includes(r.team) && r.team !== leader.team).slice(0, 2);
  if (mine.length) {
    const first = mine[0];
    let t = `${first.team} ${verb} ${fmtRank(first.rank, rows.length)}`;
    const ahead = rows.filter((r) => r.auto_epa > first.auto_epa).map((r) => r.team);
    if (typeof first.auto_epa === 'number' && ahead.length <= 3 && first.rank > 1) {
      t += ahead.length === 0 ? ', and no team scored more in auto' : `, and only ${ordinalList(ahead)} scored more in auto`;
    }
    if (mine[1]) t += `. ${mine[1].team} ${verb} ${fmtRank(mine[1].rank, rows.length)}`;
    s += ` ${t}.`;
  } else {
    s += ` The median team ${complete ? 'scored' : 'scores'} ${rating(medianOf(rows, (r) => r.epa))}.`;
  }
  return s;
}

/** Column definitions for a column set. */
export function columnsFor(S, set, { asofMode, traj, model, rowsAll, view = 'list' }) {
  const keys = S.keys;
  const rscale = view === 'tiers' ? rangeScale(rowsAll) : null;
  const has = (vk) => keys.vector.includes(vk);
  const maxEpa = Math.max(1, ...rowsAll.map((r) => r.epa));
  const cols = [];
  cols.push({ id: 'rank', label: '#', n: true, sortable: true, sticky: 'a', get: (r) => r.rank, render: (r) => r.rank, csv: 'Rank' });
  cols.push({ id: 'watch', label: 'Watch', sortable: false, noCsv: true, render: (r, i, ctx) => watchButton(r.team, ctx.watch.has(r.team)) });
  cols.push({ id: 'team', label: 'Team', td: 'team', sortable: true, sticky: 'b', get: (r) => r.team, render: (r) => html`<a href="${router.link('team', [r.team], { season: S.season, model })}">${r.team}</a>`, csv: 'Team' });
  cols.push({ id: 'name', label: 'Name', sortable: true, get: (r) => r.name || '', render: (r) => r.name || '', csv: 'Name' });
  const epaCol = {
    id: 'epa', label: 'EPA', n: true, sortable: true, get: (r) => r.epa, csv: 'EPA (points per match)', csvValue: (r) => round1(r.epa),
    render: (r) => html`<span class="pw-cell-bar">${!asofMode && r.count < 6 ? html`<span class="pw-small">n = ${r.count}</span>` : ''}${raw(barCell(r.epa, maxEpa, 120, rating(r.epa)))}</span>`,
  };
  const RW = (typeof window !== 'undefined' && window.innerWidth < 600) ? 150 : 240;
  const rangeBarCol = rscale ? {
    id: 'rangebar', label: RW < 200 ? 'Range' : 'Where they could really be', thClass: 'pw-th-range', sortable: false, noCsv: true,
    headExtra: raw(rangeAxis(rscale, RW)),
    render: (r, i, ctx) => raw(rangeCell(r, rscale, { you: ctx && ctx.watch && ctx.watch.has(r.team), width: RW })),
  } : null;
  if (rscale) {
    for (const c of cols) if (c.id === 'watch' || c.id === 'name') c.cls = 'pw-hide-narrow';
    epaCol.render = (r) => html`<span class="pw-cell-bar">${!asofMode && r.count < 6 ? html`<span class="pw-small">n = ${r.count}</span>` : ''}${rating(r.epa)}</span>`;
  }
  const rangeCol = {
    id: 'range', label: 'Likely range', n: true, sortable: false,
    get: (r) => rangeOf(r.epa, r.epa_se),
    render: (r) => { const g = rangeOf(r.epa, r.epa_se); return g ? `${rating(g.lo, 0)}–${rating(g.hi, 0)}` : '–'; },
    csv: 'Likely range low (points per match)', csvValue: (r) => { const g = rangeOf(r.epa, r.epa_se); return g ? round1(g.lo) : null; },
  };
  const rangeHi = { id: 'range_hi', label: 'Range high', noShow: true, get: (r) => (rangeOf(r.epa, r.epa_se) || {}).hi, render: () => '', csv: 'Likely range high (points per match)', csvValue: (r) => { const g = rangeOf(r.epa, r.epa_se); return g ? round1(g.hi) : null; } };
  const num = (id, label, key, csvLabel, f = rating, nd = 1) => ({ id, label, n: true, sortable: true, get: (r) => r[key], render: (r) => f(r[key]), csv: csvLabel || `${label} (points per match)`, csvValue: (r) => (typeof r[key] === 'number' ? Number(r[key].toFixed(nd)) : null) });
  if (set === 'ratings') {
    cols.push(epaCol, ...(rangeBarCol ? [rangeBarCol] : []), rangeCol, rangeHi, num('auto', 'Auto', 'auto_epa'), num('teleop', 'Teleop', 'teleop_epa'), num('endgame', 'Endgame', 'endgame_epa'));
    if (!asofMode) {
      cols.push({ id: 'norm', label: 'Norm EPA', n: true, sortable: true, get: (r) => r.norm_epa, render: (r) => int(r.norm_epa), csv: 'Norm EPA (1500 = average)', csvValue: (r) => r.norm_epa });
      cols.push({ id: 'record', label: 'Record', n: true, sortable: true, get: (r) => (r.count ? r.winrate : null), render: (r) => (r.count ? record(r.wins, r.losses, r.ties) : '–'), csv: 'Record (W-L-T)', csvValue: (r) => (r.count ? record(r.wins, r.losses, r.ties) : '') });
      cols.push({
        id: 'cons', label: 'Consistency', n: true, sortable: true, get: (r) => (r.cons_n >= CONS_MIN_N ? r.cons_sd : null),
        render: (r) => {
          const sp = traj && traj.has(r.team) ? raw(spark(traj.get(r.team))) : '';
          return html`<span class="pw-cell-bar">${consText(r.cons_sd, r.cons_n)}${sp}</span>`;
        },
        csv: 'Consistency (SD of match residuals, points)', csvValue: (r) => (r.cons_n >= CONS_MIN_N ? round1(r.cons_sd) : null),
      });
      cols.push({ id: 'cons_n', label: 'Cons. n', n: true, sortable: false, noShow: true, get: (r) => r.cons_n, render: (r) => r.cons_n, csv: 'Consistency n (qual matches)', csvValue: (r) => r.cons_n });
      cols.push({
        id: 'change', label: 'Differs from pre-event', thClass: 'pw-th-wrap', sortable: true, get: (r) => (changeDir(r.change_z) ? Math.abs(r.change_z) : null),
        render: (r) => changeCell(r.change_z, { event: r.change_event, first: (r.events || [])[0] === r.change_event }),
        csv: 'Differs from pre-event rating (z, SD of match noise)', csvValue: (r) => (typeof r.change_z === 'number' ? Number(r.change_z.toFixed(2)) : null),
      });
      cols.push({ id: 'change_event', label: 'At event', noShow: true, get: (r) => r.change_event, render: () => '', csv: 'Event of that comparison', csvValue: (r) => r.change_event || '' });
    }
  } else if (set === 'components') {
    cols.push(epaCol);
    for (const vk of keys.vector.filter((k) => /^comp_\d+_epa$/.test(k))) {
      const u = unit(vk, keys);
      const label = vectorLabel(vk, keys);
      cols.push({ id: vk, label: u === 'count' ? `${label} (count)` : label, n: true, sortable: true, get: (r) => r[vk], render: (r) => rating(r[vk]), csv: `${label} (${u === 'count' ? 'count' : 'points'} per match)`, csvValue: (r) => round1(r[vk]) });
    }
  } else if (set === 'rp') {
    cols.push(epaCol);
    for (const vk of ['rp_1_epa', 'rp_2_epa', 'rp_3_epa'].filter(has)) {
      const label = vectorLabel(vk, keys);
      cols.push({ id: vk, label, n: true, sortable: true, get: (r) => r[vk], render: (r) => rp(r[vk]), csv: `${label} (RP per match)`, csvValue: (r) => (typeof r[vk] === 'number' ? Number(r[vk].toFixed(2)) : null) });
    }
    if (has('tiebreaker_epa')) cols.push(num('tiebreaker_epa', 'Tiebreaker', 'tiebreaker_epa', 'Tiebreaker EPA (points per match)'));
  } else if (set === 'robot') {
    cols.push(epaCol);
    (S.robot.fields || []).forEach((f, fi) => {
      if (!f.active) return;
      f.codes.forEach((code, ci) => {
        const label = `${compLabel(f.key.replace(/([a-z])([A-Z])/g, '$1_$2'))}: ${code}`;
        const rate = (r) => {
          const c = S.robot.counts[f.key] && S.robot.counts[f.key][r._i];
          if (!c) return null;
          const tot = c.reduce((a, b) => a + b, 0);
          return tot ? c[ci] / tot : null;
        };
        cols.push({ id: `robot:${f.key}:${code}`, label, n: true, sortable: true, get: rate, render: (r) => pct(rate(r)), csv: `${label} (share of matches, %)`, csvValue: (r) => { const v = rate(r); return v === null ? null : Math.round(v * 100); } });
      });
    });
  }
  return cols;
}

const round1 = (x) => (typeof x === 'number' ? Number(x.toFixed(1)) : null);

export function parseSort(s, cols) {
  const raw0 = String(s || '-epa');
  const dir = raw0.startsWith('-') ? -1 : 1;
  const id = raw0.replace(/^-/, '');
  if (cols.some((c) => c.id === id && c.sortable)) return { col: id, dir };
  return { col: 'epa', dir: -1 };
}

export function sortKey(sort) { return `${sort.dir < 0 ? '-' : ''}${sort.col}`; }

/** Text filter: team number prefix/contains or name contains. */
export function filterRows(rows, q) {
  const s = String(q || '').trim().toLowerCase();
  if (!s) return rows;
  return rows.filter((r) => String(r.team).includes(s) || String(r.name || '').toLowerCase().includes(s));
}

export function sortedRows(rows, cols, sort) {
  const col = cols.find((c) => c.id === sort.col);
  const g = col.get || ((r) => r.rank);
  // default tie order: team number ascending
  const base = rows.slice().sort((a, b) => a.team - b.team);
  return sortRows(base, g, sort.dir);
}

export function csvFor(cols, rows) {
  const cc = cols.filter((c) => !c.noCsv).map((c) => ({ key: c.id, label: c.csv || c.label, value: (r) => (c.csvValue ? c.csvValue(r) : c.get ? c.get(r) : r[c.id]) }));
  return toCSV(cc, rows);
}

// ---- view ----

export function title(route) { return `Teams · ${route.query.season || ''}`; }

export async function render(ctx) {
  const { root, route } = ctx;
  const q = route.query;
  const st = state.get();
  const season = st.season;
  const model = st.model;
  ctx.loading(`Loading ${season} teams`);
  const S = await getSeason(season);
  const asofId = normalizeSeasonAsof(q.asof || 'end');
  const A = asofId !== 'end' ? await getAsof(season) : null;
  if (!ctx.isCurrent()) return;
  const manifest = st.manifest;
  const modelLabel = (manifest.models.find((m) => m.id === model) || { label: 'EPA' }).label;
  const region = q.region || manifest.default_region || 'all';
  const set = SETS.some((s) => s.id === q.cols) ? q.cols : 'ratings';
  const view = q.view === 'list' ? 'list' : 'tiers';
  const asofOpts = asofOptions(S);
  const asofLabel = (asofOpts.find((o) => o.id === asofId) || { label: 'End of season' }).label;
  const live = S.status === 'live';
  const { rows: allRows, asofMode } = buildRows(S, A, { model, region, asof: asofId });
  const effSet = asofMode ? 'ratings' : set; // components, RP and robot columns exist only at the end of the season
  const robotOk = (S.robot.fields || []).some((f) => f.active);
  const regionOpts = regionOptions(S);
  if (!regionOpts.some((o) => o.id === region)) regionOpts.unshift({ id: region, label: regionLabel(region), n: 0 });
  let traj = null; // Map team -> last residuals
  let showAll = false;
  let query = q.q || '';
  let sort = null;
  let watchSet = new Set(watchlist.get());

  const eyebrowWhen = asofMode ? asofLabel.toLowerCase() : (live ? 'latest data' : 'end of season');
  const regName = regionLabel(region);
  const h1 = `${region === 'all' ? 'All' : regName} teams by ${modelLabel}`;
  const tierList = makeTiers(allRows);
  const tierOf = new Map();
  tierList.forEach((t) => t.rows.forEach((r, k) => tierOf.set(r.team, { t, first: k === 0 })));
  const tierLine = effSet === 'ratings' ? tierSentence(tierList, [...watchSet]) : null;
  const lede = teamsLede({ rows: allRows, region, status: S.status, asof: asofId, asofLabel, watch: [...watchSet], modelLabel }) + (tierLine ? ` ${tierLine}` : '');

  mount(root, html`
<section class="pw-head">
<span class="pw-eyebrow">Teams · ${season} season · ${eyebrowWhen}</span>
<h1 class="pw-h1" tabindex="-1">${h1}</h1>
<p class="pw-lede">${lede}</p>
</section>
<section class="pw-filters" aria-label="Filters">
<div class="pw-filters__group">
<label class="pw-field"><span class="pw-eyebrow">Region</span>
<select class="pw-select" id="teams-region" data-act="region">${regionOpts.map((o) => html`<option value="${o.id}"${o.id === region ? html` selected` : ''}>${o.label}</option>`)}</select></label>
${asofControl({ scope: 'season', value: asofId, options: asofOpts, id: 'teams-asof' })}
${effSet === 'ratings' ? seg({ label: 'Layout', act: 'view', value: view, options: [{ id: 'tiers', label: 'Tiers' }, { id: 'list', label: 'Ranked list' }] }) : ''}
${seg({ label: 'Columns', act: 'cols', value: effSet, options: SETS.map((s) => ({ ...s, disabled: asofMode ? s.id !== 'ratings' : (s.id === 'robot' && !robotOk) })) })}
</div>
<div class="pw-filters__group">
<input class="pw-input" id="teams-q" type="search" placeholder="Filter teams" aria-label="Filter teams" value="${query}" style="width: 200px">
<button class="pw-btn" type="button" data-act="csv" id="teams-csv">Download CSV</button>
</div>
</section>
${asofMode ? html`<p class="pw-small" role="note">Components, ranking points and robot columns, norm EPA, records and consistency exist only for the end of the season, so they are hidden here. Ranks are recomputed among ${regName} teams as of ${asofLabel.toLowerCase()}.</p>` : ''}
<figure class="pw-figure pw-figure--flush" id="teams-fig" aria-labelledby="teams-fig-t"></figure>`);
  const fig = root.querySelector('#teams-fig');

  const effView = effSet === 'ratings' ? view : 'list';
  const cols = () => columnsFor(S, effSet, { asofMode, traj, model, rowsAll: allRows, view: effView });

  function paint() {
    const columns = cols();
    sort = sort || parseSort(q.sort, columns);
    const filtered = filterRows(allRows, query);
    const sorted = sortedRows(filtered, columns, sort);
    const shown = showAll || query ? sorted : sorted.slice(0, PAGE);
    const n = allRows.length;
    const sortCol = columns.find((c) => c.id === sort.col);
    const byEpa = sort.col === 'epa' && sort.dir < 0;
    const ttl = query
      ? `${filtered.length} of ${n} ${regionShort(region)} teams match “${query}”`
      : shown.length < n ? (byEpa ? `Top ${shown.length} of ${n} ${regionShort(region)} teams, ${season}` : `${shown.length} of ${n} ${regionShort(region)} teams, ${season}`)
        : `All ${n} ${regionShort(region)} teams, ${season}`;
    const hasCons = columns.some((c) => c.id === 'cons');
    const hasChange = columns.some((c) => c.id === 'change');
    const grouped = effView === 'tiers' && byEpa && !query;
    const table = dataTable({
      columns: columns.filter((c) => !c.noShow).map((c) => ({ ...c, render: (r, i) => c.render(r, i, { watch: watchSet }) })),
      rows: shown, sort, labelledby: 'teams-fig-t',
      groupRow: grouped ? (r, i) => {
        const e = tierOf.get(r.team);
        if (!e || !(e.first || i === 0)) return null;
        const next = tierList[e.t.n];
        return html`<span class="pw-tier__name">Tier ${e.t.n}</span><span class="pw-tier__blurb">${tierBlurb(e.t, next)}${e.first ? '' : ' (continued)'}</span>`;
      } : null,
      rowClass: (r) => (watchSet.has(r.team) ? 'you' : ''),
      rowAttrs: (r) => `data-team="${r.team}"`,
    });
    const defs = [
      html`<b>EPA:</b> Expected Points Added (${modelLabel}, recomputed by PitWall from TBA match data), points per match.`,
      !asofMode && effSet === 'ratings' ? html` <b>Norm EPA:</b> EPA rescaled so 1500 is average across all teams worldwide; comparable across seasons. <b>Record:</b> wins-losses-ties, all ${season} matches.` : '',
      !asofMode && hasCons ? html` <b>Consistency:</b> SD of match residuals (actual minus predicted, points) over n qual matches; blank below n = 4; bars show the last 12 residuals.` : '',
      hasChange ? html` <b>Differs from pre-event:</b> ${changeCaption()}` : '',
      effView === 'tiers'
        ? html` <b>Range bars:</b> the dot is the best estimate; the band fades out across the likely range, where the team's true level sits about 95% of the time (checked on 2025 and 2026 matches the model was not tuned on). Same scale on every row. <b>Tiers:</b> a new tier starts when its first team is at least 90% likely to be below the team at the top of the tier above; within a tier the order means little. <b>n = k:</b> rating rests on fewer than 6 matches.`
        : html` <b>Bars:</b> EPA, same scale on every row. <b>Likely range:</b> where the team's true level sits about 95% of the time. <b>n = k:</b> rating rests on fewer than 6 matches, treat as a guess.`,
      html` n = ${n} ${regionShort(region)} teams. CSV downloads all ${filtered.length} rows in this view. Source: The Blue Alliance, fetched ${dateTime(S.generated_at)}.`,
    ];
    mount(fig, html`<div class="pw-figure__pad">
<h2 class="pw-figure__title" id="teams-fig-t">${ttl}</h2>
<p class="pw-figure__sub">${grouped ? `Grouped into ${tierList.length} tiers by how far apart the ranges are. ` : ''}Sorted by ${sortCol.label}, ${sort.dir < 0 ? 'highest' : 'lowest'} first. ${effSet === 'ratings' ? `Ratings are ${modelLabel} in points per match.` : ''} Click a column to sort.</p>
</div>
${table}
<figcaption class="pw-caption pw-caption--row"><span>${defs}</span>${shown.length < filtered.length ? html`<button type="button" class="pw-btn" data-act="show-all">Show all ${filtered.length}</button>` : ''}</figcaption>`);
  }
  paint();

  // Sparklines come from the trajectory shard; fill them in once it arrives.
  if (effSet === 'ratings' && !asofMode) {
    getTraj(season).then((T) => {
      if (!ctx.isCurrent()) return;
      const col = T.models[model] && T.models[model].resid;
      if (!col) return;
      traj = new Map();
      T.team.forEach((t, i) => { const v = (col[i] || []).filter((x) => typeof x === 'number'); if (v.length) traj.set(t, v.slice(-12)); });
      const ae = document.activeElement;
      const keep = ae && ae.dataset && ae.dataset.team && ae.dataset.act === 'watch' ? ae.dataset.team : null;
      paint();
      if (keep) { const b = fig.querySelector(`[data-act="watch"][data-team="${keep}"]`); if (b) b.focus(); }
    }).catch(() => {});
  }

  const onClick = (e) => {
    const t = e.target.closest('[data-act]');
    if (!t || !root.contains(t)) return;
    const act = t.dataset.act;
    if (act === 'sort') {
      const id = t.dataset.col;
      const dir = sort.col === id ? -sort.dir : (['rank', 'team', 'name'].includes(id) ? 1 : -1);
      sort = { col: id, dir };
      state.setQuery({ sort: sortKey(sort) === '-epa' ? null : sortKey(sort) }, { silent: true });
      paint();
      const b = fig.querySelector(`[data-act="sort"][data-col="${id}"]`);
      if (b) b.focus();
    } else if (act === 'watch') {
      watchlist.toggle(Number(t.dataset.team));
    } else if (act === 'view') {
      state.setQuery({ view: t.dataset.value === 'tiers' ? null : t.dataset.value }, { replace: true });
    } else if (act === 'cols') {
      state.setQuery({ cols: t.dataset.value === 'ratings' ? null : t.dataset.value, sort: null }, { replace: true });
    } else if (act === 'show-all') {
      showAll = true;
      paint();
      const b = fig.querySelector('.pw-table a, .pw-sort'); if (b) b.focus();
    } else if (act === 'csv') {
      const columns = cols();
      const filtered = filterRows(allRows, query);
      const rows = sortedRows(filtered, columns, sort || parseSort(q.sort, columns));
      download(csvFilename({ view: 'teams', season, region, asof: asofMode ? asofId : undefined }), csvFor(columns, rows));
    }
  };
  const onChange = (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.act === 'region') state.setQuery({ region: t.value, sort: null });
    else if (t.dataset && t.dataset.act === 'asof') {
      const v = readAsof(t);
      state.setQuery({ asof: v === 'end' ? null : v, cols: null, sort: null });
    }
  };
  const onInput = (e) => {
    if (e.target.id === 'teams-q') {
      query = e.target.value;
      state.setQuery({ q: query.trim() || null }, { silent: true });
      paint();
    }
  };
  const unWatch = watchlist.subscribe((list) => { watchSet = new Set(list); const ae = document.activeElement; const keep = ae && ae.dataset && ae.dataset.act === 'watch' ? ae.dataset.team : null; paint(); if (keep) { const b = fig.querySelector(`[data-act="watch"][data-team="${keep}"]`); if (b) b.focus(); } });
  root.addEventListener('click', onClick);
  root.addEventListener('change', onChange);
  root.addEventListener('input', onInput);
  ctx.setTitle(`${regName} teams · ${season}`);
  return () => {
    root.removeEventListener('click', onClick);
    root.removeEventListener('change', onChange);
    root.removeEventListener('input', onInput);
    unWatch();
  };
}

export default render;

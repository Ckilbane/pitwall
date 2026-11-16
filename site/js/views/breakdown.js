// Breakdown (WP5, SPEC 5.6): one team's rating vector with the season's component names: value,
// rank in a region, region median, world p90/p75/p25 (year percentiles).
// Route: #/breakdown?season=&model=&team=&region=
import { html, raw, mount } from '../core/html.js';
import * as state from '../core/state.js';
import * as router from '../core/router.js';
import * as watchlist from '../core/watchlist.js';
import { getSeason, tbl } from '../core/data.js';
import { rating, rank as fmtRank, vectorLabel, unit, median, dateTime } from '../core/fmt.js';
import { matchRegion, regionOptions, regionShort, regionLabel, teamRegion } from '../core/regions.js';
import { toCSV, csvFilename, download } from '../core/csv.js';
import { pageHead, note } from '../components/page.js';
import { figure } from '../components/figure.js';
import { dataTable } from '../components/table.js';
import { watchButton } from '../components/team.js';
import { rangeStrip } from '../charts/strip.js';

const dec = (vk, keys) => (unit(vk, keys) === 'RP score' ? 2 : 1);

/** Rows of the breakdown table for team row i (exported for tests). */
export function computeRows(S, model, i, region) {
  const T = tbl(S.teams);
  const rows = T.rows(model);
  const members = rows.filter((r) => r.count > 0 && matchRegion(r, region));
  const pct = ((S.year.models[model] || {}).percentiles) || {};
  const me = rows[i];
  return S.keys.vector.map((vk) => {
    const v = me[vk];
    const vals = members.map((r) => r[vk]).filter((x) => typeof x === 'number');
    const inside = members.includes(me);
    const p = pct[vk] || {};
    return {
      vk, label: vectorLabel(vk, S.keys), unit: unit(vk, S.keys), dec: dec(vk, S.keys), value: v,
      rank: typeof v === 'number' && inside ? 1 + vals.filter((x) => x > v).length : null, of: vals.length,
      median: median(vals), p99: p.p99 ?? null, p90: p.p90 ?? null, p75: p.p75 ?? null, p25: p.p25 ?? null,
    };
  });
}

export async function render(ctx) {
  const { root, route } = ctx;
  const st = state.get();
  const { season, model } = st;
  ctx.loading(`Loading ${season} teams`);
  const S = await getSeason(season);
  if (!ctx.isCurrent()) return undefined;
  const T = tbl(S.teams);
  const idx = T.index('team');
  const q = route.query;
  const modelName = (st.manifest.models.find((m) => m.id === model) || { label: 'EPA' }).label;
  let notice = '';
  let team = parseInt(q.team, 10);
  if (!idx.has(team)) {
    if (q.team) notice = `No ${season} rating for team ${q.team}; showing another team.`;
    const wl = watchlist.get().filter((t) => idx.has(t));
    team = wl[0] || T.rows(model).filter((r) => r.count > 0).sort((a, b) => b.epa - a.epa)[0].team;
  }
  const i = idx.get(team);
  const row = T.rows(model)[i];
  const region = q.region || teamRegion(row);
  const rs = regionShort(region);
  const rows = computeRows(S, model, i, region);
  const total = rows[0];
  const scored = rows.filter((r) => r.vk !== 'tiebreaker_epa' && r.median !== null && typeof r.value === 'number' && r.rank !== null);
  const above = scored.filter((r) => r.value > r.median);
  const best = scored.filter((r) => !/^rp_/.test(r.vk) && r.vk !== 'epa').sort((a, b) => a.rank / a.of - b.rank / b.of)[0];
  const guess = row.count < 6 ? ` n = ${row.count} matches, treat as a guess.` : '';
  const lede = total.rank
    ? `${team} is above the ${rs} median in ${above.length} of ${scored.length} components. ${best ? `Its best relative to ${rs} is ${best.label}, ${fmtRank(best.rank, best.of)}; ` : ''}overall it is ${fmtRank(total.rank, total.of)} at ${rating(total.value)} points per match (${modelName}).${guess}`
    : `${team} is not in ${regionLabel(region)} in ${season}, so it has no rank there. Pick another region to compare within.`;
  const you = watchlist.has(team);
  const regOpts = regionOptions(S);
  if (!regOpts.some((o) => o.id === region)) regOpts.unshift({ id: region, label: regionLabel(region), n: 0 });

  const fv = (r, x) => (typeof x === 'number' ? rating(x, r.dec) : '–');
  const columns = [
    { id: 'label', label: 'Component', render: (r) => r.label },
    { id: 'unit', label: 'Unit', render: (r) => (r.unit === 'RP score' ? 'RP score' : `${r.unit} per match`) },
    { id: 'v', label: String(team), n: true, render: (r) => fv(r, r.value) },
    { id: 'rank', label: `Rank in ${rs}`, n: true, render: (r) => (r.rank ? fmtRank(r.rank, r.of) : '–') },
    { id: 'med', label: `${rs} median`, n: true, render: (r) => fv(r, r.median) },
    { id: 'p90', label: 'World p90', n: true, render: (r) => fv(r, r.p90) },
    { id: 'p75', label: 'World p75', n: true, render: (r) => fv(r, r.p75) },
    { id: 'p25', label: 'World p25', n: true, render: (r) => fv(r, r.p25) },
    { id: 'strip', label: 'Against the world', render: (r) => (r.p99 === null ? '' : raw(rangeStrip({ value: r.value, p25: r.p25, p75: r.p75, p90: r.p90, median: r.median, top: r.p99, you }))) },
  ];

  mount(root, html`
${pageHead({ eyebrow: `Breakdown · ${season} season · ${modelName}`, title: `${team} ${row.name || ''}`, lede })}
<section class="pw-filters" aria-label="Choose team and region">
<div class="pw-filters__group">
<label class="pw-field" for="bd-team"><span class="pw-eyebrow">Team</span><input id="bd-team" class="pw-input pw-input--team" inputmode="numeric" autocomplete="off" value="${team}"></label>
<label class="pw-field" for="bd-region"><span class="pw-eyebrow">Compare within</span><select id="bd-region" class="pw-select">${regOpts.map((o) => html`<option value="${o.id}"${o.id === region ? html` selected` : ''}>${o.label}</option>`)}</select></label>
${watchButton(team, you, { btn: true })}
</div>
<button type="button" class="pw-btn" data-act="csv">Download CSV</button>
</section>
${note(notice, { live: true })}
${figure({
    id: 'bd', cls: 'pw-figure--flush',
    title: `${team} is above the ${rs} median in ${above.length} of ${scored.length} rating components`,
    sub: `${modelName} components at the end of the ${season} data; ranks among ${total.of} ${rs} teams that played.`,
    body: dataTable({ columns, rows, labelledby: 'bd-t', rowClass: (r) => (r.vk === 'epa' ? 'pw-strong' : '') }),
    caption: html`<b>Strip:</b> bar = ${team}'s value from zero; shaded band = world 25th to 75th percentile; solid tick = world 90th percentile; dashed tick = ${rs} median. Each row has its own scale, 0 to the world 99th percentile. <b>Units:</b> points per match; count = game pieces per match; RP score = contribution to a bonus ranking point. n = ${row.count} matches for ${team}, ${total.of} ${rs} teams ranked. Source: The Blue Alliance, fetched ${dateTime(S.generated_at)}.`,
  })}`);
  ctx.setTitle(`Breakdown · ${team} · ${season}`);

  const onChange = (e) => {
    if (e.target.id === 'bd-team') { const n = parseInt(e.target.value, 10); if (n > 0) state.setQuery({ team: n }); }
    else if (e.target.id === 'bd-region') state.setQuery({ region: e.target.value });
  };
  const onClick = (e) => {
    const t = e.target.closest('[data-act]');
    if (!t || !root.contains(t)) return;
    if (t.dataset.act === 'watch') { watchlist.toggle(team); ctx.retry(); }
    else if (t.dataset.act === 'csv') {
      download(csvFilename({ view: `breakdown-${team}`, season, region }), toCSV([
        { key: 'label', label: 'Component' }, { key: 'unit', label: 'Unit' }, { key: 'value', label: `${team} (${modelName})` },
        { key: 'rank', label: `Rank in ${rs}` }, { key: 'of', label: 'Teams ranked' }, { key: 'median', label: `${rs} median` },
        { key: 'p90', label: 'World p90' }, { key: 'p75', label: 'World p75' }, { key: 'p25', label: 'World p25' },
      ], rows.map((r) => ({ ...r, value: r.value === null ? null : Number(r.value.toFixed(r.dec)), median: r.median === null ? null : Number(r.median.toFixed(r.dec)) }))));
    }
  };
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  void router;
  return () => { root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); };
}

export default render;

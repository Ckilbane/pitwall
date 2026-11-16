// Models (WP5): honest backtest scoreboard from the shards, slices, calibration, settings and
// "How this rating works" for each model. Numbers come from data; only the explanations are prose.
// Route: #/models?season=   (the season picks the slices, week and calibration figures)
import { html, raw, mount } from '../core/html.js';
import * as state from '../core/state.js';
import { getManifest, getModels, getSeason } from '../core/data.js';
import { int, dateTime } from '../core/fmt.js';
import { toCSV, download, csvFilename } from '../core/csv.js';
import { CHANGE_Z } from '../core/features.js';
import { pageHead } from '../components/page.js';
import { figure, fitChart } from '../components/figure.js';
import { dataTable } from '../components/table.js';
import { lineChart, lineDataTable } from '../charts/line.js';
import { calibrationChart } from '../charts/calibration.js';
import { diffPlot } from '../charts/dotplot.js';

/** Week-1 look-ahead: the label always; the measured size only when the data carries it. */
export function lookaheadText(la, year) {
  const y = la && (la[year] || la);
  const d = y && y.epa && y.epa.leakfree_minus_standard;
  if (d && d.acc && d.brier) {
    return ` Measured for ${year}: recomputing the constants without the matches being predicted changes EPA's week-1 accuracy by ${sg(d.acc.diff * 100, 2, ' percentage points')} and its Brier score by ${sg(d.brier.diff, 5, '')}.`;
  }
  return ' The measured size of this effect is not in the published data yet, so it is not quoted here.';
}

const MET = {
  acc: { label: 'Accuracy', unit: '%', higher: true, f: (x) => `${(x * 100).toFixed(1)}%`, d: (x) => sg(x * 100, 2, ' pts') },
  brier: { label: 'Brier score', unit: '', higher: false, f: (x) => x.toFixed(4), d: (x) => sg(x, 4, '') },
  log_loss: { label: 'Log loss', unit: '', higher: false, f: (x) => x.toFixed(3), d: (x) => sg(x, 3, '') },
  score_mae: { label: 'Score MAE', unit: 'points', higher: false, f: (x) => x.toFixed(2), d: (x) => sg(x, 2, ' pts') },
};
function sg(x, nd, suffix) { return `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(nd)}${suffix}`; }
const M = (id) => (id === 'fast' ? 'Fast EPA' : 'EPA');

/** 'better' | 'worse' | 'same' for Fast EPA's difference, using its interval when given. */
export function judge(metric, diff, lo, hi) {
  const higher = MET[metric].higher;
  if (typeof lo === 'number' && typeof hi === 'number') {
    if (lo > 0) return higher ? 'better' : 'worse';
    if (hi < 0) return higher ? 'worse' : 'better';
    return 'same';
  }
  if (diff === 0) return 'same';
  return (diff > 0) === higher ? 'better' : 'worse';
}

/** Does the tuning record's holdout check describe the same run as the published scoreboard? */
export function holdoutMatches(h, e, f) {
  if (!h || !e || !f || !h.epa || !h.fast) return false;
  const close = (x, y) => typeof x === 'number' && typeof y === 'number' && Math.abs(x - y) < 5e-4;
  return ['acc', 'brier'].every((m) => close(h.epa[m], e.overall[m]) && close(h.fast[m], f.overall[m]));
}

const pc = (x) => `${(x * 100).toFixed(1)}%`;

/**
 * Lede sentences for the newest season that Fast EPA was not tuned on, every number read from
 * models.json (scoreboard, tuning) and season.predict. No verdict is written in advance: each
 * comparison word (higher/lower) is computed, so whatever numbers ship are described correctly.
 * Returns {year, held, sentences: string[], mismatch: string|null} or null.
 */
export function findings(sb, tuning, kEpa, kFast) {
  const tuned = (tuning && tuning.tuned_on) || [];
  const years = Object.keys(sb).map(Number).filter((y) => sb[y].epa && sb[y].fast && !tuned.includes(y)).sort((a, b) => b - a);
  if (!years.length) return null;
  const year = years[0];
  const e = sb[year].epa; const f = sb[year].fast;
  const hc = tuning && tuning.holdout_check && tuning.holdout_check[year];
  const same = holdoutMatches(hc, e, f);
  const ci = same ? (hc.fast_minus_epa_ci95 || {}) : {};
  const held = ((tuning && tuning.search && tuning.search.holdout) || Object.keys((tuning && tuning.holdout_check) || {}).map(Number)).includes(year);
  const iv = (m, scale, nd) => (ci[m] && typeof ci[m].lo === 'number' ? ` (95% interval ${sg(ci[m].lo * scale, nd, '')} to ${sg(ci[m].hi * scale, nd, '')}, which ${ci[m].lo <= 0 && ci[m].hi >= 0 ? 'includes' : 'excludes'} 0)` : '');
  const cmp = (x, y) => (x > y ? 'higher' : x < y ? 'lower' : 'the same');
  const out = [];
  const E = e.overall; const F = f.overall;
  out.push(`On ${year}, ${held ? (tuning && tuning.post_hoc_seed_recheck ? 'held out from tuning, except one seed choice re-checked after 2025 results were seen' : 'held out from tuning') : 'out of sample'} (Fast EPA's settings were tuned on ${tuned.join(', ') || 'no listed season'}), Fast EPA called ${pc(F.acc)} of winners and EPA ${pc(E.acc)}: a difference of ${sg((F.acc - E.acc) * 100, 2, ' percentage points')}${iv('acc', 100, 2)}.`);
  out.push(`Brier score (lower is better): Fast EPA ${F.brier.toFixed(4)}, EPA ${E.brier.toFixed(4)}, so Fast EPA's is ${cmp(F.brier, E.brier)}${iv('brier', 1, 4)}.`);
  out.push(`Score error: Fast EPA misses alliance scores by ${F.score_mae.toFixed(2)} points on average, EPA by ${E.score_mae.toFixed(2)}, so Fast EPA's error is ${cmp(F.score_mae, E.score_mae)}${iv('score_mae', 1, 2)}.`);
  if (typeof kEpa === 'number' && typeof kFast === 'number' && kEpa !== kFast) {
    const conf = typeof F.conf === 'number' && typeof E.conf === 'number' ? ` Average confidence in the favourite: Fast EPA ${pc(F.conf)}, EPA ${pc(E.conf)}.` : '';
    const ke = tuning && tuning.same_k_check && tuning.same_k_check[year];
    const keTxt = ke && ke.brier && typeof ke.brier.epa === 'number'
      ? ` Given the same k, EPA's Brier score would be ${ke.brier.epa.toFixed(4)}, so ${Math.round(Math.max(0, Math.min(1, (E.brier - ke.brier.epa) / (E.brier - F.brier || 1))) * 100)}% of the Brier gap comes from k alone.`
      : '';
    out.push(`The two models use different win-probability constants (k ${String(kFast).replace('-', '−')} for Fast EPA, ${String(kEpa).replace('-', '−')} for EPA). A k further from zero makes probabilities sharper, which moves Brier score and log loss without changing which alliance is favoured.${conf}${keTxt}`);
  }
  let mismatch = null;
  if (hc && hc.fast && !same) {
    mismatch = `The tuning record's own check of ${year} reports different numbers for the same Fast EPA settings (accuracy ${pc(hc.fast.acc)}, Brier ${hc.fast.brier.toFixed(4)}, score error ${hc.fast.score_mae.toFixed(2)} points; EPA ${pc(hc.epa.acc)}, ${hc.epa.brier.toFixed(4)}, ${hc.epa.score_mae.toFixed(2)}). The two pipeline paths are being reconciled, so treat the Fast EPA rows as provisional.`;
  }
  return { year, held, sentences: out, mismatch, ciOk: same };
}

function seasonTag(y, tuning) {
  const tuned = (tuning && tuning.tuned_on) || [];
  const hold = Object.keys((tuning && tuning.holdout_check) || {}).map(Number);
  if (tuned.includes(y)) return { tag: 'Tuning set', short: 'tuned on' };
  if (hold.includes(y)) return { tag: 'Held out', short: 'held out' };
  return { tag: 'Out of sample', short: 'out of sample' };
}

function flatten(o, pre = '') {
  const out = [];
  for (const [k, v] of Object.entries(o || {})) {
    if (v && typeof v === 'object' && !Array.isArray(v)) out.push(...flatten(v, `${pre}${k}.`));
    else out.push([`${pre}${k}`, Array.isArray(v) ? v.join(', ') : String(v)]);
  }
  return out;
}

const SLICE_LABEL = {
  event_type: { regional: 'Regional events', district: 'District events', district_cmp: 'District championships', champs_div: 'Championship divisions', einstein: 'Einstein' },
  comp_level: { qual: 'Qualification matches', elim: 'Playoff matches' },
  season_matches: { first3: 'A team’s first 3 matches of the season', later: 'Later matches' },
  event_matches: { first3: 'A team’s first 3 matches at an event', later: 'Later matches at an event' },
};
const GROUP_LABEL = { event_type: 'Event type', comp_level: 'Match type', season_matches: 'Season stage', event_matches: 'Event stage' };

const METRICS = Object.keys(MET);
const mFmt = (m, x) => (typeof x === 'number' ? MET[m].f(x) : 'not scored');

/** Scoreboard rows: per season an EPA row, a Fast EPA row and the difference row. */
function scoreboard(sb, seasons, tuning) {
  const rows = [];
  for (const y of seasons) {
    const e = sb[y] && sb[y].epa; const f = sb[y] && sb[y].fast; const t = seasonTag(y, tuning);
    rows.push({ k: 'e', season: y, tag: t.tag, model: 'EPA', o: e && e.overall });
    rows.push({ k: 'f', season: y, tag: '', model: 'Fast EPA', o: f && f.overall });
    rows.push({ k: 'd', season: y, tag: '', model: 'Fast EPA minus EPA', e: e && e.overall, f: f && f.overall });
  }
  const cell = (r, m) => {
    if (r.k !== 'd') return mFmt(m, r.o && r.o[m]);
    if (!r.e || !r.f) return '';
    const d = r.f[m] - r.e[m]; const j = judge(m, d);
    return html`<span class="${j === 'better' ? 'pw-delta pw-delta--up' : j === 'worse' ? 'pw-delta pw-delta--down' : ''}">${MET[m].d(d)}${j === 'same' ? '' : ` ${j}`}</span>`;
  };
  const columns = [
    { id: 'season', label: 'Season', td: 'team', render: (r) => (r.k === 'e' ? String(r.season) : '') },
    { id: 'tag', label: 'Fast EPA tuning', render: (r) => r.tag },
    { id: 'model', label: 'Model', render: (r) => r.model },
    { id: 'n', label: 'Matches', n: true, render: (r) => (r.o && r.o.n ? int(r.o.n) : '') },
    ...METRICS.map((m) => ({ id: m, label: m === 'score_mae' ? 'Score MAE (pts)' : MET[m].label, n: true, render: (r) => cell(r, m) })),
  ];
  return { rows, table: dataTable({ columns, rows, labelledby: 'mod-sb-t', rowClass: (r) => (r.k === 'd' ? 'pw-diffrow' : r.k === 'e' ? 'pw-season-start' : '') }) };
}

function diffFigure(sb, tuning, seasons, draws) {
  const hold = (tuning && tuning.holdout_check) || {};
  const unitOf = (m) => (m === 'acc' ? 'percentage points' : m === 'score_mae' ? 'points' : 'unitless');
  const panels = METRICS.map((m) => {
    const rows = seasons.map((y) => {
      const r = sb[y]; const t = seasonTag(y, tuning);
      if (!r || !r.epa || !r.fast) return { label: String(y), sub: t.short, value: null };
      const scale = m === 'acc' ? 100 : 1;
      const d = (r.fast.overall[m] - r.epa.overall[m]) * scale;
      const ci = holdoutMatches(hold[y], r.epa, r.fast) && hold[y].fast_minus_epa_ci95 ? hold[y].fast_minus_epa_ci95[m] : null;
      const j = judge(m, d, ci ? ci.lo : undefined, ci ? ci.hi : undefined);
      return { label: String(y), sub: t.short, value: d, lo: ci ? ci.lo * scale : undefined, hi: ci ? ci.hi * scale : undefined, good: j === 'better' ? true : j === 'worse' ? false : null };
    });
    const nd = m === 'acc' || m === 'score_mae' ? 2 : m === 'log_loss' ? 3 : 4;
    draws[`diff-${m}`] = (w) => diffPlot(rows, {
      width: w, title: `Difference (${unitOf(m)})`, fmt: (v) => sg(v, nd, ''), ariaLabel: `${MET[m].label}, Fast EPA minus EPA, by season: ${rows.map((r) => `${r.label} ${r.value === null ? 'not scored' : sg(r.value, nd, '')}`).join(', ')}`,
    });
    return html`<div><h3 class="pw-panel__title">${MET[m].label}: Fast EPA minus EPA, ${MET[m].higher ? 'right' : 'left'} of zero is better</h3><div class="pw-chart" data-chart="diff-${m}"></div></div>`;
  });
  const cnt = (m) => { let b = 0; let n = 0; for (const y of seasons) { const r = sb[y]; if (!r || !r.epa || !r.fast) continue; n++; if (judge(m, r.fast.overall[m] - r.epa.overall[m]) === 'better') b++; } return { b, n }; };
  const br = cnt('brier'); const ac = cnt('acc'); const ma = cnt('score_mae');
  return figure({
    id: 'mod-diff',
    title: br.n ? `Fast EPA had the lower Brier score in ${br.b} of ${br.n} seasons, the higher accuracy in ${ac.b} and the lower score error in ${ma.b}` : 'No seasons scored yet',
    sub: 'Each backtest metric, Fast EPA minus EPA, by season. Whiskers are 95% intervals where the tuning record has one for the same run.',
    body: html`<div class="pw-panels pw-panels--2">${panels}</div>`,
    caption: html`<b>Squares:</b> the difference; green = Fast EPA better, orange = worse, dark = interval includes zero. <b>Zero line:</b> no difference. n = matches per season in the scoreboard. Every score uses only ratings built before the match. Source: data/models.json scoreboard and tuning record.`,
  });
}

function weekFigure(bt, year, draws) {
  const mk = (id) => {
    const w = (bt[id] && bt[id].slices && bt[id].slices.week) || {};
    return Object.keys(w).map(Number).sort((a, b) => a - b).filter((k) => w[k] && typeof w[k].acc === 'number').map((k) => ({ x: k, y: w[k].acc * 100, n: w[k].n }));
  };
  const e = mk('epa'); const f = mk('fast');
  if (!e.length) return '';
  const fm = new Map(f.map((p) => [p.x, p.y]));
  const both = e.filter((p) => fm.has(p.x));
  draws.week = (w) => lineChart({ width: w, series: [
    { id: 'epa', label: `EPA · ${e[e.length - 1].y.toFixed(1)}%`, role: 'context', points: e },
    { id: 'fast', label: `Fast EPA · ${f.length ? f[f.length - 1].y.toFixed(1) : ''}%`, role: 'focus', points: f },
  ], x: { title: 'Competition week (8 = Championship)', min: 1, max: 8 }, y: { title: 'Accuracy (% of matches)' }, ariaLabel: `Accuracy by week in ${year}: ${both.map((p) => `week ${p.x} EPA ${p.y.toFixed(1)}%, Fast EPA ${fm.get(p.x).toFixed(1)}%`).join('; ')}` });
  const fw = both.filter((p) => fm.get(p.x) > p.y).length; const ew = both.filter((p) => p.y > fm.get(p.x)).length;
  const series = [
    { id: 'epa', label: `EPA · ${e[e.length - 1].y.toFixed(1)}%`, role: 'context', points: e },
    { id: 'fast', label: `Fast EPA · ${f.length ? f[f.length - 1].y.toFixed(1) : ''}%`, role: 'focus', points: f },
  ];
  return figure({
    id: 'mod-week',
    title: `Week by week in ${year}, Fast EPA called more winners in ${fw} of ${both.length} weeks and EPA in ${ew}`,
    sub: `Share of matches won by the favourite, by competition week, ${year}. Every prediction uses only earlier matches.`,
    body: html`<div class="pw-chart" data-chart="week"></div>`,
    data: raw(lineDataTable([{ id: 'e', label: 'EPA (%)', points: e }, { id: 'f', label: 'Fast EPA (%)', points: f }], 'Week')),
    caption: html`<b>Lines:</b> grey = EPA, dark = Fast EPA. n = ${e.map((p) => `week ${p.x}: ${int(p.n)}`).join(', ')} matches. Source: season.json backtest slices, ${year}.`,
  });
}

function calibFigure(bt, year) {
  const pts = (id) => ((bt[id] && bt[id].calibration) || []).filter((b) => b.n > 0 && typeof b.mean_pred === 'number');
  const gap = (id) => { let n = 0; let g = 0; for (const b of pts(id)) { n += b.n; g += b.n * Math.abs(b.mean_pred - b.frac_red_win); } return n ? (g / n) * 100 : null; };
  if (!pts('epa').length) return '';
  const ge = gap('epa'); const gf = gap('fast');
  const rows = (bt.epa.calibration || []).map((b, i) => ({ bin: `${Math.round((b.lo ?? i / 10) * 100)}-${Math.round((b.hi ?? (i + 1) / 10) * 100)}%`, b, f: (bt.fast.calibration || [])[i] || {} }));
  const f3 = (x) => (typeof x === 'number' ? x.toFixed(3) : '');
  return figure({
    id: 'mod-cal',
    title: gf === null ? `Calibration in ${year}` : `Predicted and actual red-win rates differ by ${ge.toFixed(1)} points on average for EPA and ${gf.toFixed(1)} for Fast EPA in ${year}`,
    sub: `Red win probability predicted before each match against how often red won, ten bins, ${year}.`,
    body: html`<div class="pw-chart pw-chart--square">${raw(calibrationChart([
      { label: 'EPA', hollow: false, pts: pts('epa').map((b) => [b.mean_pred * 100, b.frac_red_win * 100, b.n]) },
      { label: 'Fast EPA', hollow: true, pts: pts('fast').map((b) => [b.mean_pred * 100, b.frac_red_win * 100, b.n]) },
    ], { x: 'Predicted red win probability (%)', y: 'Red won (% of matches in the bin)', ariaLabel: `Calibration of EPA and Fast EPA in ${year}` }))}</div>`,
    data: dataTable({ label: 'Calibration bins', columns: [
      { id: 'bin', label: 'Bin', render: (r) => r.bin }, { id: 'en', label: 'EPA n', n: true, render: (r) => int(r.b.n) },
      { id: 'ep', label: 'EPA predicted', n: true, render: (r) => f3(r.b.mean_pred) }, { id: 'ef', label: 'EPA red won', n: true, render: (r) => f3(r.b.frac_red_win) },
      { id: 'fn', label: 'Fast n', n: true, render: (r) => int(r.f.n) }, { id: 'fp', label: 'Fast predicted', n: true, render: (r) => f3(r.f.mean_pred) }, { id: 'ff', label: 'Fast red won', n: true, render: (r) => f3(r.f.frac_red_win) }], rows }),
    caption: html`<b>Squares:</b> solid = EPA, hollow = Fast EPA, one per bin of predicted probability; on the dashed line a 70% prediction wins 70% of the time. Average gap weighted by matches per bin. n = ${int((bt.epa.overall || {}).n)} matches. Ties count as half a red win. Source: season.json backtest calibration, ${year}.`,
  });
}

function slicesTable(bt, year) {
  const rows = [];
  for (const g of Object.keys(GROUP_LABEL)) {
    const e = (bt.epa.slices || {})[g] || {}; const f = (bt.fast.slices || {})[g] || {};
    for (const k of Object.keys(e)) rows.push({ g: GROUP_LABEL[g], label: (SLICE_LABEL[g] || {})[k] || k, e: e[k], f: f[k] || {} });
  }
  const c = (x, m) => (x && typeof x[m] === 'number' ? MET[m].f(x[m]) : '');
  return figure({
    id: 'mod-sl', cls: 'pw-figure--flush',
    title: `Backtest by slice, ${year}`,
    sub: 'Accuracy, Brier score and score error for both models within each group of matches.',
    body: dataTable({ labelledby: 'mod-sl-t', rows, columns: [
      { id: 'g', label: 'Group', render: (r) => r.g }, { id: 'l', label: 'Slice', render: (r) => r.label }, { id: 'n', label: 'Matches', n: true, render: (r) => int(r.e.n) },
      { id: 'ae', label: 'Acc. EPA', n: true, render: (r) => c(r.e, 'acc') }, { id: 'af', label: 'Acc. Fast', n: true, render: (r) => c(r.f, 'acc') },
      { id: 'be', label: 'Brier EPA', n: true, render: (r) => c(r.e, 'brier') }, { id: 'bf', label: 'Brier Fast', n: true, render: (r) => c(r.f, 'brier') },
      { id: 'me', label: 'Score MAE EPA', n: true, render: (r) => c(r.e, 'score_mae') }, { id: 'mf', label: 'Score MAE Fast', n: true, render: (r) => c(r.f, 'score_mae') }] }),
    caption: html`Score MAE in points. A team's first 3 matches: at least one of the six teams had played fewer than 3 matches before this one. Source: season.json backtest slices, ${year}.`,
  });
}

function howItWorks(m) {
  const consts = Object.entries(m.constants || {});
  const set = flatten(m.settings);
  const t = m.tuning;
  const per = (t && t.chosen && t.chosen.per_season) || {};
  const base = (t && t.baseline && t.baseline.per_season) || {};
  const f4 = (x) => (typeof x === 'number' ? x.toFixed(4) : '');
  const p1 = (x) => (typeof x === 'number' ? `${(x * 100).toFixed(1)}%` : '');
  return html`<section class="pw-section pw-how" aria-labelledby="how-${m.id}">
<h3 class="pw-h3" id="how-${m.id}">How ${m.label} works</h3>
<div class="pw-prose"><p><b>${m.short_description}</b></p>${(m.how_it_works || []).map((p) => html`<p>${p}</p>`)}</div>
${consts.length ? dataTable({ label: `${m.label} constants`, columns: [{ id: 'k', label: 'Constant', render: (r) => r[0] }, { id: 'v', label: 'Value', n: true, render: (r) => String(r[1].value) }, { id: 'm', label: 'Meaning', render: (r) => html`<span class="wrap">${r[1].meaning}</span>` }], rows: consts }) : ''}
${set.length ? dataTable({ label: `${m.label} settings`, columns: [{ id: 'k', label: 'Setting (frozen)', render: (r) => r[0] }, { id: 'v', label: 'Value', n: true, render: (r) => r[1] }], rows: set }) : ''}
${t ? html`<p class="pw-small">Tuned on ${(t.tuned_on || []).join(', ')} (${t.tuned_at ? dateTime(t.tuned_at) : 'date not recorded'}). Objective: ${t.objective}. ${t.search ? `${t.search.configs_tried} settings tried; ${(t.search.holdout || []).join(', ')} ${t.search.holdout_used_for_selection ? 'was' : 'was not'} used to choose them.` : ''}${t.post_hoc_seed_recheck ? ' One exception: the choice of starting seed was re-checked after the 2025 results of both seed options had been seen. The rule for that choice used 2022-2024 only, but 2025 is not strictly untouched for it.' : ''}</p>
${dataTable({ label: 'Tuning record by season', rows: (t.tuned_on || []).map(String), columns: [{ id: 's', label: 'Tuning season', td: 'team', render: (y) => y },
    { id: 'b0', label: 'EPA Brier', n: true, render: (y) => f4((base[y] || {}).brier) }, { id: 'b1', label: 'Fast Brier', n: true, render: (y) => f4((per[y] || {}).brier) },
    { id: 'a0', label: 'EPA accuracy', n: true, render: (y) => p1((base[y] || {}).acc) }, { id: 'a1', label: 'Fast accuracy', n: true, render: (y) => p1((per[y] || {}).acc) }] })}` : ''}
<p class="pw-small">Source: ${m.source}. Config hash ${m.config_hash ? m.config_hash.slice(0, 12) : 'none'}.</p>
</section>`;
}

export async function render(ctx) {
  const { root } = ctx;
  const st = state.get();
  ctx.loading('Loading the model scoreboard');
  const [manifest, models] = await Promise.all([getManifest(), getModels()]);
  const ids = manifest.seasons.map((s) => s.season);
  let year = ids.includes(st.season) ? st.season : ids[0];
  let S = await getSeason(year);
  let fell = null;
  const scored = (x) => x.backtest && x.backtest.epa && x.backtest.epa.overall && x.backtest.epa.overall.n;
  if (!scored(S)) {
    for (const y of ids) { const s2 = await getSeason(y); if (scored(s2)) { fell = year; year = y; S = s2; break; } }
  }
  if (!ctx.isCurrent()) return undefined;
  const fast = models.models.find((m) => m.id === 'fast');
  const tuning = fast && fast.tuning;
  const sb = {};
  for (const r of models.scoreboard) (sb[r.season] = sb[r.season] || {})[r.model] = r;
  const seasons = ids.slice().sort((a, b) => b - a);
  const pr = S.predict.models;
  const v = findings(sb, tuning, pr.epa && pr.epa.k, pr.fast && pr.fast.k);
  const sbt = scoreboard(sb, seasons, tuning);
  const bt = S.backtest;
  const oos = seasons.filter((y) => seasonTag(y, tuning).tag !== 'Tuning set');
  const audit = ['epa', 'fast'].map((id) => (bt[id] && bt[id].leak_audit ? `${M(id)}: ${bt[id].leak_audit.cuts} cuts, ${bt[id].leak_audit.passed ? 'passed' : 'failed'}` : '')).filter(Boolean).join('; ');
  const la = tuning && tuning.week1_lookahead;
  const draws = {};

  mount(root, html`
${pageHead({ eyebrow: 'Models · backtest on pre-match predictions', title: 'How well the models predict', lede: v ? v.sentences[0] : 'Backtest results are not available yet.' })}
${v ? html`<ul class="pw-findings">${v.sentences.slice(1).map((t) => html`<li>${t}</li>`)}</ul>` : ''}
${v && v.mismatch ? html`<p class="pw-caution" role="note">${v.mismatch}</p>` : ''}
<p class="pw-small pw-prose">Every prediction here is a true pre-match prediction: the ratings come only from earlier matches. Accuracy is the share of matches the favourite won. Brier score and log loss grade the win probabilities (lower is better). Score MAE is the average miss on an alliance's score, in points.</p>
<section class="pw-section" aria-labelledby="mod-sb-t">
<div class="pw-section__head"><h2 class="pw-h2" id="mod-sb-t">Scoreboard by season</h2><button class="pw-btn" type="button" data-act="csv">Download CSV</button></div>
<p class="pw-small">${tuning ? `Fast EPA's settings were tuned on ${(tuning.tuned_on || []).join(', ')}.` : ''} ${oos.length ? `${oos.join(', ')} ${oos.length > 1 ? 'were' : 'was'} not used for tuning (out of sample).` : ''}${tuning && tuning.post_hoc_seed_recheck ? ' One exception: the choice of starting seed was re-checked after the 2025 results of both seed options had been seen. The rule for that choice used 2022-2024 only, but 2025 is not strictly untouched for it.' : ''} Higher accuracy is better; lower Brier score, log loss and score MAE are better. Difference rows are Fast EPA minus EPA.</p>
${sbt.table}
</section>
${diffFigure(sb, tuning, seasons.slice().sort((a, b) => a - b), draws)}
<h2 class="pw-h2">${year} in detail${fell ? ` (${fell} has no scored matches yet)` : ''}</h2>
<p class="pw-small">Change the season in the header to see another. Early matches are harder for any model because ratings rest on few matches.</p>
${weekFigure(bt, year, draws)}
${calibFigure(bt, year)}
${slicesTable(bt, year)}
<section class="pw-section" aria-labelledby="mod-la">
<h2 class="pw-h2" id="mod-la">Known look-ahead and other caveats</h2>
<div class="pw-prose">
<p>Season constants (score SD, foul rate, component means) come from the season's own week-1 matches, including the week-1 matches being predicted. The Statbotics model works this way, and PitWall keeps it for parity. Every season's backtest labels it <code>${(bt.epa || {}).year_constants || 'season_week1'}</code>.${lookaheadText(la, year)}</p>
<p>Leak audit (predictions must not change when later matches are removed): ${audit || 'not reported'}.</p>
<p>During a live season the constants move as week 1 completes, so early-season ratings shift between updates. Ratings with fewer than 6 matches behind them are guesses.</p>
<p>Season features shown elsewhere: consistency is the SD of a team's qual-match residuals (actual minus predicted contribution), always printed with its n and left blank below n = 4. The pre-event comparison marks a team whose first two or three matches at an event (k) differ from its rating entering the event by more than ${CHANGE_Z} SD of match noise; at a team's first event that rating is the preseason prior, itself an estimate. Opponent effect is experimental: how much less (or more) opponents scored than predicted, with the season mean subtracted, shown only when it exceeds 2 standard errors over at least 12 matches. A harder schedule slightly inflates it.</p>
</div>
</section>
<section class="pw-section" aria-labelledby="mod-how"><h2 class="pw-h2" id="mod-how">How this rating works</h2>${models.models.map((m) => howItWorks(m))}</section>`);
  ctx.setTitle(`Models · ${year}`);
  const stops = [...root.querySelectorAll('[data-chart]')].map((h) => fitChart(h, draws[h.dataset.chart]));

  const onClick = (e) => {
    const t = e.target.closest('[data-act="csv"]');
    if (!t || !root.contains(t)) return;
    const flat = sbt.rows.filter((r) => r.k !== 'd' && r.o).map((r) => ({ season: r.season, status: seasonTag(r.season, tuning).tag, model: r.model, ...r.o }));
    download(csvFilename({ view: 'models', season: 'scoreboard' }), toCSV([{ key: 'season', label: 'Season' }, { key: 'status', label: 'Fast EPA tuning' }, { key: 'model', label: 'Model' }, { key: 'n', label: 'Matches' }, { key: 'acc', label: 'Accuracy (0-1)' }, { key: 'brier', label: 'Brier' }, { key: 'log_loss', label: 'Log loss' }, { key: 'score_mae', label: 'Score MAE (points)' }], flat));
  };
  root.addEventListener('click', onClick);
  return () => { stops.forEach((f) => f()); root.removeEventListener('click', onClick); };
}

export default render;

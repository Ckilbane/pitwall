// Pure helpers for the Events, Event and Match views (WP4b). No DOM; imports only pure core modules,
// so it runs in node tests.
import { matchLabel } from '../core/fmt.js';
import { eventAsofMatchIndex } from '../components/asof.js';
//
// Data shapes are SPEC 3.5 (event files) and 3.4 (season.json). "Row index" always means the row
// of the `matches` table (processing order). "idx" (as-of position) means: matches with row index
// < idx have been played; idx = 0 is the start of the event, idx = matches.n is the end.

// ---------- small table helpers (same shape as core tbl(), without the dependency) ----------

/** Column `name` of a table; model columns live under t.models[model]. */
export function col(t, name, model) {
  if (!t) return [];
  if (model && t.models && t.models[model] && name in t.models[model]) return t.models[model][name];
  return (t.cols && t.cols[name]) || [];
}

/** Row objects merging plain columns and one model's columns (model wins on a name clash). */
export function tableRows(t, model) {
  const rows = [];
  if (!t) return rows;
  const plain = Object.keys(t.cols || {});
  const mod = model && t.models && t.models[model] ? Object.keys(t.models[model]) : [];
  for (let i = 0; i < t.n; i++) {
    const r = { _i: i };
    for (const k of plain) r[k] = t.cols[k][i];
    for (const k of mod) r[k] = t.models[model][k][i];
    rows.push(r);
  }
  return rows;
}

// ---------- keys and labels ----------

/** "2025gal_qm94" -> {event: "2025gal", season: 2025, part: "qm94"} or null. */
export function parseMatchKey(key) {
  const m = /^((\d{4})[a-z0-9]+)_([a-z]+\d+(?:m\d+)?)$/i.exec(String(key || ''));
  return m ? { event: m[1], season: Number(m[2]), part: m[3] } : null;
}

/** Season from an event key ("2025mrcmp" -> 2025). */
export function seasonOfEvent(eventKey) {
  const m = /^(\d{4})/.exec(String(eventKey || ''));
  return m ? Number(m[1]) : null;
}

const LEVEL_NAME = { qm: 'Qualification', ef: 'Eighth-final', qf: 'Quarterfinal', sf: 'Semifinal', f: 'Final' };
export function levelName(level) { return LEVEL_NAME[level] || String(level || ''); }

const TYPE_NAME = {
  regional: 'Regional', district: 'District event', district_cmp: 'District championship',
  district_cmp_division: 'District championship division', champs_div: 'Championship division',
  cmp_division: 'Championship division', einstein: 'Championship finals', cmp_finals: 'Championship finals',
  festival_of_champions: 'Festival of Champions', remote: 'Remote event', offseason: 'Offseason',
  preseason: 'Preseason', unlabeled: 'Other',
};
export function typeLabel(type) {
  if (TYPE_NAME[type]) return TYPE_NAME[type];
  const s = String(type || 'other').replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Week heading. Upstream numbering: 1-7 in season, 8 = Championship, null/0 = other. */
export function weekLabel(week) {
  if (week == null) return 'Other events';
  if (week === 8) return 'Championship';
  if (week === 0) return 'Preseason';
  return `Week ${week}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** "Apr 2-5" / "Mar 30-Apr 2" from two YYYY-MM-DD strings (no timezone maths: dates are local to the event). */
export function dateRange(start, end) {
  const p = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? { m: Number(m[2]) - 1, d: Number(m[3]) } : null; };
  const a = p(start), b = p(end);
  if (!a) return '';
  if (!b || (a.m === b.m && a.d === b.d)) return `${MONTHS[a.m]} ${a.d}`;
  if (a.m === b.m) return `${MONTHS[a.m]} ${a.d}–${b.d}`;
  return `${MONTHS[a.m]} ${a.d}–${MONTHS[b.m]} ${b.d}`;
}

// ---------- win probability on the diverging scale ----------

export { wpBand, favoured } from '../components/wp.js';

/** Did the model call the match? true/false, or null when unplayed, tied or exactly 50%. */
export function called(p, winner) {
  if (p == null || (winner !== 'red' && winner !== 'blue')) return null;
  if (p === 0.5) return null;
  return (p > 0.5) === (winner === 'red');
}

// ---------- event context ----------

const CTX = new WeakMap();

/** Memoised indices over an event file. */
export function context(ev) {
  let c = CTX.get(ev);
  if (c) return c;
  const m = ev.matches, tm = ev.team_matches, te = ev.team_events;
  const teamIndex = new Map();
  col(te, 'team').forEach((t, i) => teamIndex.set(t, i));
  const rowsByTeam = new Map();
  const tmTeam = col(tm, 'team'), tmM = col(tm, 'm');
  for (let r = 0; r < tm.n; r++) {
    const t = tmTeam[r];
    if (!rowsByTeam.has(t)) rowsByTeam.set(t, []);
    rowsByTeam.get(t).push(r);
  }
  const rowsByMatch = new Map();
  for (let r = 0; r < tm.n; r++) {
    const mi = tmM[r];
    if (!rowsByMatch.has(mi)) rowsByMatch.set(mi, []);
    rowsByMatch.get(mi).push(r);
  }
  const keyIndex = new Map();
  col(m, 'key').forEach((k, i) => keyIndex.set(k, i));
  c = { teamIndex, rowsByTeam, rowsByMatch, keyIndex, nMatches: m.n };
  CTX.set(ev, c);
  return c;
}

/** Has the match been played (both scores present)? */
export function isPlayed(ev, mi) {
  const m = ev.matches.cols;
  return m.red_score[mi] != null && m.blue_score[mi] != null;
}

/** Number of leading matches that count as played in the live data (for "live" events). */
export function playedCount(ev) {
  let n = 0;
  for (let i = 0; i < ev.matches.n; i++) if (isPlayed(ev, i)) n++;
  return n;
}

/** Resolve the `asof` query value to idx (how many leading matches count as played). WP5's rule: a label means "after that match". */
export function asofIndex(ev, asof) {
  return eventAsofMatchIndex(ev, asof);
}

/** Label of the last match played at idx ("Q31"), or null at the start. */
export function lastPlayedLabel(ev, idx) {
  if (idx <= 0) return null;
  const i = Math.min(idx, ev.matches.n) - 1;
  const c = ev.matches.cols;
  return matchLabel(c.comp_level[i], c.set_number[i], c.match_number[i]);
}

// ---------- ratings as of a match ----------

/**
 * Rating vectors by team as of idx, for one model.
 * Start: team_events pre_<vk>. After that: post vector of the team's latest updated row before idx.
 * Returns Map team -> {v: {vk: value}, n: updated matches so far}.
 */
export function ratingsAsOf(ev, model, idx, keys) {
  const te = ev.team_events, tm = ev.team_matches;
  const ks = keys || ['epa', 'auto_epa', 'teleop_epa', 'endgame_epa'];
  const c = context(ev);
  const out = new Map();
  const tmM = col(tm, 'm');
  const postEpa = col(tm, 'epa', model);
  const teams = col(te, 'team');
  for (let i = 0; i < teams.length; i++) {
    const t = teams[i];
    const rows = c.rowsByTeam.get(t) || [];
    let last = -1, n = 0;
    for (const r of rows) {
      if (tmM[r] >= idx) break;
      if (postEpa[r] != null) { last = r; n++; }
    }
    const v = {};
    for (const k of ks) v[k] = last >= 0 ? col(tm, k, model)[last] : col(te, 'pre_' + k, model)[i];
    out.set(t, { v, n, row: i });
  }
  return out;
}

/** The pre-event vector for team row i (team_events). */
export function preEvent(ev, model, i, keys) {
  const ks = keys || ['epa', 'auto_epa', 'teleop_epa', 'endgame_epa'];
  const v = {};
  for (const k of ks) v[k] = col(ev.team_events, 'pre_' + k, model)[i];
  return v;
}

/** Pre-match vectors for the 6 slots of a match as arrays aligned with `vector` (derived by the SPEC 3.5.4 rule). */
export function preMatchVectors(ev, model, mi, vector) {
  const tm = ev.team_matches, te = ev.team_events, c = context(ev);
  const tmM = col(tm, 'm'), tmTeam = col(tm, 'team'), tmAlliance = col(tm, 'alliance');
  const post = col(tm, 'epa', model);
  const red = [], blue = [];
  for (const r of c.rowsByMatch.get(mi) || []) {
    const t = tmTeam[r];
    let src = -1;
    for (const q of c.rowsByTeam.get(t)) {
      if (tmM[q] >= mi) break;
      if (post[q] != null) src = q;
    }
    const vec = vector.map((k) => (src >= 0 ? col(tm, k, model)[src] : col(te, 'pre_' + k, model)[c.teamIndex.get(t)]));
    (tmAlliance[r] === 0 ? red : blue).push({ team: t, vec, row: r });
  }
  return { red, blue };
}

// ---------- rankings replay ----------

/** Ranking-point columns present in this event's matches (rp_1..rp_3). */
export function rpKeys(ev) {
  return [1, 2, 3].filter((i) => ('red_rp_' + i) in ev.matches.cols);
}

/**
 * Ranking points per qualification win. 2 up to 2024, 3 from 2025: read from the data instead of
 * assuming it, by comparing the official team_events RP total with the replayed bonus RPs and ties.
 */
export function inferWinRp(ev) {
  const dflt = (seasonOfEvent(ev.event) || ev.season || 0) >= 2025 ? 3 : 2;
  const te = ev.team_events.cols;
  const base = standingsAsOf(ev, ev.matches.n, 0);
  let official = 0, replay = 0, wins = 0;
  for (const r of base) {
    const i = ev.team_events.cols.team.indexOf(r.team);
    if (i < 0 || te.rps[i] == null) continue;
    official += te.rps[i]; replay += r.rp; wins += r.w;
  }
  if (!wins) return dflt;
  const v = Math.round((official - replay) / wins);
  return v >= 2 && v <= 3 ? v : dflt;
}

/**
 * Qualification standings from the matches before idx: W-L-T, ranking points (2 per win, 1 per tie,
 * plus each bonus RP), matches counted. DQ'd teams score 0 RP; surrogate appearances are not counted.
 * Sorted by RP per match, then team number (official tiebreakers are not replayed).
 */
export function standingsAsOf(ev, idx, winRp) {
  const m = ev.matches.cols;
  const winPts = winRp == null ? inferWinRp(ev) : winRp;
  const keys = rpKeys(ev);
  const acc = new Map();
  const get = (t) => { if (!acc.has(t)) acc.set(t, { team: t, w: 0, l: 0, t: 0, rp: 0, n: 0 }); return acc.get(t); };
  for (const t of col(ev.team_events, 'team')) get(t);
  for (let i = 0; i < Math.min(idx, ev.matches.n); i++) {
    if (m.comp_level[i] !== 'qm' || !isPlayed(ev, i)) continue;
    for (const side of ['red', 'blue']) {
      const other = side === 'red' ? 'blue' : 'red';
      const result = m.winner[i] === side ? 'w' : m.winner[i] === other ? 'l' : 't';
      const dq = new Set(m[side + '_dq'][i] || []);
      const sur = new Set(m[side + '_surrogate'][i] || []);
      for (const t of m[side][i]) {
        if (sur.has(t)) continue;
        const a = get(t);
        a.n++; a[result]++;
        if (dq.has(t)) continue;
        a.rp += result === 'w' ? winPts : result === 't' ? 1 : 0;
        for (const k of keys) if (m[`${side}_rp_${k}`][i]) a.rp += 1;
      }
    }
  }
  const rows = [...acc.values()].map((a) => ({ ...a, rpm: a.n ? a.rp / a.n : 0 }));
  rows.sort((x, y) => y.rpm - x.rpm || y.rp - x.rp || x.team - y.team);
  rows.forEach((r, i) => { r.rank = i + 1; });
  return rows;
}

// ---------- accuracy ----------

/**
 * Prediction accuracy over matches before idx, for one model. Rows: all/qual/playoff.
 * called = share of decided matches where the favoured alliance won (ties and exact 50% excluded);
 * brier = mean squared error of the red win probability (tie = 0.5); score MAE/RMSE/bias over alliances.
 */
export function accuracy(ev, model, idx) {
  const m = ev.matches, mc = m.cols;
  const p = col(m, 'epa_win_prob', model);
  const rs = col(m, 'epa_red_score_pred', model), bs = col(m, 'epa_blue_score_pred', model);
  const empty = () => ({ n: 0, decided: 0, correct: 0, brierSum: 0, brierN: 0, absSum: 0, sqSum: 0, biasSum: 0, scoreN: 0 });
  const acc = { all: empty(), qual: empty(), playoff: empty() };
  for (let i = 0; i < Math.min(idx, m.n); i++) {
    if (!isPlayed(ev, i) || p[i] == null) continue;
    const slots = [acc.all, mc.comp_level[i] === 'qm' ? acc.qual : acc.playoff];
    const w = mc.winner[i];
    const y = w === 'red' ? 1 : w === 'blue' ? 0 : w === 'tie' ? 0.5 : null;
    const call = called(p[i], w);
    for (const a of slots) {
      a.n++;
      if (call !== null) { a.decided++; if (call) a.correct++; }
      if (y !== null) { a.brierSum += (p[i] - y) ** 2; a.brierN++; }
      if (rs[i] != null && bs[i] != null) {
        for (const [pred, actual] of [[rs[i], mc.red_score[i]], [bs[i], mc.blue_score[i]]]) {
          const e = pred - actual;
          a.absSum += Math.abs(e); a.sqSum += e * e; a.biasSum += e; a.scoreN++;
        }
      }
    }
  }
  const fin = (a) => ({
    n: a.n, decided: a.decided, correct: a.correct,
    calledShare: a.decided ? a.correct / a.decided : null,
    brier: a.brierN ? a.brierSum / a.brierN : null,
    mae: a.scoreN ? a.absSum / a.scoreN : null,
    rmse: a.scoreN ? Math.sqrt(a.sqSum / a.scoreN) : null,
    bias: a.scoreN ? a.biasSum / a.scoreN : null,
  });
  return { all: fin(acc.all), qual: fin(acc.qual), playoff: fin(acc.playoff) };
}

/** Biggest misses: decided matches where the winner had the lowest pre-match win probability. */
export function biggestMisses(ev, model, idx, limit = 5) {
  const p = col(ev.matches, 'epa_win_prob', model);
  const w = ev.matches.cols.winner;
  const out = [];
  for (let i = 0; i < Math.min(idx, ev.matches.n); i++) {
    if (!isPlayed(ev, i) || p[i] == null) continue;
    if (w[i] !== 'red' && w[i] !== 'blue') continue;
    const pWin = w[i] === 'red' ? p[i] : 1 - p[i];
    out.push({ mi: i, pWinner: pWin, winner: w[i] });
  }
  out.sort((a, b) => a.pWinner - b.pWinner || a.mi - b.mi);
  return out.slice(0, limit);
}

// ---------- match decomposition ----------

/** Vector key -> breakdown key ("auto_epa" -> "auto", "epa" -> "no_foul", "comp_3_epa" -> "comp_3"). */
export function actualKeyOf(vk) {
  if (vk === 'epa') return 'no_foul';
  return vk.replace(/_epa$/, '');
}

/**
 * Rows for the predicted-vs-actual table of one alliance. Each row: {id, kind, vk, label key, pred:{model: value},
 * actual}. kind: points | count | rp. The two score rows come first (with fouls, without fouls).
 */
export function componentRows(season, ev, mi, side, models) {
  const mc = ev.matches.cols;
  const vector = season.keys.vector;
  const units = season.keys.units || {};
  const names = season.keys.names || {};
  const rows = [];
  const idxOf = (vk) => vector.indexOf(vk);
  const predVec = (model) => col(ev.matches, side + '_pred', model)[mi];
  const scorePred = (model) => col(ev.matches, `epa_${side}_score_pred`, model)[mi];
  const noFoulPred = (model) => { const v = predVec(model); return v ? v[0] : null; };
  rows.push({
    id: 'score', kind: 'points', label: 'Score with fouls', strong: true,
    pred: Object.fromEntries(models.map((m) => [m, scorePred(m)])), actual: mc[side + '_score'][mi],
  });
  rows.push({
    id: 'no_foul', kind: 'points', label: 'Score without fouls', strong: true,
    pred: Object.fromEntries(models.map((m) => [m, noFoulPred(m)])), actual: mc[side + '_no_foul'][mi],
  });
  for (const vk of vector) {
    if (vk === 'epa') continue;
    const i = idxOf(vk);
    const bk = actualKeyOf(vk);
    const actualCol = mc[`${side}_${bk}`];
    const unit = units[vk] || 'points';
    let label;
    if (vk === 'auto_epa') label = 'Auto points';
    else if (vk === 'teleop_epa') label = 'Teleop points';
    else if (vk === 'endgame_epa') label = 'Endgame points';
    else if (vk === 'tiebreaker_epa') label = 'Tiebreaker points';
    else label = names[bk] || bk;
    rows.push({
      id: bk, vk, kind: unit === 'rp_score' ? 'rp' : unit === 'count' ? 'count' : 'points',
      nameKey: ['auto_epa', 'teleop_epa', 'endgame_epa', 'tiebreaker_epa'].includes(vk) ? null : (names[bk] || bk),
      label, group: vk.startsWith('comp_') ? 'comp' : unit === 'rp_score' ? 'rp' : 'phase',
      pred: Object.fromEntries(models.map((m) => { const v = predVec(m); return [m, v ? v[i] : null]; })),
      actual: actualCol ? actualCol[mi] : null,
    });
  }
  return rows;
}

/**
 * Robot-attributed vs alliance split for one alliance (SPEC 3.5.7).
 * scored[slot] = robot-attributed points for each of the three robots; remainder = no_foul - sum(scored).
 * Returns null when this match has no per-robot data.
 */
export function robotSplit(ev, mi, side) {
  const rb = ev.robot;
  if (!rb || !rb.scored || !rb.scored[mi]) return null;
  const off = side === 'red' ? 0 : 3;
  const mc = ev.matches.cols;
  const scored = rb.scored[mi].slice(off, off + 3);
  if (scored.every((x) => x == null)) return null;
  const sum = scored.reduce((a, b) => a + (b || 0), 0);
  const noFoul = mc[side + '_no_foul'][mi];
  const fields = (rb.fields || []).filter((f) => f.active !== false);
  const facts = [0, 1, 2].map((s) => fields.map((f) => {
    const arr = rb.values && rb.values[f.key] ? rb.values[f.key][mi] : null;
    return { key: f.key, value: arr && arr[off + s] != null ? f.codes[arr[off + s]] : null };
  }));
  const slotPoints = [0, 1, 2].map((s) => {
    const o = {};
    for (const [k, v] of Object.entries(rb.points || {})) {
      const x = v && v[mi] ? v[mi][off + s] : null;
      if (x) o[k] = x;
    }
    return o;
  });
  const ok = rb.ok && rb.ok[mi] ? rb.ok[mi][side === 'red' ? 0 : 1] : null;
  return { scored, sum, noFoul, remainder: noFoul == null ? null : noFoul - sum, facts, slotPoints, ok };
}

// ---------- events list ----------

export function eventStatus(row) {
  if (row.status === 'Completed') return 'complete';
  if ((row.completed_count || 0) > 0) return 'live';
  return 'upcoming';
}

/** Group event rows (already filtered) by week in ascending week order; null weeks last. */
export function groupByWeek(rows) {
  const g = new Map();
  for (const r of rows) {
    const k = r.week == null ? 99 : r.week;
    if (!g.has(k)) g.set(k, []);
    g.get(k).push(r);
  }
  return [...g.entries()].sort((a, b) => a[0] - b[0]).map(([k, list]) => ({ week: k === 99 ? null : k, rows: list }));
}

/** Alliance playoff result text from TBA status. */
export function allianceResult(a) {
  const s = a && a.status;
  if (!s) return '';
  if (s.status === 'won') return 'Won the event';
  const names = { f: 'finals', sf: 'semifinals', qf: 'quarterfinals', ef: 'eighth-finals' };
  if (s.status === 'eliminated') return s.level === 'f' ? 'Finalist' : `Eliminated in ${names[s.level] || 'playoffs'}`;
  if (s.status === 'playing') return `Playing in ${names[s.level] || 'playoffs'}`;
  return String(s.status || '');
}

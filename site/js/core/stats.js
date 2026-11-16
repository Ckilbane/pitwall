// Small ranking helpers shared by Teams and Team (competition ranking: ties share a rank).
import { median } from './fmt.js';

const ok = (v) => typeof v === 'number' && Number.isFinite(v);

/** Rank (1 = highest) of `value` among rows' getter values; ties share the better rank. */
export function rankOf(rows, getter, value) {
  if (!ok(value)) return null;
  let ahead = 0;
  for (const r of rows) { const v = getter(r); if (ok(v) && v > value) ahead++; }
  return ahead + 1;
}

export function countOf(rows, getter) {
  let n = 0;
  for (const r of rows) if (ok(getter(r))) n++;
  return n;
}

export function medianOf(rows, getter) { return median(rows.map(getter)); }

/** Rows sorted by value descending (nulls dropped), ties by team number. */
export function topBy(rows, getter, n) {
  return rows.filter((r) => ok(getter(r))).sort((a, b) => getter(b) - getter(a) || a.team - b.team).slice(0, n);
}

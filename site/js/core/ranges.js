// EPA ranges, tiers and head-to-head odds (SPEC 5.10).
//
// The pipeline exports, per team and model:
//   epa_se    calibrated SD of the error in a team's current EPA (points per match). EPA +- Z95 * epa_se
//             held the team's next-6-match level about 95% of the time (fit 2022-2024, 2025-2026 held out).
//   match_sd  how much one match swings around a team's EPA (points), shrunk toward the season pool.
//
//   rangeOf(epa, se)          -> {lo, hi} or null
//   pTrueAbove(a, b)          -> chance a's true EPA is above b's (0..1)
//   pMatchAbove(a, b)         -> chance a adds more points than b in one match (0..1)
//   tiers(rows)               -> rows (sorted by EPA, desc) grouped: [{n, rows, leader}]
//   verdict(p, a, b)          -> short plain-language call for pMatchAbove
export const Z95 = 1.96;
export const TIER_P = 0.9; // a new tier starts when its first team is 90% likely below the tier's leader

const num = (x) => typeof x === 'number' && Number.isFinite(x);

/** Standard normal CDF (Abramowitz-Stegun 7.1.26 on erf; |error| < 1.5e-7). */
export function phi(z) {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(z * z) / 2);
  return z >= 0 ? (1 + y) / 2 : (1 - y) / 2;
}

export function rangeOf(epa, se) {
  if (!num(epa) || !num(se)) return null;
  return { lo: epa - Z95 * se, hi: epa + Z95 * se };
}

export function pTrueAbove(a, b) {
  if (!num(a.epa) || !num(b.epa) || !num(a.epa_se) || !num(b.epa_se)) return null;
  const s = Math.sqrt(a.epa_se ** 2 + b.epa_se ** 2);
  return s > 0 ? phi((a.epa - b.epa) / s) : (a.epa > b.epa ? 1 : a.epa < b.epa ? 0 : 0.5);
}

/** One match: match_sd is the SD of actual minus pre-match rating, so it already holds the rating's
 *  own error. Checked on opposing teams in 2024-2026 quals: predicted and actual rates agree within
 *  about 3 points per 10% bin (pipeline/validate/ranges.md). */
export function matchSpread(r) {
  return num(r.match_sd) ? r.match_sd : null;
}

export function pMatchAbove(a, b) {
  const sa = matchSpread(a); const sb = matchSpread(b);
  if (sa === null || sb === null || !num(a.epa) || !num(b.epa)) return null;
  const s = Math.sqrt(sa ** 2 + sb ** 2);
  return s > 0 ? phi((a.epa - b.epa) / s) : 0.5;
}

/** Greedy tiers over rows sorted by EPA (highest first). Rows without a range join the current tier. */
export function tiers(rows) {
  const out = [];
  let cur = null;
  for (const r of rows) {
    const p = cur ? pTrueAbove(cur.leader, r) : null;
    if (!cur || (p !== null && p >= TIER_P)) {
      cur = { n: out.length + 1, rows: [], leader: r };
      out.push(cur);
    }
    cur.rows.push(r);
  }
  return out;
}

/** Plain description of a tier for its header row. */
export function tierBlurb(t, next) {
  if (t.rows.length === 1) return next ? 'Clear of the next tier' : 'On its own';
  return next ? `${t.rows.length} teams too close to separate` : `${t.rows.length} teams, overlapping`;
}

/** Out of 10, rounded, for words like "about 6 in 10". */
export const outOf10 = (p) => Math.max(0, Math.min(10, Math.round(p * 10)));

export function verdict(p, a, b) {
  if (p === null) return { head: 'Not enough data', text: `One of these teams has no range yet, so no call between ${a.team} and ${b.team}.` };
  const fav = p >= 0.5 ? a : b; const dog = p >= 0.5 ? b : a; const q = Math.max(p, 1 - p);
  const n = outOf10(q);
  if (q < 0.56) return { head: 'Coin flip', text: `${a.team} would add more points than ${b.team} in about half of their matches. EPA can't tell these two apart.` };
  if (q < 0.66) return { head: `Slight edge: ${fav.team}`, text: `${fav.team} would add more points than ${dog.team} in about ${n} of 10 matches. Close enough that scouting should decide.` };
  if (q < 0.8) return { head: `${fav.team} usually adds more`, text: `${fav.team} would add more points than ${dog.team} in about ${n} of 10 matches.` };
  return { head: `${fav.team} clearly ahead`, text: `${fav.team} would add more points than ${dog.team} in about ${n} of 10 matches.` };
}

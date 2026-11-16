// Portions © 2020 Abhijit Gupta (Statbotics), MIT License. See THIRD_PARTY.md.
// PitWall match predictor (WP3). Pure ES module, no DOM, no imports.
//
// Port of the vendored Statbotics EPA.predict_match (pipeline/vendor/backend/src/models/epa/main.py)
// and its helpers post_process_breakdown / get_score_from_breakdown (models/epa/breakdown.py),
// for seasons 2022-2026. Given each team's pre-match rating vector it returns exactly what the
// pipeline recorded for a match: alliance breakdowns (red_pred / blue_pred), RP probabilities,
// predicted scores (with foul inflation) and the red win probability.
//
// Both models use this file: Fast EPA reuses EPA.predict_match unchanged (SPEC 4.3); only k may
// differ, and it comes from season.predict.models[model].k.
//
// Upstream steps, in order (per alliance, the opponent's sum is the raw, unprocessed sum):
//   1. pred = sum of the alliance's (first 3) team vectors, 18-slot order, summed (a + b) + c.
//   2. post_process_breakdown:
//        rp_1, rp_2 (and rp_3 from 2025) -> unit_sigmoid(x) = 1 / (1 + exp(-4 (x - 0.5)));
//        2023: grid overflow (top > 9 spills to middle, middle > 9 to bottom, bottom > 9 back to
//              middle, adjusting teleop grid, cube and cone points and the total);
//        2025: processor algae revalued (+3 per own processor algae to processor points, +3 per
//              opponent processor algae to net points, +3 per either to teleop and total).
//   3. score = get_score_from_breakdown: 2023 piece formula; every other season no_foul (slot 0).
//   4. winProbRed = 1 / (1 + 10^(k * (red - blue) / score_sd)) on scores WITHOUT fouls.
//   5. reported scores = score * (1 + foul_rate).
// Steps 2018 (switch/scale power) and 2016/2017 (elim RP bonus) exist upstream but are outside
// 2022-2026 and are not ported.

export const NUM_TEAMS = 3;

// Position of each vector key (VK) in the upstream 18-slot rating vector (SPEC 3.2).
export const SLOT_INDEX = Object.freeze({
  epa: 0, auto_epa: 1, teleop_epa: 2, endgame_epa: 3,
  rp_1_epa: 4, rp_2_epa: 5, rp_3_epa: 6, tiebreaker_epa: 7,
  comp_0_epa: 8, comp_1_epa: 9, comp_2_epa: 10, comp_3_epa: 11, comp_4_epa: 12,
  comp_5_epa: 13, comp_6_epa: 14, comp_7_epa: 15, comp_8_epa: 16, comp_9_epa: 17,
});

// Copy of src/breakdown.py key_to_name comp names for 2022-2026 (checked against the fixtures,
// which carry the vendored table). Only used to locate slots by name, as upstream does with
// all_keys[year].index(name).
export const COMP_NAMES = Object.freeze({
  2022: ['auto_taxi_points', 'auto_cargo_lower', 'auto_cargo_upper', 'teleop_cargo_lower',
    'teleop_cargo_upper', 'endgame_points'],
  2023: ['auto_charge_station_points', 'auto_pieces', 'teleop_grid_points', 'bottom_pieces',
    'middle_pieces', 'top_pieces', 'cube_points', 'cone_points', 'links',
    'endgame_charge_station_points'],
  2024: ['auto_leave_points', 'auto_note_points', 'teleop_note_points', 'speaker_points',
    'amplified_notes', 'endgame_park_points', 'endgame_on_stage_points', 'endgame_harmony_points',
    'endgame_trap_points', 'endgame_spotlight_points'],
  2025: ['auto_coral_points', 'teleop_coral_points', 'coral_l1', 'coral_l2', 'coral_l3',
    'coral_l4', 'processor_algae', 'processor_algae_points', 'net_algae_points', 'barge_points'],
  2026: ['auto_fuel', 'auto_tower', 'transition_fuel', 'first_shift_fuel', 'second_shift_fuel',
    'endgame_fuel', 'endgame_tower'],
});

const STANDARD_KEYS = ['no_foul_points', 'auto_points', 'teleop_points', 'endgame_points',
  'rp_1', 'rp_2', 'rp_3', 'tiebreaker_points'];

/** upstream all_keys[year]: 8 standard names, then comp names, padded with "empty" to 18. */
export function allKeys(year) {
  const comps = COMP_NAMES[year];
  if (!comps) throw new RangeError(`predict.js supports seasons 2022-2026, not ${year}`);
  const keys = STANDARD_KEYS.concat(comps);
  while (keys.length < 18) keys.push('empty');
  return keys;
}

/** Seasons with a third ranking point (upstream: year >= 2025). */
export function hasRp3(year) {
  return year >= 2025;
}

export function unitSigmoid(x) {
  return 1 / (1 + Math.exp(-4 * (x - 0.5)));
}

function idx(keys, name) {
  const i = keys.indexOf(name);
  if (i < 0) throw new Error(`no slot named ${name}`);
  return i;
}

/** Sum of team vectors slot by slot, in team order: ((a + b) + c), as numpy's axis-0 sum. */
export function sumVectors(vecs) {
  const out = new Array(18).fill(0);
  if (!vecs.length) return out;
  for (let s = 0; s < 18; s++) {
    let acc = vecs[0][s];
    for (let t = 1; t < vecs.length; t++) acc += vecs[t][s];
    out[s] = acc;
  }
  return out;
}

/** Port of post_process_breakdown for 2022-2026. Returns a new 18-slot array. */
export function postProcessBreakdown(year, breakdown, oppBreakdown) {
  const keys = allKeys(year);
  const bd = breakdown.slice();
  let totalChange = 0;

  // year >= 2016
  bd[idx(keys, 'rp_1')] = unitSigmoid(bd[idx(keys, 'rp_1')]);
  bd[idx(keys, 'rp_2')] = unitSigmoid(bd[idx(keys, 'rp_2')]);
  if (hasRp3(year)) bd[idx(keys, 'rp_3')] = unitSigmoid(bd[idx(keys, 'rp_3')]);

  if (year === 2023) {
    let bottom = bd[idx(keys, 'bottom_pieces')];
    let middle = bd[idx(keys, 'middle_pieces')];
    let top = bd[idx(keys, 'top_pieces')];
    if (top > 9) {
      const extraTop = top - 9;
      middle += extraTop;
      top = 9;
      totalChange -= 2 * extraTop;
    }
    if (middle > 9) {
      const extraMid = middle - 9;
      bottom += extraMid;
      middle = 9;
      totalChange -= extraMid;
    }
    if (bottom > 9) {
      // upstream moves the bottom overflow back to middle and does not re-check middle
      const extraBot = bottom - 9;
      middle += extraBot;
      bottom = 9;
      totalChange += extraBot;
    }
    let teleopGrid = bd[idx(keys, 'teleop_grid_points')];
    let cube = bd[idx(keys, 'cube_points')];
    let cone = bd[idx(keys, 'cone_points')];
    const grid = cube + cone;
    teleopGrid += totalChange;
    if (grid > 0) {
      cube += (totalChange * cube) / grid;
      cone += (totalChange * cone) / grid;
    }
    bd[idx(keys, 'bottom_pieces')] = bottom;
    bd[idx(keys, 'middle_pieces')] = middle;
    bd[idx(keys, 'top_pieces')] = top;
    bd[idx(keys, 'teleop_grid_points')] = teleopGrid;
    bd[idx(keys, 'cube_points')] = cube;
    bd[idx(keys, 'cone_points')] = cone;
  } else if (year === 2025) {
    // upstream values processor algae at 3 internally; in the game it is 6 for you, and an
    // expected 3 for the opponent at the net
    const my = bd[idx(keys, 'processor_algae')];
    const opp = oppBreakdown[idx(keys, 'processor_algae')];
    const both = my + opp;
    bd[idx(keys, 'processor_algae_points')] += 3 * my;
    bd[idx(keys, 'net_algae_points')] += 3 * opp;
    bd[idx(keys, 'teleop_points')] += 3 * both;
    totalChange += 3 * both;
  }

  bd[0] += totalChange;
  return bd;
}

/** Port of get_score_from_breakdown for 2022-2026 (no elim terms in these seasons). */
export function scoreFromBreakdown(year, bd) {
  const keys = allKeys(year);
  if (year === 2023) {
    let score = 0;
    score += bd[idx(keys, 'auto_charge_station_points')];
    score += 2 * bd[idx(keys, 'bottom_pieces')];
    score += 3 * bd[idx(keys, 'middle_pieces')];
    score += 5 * bd[idx(keys, 'top_pieces')];
    score += bd[idx(keys, 'auto_pieces')];
    score += 5 * Math.min(9, bd[idx(keys, 'links')]);
    score += Math.min(30, bd[idx(keys, 'endgame_charge_station_points')]);
    return score;
  }
  return bd[idx(keys, 'no_foul_points')];
}

/**
 * Core predictor on 18-slot vectors.
 * @param {number} year
 * @param {number[][]} redVecs18  up to 3 (extra entries ignored, as upstream slices [:3])
 * @param {number[][]} blueVecs18
 * @param {{k:number, scoreSd:number, foulRate:number, elim?:boolean}} c
 */
export function predictMatch18(year, redVecs18, blueVecs18, c) {
  const red = redVecs18.slice(0, NUM_TEAMS);
  const blue = blueVecs18.slice(0, NUM_TEAMS);
  const redSum = sumVectors(red);
  const blueSum = sumVectors(blue);
  const redBd = postProcessBreakdown(year, redSum, blueSum);
  const blueBd = postProcessBreakdown(year, blueSum, redSum);
  const keys = allKeys(year);
  const rps = (bd) => [bd[idx(keys, 'rp_1')], bd[idx(keys, 'rp_2')],
    hasRp3(year) ? bd[idx(keys, 'rp_3')] : null];

  const redScore = scoreFromBreakdown(year, redBd);
  const blueScore = scoreFromBreakdown(year, blueBd);
  const normDiff = (redScore - blueScore) / c.scoreSd;
  const winProbRed = 1 / (1 + Math.pow(10, c.k * normDiff));
  const f = 1 + c.foulRate;
  return {
    red: { score: redScore * f, scoreNoFouls: redScore, breakdown18: redBd, rp: rps(redBd) },
    blue: { score: blueScore * f, scoreNoFouls: blueScore, breakdown18: blueBd, rp: rps(blueBd) },
    winProbRed,
    winner: winProbRed >= 0.5 ? 'red' : 'blue',
  };
}

/** Season vector keys (VK) for a decoded season.json (or any {keys: {vector}}). */
function vectorKeys(season) {
  const vk = season && season.keys && season.keys.vector;
  if (!Array.isArray(vk) || !vk.length) throw new TypeError('season.keys.vector missing');
  return vk;
}

/**
 * One team's rating -> 18-slot vector. Accepts an array aligned with season.keys.vector, or an
 * object keyed by VK (`epa`, `auto_epa`, ...) or by `pre_<vk>` (team_events rows).
 * Slots outside VK are 0; they never affect a 2022-2026 prediction.
 */
export function toSlots(vec, vk) {
  const out = new Array(18).fill(0);
  for (let i = 0; i < vk.length; i++) {
    const key = vk[i];
    let v;
    if (Array.isArray(vec)) v = vec[i];
    else if (vec) v = vec[key] !== undefined ? vec[key] : vec[`pre_${key}`];
    if (typeof v !== 'number' || !Number.isFinite(v)) {
      throw new TypeError(`rating vector has no number for ${key}`);
    }
    out[SLOT_INDEX[key]] = v;
  }
  return out;
}

/** 18-slot array -> {vk: value} and the VK-aligned array (red_pred / blue_pred layout). */
function byVk(bd18, vk) {
  const obj = {};
  const arr = vk.map((k) => {
    obj[k] = bd18[SLOT_INDEX[k]];
    return obj[k];
  });
  return { obj, arr };
}

/**
 * SPEC 5.2 API.
 * @param {object} season  decoded season.json: uses season.season, season.keys.vector,
 *                         season.predict.{score_sd, foul_rate, models[model].k}
 * @param {Array} redVecs  3 team vectors (arrays aligned with keys.vector, or objects by VK)
 * @param {Array} blueVecs
 * @param {{model?: string, elim?: boolean}} opts  model defaults to "epa"
 * @returns {{red: {score, scoreNoFouls, breakdown, vector, rp}, blue: {...}, winProbRed, winner}}
 *   breakdown: {vk: alliance value} after post-processing (rp entries are probabilities, point
 *   entries without foul scaling, like red_pred); vector: same values aligned with keys.vector;
 *   rp: [p1, p2, p3] (p3 null before 2025); score: with foul inflation (= epa_*_score_pred).
 */
export function predictAlliances(season, redVecs, blueVecs, opts = {}) {
  const vk = vectorKeys(season);
  const year = Number(season.season);
  const p = season.predict;
  if (!p) throw new TypeError('season.predict missing');
  const model = opts.model || 'epa';
  const m = p.models && p.models[model];
  if (!m || typeof m.k !== 'number') throw new TypeError(`no k for model ${model}`);
  const res = predictMatch18(
    year,
    redVecs.slice(0, NUM_TEAMS).map((v) => toSlots(v, vk)),
    blueVecs.slice(0, NUM_TEAMS).map((v) => toSlots(v, vk)),
    { k: m.k, scoreSd: p.score_sd, foulRate: p.foul_rate, elim: !!opts.elim },
  );
  const side = (s) => {
    const b = byVk(s.breakdown18, vk);
    return { score: s.score, scoreNoFouls: s.scoreNoFouls, breakdown: b.obj, vector: b.arr, rp: s.rp };
  };
  return { red: side(res.red), blue: side(res.blue), winProbRed: res.winProbRed, winner: res.winner };
}


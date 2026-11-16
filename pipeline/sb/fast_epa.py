"""Fast EPA (SPEC 4.3, 4.4): the vendored EPA with faster early dampening, optional between-event
passback, optional piece-count seed and per-robot attribution.  Every behaviour is a config
switch; with the EPA-equivalent settings (`EPA_EQUIVALENT`) the run is byte-identical to EPA.

    from sb.fast_epa import FastEPA, load_config
    model = FastEPA(load_config(), robot_facts)        # used through sb.model_runner.run_model

The prediction step is upstream `EPA.predict_match`, unchanged (only `k` may differ), so the
browser predictor serves both models.

Leak-freedom: every override reads only state built from matches already processed.  Passback
replays a team's qual matches at its PREVIOUS event (all processed before the current match)
and runs just before the team's first match at a new event, i.e. before that match is
predicted.

Robot facts: `sb.perrobot.robot_facts(year, raw)` (WP2) when that module exists; otherwise
`build_robot_facts` below, a self-contained reader of docs/perrobot_fields.json with the same
output shape (plus `AllianceFacts.teams`).
"""
import copy
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import sb  # noqa: F401
import sb.loader  # noqa: F401  (installs the shim)
from sb.model_runner import RecordingMixin

from src.constants import EPS  # noqa: E402
from src.models.epa.breakdown import post_process_attrib  # noqa: E402
from src.models.epa.constants import (  # noqa: E402
    INIT_PENALTY,
    MEAN_REVERSION,
    NORM_MEAN,
    NORM_SD,
    YEAR_ONE_WEIGHT,
)
from src.models.epa.init import get_constants  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402
from src.models.epa.math import EPARating, inv_unit_sigmoid  # noqa: E402
from src.models.template import Model  # noqa: E402
from src.models.types import Attribution  # noqa: E402
from src.utils.utils import r  # noqa: E402

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(PIPELINE)
CONFIG_PATH = os.path.join(PIPELINE, "models", "fast_epa.json")
PERROBOT_PATH = os.path.join(REPO, "docs", "perrobot_fields.json")

SLOT_INDEX = {"no_foul": 0, "auto": 1, "teleop": 2, "endgame": 3, "rp_1": 4, "rp_2": 5,
              "rp_3": 6, "tiebreaker": 7, **{f"comp_{i}": 8 + i for i in range(10)}}
AGGREGATES = (0, 1, 2, 3)  # total(no_foul), auto, teleop, endgame
ACTIVE_RATE = 0.99

# piece-count rating per season (SPEC 4.3 seed "pieces")
PIECE_COMPS = {2022: [1, 2, 3, 4], 2023: [3, 4, 5], 2024: [1, 2], 2025: [2, 3, 4, 5]}

EPA_EQUIVALENT: Dict[str, Any] = {
    "percent": {"first_n": 0, "first": 1 / 3, "start": 1 / 3, "end": 0.2, "ramp_start": 6,
                "ramp_len": 6},
    "elim_weight": 1 / 3,
    "mean_reversion": 0.4,
    "year_one_weight": 0.7,
    "seed": "norm_epa",
    "robot_attribution": False,
    "robot_weight": 1.0,
    "residual_split": "equal",
    "passback": {"enabled": False, "percent_scale": 0.5, "scope": "quals"},
    "win_k": -0.625,
}


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def settings_of(config: Optional[dict]) -> Dict[str, Any]:
    """Full settings dict: EPA-equivalent defaults overlaid with config['settings'] (or config)."""
    s = copy.deepcopy(EPA_EQUIVALENT)
    if config:
        src = config.get("settings", config)
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(s.get(k), dict):
                s[k].update(v)
            else:
                s[k] = v
    return s


def _is_epa_percent(p: Dict[str, Any]) -> bool:
    e = EPA_EQUIVALENT["percent"]
    return (p["first_n"] == 0 and abs(p["start"] - e["start"]) < 1e-12
            and abs(p["end"] - e["end"]) < 1e-12 and p["ramp_start"] == e["ramp_start"]
            and p["ramp_len"] == e["ramp_len"])


# --------------------------------------------------------------------------------------------
# Robot facts (fallback for WP2's sb.perrobot)
# --------------------------------------------------------------------------------------------

@dataclass
class AllianceFacts:
    values: Dict[str, List[Optional[str]]]
    points: np.ndarray  # (3, 18), NaN = not robot-attributable
    ok: bool
    teams: List[int] = field(default_factory=list)  # team_keys[0..2] as numbers


@dataclass
class RobotFacts:
    by_match: Dict[str, Tuple[Optional[AllianceFacts], Optional[AllianceFacts]]]
    active_parts: List[dict]
    match_rate: Dict[str, float]


def _part_points(part: dict, vals: List[Optional[str]], bd: dict) -> Tuple[List[float], bool]:
    rule = part["rule"]
    check = bd.get(part["check_field"])
    check = 0 if check is None else check
    pts = [0.0, 0.0, 0.0]
    if rule == "map":
        pts = [float(part["points"].get(v, 0)) if v is not None else 0.0 for v in vals]
    elif rule == "split_once":
        q = [i for i, v in enumerate(vals) if v in part["qualifying"]]
        for i in q:
            pts[i] = check / len(q)
    elif rule == "map_bridge":
        level = bd.get(part["state_field"]) == part["level_value"]
        table = part["points_level"] if level else part["points_unlevel"]
        pts = [float(table.get(v, 0)) if v is not None else 0.0 for v in vals]
    elif rule == "chain_share":
        locs = defaultdict(list)
        for i, v in enumerate(vals):
            if v in part["locations"]:
                locs[v].append(i)
        sharing = [i for idx in locs.values() if len(idx) >= 2 for i in idx]
        for i in sharing:
            pts[i] = check / len(sharing)
    elif rule == "location_flag":
        for i, v in enumerate(vals):
            if v in part["locations"] and bd.get(part["flag_prefix"] + v) is True:
                pts[i] += float(part["points_per_robot"])
    elif rule == "location_credit":
        onstage = [i for i, v in enumerate(vals) if v in part["locations"]]
        for loc in part["locations"]:
            if bd.get(part["flag_prefix"] + loc) is True:
                at = [i for i in onstage if vals[i] == loc] or onstage
                for i in at:
                    pts[i] += part["points_per_flag"] / len(at)
    else:
        return pts, False
    ok = abs(sum(pts) - check) < 1e-6
    return pts, ok


def build_robot_facts(year: int, raw: dict, spec_path: str = PERROBOT_PATH) -> RobotFacts:
    """Per-robot facts from the raw dump using docs/perrobot_fields.json (SPEC 4.4 shape)."""
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    season = spec["seasons"].get(str(year), {"fields": []})
    parts = []
    for fld in season["fields"]:
        for p in fld["parts"]:
            parts.append({**p, "key": fld["key"], "tba": fld["tba"],
                          "id": f"{fld['key']}:{p['slot']}:{p['check_field']}"})
    tally = {p["id"]: [0, 0] for p in parts}
    staged = []
    for ed in (raw.get("event_data") or {}).values():
        for m in ed.get("matches") or []:
            sbd = m.get("score_breakdown")
            pair = []
            for color in ("red", "blue"):
                bd = sbd.get(color) if isinstance(sbd, dict) else None
                if not isinstance(bd, dict):
                    pair.append(None)
                    continue
                keys = m["alliances"][color]["team_keys"][:3]
                teams = [int(k[3:]) for k in keys]
                values: Dict[str, List[Optional[str]]] = {}
                per_part = []
                for p in parts:
                    vals = [bd.get(p["tba"].replace("{n}", str(n))) for n in (1, 2, 3)]
                    values[p["key"]] = vals
                    if all(v is None for v in vals):
                        per_part.append((p, None, False))
                        tally[p["id"]][1] += 1
                        continue
                    pts, ok = _part_points(p, vals, bd)
                    per_part.append((p, pts, ok))
                    tally[p["id"]][0] += int(ok)
                    tally[p["id"]][1] += 1
                pair.append((teams, values, per_part))
            staged.append((m["key"], pair))
    rate = {pid: (a / n if n else 0.0) for pid, (a, n) in tally.items()}
    active_ids = {pid for pid, x in rate.items() if x >= ACTIVE_RATE}
    active = [p for p in parts if p["id"] in active_ids]
    by_match = {}
    for key, pair in staged:
        out = []
        for a in pair:
            if a is None:
                out.append(None)
                continue
            teams, values, per_part = a
            pts = np.full((3, 18), np.nan)
            ok = True
            for p, pp, pok in per_part:
                if p["id"] not in active_ids:
                    continue
                ok = ok and pok and pp is not None
                if pp is None or p.get("no_comp_slot") or not p["slot"].startswith("comp_"):
                    continue
                s = SLOT_INDEX[p["slot"]]
                col = np.nan_to_num(pts[:, s])
                pts[:, s] = col + np.array(pp)
            out.append(AllianceFacts(values=values, points=pts, ok=bool(ok), teams=teams))
        by_match[key] = (out[0], out[1])
    return RobotFacts(by_match=by_match, active_parts=active, match_rate=rate)


def robot_facts(year: int, raw: dict):
    """WP2's sb.perrobot.robot_facts when it exists, else the fallback above."""
    try:
        from sb import perrobot  # type: ignore
    except ImportError:
        return build_robot_facts(year, raw)
    return perrobot.robot_facts(year, raw)


def robot_slot_map(facts) -> Tuple[List[int], Dict[int, List[int]]]:
    """(robot-credited comp slots, {aggregate slot K: [robot slots contained in K]})."""
    slots = set()
    agg: Dict[int, set] = defaultdict(set)
    for p in getattr(facts, "active_parts", []) or []:
        if p.get("no_comp_slot") or not str(p.get("slot", "")).startswith("comp_"):
            continue
        c = SLOT_INDEX[p["slot"]]
        slots.add(c)
        for k in p.get("also_in", []):
            if k in SLOT_INDEX and SLOT_INDEX[k] in AGGREGATES:
                agg[SLOT_INDEX[k]].add(c)
    return sorted(slots), {k: sorted(v) for k, v in sorted(agg.items())}


# --------------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------------

def _piece_z(team_years: Dict[int, Any], year: int) -> Dict[int, float]:
    comps = PIECE_COMPS.get(year)
    if not comps:
        return {}
    vals = {}
    for t, ty in team_years.items():
        if (getattr(ty, "count", 0) or 0) <= 0:
            continue
        xs = [getattr(ty, f"comp_{i}_epa", None) for i in comps]
        if any(x is None for x in xs):
            return {}  # prior season not computed in-process (CSV/archive): no comp ratings
        vals[t] = float(sum(float(x) for x in xs))
    if len(vals) < 2:
        return {}
    a = np.array(list(vals.values()))
    mu, sd = float(a.mean()), float(a.std())
    if sd <= 0:
        return {}
    return {t: (v - mu) / sd for t, v in vals.items()}


class FastEPACore(EPA):
    """Fast EPA behaviour on top of the vendored EPA (no recording; see FastEPA)."""

    def __init__(self, config: Optional[dict] = None, robot_facts=None):
        super().__init__()
        self.config = config if config is not None else load_config()
        self.s = settings_of(self.config)
        self.facts = robot_facts if self.s["robot_attribution"] else None
        self.robot_slots, self.robot_agg = robot_slot_map(self.facts) if self.facts else ([], {})
        self._epa_percent = _is_epa_percent(self.s["percent"])
        self._in_pb = 0
        self.cur_event: Dict[int, str] = {}
        self.event_quals: Dict[int, List[Tuple[Any, Any]]] = defaultdict(list)
        self.passbacks = 0

    # ---- dampening --------------------------------------------------------------------------
    def percent(self, x: int) -> float:
        p = self.s["percent"]
        if self._epa_percent:
            return EPA.percent_func(self.year_num, x)
        if x < p["first_n"]:
            return float(p["first"])
        v = p["start"] - (p["start"] - p["end"]) * (x - p["ramp_start"]) / p["ramp_len"]
        return float(min(p["start"], max(p["end"], v)))

    # ---- season start / seed ----------------------------------------------------------------
    def _init_rating(self, year, z1: Optional[float], z2: Optional[float], ty1, ty2,
                     mean_reversion: float) -> EPARating:
        """Upstream get_init_epa with year_one_weight as a setting and an optional z seed."""
        num_teams, year_mean, year_sd = get_constants(year)
        yow = self.s["year_one_weight"]
        INIT_EPA = NORM_MEAN - INIT_PENALTY * NORM_SD
        norm_epa_1 = norm_epa_2 = INIT_EPA
        if z1 is not None:
            norm_epa_1 = NORM_MEAN + NORM_SD * z1
        elif ty1 is not None and ty1.norm_epa is not None:
            norm_epa_1 = ty1.norm_epa
        if z2 is not None:
            norm_epa_2 = NORM_MEAN + NORM_SD * z2
        elif ty2 is not None and ty2.norm_epa is not None:
            norm_epa_2 = ty2.norm_epa

        prev_norm_epa = yow * norm_epa_1 + (1 - yow) * norm_epa_2
        curr_norm_epa = (1 - mean_reversion) * prev_norm_epa + mean_reversion * INIT_EPA
        curr_epa_z_score = (curr_norm_epa - NORM_MEAN) / NORM_SD
        curr_epa_z_score = max(-year_mean / num_teams / year_sd, curr_epa_z_score)

        mean = year.get_mean_components()
        sd_frac = (year_sd or 0) / (year_mean or 1)
        sd = mean * sd_frac
        if year.year >= 2016:
            mean[4] = max(-1, inv_unit_sigmoid(max(EPS, min(1 - EPS, mean[4]))))
            mean[5] = max(-1, inv_unit_sigmoid(max(EPS, min(1 - EPS, mean[5]))))
            mean[6] = max(-1, inv_unit_sigmoid(max(EPS, min(1 - EPS, mean[6]))))
        curr_epa_mean = mean / num_teams + sd * curr_epa_z_score
        return EPARating(curr_epa_mean)

    def start_season(self, year, all_team_years, team_years) -> None:
        Model.start_season(self, year, all_team_years, team_years)
        self.k = float(self.s["win_k"])
        mr_default = float(self.s["mean_reversion"])

        piece_z: Dict[int, Dict[int, float]] = {}
        if self.s["seed"] == "pieces":
            for py in range(self.year_num - 1, self.year_num - 5, -1):
                if py in all_team_years:
                    piece_z[py] = _piece_z(all_team_years[py], py)
        elif self.s["seed"] != "norm_epa":
            raise ValueError(f"unknown seed {self.s['seed']!r}")

        init_rating = self._init_rating(year, None, None, None, None, mr_default)
        self.epas: Dict[int, EPARating] = defaultdict(lambda: init_rating)
        self.counts: Dict[int, int] = defaultdict(int)

        for team_year in team_years.values():
            num = team_year.team
            found: List[Tuple[int, Any]] = []
            for past_year in range(self.year_num - 1, self.year_num - 5, -1):
                pty = all_team_years.get(past_year, {}).get(num, None)
                if pty is not None:
                    found.append((past_year, pty))
            (y1, ty1) = found[0] if len(found) > 0 else (None, None)
            (y2, ty2) = found[1] if len(found) > 1 else (None, None)

            mean_reversion = mr_default
            if year.year == 2026 and team_year.district == "isr":
                mean_reversion = 0

            z1 = z2 = None
            if piece_z and y1 is not None and num in piece_z.get(y1, {}):
                z1 = piece_z[y1][num]
                if y2 is not None:
                    z2 = piece_z.get(y2, {}).get(num)
            rating = self._init_rating(year, z1, z2, ty1, ty2, mean_reversion)
            self.epas[num] = rating
            team_year.epa_start = r(rating.mean[0], 2)
            self.post_record_team(num, None, team_year)

    # ---- passback ---------------------------------------------------------------------------
    def predict_match(self, match, event):
        pb = self.s["passback"]
        if pb.get("enabled") and not self._in_pb:
            teams = match.get_red()[: self.num_teams] + match.get_blue()[: self.num_teams]
            for t in sorted(set(teams)):
                prev = self.cur_event.get(t)
                if prev is not None and prev != match.event:
                    self._passback(t, float(pb["percent_scale"]))
                if prev != match.event:
                    self.cur_event[t] = match.event
                    self.event_quals[t] = []
        return super().predict_match(match, event)

    def _passback(self, team: int, scale: float) -> None:
        history = self.event_quals.get(team) or []
        if not history:
            return
        self._in_pb += 1
        rec_prev = getattr(self, "_rec_off", 0)
        if hasattr(self, "_rec_off"):
            self._rec_off = rec_prev + 1
        try:
            for m, ev in history:
                _, red_pred, blue_pred = EPA.predict_match(self, m, ev)
                attrs = FastEPACore.attribute_match(self, m, red_pred, blue_pred)
                a = attrs.get(team)
                if a is None:
                    continue
                weight = self.s["elim_weight"] if m.elim else 1
                p = self.percent(self.counts[team]) * scale
                self.epas[team].add_obs(a.epa, p, weight)
                self.passbacks += 1
        finally:
            self._in_pb -= 1
            if hasattr(self, "_rec_off"):
                self._rec_off = rec_prev

    # ---- attribution ------------------------------------------------------------------------
    def attribute_match(self, match, red_pred, blue_pred):
        out = EPA.attribute_match(self, match, red_pred, blue_pred)
        if self.facts is None or not self.robot_slots:
            return out
        pair = self.facts.by_match.get(match.key)
        if pair is None:
            return out
        red_bd, blue_bd = match.get_breakdowns()
        red_teams = match.get_red()[: self.num_teams]
        blue_teams = match.get_blue()[: self.num_teams]
        w = float(self.s["robot_weight"])
        for teams, bd, pred_bd, af in (
            (red_teams, red_bd, red_pred.breakdown, pair[0]),
            (blue_teams, blue_bd, blue_pred.breakdown, pair[1]),
        ):
            if af is None or not af.ok or len(teams) != 3:
                continue
            ft = getattr(af, "teams", None)
            if ft is not None and list(ft)[:3] != list(teams):
                continue  # counted three differ from TBA's first three team keys (SPEC R6)
            y = np.asarray(af.points, dtype=float)
            # facts must reproduce the alliance's own slot values (e.g. no zero-score alliance)
            if any(abs(np.nansum(y[:, c]) - float(bd[c])) > 1e-6 or np.isnan(y[:, c]).any()
                   for c in self.robot_slots):
                continue
            E = bd - pred_bd
            mus = [self.epas[t].mean for t in teams]
            errs = [np.array(E / self.num_teams, dtype=float) for _ in teams]
            own = [{c: float(y[i, c] - mus[i][c]) for c in self.robot_slots} for i in range(3)]
            for i in range(3):
                for c in self.robot_slots:
                    errs[i][c] = own[i][c]
            for K, RK in self.robot_agg.items():
                rs = [sum(own[i][c] for c in RK) for i in range(3)]
                Q = float(E[K]) - sum(rs)
                if self.s["residual_split"] == "share":
                    sh = [max(0.5, float(mus[i][K]) - sum(float(mus[i][c]) for c in RK))
                          for i in range(3)]
                    tot = sum(sh)
                    split = [x / tot for x in sh]
                elif self.s["residual_split"] == "equal":
                    split = [1 / 3] * 3
                else:
                    raise ValueError(f"unknown residual_split {self.s['residual_split']!r}")
                for i in range(3):
                    errs[i][K] = rs[i] + split[i] * Q
            for i, t in enumerate(teams):
                err = w * errs[i] + (1 - w) * (E / self.num_teams)
                attrib = post_process_attrib(self.year_obj, self.epas[t].mean, err, match.elim)
                out[t] = Attribution(attrib)
        return out

    # ---- update -----------------------------------------------------------------------------
    def update_team(self, team, attrib, match) -> None:
        weight = self.s["elim_weight"] if match.elim else 1
        percent = self.percent(self.counts[team])
        self.epas[team].add_obs(attrib.epa, percent, weight)
        if not match.elim:
            self.counts[team] += 1
            if self.s["passback"].get("enabled") and not self._in_pb:
                self.event_quals[team].append((match, None))
        elif self.s["passback"].get("enabled") and self.s["passback"].get("scope") == "all" \
                and not self._in_pb:
            self.event_quals[team].append((match, None))


class FastEPA(RecordingMixin, FastEPACore):
    """Fast EPA with recording (the model `run_model(..., "fast")` uses)."""

    def __init__(self, config: Optional[dict] = None, robot_facts=None):
        FastEPACore.__init__(self, config, robot_facts)
        self._rec_init()

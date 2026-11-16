"""Shard writers (SPEC 3): ModelRuns + features + backtests + per-robot facts -> data/ files.

    files = write_season(year, runs, features, backtests, robot, raw, out_dir, meta)
    write_global(out_dir, seasons_meta, models_info, history_rows, manifest_meta)

Write order inside a season: event files, asof, traj, season.json (embeds the event file hashes).
The manifest is written last, by `write_global`.  All output goes through `sb.jsonout`, so the bytes
are a pure function of the inputs (no wall clock: `meta["generated_at"]` is an input).

Inputs
  runs        {"epa": ModelRun, "fast": ModelRun}; duck-typed (SPEC 4.1): model_id, year, objs,
              alliance_pred {match_key: (red18, blue18)}, attrib {match_key: {team: vec18}},
              updated {(match_key, team)}, config_hash, optional `k` (win-prob constant).
  features    {"epa": SeasonFeatures | None, "fast": ...} (SPEC 4.6: .team_year[team],
              .team_event[(team, event)] dicts) or None -> all feature columns null.
  backtests   {"epa": summary, "fast": summary} (backtest.score_season) or None / missing model ->
              a documented-formula fallback computed here (marked leak_audit.cuts == 0).
  robot       perrobot.RobotFacts or None (no per-robot data).
  raw         the raw TBA dump dict (only /event/{key}/alliances is read from it).
  meta        generated_at (ISO Z), data_version, plus optional current_season, updated_at,
              last_tba_change_at, stale, stale_reason, inputs_hash, code_hash, today, k {model: k}.

Rounding (SPEC 3.1 as amended): team_matches post vectors, team_events pre_* and attr_* are exported
with 3 decimals (rp 4) from the unrounded vectors a ModelRun carries (`pre_vec`, `post_vec`,
`final_vec`; fallback = upstream's 2-decimal pre_epas/epas); predict.foul_rate is a full float.
Other ratings 2 decimals (rp_*_epa 4), norm/unitless integers, probabilities and
metrics 4, predicted scores 2.  Round trips against the CSV export therefore compare equal to
`round(csv_value, nd)`; `TeamYear.rp_3_epa` is the one column upstream stores with 5 decimals.
"""
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

import sb  # noqa: F401
from sb import season_meta as sm
from sb.export import processing_order
from sb.jsonout import dump_gz, dump_json, rint, rnd

MODELS = ("epa", "fast")
SCHEMA = 1

# Year metric fields (everything after the percentile block in sb.models.Year), upstream order.
_YEAR_METRIC_START = "count"


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------

def _vk_nd(vk: str) -> int:
    return 4 if vk.startswith("rp_") else 2


def _vnd(vk: str) -> int:
    """Decimals of exported pre/post rating vectors and attributions: 3 (rp slots 4), orchestrator change."""
    return 4 if vk.startswith("rp_") else 3


_FULL_NAMES = list(sm.SLOT_INDEX)


def _vget(run: Any, mo: Any, match_key: str, team: int, which: str) -> Optional[Dict[str, Any]]:
    """Pre- or post-match rating vector of (match, team) as {vk name: value}.

    Uses the unrounded vectors a ModelRun may carry (`pre_vec` / `post_vec`: {(match_key, team): 18-slot
    array}); upstream's own pre_epas/epas are rounded to 2 decimals, which is the fallback.
    """
    d = (mo.pre_epas if which == "pre" else mo.epas) or {}
    base = d.get(str(team))
    if base is None:
        return None
    vec = getattr(run, f"{which}_vec", None)
    if vec:
        arr = vec.get((match_key, team))
        if arr is not None:
            return {n: float(arr[i]) for n, i in sm.SLOT_INDEX.items()}
    return base


def _enum(x: Any) -> Any:
    return getattr(x, "value", x)


def _metric_nd(name: str) -> int:
    return 2 if ("score_rmse" in name or "score_error" in name) else 4


def _metric(name: str, v: Any) -> Any:
    if v is None:
        return None
    if name.endswith("count"):
        return int(v)
    return rnd(v, _metric_nd(name))


def _split_ints(s: Optional[str]) -> List[int]:
    return [int(x) for x in (s or "").split(",") if x != ""]


def _check_meta(meta: Dict[str, Any]) -> None:
    for k in ("generated_at", "data_version"):
        if not meta.get(k):
            raise ValueError(f"meta[{k!r}] is required")


@dataclass
class SeasonFiles:
    season: int
    out_dir: str
    files: Dict[str, Dict[str, Any]]          # season/asof/traj -> {path, hash, bytes}
    events: Dict[str, Dict[str, Any]]         # event key -> {path, hash, bytes}
    manifest_entry: Dict[str, Any]
    backtest: Dict[str, Any]
    history: Dict[str, Any]
    counts: Dict[str, int]
    notes: List[str] = field(default_factory=list)

    @property
    def events_count(self) -> int:
        return len(self.events)

    @property
    def events_bytes(self) -> int:
        return sum(v["bytes"] for v in self.events.values())


def _model_k(runs: Dict[str, Any], model: str, year: int, meta: Dict[str, Any]) -> float:
    k = (meta.get("k") or {}).get(model)
    if k is None:
        k = getattr(runs[model], "k", None)
    if k is None:
        from src.models.epa.main import EPA

        k = EPA.k_func(year)
    return float(k)


# ---------------------------------------------------------------------------------------------
# fallback backtest (documented formulas, SPEC 4.5); WP3b's backtest.score_season replaces it
# ---------------------------------------------------------------------------------------------

def _empty_metrics() -> Dict[str, Any]:
    return {"n": 0, "ties": 0, "acc": None, "acc_no_ties": None, "brier": None, "log_loss": None,
            "conf": None, "score_n": 0, "score_mae": None, "score_rmse": None, "score_bias": None,
            "rp": {}}


def _metrics_from(rows: List[Dict[str, Any]], rp_keys: Sequence[str]) -> Dict[str, Any]:
    from src.constants import EPS

    out = _empty_metrics()
    if not rows:
        return out
    p = np.clip(np.array([r["p"] for r in rows]), EPS, 1 - EPS)
    o = np.array([r["o"] for r in rows])
    out["n"] = len(rows)
    out["ties"] = int((o == 0.5).sum())
    out["acc"] = rnd(((p > 0.5) == (o > 0.5)).mean(), 4)
    nt = o != 0.5
    out["acc_no_ties"] = rnd(((p > 0.5) == (o > 0.5))[nt].mean(), 4) if nt.any() else None
    out["brier"] = rnd(((p - o) ** 2).mean(), 4)
    out["log_loss"] = rnd(-(o * np.log(p) + (1 - o) * np.log(1 - p)).mean(), 4)
    out["conf"] = rnd(np.maximum(p, 1 - p).mean(), 4)
    pred = np.array([x for r in rows for x in (r["rp_pred"], r["bp_pred"])])
    act = np.array([x for r in rows for x in (r["r_act"], r["b_act"])], dtype=float)
    out["score_n"] = len(pred)
    d = pred - act
    out["score_mae"] = rnd(np.abs(d).mean(), 4)
    out["score_rmse"] = rnd(np.sqrt((d ** 2).mean()), 4)
    out["score_bias"] = rnd(d.mean(), 4)
    for rk in rp_keys:
        pr, ac = [], []
        for r in rows:
            if r["qual"] and r["rp"].get(rk) is not None:
                for pp, aa in r["rp"][rk]:
                    pr.append(pp)
                    ac.append(aa)
        if pr:
            pr_a, ac_a = np.array(pr), np.array(ac, float)
            out["rp"][rk] = {"n": len(pr), "brier": rnd(((pr_a - ac_a) ** 2).mean(), 4),
                             "acc": rnd(((pr_a > 0.5) == (ac_a > 0.5)).mean(), 4)}
        else:
            out["rp"][rk] = {"n": 0, "brier": None, "acc": None}
    return out


def fallback_backtest(year: int, run: Any, order: List[Any], config_hash: str = "") -> Dict[str, Any]:
    """Backtest summary from the recorded pre-match predictions (documented SPEC 4.5 formulas).

    Not a replacement for `backtest.score_season`: `leak_audit.cuts` is 0 (nothing was audited) and
    `passed` is false.  Used only until WP3b's harness is wired in.
    """
    objs = run.objs
    events = objs[2]
    rp_keys = ["rp_1", "rp_2"] + (["rp_3"] if sm.has_rp3(year) else [])
    seen_season: Dict[int, int] = defaultdict(int)
    seen_event: Dict[Tuple[int, str], int] = defaultdict(int)
    upd = getattr(run, "updated", set())
    rows: List[Dict[str, Any]] = []
    for m in order:
        teams = m.get_red() + m.get_blue()
        # counts BEFORE this match
        sc = min((seen_season[t] for t in teams), default=0)
        ec = min((seen_event[(t, m.event)] for t in teams), default=0)
        completed = _enum(m.status) == "Completed"
        if (completed and m.epa_win_prob is not None and (m.red_score or -1) >= 0
                and (m.blue_score or -1) >= 0):
            w = m.get_winner()
            o = 1.0 if _enum(w) == "red" else (0.0 if _enum(w) == "blue" else 0.5)
            ev = events[m.event]
            rp = {}
            for rk in rp_keys:
                rp[rk] = [(getattr(m, f"epa_red_{rk}_pred") or 0.0, 1.0 if getattr(m, f"red_{rk}") else 0.0),
                          (getattr(m, f"epa_blue_{rk}_pred") or 0.0, 1.0 if getattr(m, f"blue_{rk}") else 0.0)]
            rows.append({
                "p": float(m.epa_win_prob), "o": o, "rp_pred": m.epa_red_score_pred,
                "bp_pred": m.epa_blue_score_pred, "r_act": m.red_score, "b_act": m.blue_score,
                "week": ev.week, "etype": _enum(ev.type), "qual": not m.elim, "rp": rp,
                "sc": "first3" if sc < 3 else "later", "ec": "first3" if ec < 3 else "later",
            })
        for t in teams:
            if (m.key, t) in upd:
                seen_season[t] += 1
                seen_event[(t, m.event)] += 1

    def sl(pred) -> Dict[str, Any]:
        return _metrics_from([r for r in rows if pred(r)], rp_keys)

    slices: Dict[str, Any] = {
        "week": {str(w): sl(lambda r, w=w: r["week"] == w) for w in range(1, 9)
                 if any(r["week"] == w for r in rows)},
        "event_type": {t: sl(lambda r, t=t: r["etype"] == t)
                       for t in ("regional", "district", "district_cmp", "champs_div", "einstein")
                       if any(r["etype"] == t for r in rows)},
        "comp_level": {"qual": sl(lambda r: r["qual"]), "elim": sl(lambda r: not r["qual"])},
        "season_matches": {k: sl(lambda r, k=k: r["sc"] == k) for k in ("first3", "later")},
        "event_matches": {k: sl(lambda r, k=k: r["ec"] == k) for k in ("first3", "later")},
    }
    cal = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        sel = [r for r in rows if (lo <= r["p"] < hi) or (i == 9 and r["p"] == 1.0)]
        cal.append({"lo": lo, "hi": hi, "n": len(sel),
                    "mean_pred": rnd(np.mean([r["p"] for r in sel]), 4) if sel else None,
                    "frac_red_win": rnd(np.mean([r["o"] for r in sel]), 4) if sel else None})
    return {
        "model": run.model_id, "season": year,
        "config_hash": getattr(run, "config_hash", "") or config_hash,
        "year_constants": "season_week1",
        "leak_audit": {"cuts": 0, "max_abs_diff_win_prob": 0.0, "passed": False},
        "overall": _metrics_from(rows, rp_keys), "slices": slices, "calibration": cal,
    }


# ---------------------------------------------------------------------------------------------
# table helpers
# ---------------------------------------------------------------------------------------------

def _table(n: int, cols: Dict[str, List[Any]], models: Optional[Dict[str, Dict[str, List[Any]]]] = None) -> Dict[str, Any]:
    t: Dict[str, Any] = {"n": n, "cols": cols}
    if models is not None:
        t["models"] = models
    return t


def _feat_ty(features: Optional[Dict[str, Any]], model: str, team: int) -> Dict[str, Any]:
    f = (features or {}).get(model)
    if f is None:
        return {}
    return (getattr(f, "team_year", None) or {}).get(team, {}) or {}


def _feat_te(features: Optional[Dict[str, Any]], model: str, team: int, event: str) -> Dict[str, Any]:
    f = (features or {}).get(model)
    if f is None:
        return {}
    return (getattr(f, "team_event", None) or {}).get((team, event), {}) or {}


def _fi(v: Any) -> Optional[int]:
    return None if v is None else int(v)


# ---------------------------------------------------------------------------------------------
# write_season
# ---------------------------------------------------------------------------------------------

_TY_RANK_FIELDS = [
    "total_epa_rank", "total_epa_percentile", "total_team_count",
    "country_epa_rank", "country_epa_percentile", "country_team_count",
    "state_epa_rank", "state_epa_percentile", "state_team_count",
    "district_epa_rank", "district_epa_percentile", "district_team_count",
]
_EVENT_MODEL_FIELDS = [
    "epa_max", "epa_top_8", "epa_top_24", "epa_mean", "epa_sd", "count", "epa_conf", "epa_acc",
    "epa_mse", "epa_score_rmse", "epa_score_error", "rp_count", "epa_rp_1_error", "epa_rp_1_acc",
    "epa_rp_2_error", "epa_rp_2_acc", "epa_rp_3_error", "epa_rp_3_acc",
]
_FEATURE_TY = ["cons_sd", "cons_n", "change_flag", "change_event", "change_z", "opp_effect",
               "opp_effect_se", "opp_n"]


def _vec(d: Optional[Dict[str, Any]], vk: Sequence[str]) -> List[Optional[float]]:
    if d is None:
        return [None] * len(vk)
    return [rnd(d.get(k), _vk_nd(k)) for k in vk]


def write_season(
    year: int,
    runs: Dict[str, Any],
    features: Optional[Dict[str, Any]],
    backtests: Optional[Dict[str, Any]],
    robot: Any,
    raw: Optional[Dict[str, Any]],
    out_dir: str,
    meta: Dict[str, Any],
) -> SeasonFiles:
    _check_meta(meta)
    for m in MODELS:
        if m not in runs:
            raise ValueError(f"runs must contain {m!r}")
    gen, dver = meta["generated_at"], meta["data_version"]
    today = meta.get("today") or gen[:10]
    base = runs["epa"]
    year_obj, team_years, events, team_events, matches, _ = base.objs
    order = processing_order(matches)
    seq_of = {m.key: i for i, m in enumerate(order)}
    VK = sm.vector_keys(year)
    BK = sm.breakdown_keys(year)
    slot_idx = [sm.SLOT_INDEX[k] for k in VK]
    keys = sm.keys_block(year)
    notes: List[str] = []

    sdir = os.path.join(out_dir, str(year))
    edir = os.path.join(sdir, "events")
    os.makedirs(edir, exist_ok=True)
    # clear stale event files of a previous run into the same dir (deterministic tree)
    for fn in os.listdir(edir):
        if fn.endswith(".json.gz"):
            os.remove(os.path.join(edir, fn))

    # ---- per-event match lists ------------------------------------------------------------
    ev_matches: Dict[str, List[Any]] = defaultdict(list)
    for m in order:
        ev_matches[m.event].append(m)
    ev_tes: Dict[str, List[Any]] = defaultdict(list)
    for te in team_events.values():
        ev_tes[te.event].append(te)

    ev_sorted = sorted(events.values(), key=lambda e: (e.start_date or "", e.key))
    ev_index = {e.key: i for i, e in enumerate(ev_sorted)}

    # robot facts helpers
    rfields = list(getattr(robot, "fields", None) or [])
    rcode = {f["key"]: {c: i for i, c in enumerate(f["codes"])} for f in rfields}
    rby = getattr(robot, "by_match", {}) if robot is not None else {}
    event_point_slots: List[str] = []
    if robot is not None and rfields:
        seen = set()
        for p in robot.active_parts:
            seen.add(p["slot"])
            for k in p.get("also_in", []):
                if k in ("auto", "teleop", "endgame"):
                    seen.add(k)
        order_slots = ["auto", "teleop", "endgame"] + [f"comp_{i}" for i in range(10)]
        event_point_slots = [s for s in order_slots if s in seen]
    robot_counts: Dict[Tuple[int, str], List[int]] = {}

    traj: Dict[int, List[Tuple]] = defaultdict(list)     # team -> [(seq, event_idx, week, {model: (epa, resid)})]
    first_pre: Dict[str, Dict[int, Dict[str, Any]]] = {m: {} for m in MODELS}  # model -> team -> pre dict (season first match)
    asof_entries: Dict[str, Dict[int, List[Tuple[int, int, List[Optional[float]]]]]] = {
        m: defaultdict(list) for m in MODELS}           # model -> team -> [(week, seq, [epa,auto,teleop,endgame])]

    event_files: Dict[str, Dict[str, Any]] = {}
    ev_counts: Dict[str, Tuple[int, int]] = {}

    raw_ed = (raw or {}).get("event_data") or {}

    for ev in ev_sorted:
        key = ev.key
        ms = ev_matches.get(key, [])
        tes = sorted(ev_tes.get(key, []), key=lambda t: t.team)
        ev_counts[key] = (len(ms), sum(1 for m in ms if _enum(m.status) == "Completed"))
        if not ms and not tes:
            continue
        mrun = {mid: runs[mid].objs[4] for mid in MODELS}   # match dicts per model
        terun = {mid: runs[mid].objs[3] for mid in MODELS}

        # ---- matches ----
        c: Dict[str, List[Any]] = {k: [] for k in (
            "key", "comp_level", "set_number", "match_number", "elim", "time", "predicted_time",
            "status", "video", "seq", "red", "blue", "red_dq", "blue_dq", "red_surrogate",
            "blue_surrogate", "winner")}
        for bk in BK:
            c[f"red_{bk}"] = []
            c[f"blue_{bk}"] = []
        mm: Dict[str, Dict[str, List[Any]]] = {mid: {k: [] for k in (
            "epa_winner", "epa_win_prob", "epa_red_score_pred", "epa_blue_score_pred",
            "epa_red_rp_1_pred", "epa_red_rp_2_pred", "epa_red_rp_3_pred", "epa_blue_rp_1_pred",
            "epa_blue_rp_2_pred", "epa_blue_rp_3_pred", "red_pred", "blue_pred")} for mid in MODELS}
        for m in ms:
            c["key"].append(m.key)
            c["comp_level"].append(_enum(m.comp_level))
            c["set_number"].append(m.set_number)
            c["match_number"].append(m.match_number)
            c["elim"].append(bool(m.elim))
            c["time"].append(m.time)
            c["predicted_time"].append(m.predicted_time)
            c["status"].append(_enum(m.status))
            c["video"].append(m.video)
            c["seq"].append(seq_of[m.key])
            c["red"].append(m.get_red())
            c["blue"].append(m.get_blue())
            c["red_dq"].append(_split_ints(m.red_dq))
            c["blue_dq"].append(_split_ints(m.blue_dq))
            c["red_surrogate"].append(_split_ints(m.red_surrogate))
            c["blue_surrogate"].append(_split_ints(m.blue_surrogate))
            c["winner"].append(_enum(m.winner))
            for bk in BK:
                for col in ("red", "blue"):
                    v = getattr(m, f"{col}_{bk}")
                    if bk == "score" and v is not None and v < 0:
                        v = None
                    if isinstance(v, np.generic):
                        v = v.item()
                    c[f"{col}_{bk}"].append(v)
            for mid in MODELS:
                mo = mrun[mid][m.key]
                d = mm[mid]
                d["epa_winner"].append(_enum(mo.epa_winner))
                d["epa_win_prob"].append(rnd(mo.epa_win_prob, 4))
                d["epa_red_score_pred"].append(rnd(mo.epa_red_score_pred, 2))
                d["epa_blue_score_pred"].append(rnd(mo.epa_blue_score_pred, 2))
                for col in ("red", "blue"):
                    for rk in (1, 2, 3):
                        d[f"epa_{col}_rp_{rk}_pred"].append(rnd(getattr(mo, f"epa_{col}_rp_{rk}_pred"), 4))
                ap = runs[mid].alliance_pred.get(m.key)
                for ci, col in enumerate(("red_pred", "blue_pred")):
                    if ap is None:
                        d[col].append(None)
                    else:
                        arr = ap[ci]
                        d[col].append([rnd(arr[si], _vk_nd(vk)) for vk, si in zip(VK, slot_idx)])
        matches_t = _table(len(ms), c, mm)

        # ---- team_matches ----
        tm_cols: Dict[str, List[Any]] = {"m": [], "team": [], "alliance": [], "slot": []}
        tm_models: Dict[str, Dict[str, List[Any]]] = {
            mid: {**{vk: [] for vk in VK}, "attr_epa": [], "attr_auto_epa": [], "attr_teleop_epa": [],
                  "attr_endgame_epa": []} for mid in MODELS}
        ev_first_pre: Dict[str, Dict[int, Dict[str, Any]]] = {mid: {} for mid in MODELS}
        for mi, m in enumerate(ms):
            for ai, teams in enumerate((m.get_red(), m.get_blue())):
                for si, t in enumerate(teams, 1):
                    tm_cols["m"].append(mi)
                    tm_cols["team"].append(t)
                    tm_cols["alliance"].append(ai)
                    tm_cols["slot"].append(si)
                    for mid in MODELS:
                        mo = mrun[mid][m.key]
                        pre = _vget(runs[mid], mo, m.key, t, "pre")
                        if pre is not None and t not in ev_first_pre[mid]:
                            ev_first_pre[mid][t] = pre
                        if pre is not None and t not in first_pre[mid]:
                            first_pre[mid][t] = pre
                        post = _vget(runs[mid], mo, m.key, t, "post")
                        d = tm_models[mid]
                        for vk in VK:
                            d[vk].append(None if post is None else rnd(post.get(vk), _vnd(vk)))
                        at = runs[mid].attrib.get(m.key, {}).get(t)
                        if post is None or at is None:
                            for k in ("attr_epa", "attr_auto_epa", "attr_teleop_epa", "attr_endgame_epa"):
                                d[k].append(None)
                            resid = None
                        else:
                            d["attr_epa"].append(rnd(at[0], 3))
                            d["attr_auto_epa"].append(rnd(at[1], 3))
                            d["attr_teleop_epa"].append(rnd(at[2], 3))
                            d["attr_endgame_epa"].append(rnd(at[3], 3))
                        if post is not None:
                            asof_entries[mid][t].append(
                                (m.week, seq_of[m.key],
                                 [rnd(post.get(k), 2) for k in ("epa", "auto_epa", "teleop_epa", "endgame_epa")]))
                    # trajectory entry (one per team-match with a post vector under any model)
                    posts = {mid: _vget(runs[mid], mrun[mid][m.key], m.key, t, "post") for mid in MODELS}
                    if any(p is not None for p in posts.values()):
                        ent = {}
                        for mid in MODELS:
                            p = posts[mid]
                            at = runs[mid].attrib.get(m.key, {}).get(t)
                            pre = _vget(runs[mid], mrun[mid][m.key], m.key, t, "pre")
                            if p is None:
                                ent[mid] = (None, None)
                            else:
                                r = None
                                if at is not None and pre is not None:
                                    r = rnd(at[0] - pre["epa"], 1)
                                ent[mid] = (rnd(p.get("epa"), 2), r)
                        traj[t].append((seq_of[m.key], ev_index[key], m.week, ent))
        team_matches_t = _table(len(tm_cols["m"]), tm_cols, tm_models)

        # ---- team_events ----
        te_cols: Dict[str, List[Any]] = {k: [] for k in (
            "team", "team_name", "first_event", "wins", "losses", "ties", "count", "winrate",
            "qual_wins", "qual_losses", "qual_ties", "qual_count", "qual_winrate", "rps",
            "rps_per_match", "rank", "num_teams")}
        te_models: Dict[str, Dict[str, List[Any]]] = {}
        for mid in MODELS:
            d = {k: [] for k in ("epa_start", "epa_pre_elim", "epa_mean", "epa_max")}
            d.update({vk: [] for vk in VK})
            d.update({"unitless_epa": [], "norm_epa": []})
            d.update({f"pre_{vk}": [] for vk in VK})
            d.update({"change_k": [], "change_mean_resid": [], "change_z": [], "change_flag": []})
            te_models[mid] = d
        for te in tes:
            te_cols["team"].append(te.team)
            te_cols["team_name"].append(te.team_name)
            te_cols["first_event"].append(te.first_event)
            for k in ("wins", "losses", "ties", "count", "qual_wins", "qual_losses", "qual_ties",
                      "qual_count", "rps", "rank", "num_teams"):
                te_cols[k].append(getattr(te, k))
            te_cols["winrate"].append(rnd(te.winrate, 4))
            te_cols["qual_winrate"].append(rnd(te.qual_winrate, 4))
            te_cols["rps_per_match"].append(rnd(te.rps_per_match, 4))
            for mid in MODELS:
                mte = terun[mid][f"{te.team}_{key}"]
                d = te_models[mid]
                d["epa_start"].append(rnd(mte.epa_start, 2))
                d["epa_pre_elim"].append(rnd(mte.epa_pre_elim, 2))
                d["epa_mean"].append(rnd(mte.epa_mean, 2))
                d["epa_max"].append(rnd(mte.epa_max, 2))
                for vk in VK:
                    d[vk].append(rnd(getattr(mte, vk), _vk_nd(vk)))
                d["unitless_epa"].append(rint(mte.unitless_epa))
                d["norm_epa"].append(rint(mte.norm_epa))
                pre = ev_first_pre[mid].get(te.team)
                fin = (getattr(runs[mid], "final_vec", None) or {}).get(te.team)
                for vk in VK:
                    if pre is not None:
                        d[f"pre_{vk}"].append(rnd(pre.get(vk), _vnd(vk)))
                    elif fin is not None:  # has not played here yet: its current vector
                        d[f"pre_{vk}"].append(rnd(fin[sm.SLOT_INDEX[vk]], _vnd(vk)))
                    else:
                        d[f"pre_{vk}"].append(rnd(getattr(mte, vk), _vnd(vk)))
                f = _feat_te(features, mid, te.team, key)
                d["change_k"].append(_fi(f.get("change_k")))
                d["change_mean_resid"].append(rnd(f.get("change_mean_resid"), 2))
                d["change_z"].append(rnd(f.get("change_z"), 4))
                d["change_flag"].append(_fi(f.get("change_flag")))
        team_events_t = _table(len(tes), te_cols, te_models)

        # ---- alliances (raw /event/{key}/alliances) ----
        alliances = _alliances((raw_ed.get(key) or {}).get("alliances"))

        # ---- robot ----
        robot_t = _event_robot(ms, rfields, rcode, rby, event_point_slots, robot_counts)

        doc = {
            "schema": SCHEMA, "kind": "event", "season": year, "event": key,
            "meta": {
                "key": ev.key, "name": ev.name, "week": ev.week, "type": _enum(ev.type),
                "district": ev.district, "state": ev.state, "country": ev.country,
                "start_date": ev.start_date, "end_date": ev.end_date, "status": _enum(ev.status),
                "num_teams": ev.num_teams, "qual_matches": ev.qual_matches,
                "current_match": ev.current_match, "video": ev.video,
            },
            "team_events": team_events_t, "matches": matches_t, "team_matches": team_matches_t,
            "alliances": alliances, "robot": robot_t,
        }
        rel = f"{year}/events/{key}.json.gz"
        data, h = dump_gz(doc, os.path.join(out_dir, rel))
        event_files[key] = {"path": rel, "hash": h, "bytes": len(data)}

    # ---- season-level robot counts (per team over completed matches), aligned with teams rows ----
    teams_sorted = sorted(team_years.values(), key=lambda t: t.team)
    team_index = {t.team: i for i, t in enumerate(teams_sorted)}

    # ---- teams table ----
    tcols: Dict[str, List[Any]] = {k: [] for k in (
        "team", "name", "country", "state", "district", "rookie_year", "wins", "losses", "ties",
        "count", "winrate", "next_event_key", "next_event_week", "events")}
    team_ev: Dict[int, List[Any]] = defaultdict(list)
    for te in team_events.values():
        team_ev[te.team].append(te)
    tmodels: Dict[str, Dict[str, List[Any]]] = {}
    for mid in MODELS:
        d = {k: [] for k in ("epa_start", "epa_pre_champs", "epa_max")}
        d.update({vk: [] for vk in VK})
        d.update({"unitless_epa": [], "norm_epa": []})
        d.update({k: [] for k in _TY_RANK_FIELDS})
        d.update({k: [] for k in _FEATURE_TY})
        tmodels[mid] = d
    for ty in teams_sorted:
        tcols["team"].append(ty.team)
        tcols["name"].append(ty.name)
        tcols["country"].append(ty.country)
        tcols["state"].append(ty.state)
        tcols["district"].append(ty.district)
        tcols["rookie_year"].append(ty.rookie_year)
        for k in ("wins", "losses", "ties", "count"):
            tcols[k].append(getattr(ty, k))
        tcols["winrate"].append(rnd(ty.winrate, 4))
        tcols["next_event_key"].append(ty.next_event_key)
        tcols["next_event_week"].append(ty.next_event_week)
        evs = sorted(team_ev.get(ty.team, []),
                     key=lambda te: (events[te.event].start_date or "", te.event))
        tcols["events"].append([te.event for te in evs])
        for mid in MODELS:
            my = runs[mid].objs[1][f"{ty.team}_{year}"]
            d = tmodels[mid]
            d["epa_start"].append(rnd(my.epa_start, 2))
            d["epa_pre_champs"].append(rnd(my.epa_pre_champs, 2))
            d["epa_max"].append(rnd(my.epa_max, 2))
            for vk in VK:
                d[vk].append(rnd(getattr(my, vk), _vk_nd(vk)))
            d["unitless_epa"].append(rint(my.unitless_epa))
            d["norm_epa"].append(rint(my.norm_epa))
            for k in _TY_RANK_FIELDS:
                v = getattr(my, k)
                d[k].append(rnd(v, 4) if k.endswith("percentile") else _fi(v))
            f = _feat_ty(features, mid, ty.team)
            d["cons_sd"].append(rnd(f.get("cons_sd"), 2))
            d["cons_n"].append(_fi(f.get("cons_n")))
            d["change_flag"].append(_fi(f.get("change_flag")))
            d["change_event"].append(f.get("change_event"))
            d["change_z"].append(rnd(f.get("change_z"), 4))
            d["opp_effect"].append(rnd(f.get("opp_effect"), 2))
            d["opp_effect_se"].append(rnd(f.get("opp_effect_se"), 2))
            d["opp_n"].append(_fi(f.get("opp_n")))
    teams_t = _table(len(teams_sorted), tcols, tmodels)

    # ---- events table (season.json) ----
    ecols: Dict[str, List[Any]] = {k: [] for k in (
        "key", "name", "week", "type", "district", "state", "country", "start_date", "end_date",
        "status", "num_teams", "qual_matches", "current_match", "video", "match_count",
        "completed_count", "file_hash", "file_bytes")}
    emodels = {mid: {k: [] for k in _EVENT_MODEL_FIELDS} for mid in MODELS}
    for ev in ev_sorted:
        ecols["key"].append(ev.key)
        ecols["name"].append(ev.name)
        ecols["week"].append(ev.week)
        ecols["type"].append(_enum(ev.type))
        for k in ("district", "state", "country", "start_date", "end_date"):
            ecols[k].append(getattr(ev, k))
        ecols["status"].append(_enum(ev.status))
        for k in ("num_teams", "qual_matches", "current_match", "video"):
            ecols[k].append(getattr(ev, k))
        mc, cc = ev_counts.get(ev.key, (0, 0))
        ecols["match_count"].append(mc)
        ecols["completed_count"].append(cc)
        ef = event_files.get(ev.key)
        ecols["file_hash"].append(ef["hash"] if ef else None)
        ecols["file_bytes"].append(ef["bytes"] if ef else None)
        for mid in MODELS:
            me = runs[mid].objs[2][ev.key]
            for k in _EVENT_MODEL_FIELDS:
                v = getattr(me, k)
                emodels[mid][k].append(_metric(k, v) if k not in ("epa_max", "epa_top_8", "epa_top_24", "epa_mean", "epa_sd") else rnd(v, 2))
    events_t = _table(len(ev_sorted), ecols, emodels)

    # ---- robot (season) ----
    robot_t_season = {"fields": [], "counts": {}}
    if rfields:
        robot_t_season["fields"] = [{"key": f["key"], "codes": f["codes"], "active": f["active"],
                                     "match_rate": rnd(f.get("match_rate"), 6)} for f in rfields]
        for f in rfields:
            nc = len(f["codes"])
            robot_t_season["counts"][f["key"]] = [
                robot_counts.get((t.team, f["key"]), [0] * nc) for t in teams_sorted]

    # ---- year block ----
    ystats = {}
    for k in ("score_mean", "score_sd", "foul_mean", "no_foul_mean", "auto_mean", "teleop_mean",
              "endgame_mean", "rp_1_mean", "rp_2_mean"):
        ystats[k] = rnd(getattr(year_obj, k), 4)
    if sm.has_rp3(year):
        ystats["rp_3_mean"] = rnd(year_obj.rp_3_mean, 4)
    ystats["tiebreaker_mean"] = rnd(year_obj.tiebreaker_mean, 4)
    for vk in VK:
        ci = sm.comp_of(vk)
        if ci is not None:
            ystats[f"comp_{ci}_mean"] = rnd(getattr(year_obj, f"comp_{ci}_mean"), 4)
    import dataclasses as _dc

    ynames = [f.name for f in _dc.fields(type(year_obj))]
    metric_names = ynames[ynames.index(_YEAR_METRIC_START):]
    ymodels = {}
    for mid in MODELS:
        yo = runs[mid].objs[0]
        perc = {}
        for vk in VK:
            stem = vk
            perc[vk] = {p: (rnd(getattr(yo, f"{stem}_{p[1:]}p"), _vk_nd(vk))
                            if hasattr(yo, f"{stem}_{p[1:]}p") else None)
                        for p in ("p99", "p90", "p75", "p25")}
        ymodels[mid] = {"percentiles": perc,
                        "metrics": {k: _metric(k, getattr(yo, k)) for k in metric_names}}
    predict = {"num_teams": 3, "score_sd": rnd(year_obj.score_sd, 4),
               "foul_rate": float(year_obj.get_foul_rate()),
               "models": {mid: {"k": _model_k(runs, mid, year, meta)} for mid in MODELS}}

    # ---- backtest ----
    bts: Dict[str, Any] = {}
    for mid in MODELS:
        b = (backtests or {}).get(mid)
        if b is None:
            b = fallback_backtest(year, runs[mid], order)
            notes.append(f"backtest[{mid}]: fallback computed in shards.py (no WP3b summary supplied)")
        bts[mid] = b

    # ---- status, counts ----
    n_matches = len(matches)
    n_completed = sum(1 for m in matches.values() if _enum(m.status) == "Completed")
    status = sm.season_status(year, meta.get("current_season"), [_enum(e.status) for e in ev_sorted],
                              [e.end_date for e in ev_sorted if e.end_date], n_completed, today)

    # ---- asof ----
    team_list = [t.team for t in teams_sorted]
    asof_models = {}
    for mid in MODELS:
        out = {k: [] for k in ("epa", "auto_epa", "teleop_epa", "endgame_epa")}
        ent = asof_entries[mid]
        for t in teams_sorted:
            lst = ent.get(t.team, [])
            pre0 = first_pre[mid].get(t.team)
            if pre0 is not None:
                start = [rnd(pre0.get(k), 2) for k in ("epa", "auto_epa", "teleop_epa", "endgame_epa")]
            else:
                my = runs[mid].objs[1][f"{t.team}_{year}"]
                fin = (getattr(runs[mid], "final_vec", None) or {}).get(t.team)
                start = ([rnd(fin[sm.SLOT_INDEX[k]], 2) for k in ("epa", "auto_epa", "teleop_epa", "endgame_epa")]
                         if fin is not None else
                         [rnd(getattr(my, k), 2) for k in ("epa", "auto_epa", "teleop_epa", "endgame_epa")])
            by_week: Dict[int, Tuple[int, List[Optional[float]]]] = {}
            for wk, sq, vec in lst:
                if wk not in by_week or sq > by_week[wk][0]:
                    by_week[wk] = (sq, vec)
            pts = [start]
            best: Optional[Tuple[int, List[Optional[float]]]] = None
            for w in range(1, 9):
                if w in by_week and (best is None or by_week[w][0] > best[0]):
                    best = by_week[w]
                pts.append(best[1] if best else start)
            for j, k in enumerate(out):
                out[k].append([p[j] for p in pts])
        asof_models[mid] = out
    asof_doc = {"schema": SCHEMA, "kind": "asof", "season": year,
                "points": [{"id": f"w{i}", "label": sm.WEEK_LABELS[i]} for i in range(9)],
                "team": team_list, "models": asof_models}

    # ---- traj ----
    tr_seq, tr_ev = [], []
    tr_models = {mid: {"epa": [], "resid": []} for mid in MODELS}
    for t in teams_sorted:
        ents = sorted(traj.get(t.team, []), key=lambda e: e[0])
        sq = [e[0] for e in ents]
        tr_seq.append([sq[0]] + [b - a for a, b in zip(sq, sq[1:])] if sq else [])
        tr_ev.append([e[1] for e in ents])
        for mid in MODELS:
            tr_models[mid]["epa"].append([e[3][mid][0] for e in ents])
            tr_models[mid]["resid"].append([e[3][mid][1] for e in ents])
    traj_doc = {"schema": SCHEMA, "kind": "traj", "season": year, "team": team_list,
                "seq": tr_seq, "event": tr_ev, "models": tr_models}

    files: Dict[str, Dict[str, Any]] = {}
    for name, doc in (("asof", asof_doc), ("traj", traj_doc)):
        rel = f"{year}/{name}.json.gz"
        data, h = dump_gz(doc, os.path.join(out_dir, rel))
        files[name] = {"path": rel, "hash": h, "bytes": len(data)}

    season_doc = {
        "schema": SCHEMA, "kind": "season", "season": year, "game": sm.GAMES.get(year, str(year)),
        "status": status, "generated_at": gen, "data_version": dver, "keys": keys,
        "year": {"stats": ystats, "models": ymodels}, "predict": predict,
        "events": events_t, "teams": teams_t, "robot": robot_t_season, "backtest": bts,
    }
    rel = f"{year}/season.json.gz"
    data, h = dump_gz(season_doc, os.path.join(out_dir, rel))
    files["season"] = {"path": rel, "hash": h, "bytes": len(data)}
    files = {k: files[k] for k in ("season", "asof", "traj")}

    entry = {
        "season": year, "game": sm.GAMES.get(year, str(year)), "status": status,
        "updated_at": meta.get("updated_at") or gen,
        "last_tba_change_at": meta.get("last_tba_change_at"),
        "stale": bool(meta.get("stale", False)), "stale_reason": meta.get("stale_reason"),
        "counts": {"teams": len(teams_sorted), "events": len(ev_sorted), "matches": n_matches,
                   "completed_matches": n_completed},
        "files": files, "events_count": len(event_files),
        "events_bytes": sum(v["bytes"] for v in event_files.values()),
        "inputs_hash": meta.get("inputs_hash") or "0" * 64,
        "code_hash": meta.get("code_hash") or "0" * 7,
    }
    return SeasonFiles(
        season=year, out_dir=out_dir, files=files, events=event_files, manifest_entry=entry,
        backtest=bts, history=history_part(season_doc), counts=entry["counts"], notes=notes)


def _alliances(raw_all: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(raw_all, list) or not raw_all:
        return None

    def num(k: Any) -> Optional[int]:
        s = str(k)
        s = s[3:] if s.startswith("frc") else s
        return int(s) if s.isdigit() else None

    out = []
    for a in raw_all:
        if not isinstance(a, dict):
            continue
        bk = a.get("backup")
        backup = None
        if isinstance(bk, dict) and num(bk.get("in")) is not None and num(bk.get("out")) is not None:
            backup = {"in": num(bk["in"]), "out": num(bk["out"])}
        st = a.get("status")
        status = None
        if isinstance(st, dict):
            status = {"level": st.get("level"), "status": st.get("status"), "record": st.get("record")}
        out.append({
            "name": a.get("name"),
            "picks": [n for n in (num(x) for x in a.get("picks") or []) if n is not None],
            "declines": [n for n in (num(x) for x in a.get("declines") or []) if n is not None],
            "backup": backup, "status": status,
        })
    return out or None


def _event_robot(ms, rfields, rcode, rby, slots, robot_counts) -> Dict[str, Any]:
    n = len(ms)
    if not rfields:
        return {"fields": [], "values": {}, "points": {}, "scored": [None] * n, "ok": [[None, None]] * n}
    values: Dict[str, List[Any]] = {f["key"]: [] for f in rfields}
    points: Dict[str, List[Any]] = {s: [] for s in slots}
    scored: List[Any] = []
    ok: List[Any] = []
    for m in ms:
        red, blue = rby.get(m.key, (None, None))
        facts = (red, blue)
        teams = (m.get_red(), m.get_blue())
        completed = _enum(m.status) == "Completed"
        for f in rfields:
            k = f["key"]
            if red is None and blue is None:
                values[k].append(None)
                continue
            row: List[Optional[int]] = []
            for fa in facts:
                vals = fa.values.get(k) if fa is not None else None
                for i in range(3):
                    v = vals[i] if vals is not None else None
                    row.append(None if v is None else rcode[k].get(v))
            values[k].append(row if any(x is not None for x in row) else None)
        for s in slots:
            if red is None and blue is None:
                points[s].append(None)
                continue
            row2: List[Optional[float]] = []
            for fa in facts:
                arr = fa.event_points.get(s) if fa is not None else None
                for i in range(3):
                    row2.append(None if arr is None else rnd(arr[i], 2))
            points[s].append(row2)
        if red is None and blue is None:
            scored.append(None)
            ok.append([None, None])
        else:
            row3: List[Optional[float]] = []
            for fa in facts:
                for i in range(3):
                    row3.append(None if fa is None else rnd(fa.scored[i], 2))
            scored.append(row3)
            ok.append([None if fa is None else bool(fa.ok) for fa in facts])
        # season counts (completed matches, first three per alliance)
        if completed:
            for fa, ts in zip(facts, teams):
                if fa is None:
                    continue
                for k, vals in fa.values.items():
                    for i, t in enumerate(ts[:3]):
                        v = vals[i]
                        if v is None or v not in rcode[k]:
                            continue
                        cnt = robot_counts.setdefault((t, k), [0] * len(rcode[k]))
                        cnt[rcode[k][v]] += 1
    return {
        "fields": [{"key": f["key"], "codes": f["codes"], "active": f["active"]} for f in rfields],
        "values": values, "points": points, "scored": scored, "ok": ok,
    }


# ---------------------------------------------------------------------------------------------
# history and global files
# ---------------------------------------------------------------------------------------------

def history_part(season_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Rows of history.json for one season, from a season.json document (also used for seasons
    whose shards are copied from the published tree)."""
    t = season_doc["teams"]
    n = t["n"]
    cols = {
        "team": list(t["cols"]["team"]), "season": [season_doc["season"]] * n,
        "name": t["cols"]["name"], "district": t["cols"]["district"], "state": t["cols"]["state"],
        "country": t["cols"]["country"], "wins": t["cols"]["wins"], "losses": t["cols"]["losses"],
        "ties": t["cols"]["ties"], "count": t["cols"]["count"],
    }
    keep = ["epa", "auto_epa", "teleop_epa", "endgame_epa", "norm_epa", "total_epa_rank",
            "total_team_count", "district_epa_rank", "district_team_count"]
    models = {mid: {k: t["models"][mid][k] for k in keep} for mid in MODELS}
    return {"season": season_doc["season"], "n": n, "cols": cols, "models": models}


def write_global(
    out_dir: str,
    seasons_meta: List[Dict[str, Any]],
    models_info: Dict[str, Any],
    history_rows: Iterable[Dict[str, Any]],
    manifest_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """models.json, history.json.gz, manifest.json (last).

    seasons_meta   [{"entry": manifest seasonEntry, "backtest": {model: summary}}] for every
                   published season (rebuilt ones from SeasonFiles, copied ones from their
                   published manifest + season.json).
    models_info    {"models": [model_info.describe(...) dicts], "tuned_on": [2022, ...]}
    history_rows   iterable of `history_part` dicts (one per published season)
    manifest_meta  data_version, generated_at, code_version; optional default_season,
                   default_model, default_region, default_watchlist, credits.
    """
    _check_meta(manifest_meta)
    gen = manifest_meta["generated_at"]
    tuned = set(models_info.get("tuned_on") or [])
    sm_sorted = sorted(seasons_meta, key=lambda s: -s["entry"]["season"])

    # models.json
    scoreboard = []
    for s in sm_sorted:
        for mid in MODELS:
            b = (s.get("backtest") or {}).get(mid)
            if b is None:
                continue
            scoreboard.append({"season": s["entry"]["season"], "model": mid,
                               "in_tuning_set": s["entry"]["season"] in tuned, "overall": b["overall"]})
    models_doc = {"schema": SCHEMA, "kind": "models", "generated_at": gen,
                  "models": models_info["models"], "scoreboard": scoreboard}
    _, mh = dump_json(models_doc, os.path.join(out_dir, "models.json"))
    mbytes = os.path.getsize(os.path.join(out_dir, "models.json"))

    # history.json.gz: rows sorted by (team, season)
    parts = sorted(history_rows, key=lambda p: p["season"])
    hcols: Dict[str, List[Any]] = {k: [] for k in parts[0]["cols"]} if parts else {}
    hmodels: Dict[str, Dict[str, List[Any]]] = {mid: {k: [] for k in parts[0]["models"][mid]} for mid in MODELS} if parts else {}
    rows = []
    for p in parts:
        for i in range(p["n"]):
            rows.append((p["cols"]["team"][i], p["season"], p, i))
    rows.sort(key=lambda r: (r[0], r[1]))
    for _, _, p, i in rows:
        for k in hcols:
            hcols[k].append(p["cols"][k][i])
        for mid in MODELS:
            for k in hmodels[mid]:
                hmodels[mid][k].append(p["models"][mid][k][i])
    hist_doc = {"schema": SCHEMA, "kind": "history", "generated_at": gen,
                "rows": {"n": len(rows), "cols": hcols, "models": hmodels}}
    data, hh = dump_gz(hist_doc, os.path.join(out_dir, "history.json.gz"))

    default_model = manifest_meta.get("default_model", "epa")
    labels = {m["id"]: m["label"] for m in models_info["models"]}
    manifest = {
        "schema": SCHEMA, "kind": "manifest", "data_version": manifest_meta["data_version"],
        "generated_at": gen, "code_version": manifest_meta.get("code_version", "0000000"),
        "default_season": manifest_meta.get("default_season", 2026),
        "default_model": default_model,
        "default_region": manifest_meta.get("default_region", "fma"),
        "default_watchlist": list(manifest_meta.get("default_watchlist", [1218, 1219])),
        "models": [{"id": mid, "label": labels.get(mid, mid), "default": mid == default_model}
                   for mid in MODELS],
        "seasons": [s["entry"] for s in sm_sorted],
        "files": {"models": {"path": "models.json", "hash": mh, "bytes": mbytes},
                  "history": {"path": "history.json.gz", "hash": hh, "bytes": len(data)}},
        "credits": manifest_meta.get("credits") or {
            "data": "The Blue Alliance", "model": "Statbotics EPA (MIT)",
            "notice": "Not affiliated with FIRST."},
    }
    dump_json(manifest, os.path.join(out_dir, "manifest.json"))
    return manifest


# ---------------------------------------------------------------------------------------------
# derived quantities (SPEC 3.5.4, 3.5.5): used by the tests and the validator, mirrored by the site
# ---------------------------------------------------------------------------------------------

def derive_pre(event_doc: Dict[str, Any], model: str, vk: Sequence[str]) -> List[List[Optional[float]]]:
    """Pre-match vector of every team_matches row, from the event file alone.

    pre(row) = post vector of the nearest earlier row of the same team whose post is non-null; if
    none, the team_events row's pre_<vk> (SPEC 3.5.4).  Exact because ratings change only through
    a team's own matches and Fast EPA's passback runs between events.
    """
    tm = event_doc["team_matches"]
    te = event_doc["team_events"]
    te_pre = {t: i for i, t in enumerate(te["cols"]["team"])}
    pre_cols = [te["models"][model][f"pre_{k}"] for k in vk]
    post_cols = [tm["models"][model][k] for k in vk]
    cur: Dict[int, List[Optional[float]]] = {}
    out: List[List[Optional[float]]] = []
    for r, t in enumerate(tm["cols"]["team"]):
        if t in cur:
            pre = cur[t]
        else:
            i = te_pre[t]
            pre = [c[i] for c in pre_cols]
        out.append(pre)
        post = [c[r] for c in post_cols]
        if post[0] is not None:
            cur[t] = post
    return out

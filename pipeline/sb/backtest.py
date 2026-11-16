"""Backtest harness (SPEC 4.5): honest pre-match scoring of a ModelRun, leak audits, paired
bootstrap comparisons, the week-1 Year-constant look-ahead measurement, and the season chain.

What is scored: for match m, the win probability / scores / RP probabilities that
`predict_match` recorded for m while the season was replayed in processing order, before m
updated anything (upstream `process_match` predicts, records, then updates).  Both models are
online, so these are true pre-match predictions.  Nothing here replays a season with hindsight.

Known look-ahead (kept for Statbotics parity, labelled `year_constants: "season_week1"` in every
summary): Year means and score_sd come from the season's week-1 matches, including the week-1
matches being predicted.  It affects week-1 predictions only (from week 2 on, week 1 is in the
past).  `lookahead_effect` measures it.

Entry points
    score_season(run, leak_audit) -> backtestSummary dict (season.schema.json)
    match_rows(run) -> MatchRows (per-match arrays; the basis for every metric and bootstrap)
    metrics(rows, mask) -> metrics dict (4.5.2)
    paired_bootstrap(rows_a, rows_b, ...) -> CIs of metric differences, paired by match
    leak_audit(year, base_objs, priors, model_id, config, robot_facts, cuts, seed) -> dict
    season_chain(years, raw_dir, archive_csv, ...) -> per-season (base, priors, epa run)
"""
import copy
import math
import os
import random
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

import sb  # noqa: F401
import sb.loader as loader
from sb.model_runner import ModelRun, load_base, run_model

from src.constants import EPS  # noqa: E402
from src.types.enums import MatchStatus, MatchWinner  # noqa: E402

YEAR_CONSTANTS = "season_week1"
EVENT_TYPES = ("regional", "district", "district_cmp", "champs_div", "einstein")
OUTCOME = {MatchWinner.RED: 1.0, MatchWinner.BLUE: 0.0, MatchWinner.TIE: 0.5}
MATCH_FIELDS = (
    ["score", "no_foul", "foul", "auto", "teleop", "endgame", "rp_1", "rp_2", "rp_3",
     "tiebreaker"] + [f"comp_{i}" for i in range(10)]
)


def _r4(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    v = round(x, 4)
    return 0.0 if v == 0 else v


# --------------------------------------------------------------------------------------------
# per-match rows
# --------------------------------------------------------------------------------------------

@dataclass
class MatchRows:
    """One entry per scored match (status Completed, both scores >= 0, prediction present,
    outcome known), in processing order."""
    year: int
    model_id: str
    keys: List[str]
    p: np.ndarray  # red win prob (recorded, rounded 4 dp as upstream stores it), clipped
    o: np.ndarray  # 1 red win, 0 blue win, 0.5 tie
    pred: np.ndarray  # (n, 2) predicted red/blue score with foul scaling
    act: np.ndarray  # (n, 2) actual red/blue score
    week: np.ndarray
    etype: np.ndarray  # str
    event: np.ndarray  # str
    elim: np.ndarray  # bool
    season_first3: np.ndarray  # bool
    event_first3: np.ndarray  # bool
    rp_pred: Dict[str, np.ndarray]  # (n, 2), NaN where absent / elim
    rp_act: Dict[str, np.ndarray]

    @property
    def n(self) -> int:
        return len(self.keys)


def processing_order(matches: Dict[str, Any]) -> List[Any]:
    return sorted(matches.values(), key=lambda m: m.time)  # == src/data/epa/calc.py


def _counted(m, num_teams: int = 3) -> List[int]:
    return m.get_red()[:num_teams] + m.get_blue()[:num_teams]


def match_rows(run: ModelRun) -> MatchRows:
    year_obj, _, events, _, matches, _ = run.objs
    year = int(year_obj.year)
    rps = ["rp_1", "rp_2"] + (["rp_3"] if year >= 2025 else [])
    season_n: Dict[int, int] = {}
    event_n: Dict[Tuple[int, str], int] = {}
    cols: Dict[str, list] = {k: [] for k in
                             ("keys", "p", "o", "pred", "act", "week", "etype", "event", "elim",
                              "s3", "e3")}
    rp_pred: Dict[str, list] = {k: [] for k in rps}
    rp_act: Dict[str, list] = {k: [] for k in rps}
    for m in processing_order(matches):
        teams = _counted(m)
        s_min = min((season_n.get(t, 0) for t in teams), default=0)
        e_min = min((event_n.get((t, m.event), 0) for t in teams), default=0)
        # counts of matches completed and updated BEFORE m
        for t in teams:
            if (m.key, t) in run.updated:
                season_n[t] = season_n.get(t, 0) + 1
                event_n[(t, m.event)] = event_n.get((t, m.event), 0) + 1
        if m.status != MatchStatus.COMPLETED:
            continue
        if m.red_score is None or m.blue_score is None or m.red_score < 0 or m.blue_score < 0:
            continue
        if m.epa_win_prob is None:
            continue
        o = OUTCOME.get(m.get_winner())
        if o is None:
            continue
        cols["keys"].append(m.key)
        cols["p"].append(min(max(float(m.epa_win_prob), EPS), 1 - EPS))
        cols["o"].append(o)
        cols["pred"].append((float(m.epa_red_score_pred), float(m.epa_blue_score_pred)))
        cols["act"].append((float(m.red_score), float(m.blue_score)))
        cols["week"].append(int(m.week))
        et = events[m.event].type
        cols["etype"].append(getattr(et, "value", et))
        cols["event"].append(m.event)
        cols["elim"].append(bool(m.elim))
        cols["s3"].append(s_min < 3)
        cols["e3"].append(e_min < 3)
        for k in rps:
            pr, ac = [], []
            for a in ("red", "blue"):
                pv = getattr(m, f"epa_{a}_{k}_pred")
                av = getattr(m, f"{a}_{k}")
                if m.elim or pv is None or av is None:
                    pr.append(np.nan)
                    ac.append(np.nan)
                else:
                    pr.append(min(max(float(pv), EPS), 1 - EPS))
                    ac.append(float(bool(av)))
            rp_pred[k].append(pr)
            rp_act[k].append(ac)
    n = len(cols["keys"])
    return MatchRows(
        year=year, model_id=run.model_id, keys=cols["keys"],
        p=np.array(cols["p"], dtype=float), o=np.array(cols["o"], dtype=float),
        pred=np.array(cols["pred"], dtype=float).reshape(n, 2),
        act=np.array(cols["act"], dtype=float).reshape(n, 2),
        week=np.array(cols["week"], dtype=int), etype=np.array(cols["etype"], dtype=object),
        event=np.array(cols["event"], dtype=object), elim=np.array(cols["elim"], dtype=bool),
        season_first3=np.array(cols["s3"], dtype=bool),
        event_first3=np.array(cols["e3"], dtype=bool),
        rp_pred={k: np.array(v, dtype=float).reshape(n, 2) for k, v in rp_pred.items()},
        rp_act={k: np.array(v, dtype=float).reshape(n, 2) for k, v in rp_act.items()},
    )


# --------------------------------------------------------------------------------------------
# metrics (4.5.2)
# --------------------------------------------------------------------------------------------

def _raw_metrics(p, o, pred, act) -> Dict[str, Optional[float]]:
    n = len(p)
    if n == 0:
        return {"acc": None, "acc_no_ties": None, "brier": None, "log_loss": None, "conf": None,
                "score_mae": None, "score_rmse": None, "score_bias": None}
    nt = o != 0.5
    err = (pred - act).ravel()
    return {
        "acc": float(np.mean((p > 0.5) == (o > 0.5))),
        "acc_no_ties": float(np.mean((p[nt] > 0.5) == (o[nt] > 0.5))) if nt.any() else None,
        "brier": float(np.mean((p - o) ** 2)),
        "log_loss": float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p))),
        "conf": float(np.mean(np.maximum(p, 1 - p))),
        "score_mae": float(np.mean(np.abs(err))),
        "score_rmse": float(math.sqrt(np.mean(err ** 2))),
        "score_bias": float(np.mean(err)),
    }


def metrics(rows: MatchRows, mask: Optional[np.ndarray] = None) -> Dict[str, Any]:
    if mask is None:
        mask = np.ones(rows.n, dtype=bool)
    p, o = rows.p[mask], rows.o[mask]
    raw = _raw_metrics(p, o, rows.pred[mask], rows.act[mask])
    out: Dict[str, Any] = {"n": int(mask.sum()), "ties": int(np.sum(o == 0.5))}
    for k in ("acc", "acc_no_ties", "brier", "log_loss", "conf"):
        out[k] = _r4(raw[k])
    out["score_n"] = int(2 * mask.sum())
    for k in ("score_mae", "score_rmse", "score_bias"):
        out[k] = _r4(raw[k])
    rp: Dict[str, Any] = {}
    for k in rows.rp_pred:
        pr = rows.rp_pred[k][mask].ravel()
        ac = rows.rp_act[k][mask].ravel()
        ok = ~np.isnan(pr) & ~np.isnan(ac)
        pr, ac = pr[ok], ac[ok]
        rp[k] = {
            "n": int(ok.sum()),
            "brier": _r4(np.mean((pr - ac) ** 2)) if len(pr) else None,
            "acc": _r4(np.mean((pr > 0.5) == (ac > 0.5))) if len(pr) else None,
        }
    out["rp"] = rp
    return out


def calibration(rows: MatchRows, mask: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
    if mask is None:
        mask = np.ones(rows.n, dtype=bool)
    p, o = rows.p[mask], rows.o[mask]
    out = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        sel = (p >= lo) & ((p < hi) if i < 9 else (p <= hi))
        out.append({"lo": lo, "hi": hi, "n": int(sel.sum()),
                    "mean_pred": _r4(p[sel].mean()) if sel.any() else None,
                    "frac_red_win": _r4(o[sel].mean()) if sel.any() else None})
    return out


def slices(rows: MatchRows) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {"week": {}, "event_type": {}, "comp_level": {},
                                      "season_matches": {}, "event_matches": {}}
    for w in range(1, 9):
        m = rows.week == w
        if m.any():
            out["week"][str(w)] = metrics(rows, m)
    for et in EVENT_TYPES:
        m = rows.etype == et
        if m.any():
            out["event_type"][et] = metrics(rows, m)
    for name, m in (("qual", ~rows.elim), ("elim", rows.elim)):
        if m.any():
            out["comp_level"][name] = metrics(rows, m)
    for key, arr in (("season_matches", rows.season_first3), ("event_matches", rows.event_first3)):
        for name, m in (("first3", arr), ("later", ~arr)):
            if m.any():
                out[key][name] = metrics(rows, m)
    return out


def score_season(run: ModelRun, leak_audit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """season.schema.json#/$defs/backtestSummary for one ModelRun."""
    rows = match_rows(run)
    la = leak_audit or {"cuts": 0, "max_abs_diff_win_prob": 0.0, "passed": False}
    la = {"cuts": int(la.get("cuts", 0)),
          "max_abs_diff_win_prob": float(la.get("max_abs_diff_win_prob", 0.0)),
          "passed": bool(la.get("passed", False))}
    return {
        "model": run.model_id,
        "season": int(run.year),
        "config_hash": run.config_hash,
        "year_constants": YEAR_CONSTANTS,
        "leak_audit": la,
        "overall": metrics(rows),
        "slices": slices(rows),
        "calibration": calibration(rows),
    }


# --------------------------------------------------------------------------------------------
# paired bootstrap
# --------------------------------------------------------------------------------------------

BOOT_METRICS = ("acc", "acc_no_ties", "brier", "log_loss", "score_mae")


def _per_match_terms(rows: MatchRows) -> Dict[str, np.ndarray]:
    p, o = rows.p, rows.o
    nt = o != 0.5
    return {
        "acc": ((p > 0.5) == (o > 0.5)).astype(float),
        "acc_no_ties": np.where(nt, ((p > 0.5) == (o > 0.5)).astype(float), np.nan),
        "brier": (p - o) ** 2,
        "log_loss": -(o * np.log(p) + (1 - o) * np.log(1 - p)),
        "score_mae": np.abs(rows.pred - rows.act).mean(axis=1),  # per match = mean of 2 alliances
    }


def paired_bootstrap(
    a: MatchRows,
    b: MatchRows,
    mask: Optional[np.ndarray] = None,
    n_boot: int = 2000,
    seed: int = 1218,
    cluster: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """CIs for metric(b) - metric(a), resampling matches with replacement (paired: the same
    resampled matches for both models).  cluster="event" resamples whole events instead."""
    if a.keys != b.keys:
        raise ValueError("rows are not aligned (different scored match sets)")
    if mask is None:
        mask = np.ones(a.n, dtype=bool)
    ta, tb = _per_match_terms(a), _per_match_terms(b)
    idx = np.flatnonzero(mask)
    rng = np.random.default_rng(seed)
    out = {}
    if cluster == "event":
        ev = a.event[idx]
        groups = [idx[ev == e] for e in sorted(set(ev))]
    for k in BOOT_METRICS:
        da, db = ta[k][idx], tb[k][idx]
        ok = ~np.isnan(da)
        point = float(np.nanmean(db) - np.nanmean(da))
        if cluster == "event":
            sums_a = np.array([np.nansum(ta[k][g]) for g in groups])
            sums_b = np.array([np.nansum(tb[k][g]) for g in groups])
            cnts = np.array([np.sum(~np.isnan(ta[k][g])) for g in groups])
            G = len(groups)
            sel = rng.integers(0, G, size=(n_boot, G))
            c = cnts[sel].sum(axis=1)
            diffs = (sums_b[sel].sum(axis=1) - sums_a[sel].sum(axis=1)) / np.maximum(c, 1)
        else:
            d = (db - da)[ok]
            m = len(d)
            diffs = np.empty(n_boot)
            for i in range(0, n_boot, 250):
                j = min(n_boot, i + 250)
                sel = rng.integers(0, m, size=(j - i, m))
                diffs[i:j] = d[sel].mean(axis=1)
        lo, hi = np.percentile(diffs, [2.5, 97.5])
        out[k] = {"diff": point, "lo": float(lo), "hi": float(hi),
                  "a": float(np.nanmean(da)), "b": float(np.nanmean(db)), "n": int(ok.sum())}
    return out


# --------------------------------------------------------------------------------------------
# leak audit (4.5.4) and perturbation checks
# --------------------------------------------------------------------------------------------

def _clear_result(m) -> None:
    m.status = MatchStatus.UPCOMING
    m.winner = None
    for a in ("red", "blue"):
        for f in MATCH_FIELDS:
            setattr(m, f"{a}_{f}", None)
        setattr(m, f"{a}_score", -1)


def _comparable(order: Sequence[Any], T: int) -> List[str]:
    """Matches whose prediction must be identical when every match with time >= T is unplayed:
    time < T, plus time == T unless a counted team already appeared earlier in that time group
    (processing order inside a tie group can let a same-time match update a shared team)."""
    keys, seen = [], set()
    for m in order:
        if m.time < T:
            keys.append(m.key)
        elif m.time == T:
            teams = set(_counted(m))
            if not (teams & seen):
                keys.append(m.key)
            seen |= teams
    return keys


def _pred_vec(run: ModelRun, key: str) -> np.ndarray:
    m = run.objs[4][key]
    return np.array([run.win_prob[key], m.epa_red_score_pred, m.epa_blue_score_pred], dtype=float)


def leak_audit(
    year: int,
    base_objs: tuple,
    priors,
    model_id: str,
    config: Optional[dict] = None,
    robot_facts=None,
    cuts: int = 3,
    seed: int = 1218,
    full_run: Optional[ModelRun] = None,
) -> Dict[str, Any]:
    """Truncation invariance: predictions up to each cut time T are unchanged when every match
    at time >= T is made unplayed (Year held fixed to the full-season object)."""
    full = full_run or run_model(year, base_objs, priors, model_id, config, robot_facts,
                                 stages="calc")
    order = processing_order(base_objs[4])
    times = sorted({m.time for m in order if m.status == MatchStatus.COMPLETED})
    rng = random.Random(seed)
    lo, hi = len(times) // 10, len(times) - 1
    picks = sorted(rng.sample(times[lo:hi], min(cuts, max(0, hi - lo))))
    worst_wp = worst_all = 0.0
    n_cmp = 0
    for T in picks:
        objs = copy.deepcopy(base_objs)
        for m in objs[4].values():
            if m.time >= T:
                _clear_result(m)
        tr = run_model(year, objs, priors, model_id, config, robot_facts, stages="calc",
                       copy_objs=False)
        for k in _comparable(order, T):
            d = np.abs(_pred_vec(tr, k) - _pred_vec(full, k))
            worst_wp = max(worst_wp, float(d[0]))
            worst_all = max(worst_all, float(d.max()))
            n_cmp += 1
    tol = 0.0 if model_id == "epa" else 1e-9
    return {"cuts": len(picks), "cut_times": picks, "compared": n_cmp,
            "max_abs_diff_win_prob": worst_wp, "max_abs_diff_any": worst_all,
            "passed": bool(worst_all <= tol)}


def perturbation_check(
    year: int,
    base_objs: tuple,
    priors,
    model_id: str,
    config: Optional[dict] = None,
    robot_facts=None,
    match_key: Optional[str] = None,
    seed: int = 1218,
    full_run: Optional[ModelRun] = None,
) -> Dict[str, Any]:
    """(a) change match m's own result: predictions of m and every match before m unchanged,
    some later prediction changes; (b) shuffle results among matches after m: predictions up to
    and including m unchanged."""
    full = full_run or run_model(year, base_objs, priors, model_id, config, robot_facts,
                                 stages="calc")
    order = processing_order(base_objs[4])
    rng = random.Random(seed)
    if match_key is None:
        done = [m for m in order if m.status == MatchStatus.COMPLETED and not m.elim]
        match_key = done[len(done) // 2 + rng.randrange(len(done) // 10)].key
    pos = next(i for i, m in enumerate(order) if m.key == match_key)
    m0 = order[pos]
    upto = [m.key for m in order[: pos + 1]]
    later = [m.key for m in order[pos + 1:]]

    # (a) perturb m: swap the alliances' results and add 37 points to the new red side
    a = copy.deepcopy(base_objs)
    mm = a[4][match_key]
    for f in MATCH_FIELDS:
        r_, b_ = getattr(mm, f"red_{f}"), getattr(mm, f"blue_{f}")
        setattr(mm, f"red_{f}", b_)
        setattr(mm, f"blue_{f}", r_)
    for f in ("score", "no_foul", "teleop"):
        v = getattr(mm, f"red_{f}")
        setattr(mm, f"red_{f}", (v or 0) + 37)
    mm.winner = MatchWinner.RED
    ra = run_model(year, a, priors, model_id, config, robot_facts, stages="calc",
                   copy_objs=False)
    diff_upto_a = max(float(np.abs(_pred_vec(ra, k) - _pred_vec(full, k)).max()) for k in upto)
    diff_later_a = max((float(np.abs(_pred_vec(ra, k) - _pred_vec(full, k)).max())
                        for k in later), default=0.0)

    # (b) shuffle future results among future completed matches (by whole alliance results)
    b = copy.deepcopy(base_objs)
    fut = [b[4][k] for k in later if b[4][k].status == MatchStatus.COMPLETED]
    res = [{f: (getattr(x, f"red_{f}"), getattr(x, f"blue_{f}")) for f in MATCH_FIELDS}
           | {"winner": x.winner} for x in fut]
    rng.shuffle(res)
    for x, rr in zip(fut, res):
        for f in MATCH_FIELDS:
            setattr(x, f"red_{f}", rr[f][0])
            setattr(x, f"blue_{f}", rr[f][1])
        x.winner = rr["winner"]
    rb = run_model(year, b, priors, model_id, config, robot_facts, stages="calc",
                   copy_objs=False)
    diff_upto_b = max(float(np.abs(_pred_vec(rb, k) - _pred_vec(full, k)).max()) for k in upto)
    diff_later_b = max((float(np.abs(_pred_vec(rb, k) - _pred_vec(full, k)).max())
                        for k in later), default=0.0)
    return {"match": match_key, "time": m0.time, "n_upto": len(upto), "n_later": len(later),
            "perturb_m": {"max_diff_upto_m": diff_upto_a, "max_diff_later": diff_later_a},
            "shuffle_future": {"max_diff_upto_m": diff_upto_b, "max_diff_later": diff_later_b}}


# --------------------------------------------------------------------------------------------
# week-1 look-ahead (Q3)
# --------------------------------------------------------------------------------------------

def _year_from(base_objs: tuple, matches: Iterable[Any]):
    y = copy.deepcopy(base_objs[0])
    return loader._avg.process_year(y, list(matches))


def week1_only(base_objs: tuple) -> tuple:
    """Copy of the season with only week-1 matches (all a week-1 prediction can depend on)."""
    objs = copy.deepcopy(base_objs)
    keep = {k: m for k, m in objs[4].items() if m.week == 1}
    return (objs[0], objs[1], objs[2], objs[3], keep, objs[5])


def lookahead_week1(
    year: int,
    base_objs: tuple,
    priors,
    model_id: str,
    config: Optional[dict] = None,
    robot_facts=None,
    mode: str = "loeo",
    min_matches: int = 100,
) -> Dict[str, Dict[str, float]]:
    """Leak-free week-1 predictions with Year constants that exclude the match being predicted.

    mode="loeo":   for each week-1 event E, Year constants from week-1 matches of OTHER events
                   (drops the self-inclusion; concurrent week-1 events still inform it).
    mode="causal": Year constants from week-1 matches with time < the cut that starts the
                   match's 6-hour block (what a live system rebuilding every 6 hours would
                   have used).  Blocks with fewer than `min_matches` earlier week-1 matches use
                   the first block that has them (residual look-ahead, counted in `n_fallback`).
    Returns {match_key: {"p": win prob, "red": score, "blue": score}} for week-1 matches.
    """
    w1 = week1_only(base_objs)
    w1_done = [m for m in w1[4].values() if m.status == MatchStatus.COMPLETED]
    out: Dict[str, Dict[str, float]] = {}

    def run_with(year_obj, keys):
        objs = copy.deepcopy(w1)
        objs = (year_obj, *objs[1:])
        rr = run_model(year, objs, priors, model_id, config, robot_facts, stages="calc",
                       copy_objs=False)
        for k in keys:
            m = rr.objs[4][k]
            out[k] = {"p": float(m.epa_win_prob), "red": float(m.epa_red_score_pred),
                      "blue": float(m.epa_blue_score_pred)}

    if mode == "loeo":
        events = sorted({m.event for m in w1[4].values()})
        for e in events:
            yo = _year_from(base_objs, [m for m in w1_done if m.event != e])
            run_with(yo, [k for k, m in w1[4].items() if m.event == e])
        return out
    if mode == "causal":
        block = 6 * 3600
        times = sorted(m.time for m in w1[4].values())
        if not times:
            return out
        t0 = times[0] - (times[0] % block)
        cuts = list(range(t0, times[-1] + block, block))
        done_times = sorted(m.time for m in w1_done)
        first_ok = next((c for c in cuts if np.searchsorted(done_times, c) >= min_matches),
                        cuts[-1])
        n_fallback = 0
        for c in cuts:
            keys = [k for k, m in w1[4].items() if c <= m.time < c + block]
            if not keys:
                continue
            use = max(c, first_ok)
            if use != c:
                n_fallback += len(keys)
            yo = _year_from(base_objs, [m for m in w1_done if m.time < use])
            run_with(yo, keys)
        out["__n_fallback__"] = {"p": float(n_fallback), "red": 0.0, "blue": 0.0}
        return out
    raise ValueError(mode)


def rows_with_overrides(rows: MatchRows, over: Dict[str, Dict[str, float]]) -> MatchRows:
    """Copy of rows with p / predicted scores replaced for the matches in `over`."""
    r = copy.deepcopy(rows)
    pos = {k: i for i, k in enumerate(r.keys)}
    for k, v in over.items():
        i = pos.get(k)
        if i is None:
            continue
        r.p[i] = min(max(v["p"], EPS), 1 - EPS)
        r.pred[i] = (v["red"], v["blue"])
    return r


# --------------------------------------------------------------------------------------------
# season chain (SPEC 2.6): 2022 <- archive 2020/2019; later seasons <- computed EPA seasons
# --------------------------------------------------------------------------------------------

def archive_priors(year: int, archive_csv: str, computed: Dict[int, Dict[int, Any]]):
    """all_team_years for `year`: computed seasons where available, archive file otherwise
    (sb.priors semantics: offseason rows dropped, non-numeric teams skipped)."""
    from sb.priors import load_archive_years, prior_years

    need = [y for y in prior_years(year) if y not in computed]
    res = {y: computed[y] for y in prior_years(year) if y in computed}
    if need:
        d = tempfile.mkdtemp(prefix="pw_arch_")
        link = os.path.join(d, "team_years.csv")
        os.symlink(os.path.abspath(archive_csv), link)
        try:
            arch = load_archive_years(d, need, False)
        finally:
            os.unlink(link)
            os.rmdir(d)
        for y in need:
            if arch.get(y):
                res[y] = arch[y]
    return dict(sorted(res.items()))


def _slim_team_years(team_years: Dict[str, Any]) -> Dict[int, Any]:
    """Prior-season TeamYears reduced to what start_season reads (norm_epa; count and comp
    ratings for the Fast EPA piece seed), so a season's full objs can be freed."""
    from sb.models import TeamYear

    keep = ("year", "team", "norm_epa", "count") + tuple(f"comp_{i}_epa" for i in range(10))
    out = {}
    for ty in team_years.values():
        out[ty.team] = TeamYear(**{k: getattr(ty, k) for k in keep})
    return out


def season_chain(
    years: Sequence[int],
    raw_dir: str,
    archive_csv: str,
    archive_seasons: Iterable[int] = (),
    keep_runs: Any = True,
    keep_raw: bool = False,
    with_facts: bool = True,
) -> Dict[int, Dict[str, Any]]:
    """Load and run EPA over `years` in ascending order, chaining priors per SPEC 2.6 through
    team_years.csv files exactly as build.py does (so priors carry norm_epa only; a Fast EPA
    "pieces" seed therefore falls back to norm_epa here just as in the shipping build).
    archive_seasons: prior seasons to take from the archive even if computed here (e.g. 2022
    for the validated 2023 replay).  keep_runs: True, False or a set of years whose EPA run is
    kept.  Returns {year: {"base", "priors", "epa", "facts", ["raw"]}}."""
    from sb.fast_epa import robot_facts

    from sb.export import export_year
    from sb.priors import build_priors

    out: Dict[int, Dict[str, Any]] = {}
    force_arch = set(archive_seasons)
    built: List[int] = []
    out_dir = tempfile.mkdtemp(prefix="pw_chain_")
    arch_dir = tempfile.mkdtemp(prefix="pw_arch_")
    os.symlink(os.path.abspath(archive_csv), os.path.join(arch_dir, "team_years.csv"))
    for y in sorted(years):
        raw = loader.read_raw(os.path.join(raw_dir, f"tba_{y}.json.gz"))
        base = load_base(y, raw)
        # exactly the shipping path (build.py): computed seasons via out/{y}/team_years.csv
        pri, _ = build_priors(y, arch_dir, out_dir, [b for b in built if b not in force_arch], False)
        epa = run_model(y, base, pri, "epa")
        export_year(epa.objs, out_dir)
        built.append(y)
        keep = keep_runs if isinstance(keep_runs, bool) else (y in set(keep_runs))
        out[y] = {"base": base, "priors": pri, "epa": epa if keep else None,
                  "facts": robot_facts(y, raw) if with_facts else None}
        if keep_raw:
            out[y]["raw"] = raw
        del raw, epa
    import shutil

    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.rmtree(arch_dir, ignore_errors=True)
    return out

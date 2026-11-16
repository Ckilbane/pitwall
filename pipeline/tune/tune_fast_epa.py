#!/usr/bin/env python3
"""Tune Fast EPA settings on past seasons and freeze the result in pipeline/models/fast_epa.json.

    python3 -I pipeline/tune/tune_fast_epa.py --raw-dir DIR \
        [--archive pipeline/priors/archive_team_years_2016_2023.csv] \
        [--tune 2022 2023 2024] [--holdout 2025] [--workers 2] [--out pipeline/models/fast_epa.json]

Run by hand in a session, never in the Action.  2026 is refused as a tuning season.

Method (small and honest; every config tried is counted and reported):
  * start at the EPA-equivalent point (byte-identical to EPA);
  * one pass of coordinate search over a fixed list of alternatives per coordinate (robot
    attribution, residual split, robot weight, dampening schedule, elim weight, mean reversion,
    passback, seed); a coordinate value is adopted only if it lowers the objective;
  * one local refinement of the dampening schedule around the adopted one;
  * win_k last: it changes only the win-probability mapping, so its candidates are evaluated
    exactly from the recorded (unrounded) win probabilities of the adopted config, no rerun.
Objective: mean over the tuning seasons of the win-prob Brier score on all scored matches
(SPEC 4.5.2); tie-break: mean score MAE.  The held-out season is scored once, for the chosen
config only (and EPA), after the search has finished.
"""
import argparse
import copy
import datetime as _dt
import json
import math
import multiprocessing as mp
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
sys.path.insert(0, PIPELINE)

import numpy as np  # noqa: E402

from sb import backtest as bt  # noqa: E402
from sb.fast_epa import EPA_EQUIVALENT  # noqa: E402
from sb.model_runner import config_hash, run_model  # noqa: E402

FORBIDDEN = {2026}
DEFAULT_ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
DEFAULT_OUT = os.path.join(PIPELINE, "models", "fast_epa.json")
OBJECTIVE = "mean over tuning seasons of win-prob Brier (all scored matches); tie-break mean score MAE"

PERCENT_PRESETS = {
    "epa": EPA_EQUIVALENT["percent"],
    "fast_first3": {"first_n": 3, "first": 0.5, "start": 1 / 3, "end": 0.2, "ramp_start": 6,
                    "ramp_len": 6},
    "spec_default": {"first_n": 3, "first": 0.6, "start": 0.4, "end": 0.2, "ramp_start": 3,
                     "ramp_len": 6},
    "uniform_fast": {"first_n": 0, "first": 0.45, "start": 0.45, "end": 0.25, "ramp_start": 3,
                     "ramp_len": 9},
    "aggressive": {"first_n": 4, "first": 0.7, "start": 0.45, "end": 0.25, "ramp_start": 4,
                   "ramp_len": 8},
    "high_floor": {"first_n": 0, "first": 1 / 3, "start": 1 / 3, "end": 0.25, "ramp_start": 6,
                   "ramp_len": 6},
}

# coordinate -> list of (label, settings patch); applied in this order
COORDINATES = [
    ("robot_attribution", [("on", {"robot_attribution": True})]),
    ("residual_split", [("share", {"residual_split": "share"})]),
    ("robot_weight", [("0.5", {"robot_weight": 0.5})]),
    ("percent", [(k, {"percent": v}) for k, v in PERCENT_PRESETS.items() if k != "epa"]),
    ("elim_weight", [("0.2", {"elim_weight": 0.2}), ("0.5", {"elim_weight": 0.5})]),
    ("mean_reversion", [("0.3", {"mean_reversion": 0.3}), ("0.5", {"mean_reversion": 0.5})]),
    ("passback", [("0.25", {"passback": {"enabled": True, "percent_scale": 0.25, "scope": "quals"}}),
                  ("0.5", {"passback": {"enabled": True, "percent_scale": 0.5, "scope": "quals"}})]),
    ("seed", [("pieces", {"seed": "pieces"})]),
]
WIN_K = [-0.55, -0.6, -0.65, -0.7, -0.75]

_CTX = {}


def _apply(settings, patch):
    s = copy.deepcopy(settings)
    for k, v in patch.items():
        s[k] = copy.deepcopy(v)
    return s


def _refine(percent):
    """Two neighbours of the adopted schedule: every rate x0.85 and x1.15 (floors included)."""
    out = []
    for f, lab in ((0.85, "x0.85"), (1.15, "x1.15")):
        p = dict(percent)
        for k in ("first", "start", "end"):
            p[k] = round(min(0.95, p[k] * f), 4)
        out.append((lab, {"percent": p}))
    return out


def _eval_config(settings):
    """Worker: run Fast EPA (calc only) on every tuning season; per-season metrics and the
    unrounded win probs needed for the win_k sweep."""
    res = {}
    for y, c in _CTX["seasons"].items():
        run = run_model(y, c["base"], c["priors"], "fast", {"settings": settings}, c["facts"],
                        stages="calc")
        rows = bt.match_rows(run)
        m = bt.metrics(rows)
        raw_p = np.array([run.win_prob[k] for k in rows.keys])
        res[y] = {"brier": m["brier"], "acc": m["acc"], "score_mae": m["score_mae"],
                  "log_loss": m["log_loss"], "n": m["n"], "raw_p": raw_p, "o": rows.o,
                  "k": run.k}
    return res


def _summ(res, seasons):
    b = float(np.mean([res[y]["brier"] for y in seasons]))
    mae = float(np.mean([res[y]["score_mae"] for y in seasons]))
    acc = float(np.mean([res[y]["acc"] for y in seasons]))
    return {"objective": round(b, 6), "score_mae": round(mae, 4), "acc": round(acc, 4),
            "per_season": {str(y): {k: res[y][k] for k in ("brier", "acc", "score_mae", "log_loss", "n")}
                           for y in seasons}}


def _rescale_k(raw_p, k_old, k_new):
    """Win prob is 1/(1+10^(k*d)); recover d from p and re-map with k_new (exact)."""
    p = np.clip(raw_p, 1e-15, 1 - 1e-15)
    d = np.log10(1 / p - 1) / k_old
    q = 1 / (1 + 10 ** (k_new * d))
    q = np.round(q, 4)  # what record_match stores
    return np.clip(q, 1e-6, 1 - 1e-6)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    ap.add_argument("--tune", type=int, nargs="+", default=[2022, 2023, 2024])
    ap.add_argument("--holdout", type=int, nargs="*", default=[2025])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--record", default=None, help="also write the full search log (JSON)")
    args = ap.parse_args(argv)

    bad = sorted(set(args.tune) & FORBIDDEN)
    if bad:
        print(f"refusing to tune on {bad}: 2026 is the out-of-sample season and is never used "
              f"for tuning", file=sys.stderr)
        return 2
    if set(args.tune) & set(args.holdout):
        print("holdout seasons must not overlap tuning seasons", file=sys.stderr)
        return 2

    t0 = time.time()
    all_years = sorted(set(args.tune) | set(args.holdout))
    chain = bt.season_chain(list(range(min(all_years), max(all_years) + 1)), args.raw_dir,
                            args.archive, keep_runs=set(args.holdout))
    _CTX["seasons"] = {y: {"base": chain[y]["base"], "priors": chain[y]["priors"],
                           "facts": chain[y]["facts"]} for y in args.tune}
    print(f"loaded {sorted(chain)} in {time.time() - t0:.0f}s", flush=True)

    tried = []  # every config evaluated, in order
    pool = mp.get_context("fork").Pool(args.workers) if args.workers > 1 else None

    def evaluate(cands):
        sets = [c[2] for c in cands]
        outs = pool.map(_eval_config, sets) if pool else [_eval_config(s) for s in sets]
        rec = []
        for (coord, label, s), res in zip(cands, outs):
            summ = _summ(res, args.tune)
            entry = {"i": len(tried), "coordinate": coord, "value": label, "settings": s,
                     "config_hash": config_hash({"settings": s}), **summ}
            tried.append(entry)
            rec.append((entry, res))
            print(f"  [{entry['i']:2d}] {coord}={label}: brier {summ['objective']:.5f} "
                  f"acc {summ['acc']:.4f} mae {summ['score_mae']:.3f}", flush=True)
        return rec

    def key(e):
        return (e["objective"], e["score_mae"])

    best_s = copy.deepcopy(EPA_EQUIVALENT)
    (best, best_res), = evaluate([("baseline", "epa_equivalent", best_s)])
    baseline = best
    for coord, alts in COORDINATES + [("percent_refine", None)]:
        if alts is None:
            alts = _refine(best_s["percent"])
            coord = "percent"
        cands = [(coord, lab, _apply(best_s, patch)) for lab, patch in alts]
        for e, res in evaluate(cands):
            if key(e) < key(best):
                best, best_res, best_s = e, res, e["settings"]
        print(f"after {coord}: best = {best['coordinate']}={best['value']} "
              f"({best['objective']:.5f})", flush=True)

    # win_k: exact re-mapping of the adopted config's win probabilities
    k_old = best_res[args.tune[0]]["k"]
    for k_new in WIN_K:
        if abs(k_new - k_old) < 1e-12:
            continue
        s = _apply(best_s, {"win_k": k_new})
        per = {}
        for y in args.tune:
            q = _rescale_k(best_res[y]["raw_p"], k_old, k_new)
            o = best_res[y]["o"]
            per[y] = {"brier": round(float(np.mean((q - o) ** 2)), 4),
                      "acc": round(float(np.mean((q > 0.5) == (o > 0.5))), 4),
                      "score_mae": best_res[y]["score_mae"],
                      "log_loss": round(float(-np.mean(o * np.log(q) + (1 - o) * np.log(1 - q))), 4),
                      "n": best_res[y]["n"]}
        summ = _summ(per, args.tune)
        e = {"i": len(tried), "coordinate": "win_k", "value": str(k_new), "settings": s,
             "config_hash": config_hash({"settings": s}), "evaluated": "remapped", **summ}
        tried.append(e)
        print(f"  [{e['i']:2d}] win_k={k_new}: brier {summ['objective']:.5f}", flush=True)
        if key(e) < key(best):
            best, best_s = e, s
    if pool:
        pool.close()
        pool.join()

    # held-out check: chosen config and EPA, scored once
    holdout = {}
    for y in args.holdout:
        c = chain[y]
        facts = c["facts"]
        fr = run_model(y, c["base"], c["priors"], "fast", {"settings": best_s}, facts,
                       stages="calc")
        re_, rf_ = bt.match_rows(c["epa"]), bt.match_rows(fr)
        me, mf = bt.metrics(re_), bt.metrics(rf_)
        boot = bt.paired_bootstrap(re_, rf_, n_boot=2000)
        holdout[str(y)] = {
            "epa": {k: me[k] for k in ("n", "acc", "acc_no_ties", "brier", "log_loss", "score_mae")},
            "fast": {k: mf[k] for k in ("n", "acc", "acc_no_ties", "brier", "log_loss", "score_mae")},
            "fast_minus_epa_ci95": {k: {kk: round(vv, 5) for kk, vv in v.items() if kk in ("diff", "lo", "hi")}
                                    for k, v in boot.items()},
        }
        print(f"holdout {y}: EPA brier {me['brier']} acc {me['acc']} | Fast brier {mf['brier']} "
              f"acc {mf['acc']}", flush=True)

    ranked = sorted(tried, key=key)
    strip = lambda e: {k: v for k, v in e.items() if k != "settings"} | {"settings": e["settings"]}  # noqa: E731
    cfg = {
        "id": "fast",
        "version": 1,
        "settings": best_s,
        "tuning": {
            "tuned_on": sorted(args.tune),
            "tuned_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "objective": OBJECTIVE,
            "search": {"method": "one-pass coordinate search from the EPA-equivalent point, "
                                 "then one dampening refinement, then win_k by exact remapping",
                       "configs_tried": len(tried),
                       "coordinates": [c for c, _ in COORDINATES] + ["percent (refine)", "win_k"],
                       "holdout": sorted(args.holdout),
                       "holdout_used_for_selection": False},
            "baseline": {"model": "epa", **{k: baseline[k] for k in ("objective", "acc", "score_mae", "per_season")}},
            "chosen": {k: best[k] for k in ("i", "coordinate", "value", "config_hash", "objective",
                                            "acc", "score_mae", "per_season")},
            "holdout_check": holdout,
            "top": [strip(e) for e in ranked[:20]],
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=1, sort_keys=False)
        f.write("\n")
    if args.record:
        with open(args.record, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"tried": tried}, f, indent=1)
            f.write("\n")
    print(f"wrote {args.out}: {len(tried)} configs, {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""EPA vs Fast EPA backtest report, 2022-2025 (run by hand; the Action uses sb.backtest directly).

    python3 -I pipeline/tune/backtest_report.py --raw-dir DIR \
        [--archive pipeline/priors/archive_team_years_2016_2023.csv] \
        [--config pipeline/models/fast_epa.json] [--out pipeline/models/fast_epa_backtest.json]

For every season: both models' backtest summaries (pre-match predictions only), paired
bootstrap 95% CIs of Fast - EPA (by match, and by event as a robustness check) overall and per
slice, leak audits, the week-1 Year-constant look-ahead effect (leave-one-event-out and causal
6-hour rebuilds), and, for context only, "hindsight" accuracy from end-of-season ratings (what a
backtest that leaks the final rating would report).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
sys.path.insert(0, PIPELINE)

import numpy as np  # noqa: E402

from sb import backtest as bt  # noqa: E402
from sb.fast_epa import load_config  # noqa: E402
from sb.model_runner import run_model  # noqa: E402

from src.models.epa.main import EPA  # noqa: E402
from src.models.epa.math import EPARating  # noqa: E402
from src.types.enums import MatchStatus  # noqa: E402

DEFAULT_ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
VEC = (["epa", "auto_epa", "teleop_epa", "endgame_epa", "rp_1_epa", "rp_2_epa", "rp_3_epa",
        "tiebreaker_epa"] + [f"comp_{i}_epa" for i in range(10)])


def _round(x, nd=5):
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    if isinstance(x, float):
        return round(x, nd)
    return x


def hindsight(run) -> dict:
    """Accuracy/Brier when every match is predicted from END-of-season ratings (leaky)."""
    year_obj, team_years, events, _, matches, _ = run.objs
    m = EPA()
    m.year_obj, m.year_num, m.num_teams, m.k = year_obj, year_obj.year, 3, run.k
    m.epas = {ty.team: EPARating(np.array([float(getattr(ty, f) or 0) for f in VEC]))
              for ty in team_years.values()}
    p, o = [], []
    for mt in matches.values():
        if mt.status != MatchStatus.COMPLETED or (mt.red_score or 0) < 0 or (mt.blue_score or 0) < 0:
            continue
        oc = bt.OUTCOME.get(mt.get_winner())
        if oc is None:
            continue
        wp, _, _ = m.predict_match(mt, events[mt.event])
        p.append(min(max(round(float(wp), 4), 1e-6), 1 - 1e-6))
        o.append(oc)
    p, o = np.array(p), np.array(o)
    return {"n": int(len(p)), "acc": round(float(np.mean((p > 0.5) == (o > 0.5))), 4),
            "brier": round(float(np.mean((p - o) ** 2)), 4)}


def compare(re_, rf_, mask=None, n_boot=2000, cluster=None):
    b = bt.paired_bootstrap(re_, rf_, mask=mask, n_boot=n_boot, cluster=cluster)
    return {k: {"epa": round(v["a"], 5), "fast": round(v["b"], 5), "diff": round(v["diff"], 5),
                "lo": round(v["lo"], 5), "hi": round(v["hi"], 5), "n": v["n"]}
            for k, v in b.items()}


def slice_masks(rows):
    out = {}
    for w in range(1, 9):
        out[f"week:{w}"] = rows.week == w
    for et in bt.EVENT_TYPES:
        out[f"event_type:{et}"] = rows.etype == et
    out["comp_level:qual"] = ~rows.elim
    out["comp_level:elim"] = rows.elim
    out["event_matches:first3"] = rows.event_first3
    out["event_matches:later"] = ~rows.event_first3
    out["season_matches:first3"] = rows.season_first3
    out["season_matches:later"] = ~rows.season_first3
    return {k: v for k, v in out.items() if v.any()}


def lookahead(y, c, model_id, cfg, facts, rows):
    w1 = rows.week == 1
    res = {"week1_n": int(w1.sum())}
    base_m = bt.metrics(rows, w1)
    res["standard_week1"] = {k: base_m[k] for k in ("acc", "brier", "log_loss", "score_mae")}
    for mode in ("loeo", "causal"):
        over = bt.lookahead_week1(y, c["base"], c["priors"], model_id, cfg, facts, mode=mode)
        nfb = over.pop("__n_fallback__", {"p": 0})["p"]
        lr = bt.rows_with_overrides(rows, over)
        mm = bt.metrics(lr, w1)
        boot = bt.paired_bootstrap(rows, lr, mask=w1, n_boot=1000)
        res[mode] = {k: mm[k] for k in ("acc", "brier", "log_loss", "score_mae")}
        res[mode]["leakfree_minus_standard"] = {
            k: {"diff": round(v["diff"], 5), "lo": round(v["lo"], 5), "hi": round(v["hi"], 5)}
            for k, v in boot.items() if k in ("acc", "brier", "score_mae")}
        full = bt.metrics(lr)
        res[mode]["season_overall"] = {k: full[k] for k in ("acc", "brier", "score_mae")}
        if mode == "causal":
            res[mode]["n_fallback_matches"] = int(nfb)
        res[mode]["_rows"] = lr
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    ap.add_argument("--config", default=None)
    ap.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    ap.add_argument("--cuts", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(PIPELINE, "models", "fast_epa_backtest.json"))
    args = ap.parse_args(argv)

    cfg_file = load_config(args.config) if args.config else load_config()
    cfg = {"settings": cfg_file["settings"]}
    tuned_on = cfg_file.get("tuning", {}).get("tuned_on", [])
    t0 = time.time()
    chain = bt.season_chain(args.years, args.raw_dir, args.archive, keep_runs=False)
    print(f"chain {time.time() - t0:.0f}s", flush=True)

    report = {"config_settings": cfg["settings"], "tuned_on": tuned_on,
              "year_constants": bt.YEAR_CONSTANTS, "seasons": {}}
    pooled = {"tune": [], "holdout": []}
    raw_epa = {}
    for y in args.years:
        c = chain[y]
        facts = c["facts"]
        epa = run_model(y, c["base"], c["priors"], "epa")
        fast = run_model(y, c["base"], c["priors"], "fast", cfg, facts)
        la_e = bt.leak_audit(y, c["base"], c["priors"], "epa", None, None, cuts=args.cuts,
                             full_run=epa)
        la_f = bt.leak_audit(y, c["base"], c["priors"], "fast", cfg, facts, cuts=args.cuts,
                             full_run=fast)
        se, sf = bt.score_season(epa, la_e), bt.score_season(fast, la_f)
        re_, rf_ = bt.match_rows(epa), bt.match_rows(fast)
        sl = {}
        for name, mask in slice_masks(re_).items():
            sl[name] = compare(re_, rf_, mask, n_boot=1000)
        la_eff = {"epa": lookahead(y, c, "epa", None, None, re_),
                  "fast": lookahead(y, c, "fast", cfg, facts, rf_)}
        # Fast vs EPA when BOTH use leak-free (causal) week-1 constants
        lf = {}
        for mode in ("loeo", "causal"):
            a, b = la_eff["epa"][mode].pop("_rows"), la_eff["fast"][mode].pop("_rows")
            lf[mode] = compare(a, b, n_boot=1000)
        season = {
            "in_tuning_set": y in tuned_on,
            "summary": {"epa": se, "fast": sf},
            "fast_minus_epa": compare(re_, rf_),
            "fast_minus_epa_event_clustered": compare(re_, rf_, cluster="event"),
            "fast_minus_epa_by_slice": sl,
            "leak_audit": {"epa": la_e, "fast": la_f},
            "week1_lookahead": la_eff,
            "fast_minus_epa_leakfree_week1": lf,
            "hindsight_end_of_season_ratings": {"epa": hindsight(epa), "fast": hindsight(fast)},
            "statbotics_year_metrics_epa": {"count": epa.objs[0].count, "acc": epa.objs[0].epa_acc,
                                            "mse": epa.objs[0].epa_mse},
        }
        report["seasons"][str(y)] = season
        pooled["tune" if y in tuned_on else "holdout"].append((re_, rf_))
        raw_epa[y] = (np.array([epa.win_prob[k] for k in re_.keys]), epa.k, re_, rf_)
        del epa, fast, c["base"]
        o = season["fast_minus_epa"]
        print(f"{y}: acc EPA {o['acc']['epa']:.4f} Fast {o['acc']['fast']:.4f} "
              f"[{o['acc']['lo']:+.4f},{o['acc']['hi']:+.4f}]  brier EPA {o['brier']['epa']:.4f} "
              f"Fast {o['brier']['fast']:.4f} [{o['brier']['lo']:+.4f},{o['brier']['hi']:+.4f}]  "
              f"leak {la_e['passed']}/{la_f['passed']}  {time.time() - t0:.0f}s", flush=True)

    for name, pairs in pooled.items():
        if not pairs:
            continue
        a = pairs[0][0]
        b = pairs[0][1]
        for x, z in pairs[1:]:
            a = _concat(a, x)
            b = _concat(b, z)
        report[f"pooled_{name}"] = {"seasons": sorted({p[0].year for p in pairs}),
                                    "fast_minus_epa": compare(a, b)}

    # calibration-only baseline: EPA with its win_k chosen on the tuning seasons from the same
    # grid Fast EPA's win_k came from (isolates how much of Fast's Brier gain is just k)
    grid = [-0.55, -0.6, -0.625, -0.65, -0.7, -0.75]
    tune_years = [y for y in raw_epa if y in tuned_on]
    if tune_years:
        def kb(k):
            return np.mean([np.mean((_rescale_k(raw_epa[y][0], raw_epa[y][1], k) - raw_epa[y][2].o) ** 2)
                            for y in tune_years])
        k_best = min(grid, key=kb)
        rec = {"grid": grid, "chosen_k": k_best,
               "tune_brier_by_k": {str(k): round(float(kb(k)), 5) for k in grid}, "seasons": {}}
        for y, (rp, k0, re_, rf_) in raw_epa.items():
            rk = bt.rows_with_overrides(re_, {})
            rk.p = _rescale_k(rp, k0, k_best)
            rec["seasons"][str(y)] = {"in_tuning_set": y in tuned_on,
                                      "fast_minus_epa_k": compare(rk, rf_)}
        report["epa_recalibrated_k"] = rec

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(_round(report), f, indent=1)
        f.write("\n")
    print(f"wrote {args.out} in {time.time() - t0:.0f}s")
    return 0


def _rescale_k(raw_p, k_old, k_new):
    p = np.clip(raw_p, 1e-15, 1 - 1e-15)
    d = np.log10(1 / p - 1) / k_old
    return np.clip(np.round(1 / (1 + 10 ** (k_new * d)), 4), 1e-6, 1 - 1e-6)


def _concat(a, b):
    return bt.MatchRows(
        year=a.year, model_id=a.model_id, keys=[f"{a.year}:{k}" for k in a.keys] + [f"{b.year}:{k}" for k in b.keys]
        if not a.keys or ":" not in a.keys[0] else a.keys + [f"{b.year}:{k}" for k in b.keys],
        p=np.concatenate([a.p, b.p]), o=np.concatenate([a.o, b.o]),
        pred=np.concatenate([a.pred, b.pred]), act=np.concatenate([a.act, b.act]),
        week=np.concatenate([a.week, b.week]), etype=np.concatenate([a.etype, b.etype]),
        event=np.concatenate([a.event, b.event]), elim=np.concatenate([a.elim, b.elim]),
        season_first3=np.concatenate([a.season_first3, b.season_first3]),
        event_first3=np.concatenate([a.event_first3, b.event_first3]),
        rp_pred={}, rp_act={})


if __name__ == "__main__":
    sys.exit(main())

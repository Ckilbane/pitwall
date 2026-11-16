"""WP3b: backtest harness (SPEC 4.5): metrics, schema, leak-freedom."""
import json
import os
import sys

import numpy as np
import pytest

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(PIPELINE)
sys.path.insert(0, PIPELINE)

from sb import backtest as bt  # noqa: E402
from sb.fast_epa import robot_facts  # noqa: E402
from sb.loader import read_raw  # noqa: E402
from sb.model_runner import load_base, run_model  # noqa: E402

FIXTURE = os.path.join(PIPELINE, "tests", "fixtures", "tba_2023_mini.json.gz")
ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
FAST_CFG = {"settings": {"robot_attribution": True, "residual_split": "share",
                         "percent": {"first_n": 3, "first": 0.6, "start": 0.4, "end": 0.2,
                                     "ramp_start": 3, "ramp_len": 6},
                         "elim_weight": 0.5, "mean_reversion": 0.3,
                         "passback": {"enabled": True, "percent_scale": 0.5, "scope": "quals"},
                         "win_k": -0.7}}


@pytest.fixture(scope="module")
def mini():
    raw = read_raw(FIXTURE)
    base = load_base(2023, raw)
    priors = bt.archive_priors(2023, ARCHIVE, {})
    return raw, base, priors, robot_facts(2023, raw)


def _rows(p, o, pred=None, act=None, **kw):
    n = len(p)
    pred = np.zeros((n, 2)) if pred is None else np.array(pred, float)
    act = np.zeros((n, 2)) if act is None else np.array(act, float)
    return bt.MatchRows(
        year=2025, model_id="x", keys=[f"m{i}" for i in range(n)], p=np.array(p, float),
        o=np.array(o, float), pred=pred, act=act, week=np.array(kw.get("week", [1] * n)),
        etype=np.array(kw.get("etype", ["district"] * n), dtype=object),
        event=np.array(kw.get("event", ["e"] * n), dtype=object),
        elim=np.array(kw.get("elim", [False] * n)),
        season_first3=np.array(kw.get("s3", [True] * n)),
        event_first3=np.array(kw.get("e3", [True] * n)),
        rp_pred={"rp_1": np.array([[0.8, 0.2]] * n)}, rp_act={"rp_1": np.array([[1.0, 1.0]] * n)})


def test_metrics_hand_computed():
    p = [0.9, 0.3, 0.6, 0.5]
    o = [1, 0, 0.5, 0]  # third is a tie
    r = _rows(p, o, pred=[[10, 20]] * 4, act=[[12, 15]] * 4)
    m = bt.metrics(r)
    assert m["n"] == 4 and m["ties"] == 1
    # upstream get_acc: (p>0.5)==(o>0.5): T, T, F (0.6 vs tie), T
    assert m["acc"] == 0.75
    assert m["acc_no_ties"] == 1.0
    assert m["brier"] == round((0.01 + 0.09 + 0.01 + 0.25) / 4, 4)
    ll = -np.mean([np.log(0.9), np.log(0.7), 0.5 * np.log(0.6) + 0.5 * np.log(0.4), np.log(0.5)])
    assert m["log_loss"] == round(float(ll), 4)
    assert m["score_n"] == 8 and m["score_mae"] == 3.5 and m["score_bias"] == 1.5
    assert m["rp"]["rp_1"] == {"n": 8, "brier": round((0.04 + 0.64) / 2, 4), "acc": 0.5}


def test_calibration_bins():
    r = _rows([0.05, 0.15, 1.0 - 1e-6, 0.95], [0, 1, 1, 0.5])
    cal = bt.calibration(r)
    assert len(cal) == 10 and [c["n"] for c in cal] == [1, 1, 0, 0, 0, 0, 0, 0, 0, 2]
    assert cal[9]["frac_red_win"] == 0.75


def test_bootstrap_identical_is_zero_and_paired():
    r = _rows([0.9, 0.3, 0.6, 0.2] * 50, [1, 0, 1, 1] * 50)
    b = bt.paired_bootstrap(r, r, n_boot=200)
    assert all(v["diff"] == 0 and v["lo"] == 0 and v["hi"] == 0 for v in b.values())
    better = _rows([0.95, 0.2, 0.7, 0.6] * 50, [1, 0, 1, 1] * 50)
    b = bt.paired_bootstrap(r, better, n_boot=200)
    assert b["brier"]["diff"] < 0 and b["brier"]["hi"] < 0
    assert b["acc"]["diff"] == 0.25 and b["acc"]["lo"] > 0


def test_summary_validates_against_schema(mini):
    jsonschema = pytest.importorskip("jsonschema")
    raw, base, priors, facts = mini
    with open(os.path.join(REPO, "docs", "schema", "season.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    sub = {"$schema": schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
           "$defs": schema["$defs"], "$ref": "#/$defs/backtestSummary"}
    for model_id, cfg in (("epa", None), ("fast", FAST_CFG)):
        run = run_model(2023, base, priors, model_id, cfg, facts)
        la = bt.leak_audit(2023, base, priors, model_id, cfg, facts, cuts=2, full_run=run)
        s = bt.score_season(run, la)
        jsonschema.validate(s, sub)
        assert s["year_constants"] == "season_week1"
        assert s["overall"]["n"] == sum(v["n"] for v in s["slices"]["comp_level"].values())
        assert s["overall"]["n"] == sum(v["n"] for v in s["slices"]["event_matches"].values())


def test_epa_overall_matches_upstream_year_metrics(mini):
    raw, base, priors, facts = mini
    run = run_model(2023, base, priors, "epa")
    m = bt.metrics(bt.match_rows(run))
    y = run.objs[0]
    assert (m["n"], m["acc"], m["brier"], m["conf"]) == (y.count, y.epa_acc, y.epa_mse, y.epa_conf)


@pytest.mark.parametrize("model_id,cfg", [("epa", None), ("fast", FAST_CFG)])
def test_leak_audit_truncation_invariance(mini, model_id, cfg):
    """SPEC 4.5.4 on the CI fixture: 10 cuts, EPA exact, Fast <= 1e-9."""
    raw, base, priors, facts = mini
    la = bt.leak_audit(2023, base, priors, model_id, cfg, facts, cuts=10)
    assert la["cuts"] == 10 and la["compared"] > 100
    assert la["passed"], la


@pytest.mark.parametrize("model_id,cfg", [("epa", None), ("fast", FAST_CFG)])
def test_prediction_ignores_own_and_future_results(mini, model_id, cfg):
    """Changing match m's result, or shuffling every later result, leaves the predictions of m
    and of every earlier match bit-identical; the change does reach later matches."""
    raw, base, priors, facts = mini
    keys = [m.key for m in bt.processing_order(base[4]) if m.key.startswith("2023inmis_qm")]
    for key in (keys[20], keys[60]):
        res = bt.perturbation_check(2023, base, priors, model_id, cfg, facts, match_key=key)
        assert res["perturb_m"]["max_diff_upto_m"] == 0.0
        assert res["perturb_m"]["max_diff_later"] > 0.0
        assert res["shuffle_future"]["max_diff_upto_m"] == 0.0
        assert res["shuffle_future"]["max_diff_later"] > 0.0


def test_first3_slices(mini):
    raw, base, priors, facts = mini
    run = run_model(2023, base, priors, "epa")
    rows = bt.match_rows(run)
    first = [k for k, s in zip(rows.keys, rows.event_first3) if s]
    # every team plays at most ~3 matches before all six counted teams have 3 at this event
    assert first[0].endswith("qm1") and rows.event_first3[0]
    assert rows.season_first3.sum() <= rows.event_first3.sum() + rows.n  # sanity
    # a champs division match late in quals: everyone has played >= 3 there
    i = rows.keys.index("2023arc_qm100")
    assert not rows.event_first3[i]


def test_tune_refuses_2026():
    sys.path.insert(0, os.path.join(PIPELINE, "tune"))
    import tune_fast_epa

    assert tune_fast_epa.main(["--raw-dir", "/nonexistent", "--tune", "2025", "2026",
                               "--holdout"]) == 2
    assert tune_fast_epa.main(["--raw-dir", "/nonexistent", "--tune", "2024",
                               "--holdout", "2024"]) == 2

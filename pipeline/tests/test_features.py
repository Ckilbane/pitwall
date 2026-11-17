"""WP3c: consistency, change detector, opponent effect (SPEC 4.6): synthetic seasons with known
residuals, null rules, and an integration run on the 2023 mini fixture."""
import math
import os
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PIPELINE)

from sb import features as F  # noqa: E402


# ---------------------------------------------------------------------------------------------
# synthetic season: one team "1" (plus filler partners/opponents) with chosen attr/pre per match
# ---------------------------------------------------------------------------------------------
def _vec(x):
    v = np.zeros(18)
    v[0] = x
    return v


class Season:
    """Builds a duck-typed ModelRun.  Each add() is one qual or elim match with red (1, 2, 3) vs
    blue (4, 5, 6); pre/attr totals are given per team for the red side, blue is a filler."""

    def __init__(self):
        self.matches, self.pre_vec, self.attrib, self.updated, self.alliance_pred = {}, {}, {}, set(), {}
        self.t = 0

    def add(self, event, red_pre, red_attr, elim=False, upd=True, blue_actual=60.0, blue_pred=60.0,
            teams_red=(1, 2, 3), teams_blue=(4, 5, 6)):
        self.t += 1
        key = f"{event}_{'sf' if elim else 'qm'}{self.t}"
        m = NS(key=key, event=event, time=self.t, elim=elim, red_no_foul=70.0, blue_no_foul=blue_actual,
               pre_epas={}, get_red=lambda tr=teams_red: list(tr), get_blue=lambda tb=teams_blue: list(tb))
        self.matches[key] = m
        for t in teams_red:
            self.pre_vec[(key, t)] = _vec(red_pre)
            if upd:
                self.attrib.setdefault(key, {})[t] = _vec(red_attr)
                self.updated.add((key, t))
        for t in teams_blue:                      # blue teams: pre 20, attr 20 (residual 0)
            self.pre_vec[(key, t)] = _vec(20.0)
            if upd:
                self.attrib.setdefault(key, {})[t] = _vec(20.0)
                self.updated.add((key, t))
        self.alliance_pred[key] = (_vec(70.0), _vec(blue_pred))
        return key

    def run(self):
        return NS(model_id="epa", year=2025, objs=(None, {}, {}, {}, self.matches, {}), pre_vec=self.pre_vec,
                  attrib=self.attrib, updated=self.updated, alliance_pred=self.alliance_pred, final_vec={})


def test_consistency_known_sd_and_null_rule():
    s = Season()
    resid = [1.0, -1.0, 2.0, -2.0, 0.0]  # sample sd (ddof 1) of these = sqrt(10/4)
    for r in resid:
        s.add("e1", 30.0, 30.0 + r)
    f = F.compute(s.run(), {})
    ty = f.team_year[1]
    assert ty["cons_n"] == 5
    assert ty["cons_sd"] == pytest.approx(math.sqrt(10 / 4))
    # < 4 matches: sd null, n still reported
    s2 = Season()
    for r in resid[:3]:
        s2.add("e1", 30.0, 30.0 + r)
    ty2 = F.compute(s2.run(), {}).team_year[1]
    assert ty2["cons_sd"] is None and ty2["cons_n"] == 3


def test_elim_and_not_updated_matches_do_not_count():
    s = Season()
    for r in (1.0, -1.0, 2.0, -2.0):
        s.add("e1", 30.0, 30.0 + r)
    s.add("e1", 30.0, 90.0, elim=True)           # elim: ignored
    s.add("e1", 30.0, 90.0, upd=False)           # not updated: ignored
    f = F.compute(s.run(), {})
    assert f.team_year[1]["cons_n"] == 4
    assert f.n_resid == 4 * 6  # six teams per qual match; the elim and not-updated rows add nothing
    assert f.sigma == pytest.approx(np.std([1, -1, 2, -2] * 3 + [0] * 12, ddof=1))


def test_change_z_flag_and_baseline_is_fixed_pre():
    s = Season()
    # background season noise so sigma is known: team 7 vs 8 elsewhere is not needed; use team 1-6 residuals
    # event e1: pre 30 constant, attr = 30 + 6 for the first 3 matches (pre then would move; here it is held
    # fixed in the fake, but the detector must use the event-entry pre = 30, not the moving one)
    for i in range(3):
        s.add("e1", 30.0 + 2 * i, 36.0)            # moving pre: 30, 32, 34 -> baseline must be 30
    for r in (-3, 3, -3, 3, -3, 3, -3, 3):
        s.add("e1", 36.0, 36.0 + r)
    f = F.compute(s.run(), {})
    te = f.team_event[(1, "e1")]
    assert te["change_k"] == 3
    assert te["change_mean_resid"] == pytest.approx(36.0 - 30.0)
    expected_z = 6.0 / (f.sigma / math.sqrt(3))
    assert te["change_z"] == pytest.approx(expected_z)
    assert te["change_flag"] == (1 if expected_z > 2 else 0)
    assert f.team_year[1]["change_event"] == "e1" and f.team_year[1]["change_flag"] == te["change_flag"]


def test_flag_signs_and_threshold_edges():
    for shift, want in ((+9.0, 1), (-9.0, -1), (0.0, 0)):
        s = Season()
        for r in (-1, 1, -1, 1, -1, 1):                # sigma ~ 1.1 from the noise matches
            s.add("e0", 30.0, 30.0 + r)
        for _ in range(3):
            s.add("e1", 30.0, 30.0 + shift)
        f = F.compute(s.run(), {})
        assert f.team_event[(1, "e1")]["change_flag"] == want, shift
    # exact threshold is not flagged (strictly greater): z == 2 -> 0
    s = Season()
    for r in (-1, 1, -1, 1, -1, 1):
        s.add("e0", 30.0, 30.0 + r)
    for _ in range(2):
        s.add("e1", 30.0, 30.0)
    f0 = F.compute(s.run(), {})
    sg = f0.sigma
    s = Season()
    for r in (-1, 1, -1, 1, -1, 1):
        s.add("e0", 30.0, 30.0 + r)
    shift = F.CONFIG["z_threshold"] * sg / math.sqrt(2) * (1 - 1e-9)
    for _ in range(2):
        s.add("e1", 30.0, 30.0 + shift)
    assert F.compute(s.run(), {}).team_event[(1, "e1")]["change_flag"] == 0


def test_change_null_when_fewer_than_two_matches_and_k_caps_at_three():
    s = Season()
    for r in (-1, 1, -1, 1):
        s.add("e0", 30.0, 30.0 + r)
    s.add("e1", 30.0, 40.0)                              # one qual at e1: k = 1 -> no verdict
    f = F.compute(s.run(), {})
    te = f.team_event[(1, "e1")]
    assert te["change_k"] == 1 and te["change_flag"] is None and te["change_z"] is None
    assert te["change_mean_resid"] is None
    # a flag of 0 is still a verdict: team-year change_event is the latest event with a verdict
    assert f.team_year[1]["change_event"] == "e0"
    s = Season()
    for r in (10, 10, 10, 0, 0, 0, 0, 0):                # only the first 3 count
        s.add("e1", 30.0, 30.0 + r)
    assert F.compute(s.run(), {}).team_event[(1, "e1")]["change_k"] == 3


def test_team_year_uses_latest_event_with_flag():
    s = Season()
    for r in (-1, 1, -1, 1, -1, 1):
        s.add("e0", 30.0, 30.0 + r)
    for _ in range(3):
        s.add("e1", 30.0, 45.0)      # up
    for _ in range(3):
        s.add("e2", 45.0, 45.0)      # stable at the new level
    f = F.compute(s.run(), {})
    assert f.team_event[(1, "e1")]["change_flag"] == 1
    assert f.team_event[(1, "e2")]["change_flag"] == 0
    assert f.team_year[1]["change_event"] == "e2" and f.team_year[1]["change_flag"] == 0


def test_opponent_effect_values_and_null_rule():
    s = Season()
    for i in range(11):
        s.add("e1", 30.0, 30.0, blue_actual=50.0, blue_pred=60.0)   # opponents scored 10 less
    f = F.compute(s.run(), {})
    assert f.team_year[1]["opp_effect"] is None and f.team_year[1]["opp_n"] is None  # n = 11 < 12
    s.add("e1", 30.0, 30.0, blue_actual=50.0, blue_pred=60.0)
    ty = F.compute(s.run(), {}).team_year[1]
    assert ty["opp_n"] == 12 and ty["opp_effect"] == pytest.approx(-10.0) and ty["opp_effect_se"] == pytest.approx(0.0)
    # varying o_j: mean and SE (ddof 1)
    s = Season()
    os_ = [-12, -8, -10, -6, -14, -10, -9, -11, -10, -10, -7, -13]
    for o in os_:
        s.add("e1", 30.0, 30.0, blue_actual=60.0 + o, blue_pred=60.0)
    ty = F.compute(s.run(), {}).team_year[1]
    assert ty["opp_effect"] == pytest.approx(np.mean(os_))
    assert ty["opp_effect_se"] == pytest.approx(np.std(os_, ddof=1) / math.sqrt(12))
    # the blue team sees red as its opponent: red actual 70 vs predicted 70 -> 0
    s4 = F.compute(s.run(), {}).team_year[4]
    assert s4["opp_effect"] == pytest.approx(0.0)


def test_opponent_effect_skips_missing_actuals():
    s = Season()
    for i in range(12):
        s.add("e1", 30.0, 30.0, blue_actual=50.0, blue_pred=60.0)
    k = s.add("e1", 30.0, 30.0, blue_actual=None, blue_pred=60.0)
    ty = F.compute(s.run(), {}).team_year[1]
    assert ty["opp_n"] == 12 and ty["cons_n"] == 13


def test_sigma_is_pooled_over_all_teams_and_matches():
    s = Season()
    for r in (-2, 2, -2, 2):
        s.add("e1", 30.0, 30.0 + r)
    f = F.compute(s.run(), {})
    assert f.n_resid == 24
    assert f.sigma == pytest.approx(np.std([-2, 2, -2, 2] * 3 + [0] * 12, ddof=1))


def test_exported_field_sets():
    s = Season()
    for r in (-1, 1, -1, 1):
        s.add("e1", 30.0, 30.0 + r)
    f = F.compute(s.run(), {})
    assert set(f.team_year[1]) == {"cons_sd", "cons_n", "change_flag", "change_event", "change_z",
                                   "opp_effect", "opp_effect_se", "opp_n", "epa_se", "match_sd"}
    assert set(f.team_event[(1, "e1")]) == {"change_k", "change_mean_resid", "change_z", "change_flag"}
    assert F.CONFIG["z_threshold"] == 3.0


# ---------------------------------------------------------------------------------------------
# integration: real mini season, both models, shards accept the output
# ---------------------------------------------------------------------------------------------
FIXTURE = os.path.join(PIPELINE, "tests", "fixtures", "tba_2023_mini.json.gz")
ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")


@pytest.fixture(scope="module")
def mini_runs():
    from sb import backtest as bt
    from sb.fast_epa import robot_facts
    from sb.loader import read_raw
    from sb.model_runner import load_base, run_model

    raw = read_raw(FIXTURE)
    base = load_base(2023, raw)
    priors = bt.archive_priors(2023, ARCHIVE, {})
    return {"epa": run_model(2023, base, priors, "epa"),
            "fast": run_model(2023, base, priors, "fast", robot_facts=robot_facts(2023, raw))}


def test_integration_on_real_runs(mini_runs):
    for mid, run in mini_runs.items():
        f = F.compute(run, run.objs[2], F.CONFIG)
        assert f.sigma and 5 < f.sigma < 30
        assert f.team_year, mid
        # recompute one team's consistency independently from the recorded vectors
        t = next(iter(f.team_year))
        rs = []
        for m in sorted(run.objs[4].values(), key=lambda x: x.time):
            if not m.elim and (m.key, t) in run.updated:
                rs.append(run.attrib[m.key][t][0] - run.pre_vec[(m.key, t)][0])
        assert f.team_year[t]["cons_n"] == len(rs)
        if len(rs) >= 4:
            assert f.team_year[t]["cons_sd"] == pytest.approx(np.std(rs, ddof=1))
        # change fields obey the null rules
        for (tt, ev), rec in f.team_event.items():
            assert (rec["change_flag"] is None) == (rec["change_k"] < 2 or rec["change_z"] is None)
            if rec["change_flag"] is not None:
                z = rec["change_z"]
                thr = F.CONFIG["z_threshold"]
                assert rec["change_flag"] == (1 if z > thr else -1 if z < -thr else 0)
        d = F.diagnostics(run, f, draws=5)
        assert d["team_events_with_verdict"] > 0


def test_shards_accept_features(mini_runs, tmp_path):
    from sb import backtest as bt
    from sb import shards

    feats = {m: F.compute(r, r.objs[2], F.CONFIG) for m, r in mini_runs.items()}
    bts = {m: bt.score_season(r, None) for m, r in mini_runs.items()}
    meta = {"generated_at": "2026-11-16T12:00:00Z", "data_version": "20261116T1200Z-0000000", "current_season": 2026}
    from sb.loader import read_raw
    files = shards.write_season(2023, mini_runs, feats, bts, None, read_raw(FIXTURE), str(tmp_path), meta)
    import gzip, json
    doc = json.loads(gzip.open(tmp_path / "2023" / "season.json.gz").read())
    col = doc["teams"]["models"]["epa"]["cons_sd"]
    assert any(v is not None for v in col)


def test_range_se_shrinks_and_caps():
    cfg = F.CONFIG
    msd0, se0 = F.range_se([], 10.0, cfg, "epa")
    assert msd0 == pytest.approx(10.0)
    assert se0 == pytest.approx(1.80 * 10.0 / math.sqrt(5))
    # many matches with a small spread: match_sd pulled from 10 toward ~1, epa_se capped at nmax = 6
    rs = [(-1) ** i for i in range(40)]
    msd, se = F.range_se(rs, 10.0, cfg, "epa")
    assert 1.0 < msd < 10.0
    assert se == pytest.approx(1.80 * msd / math.sqrt(6))
    assert F.range_se(rs, 10.0, cfg, "fast")[1] == pytest.approx(1.71 * msd / math.sqrt(6))
    assert F.range_se(rs, None, cfg, "epa") == (None, None)

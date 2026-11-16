"""WP3b: Fast EPA (SPEC 4.3, 4.4)."""
import filecmp
import json
import os
import sys
import tempfile

import numpy as np
import pytest

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PIPELINE)

import sb.model_runner as mr  # noqa: E402
from sb import backtest as bt  # noqa: E402
from sb.export import export_year  # noqa: E402
from sb.fast_epa import (  # noqa: E402
    EPA_EQUIVALENT,
    CONFIG_PATH,
    FastEPA,
    FastEPACore,
    build_robot_facts,
    load_config,
    robot_facts,
    robot_slot_map,
    settings_of,
)
from sb.loader import read_raw  # noqa: E402
from sb.model_runner import load_base, run_model  # noqa: E402

from src.models.epa.main import EPA  # noqa: E402

FIXTURE = os.path.join(PIPELINE, "tests", "fixtures", "tba_2023_mini.json.gz")
ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
RAW_DIR = os.environ.get("PITWALL_RAW_DIR", "/mnt/user-data/uploads/IDM/sbrebuild/raw")

ROBOT_PB = {"settings": {"robot_attribution": True, "robot_weight": 1.0,
                         "residual_split": "share",
                         "percent": {"first_n": 3, "first": 0.6, "start": 0.4, "end": 0.2,
                                     "ramp_start": 3, "ramp_len": 6},
                         "passback": {"enabled": True, "percent_scale": 0.5, "scope": "quals"}}}


@pytest.fixture(scope="module")
def mini():
    raw = read_raw(FIXTURE)
    base = load_base(2023, raw)
    priors = bt.archive_priors(2023, ARCHIVE, {})
    return raw, base, priors, robot_facts(2023, raw)


def _exports_equal(a_objs, b_objs, year):
    a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
    export_year(a_objs, a)
    export_year(b_objs, b)
    return [f for f in sorted(os.listdir(os.path.join(a, str(year))))
            if not filecmp.cmp(os.path.join(a, str(year), f), os.path.join(b, str(year), f),
                               shallow=False)]


def test_frozen_config_loads_and_has_tuning_record():
    cfg = load_config()
    assert cfg["id"] == "fast"
    s = settings_of(cfg)
    assert set(s) == set(EPA_EQUIVALENT)
    t = cfg["tuning"]
    assert 2026 not in t["tuned_on"]
    assert t["search"]["configs_tried"] <= 60
    assert len(t["top"]) <= 20 and t["chosen"]["config_hash"]


def test_percent_schedule():
    m = FastEPACore({"settings": {}}, None)
    m.year_num = 2023
    for x in range(0, 30):
        assert m.percent(x) == EPA.percent_func(2023, x)
    f = FastEPACore({"settings": {"percent": {"first_n": 3, "first": 0.6, "start": 0.4,
                                              "end": 0.2, "ramp_start": 3, "ramp_len": 6}}}, None)
    f.year_num = 2023
    assert [round(f.percent(x), 6) for x in (0, 2, 3, 6, 9, 20)] == [0.6, 0.6, 0.4, 0.3, 0.2, 0.2]


def test_epa_equivalent_is_byte_identical_on_fixture(mini):
    raw, base, priors, facts = mini
    e = run_model(2023, base, priors, "epa")
    f = run_model(2023, base, priors, "fast", {"settings": dict(EPA_EQUIVALENT)}, facts)
    assert _exports_equal(e.objs, f.objs, 2023) == []
    assert e.win_prob == f.win_prob


@pytest.mark.skipif(not os.path.exists(os.path.join(RAW_DIR, "tba_2023.json.gz")),
                    reason="full 2023 dump not available")
def test_epa_equivalent_is_byte_identical_2023():
    """SPEC 4.3 acceptance on the full 2023 season."""
    priors = bt.archive_priors(2023, ARCHIVE, {})
    base = load_base(2023, os.path.join(RAW_DIR, "tba_2023.json.gz"))
    e = run_model(2023, base, priors, "epa")
    f = run_model(2023, base, priors, "fast", {"settings": dict(EPA_EQUIVALENT)}, None)
    assert _exports_equal(e.objs, f.objs, 2023) == []


def test_epa_equivalent_settings_json_roundtrip(mini):
    """1/3 survives JSON, so a config file can hold the EPA-equivalent point exactly."""
    raw, base, priors, facts = mini
    s = json.loads(json.dumps(EPA_EQUIVALENT))
    e = run_model(2023, base, priors, "epa")
    f = run_model(2023, base, priors, "fast", {"settings": s}, facts)
    assert e.win_prob == f.win_prob


class _Spy(FastEPA):
    """Records the ratings each team had when attributed (test only)."""

    def __init__(self, config, facts):
        super().__init__(config, facts)
        self.spy = {}

    def attribute_match(self, match, red_pred, blue_pred):
        if not self._in_pb:
            teams = match.get_red()[:3] + match.get_blue()[:3]
            self.spy[match.key] = ({t: self.epas[t].mean.copy() for t in teams},
                                   match.get_breakdowns(),
                                   (red_pred.breakdown.copy(), blue_pred.breakdown.copy()))
        return super().attribute_match(match, red_pred, blue_pred)


def test_robot_attribution_conserves_alliance_totals(mini, monkeypatch):
    raw, base, priors, facts = mini
    holder = {}

    def make(model_id, config=None, robot_facts=None):
        holder["m"] = _Spy(config, robot_facts)
        return holder["m"]

    monkeypatch.setattr(mr, "make_model", make)
    cfg = {"settings": {**ROBOT_PB["settings"], "passback": {"enabled": False}}}
    run = run_model(2023, base, priors, "fast", cfg, facts, stages="calc")
    spy = holder["m"].spy
    slots, agg = robot_slot_map(facts)
    assert slots == [8, 17]
    n_robot = 0
    for key, (mus, (rbd, bbd), (rp, bp)) in spy.items():
        pair = facts.by_match.get(key)
        m = run.objs[4][key]
        if m.status != "Completed" or pair is None:
            continue
        for side, teams, bd, pred, af in ((0, m.get_red()[:3], rbd, rp, pair[0]),
                                          (1, m.get_blue()[:3], bbd, bp, pair[1])):
            if af is None or not af.ok or len(teams) != 3:
                continue
            if any(abs(np.nansum(af.points[:, c]) - bd[c]) > 1e-6 for c in slots):
                continue
            E = bd - pred
            errs = [run.attrib[key][t] - mus[t] for t in teams]
            for K in (0, 1, 2, 3):
                assert abs(sum(e[K] for e in errs) - E[K]) < 1e-6
            for i, t in enumerate(teams):
                for c in slots:  # robot slot target = the robot's own points
                    assert abs(run.attrib[key][t][c] - af.points[i, c]) < 1e-6
            n_robot += 1
    assert n_robot > 300


def test_robot_attribution_off_without_facts(mini):
    raw, base, priors, facts = mini
    a = run_model(2023, base, priors, "fast", {"settings": {"robot_attribution": True}}, None)
    b = run_model(2023, base, priors, "epa")
    assert a.win_prob == b.win_prob


def test_fallback_facts_match_perrobot_shape(mini):
    raw, base, priors, facts = mini
    mine = build_robot_facts(2023, raw)
    assert robot_slot_map(mine) == robot_slot_map(facts)
    for k, pair in mine.by_match.items():
        other = facts.by_match[k]
        for a, b in zip(pair, other):
            assert (a is None) == (b is None)
            if a is not None:
                assert a.ok == b.ok
                assert np.allclose(np.nan_to_num(a.points[:, [8, 17]], nan=-1),
                                   np.nan_to_num(b.points[:, [8, 17]], nan=-1))


def test_passback_runs_and_changes_ratings(mini):
    raw, base, priors, facts = mini
    a = run_model(2023, base, priors, "fast", ROBOT_PB, facts)
    off = {"settings": {**ROBOT_PB["settings"], "passback": {"enabled": False}}}
    b = run_model(2023, base, priors, "fast", off, facts)
    arc = [k for k in a.win_prob if k.startswith("2023arc")]
    inmis = [k for k in a.win_prob if k.startswith("2023inmis")]
    # first event identical (no passback possible yet), later event differs
    assert all(a.win_prob[k] == b.win_prob[k] for k in inmis)
    assert any(a.win_prob[k] != b.win_prob[k] for k in arc)


def test_pieces_seed_falls_back_to_norm_epa_without_computed_prior(mini):
    raw, base, priors, facts = mini
    a = run_model(2023, base, priors, "fast", {"settings": {"seed": "pieces"}}, facts)
    b = run_model(2023, base, priors, "epa")
    assert a.win_prob == b.win_prob  # archive priors carry no comp ratings


def test_win_k_changes_only_win_prob(mini):
    raw, base, priors, facts = mini
    a = run_model(2023, base, priors, "fast", {"settings": {"win_k": -0.7}}, facts)
    b = run_model(2023, base, priors, "epa")
    assert a.k == -0.7
    ka = list(a.win_prob)
    assert [a.objs[4][k].epa_red_score_pred for k in ka] == [b.objs[4][k].epa_red_score_pred for k in ka]
    assert any(a.win_prob[k] != b.win_prob[k] for k in ka)

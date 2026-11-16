"""WP3b: model runner (SPEC 4.1, 4.2).

Fixture tests use pipeline/tests/fixtures/tba_2023_mini.json.gz (WP1).  Full-season tests need
the raw dumps (PITWALL_RAW_DIR, default /mnt/user-data/uploads/IDM/sbrebuild/raw) and are
skipped when they are absent (CI).
"""
import filecmp
import os
import sys
import tempfile

import numpy as np
import pytest

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PIPELINE)

from sb import backtest as bt  # noqa: E402
from sb.engine import run_year  # noqa: E402
from sb.export import export_year  # noqa: E402
from sb.loader import read_raw  # noqa: E402
from sb.model_runner import config_hash, load_base, run_model  # noqa: E402

FIXTURE = os.path.join(PIPELINE, "tests", "fixtures", "tba_2023_mini.json.gz")
ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
RAW_DIR = os.environ.get("PITWALL_RAW_DIR", "/mnt/user-data/uploads/IDM/sbrebuild/raw")
FULL_ARCHIVE = os.environ.get("PITWALL_ARCHIVE_DIR",
                              "/mnt/user-data/uploads/Downloads/statbotics_v3_csvs")


def raw_path(y):
    return os.path.join(RAW_DIR, f"tba_{y}.json.gz")


def _same_exports(objs_a, objs_b, year):
    a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
    export_year(objs_a, a)
    export_year(objs_b, b)
    files = sorted(os.listdir(os.path.join(a, str(year))))
    diff = [f for f in files
            if not filecmp.cmp(os.path.join(a, str(year), f), os.path.join(b, str(year), f),
                               shallow=False)]
    return files, diff


@pytest.fixture(scope="module")
def mini():
    raw = read_raw(FIXTURE)
    base = load_base(2023, raw)
    priors = bt.archive_priors(2023, ARCHIVE, {})
    return raw, base, priors


def test_recording_epa_matches_engine_on_fixture(mini, tmp_path):
    raw, base, priors = mini
    run = run_model(2023, base, priors, "epa")
    gz = tmp_path / "mini.json.gz"
    gz.write_bytes(open(FIXTURE, "rb").read())
    ref = run_year(2023, str(gz), priors)
    files, diff = _same_exports(run.objs, ref, 2023)
    assert len(files) == 8 and diff == []
    assert run.config_hash == "" and run.model_id == "epa"


def test_recordings_complete(mini):
    raw, base, priors = mini
    run = run_model(2023, base, priors, "epa")
    matches = run.objs[4]
    assert set(run.alliance_pred) == set(matches)
    for k, (r, b) in run.alliance_pred.items():
        assert r.shape == (18,) and b.shape == (18,)
        m = matches[k]
        assert abs(run.win_prob[k] - m.epa_win_prob) < 6e-5
    for k, t in run.updated:
        assert k in run.attrib and t in run.attrib[k]
    # post vector of an updated team equals its rounded rating, attribution moved it
    some = sorted(run.updated)[0]
    assert some[1] in run.attrib[some[0]]


def test_base_objs_not_mutated(mini):
    raw, base, priors = mini
    before = [m.epa_win_prob for m in base[4].values()]
    run_model(2023, base, priors, "epa")
    assert before == [m.epa_win_prob for m in base[4].values()]
    assert all(v is None for v in before)


def test_calc_and_full_give_same_predictions(mini):
    raw, base, priors = mini
    a = run_model(2023, base, priors, "epa", stages="full")
    b = run_model(2023, base, priors, "epa", stages="calc")
    assert a.win_prob == b.win_prob


def test_config_hash_canonical():
    a = config_hash({"settings": {"b": 1, "a": {"y": 2, "x": 1}}})
    b = config_hash({"settings": {"a": {"x": 1, "y": 2}, "b": 1}})
    assert a == b and len(a) == 64
    assert config_hash(None) == ""


@pytest.mark.skipif(not os.path.exists(raw_path(2023)), reason="full 2023 dump not available")
@pytest.mark.parametrize("year", [2023, 2024, 2025])
def test_recording_epa_byte_identical_full_season(year):
    """SPEC 4.2 acceptance: RecordingEPA export == engine.run_year export (2023-2025)."""
    if not os.path.exists(raw_path(year)):
        pytest.skip("dump missing")
    priors = bt.archive_priors(year, ARCHIVE, {})
    base = load_base(year, raw_path(year))
    run = run_model(year, base, priors, "epa")
    ref = run_year(year, raw_path(year), priors)
    files, diff = _same_exports(run.objs, ref, year)
    assert diff == []


@pytest.mark.skipif(not (os.path.exists(raw_path(2023))
                         and os.path.exists(os.path.join(FULL_ARCHIVE, "team_years.csv"))),
                    reason="2023 dump or Statbotics archive not available")
def test_2023_replay_matches_statbotics_archive():
    """Validated number: 2023 seeded from archive 2020/2022 -> auto_epa MAE vs archive ~0.003."""
    import csv

    priors = bt.archive_priors(2023, ARCHIVE, {})
    run = run_model(2023, load_base(2023, raw_path(2023)), priors, "epa")
    ours = {ty.team: float(ty.auto_epa) for ty in run.objs[1].values()}
    errs = []
    with open(os.path.join(FULL_ARCHIVE, "team_years.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["year"] != "2023" or row["offseason"] != "f" or not row["team"].isdigit():
                continue
            t = int(row["team"])
            if t in ours and row["auto_epa"] not in ("", "NULL"):
                errs.append(abs(ours[t] - float(row["auto_epa"])))
    assert len(errs) > 3000
    mae = float(np.mean(errs))
    assert mae < 0.005, mae


def test_unrounded_vectors(mini):
    raw, base, priors = mini
    run = run_model(2023, base, priors, "epa")
    matches = run.objs[4]
    n = 0
    for (k, t), v in run.pre_vec.items():
        assert v.shape == (18,)
        pre = matches[k].pre_epas[str(t)]
        assert abs(round(float(v[0]), 2) - pre["epa"]) < 1e-9
        assert abs(round(float(v[1]), 2) - pre["auto_epa"]) < 1e-9
        n += 1
    assert n == sum(len(m.pre_epas) for m in matches.values())
    for (k, t), v in run.post_vec.items():
        assert abs(round(float(v[0]), 2) - matches[k].epas[str(t)]["epa"]) < 1e-9
    assert {(k, t) for (k, t) in run.post_vec} >= run.updated
    tys = {ty.team: ty for ty in run.objs[1].values()}
    assert set(tys) <= set(run.final_vec)
    for t, ty in tys.items():
        assert abs(round(float(run.final_vec[t][0]), 2) - float(ty.epa)) < 1e-9

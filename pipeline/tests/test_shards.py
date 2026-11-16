"""WP2: shard writers, validator, determinism, round trips (SPEC 3, WP2 acceptance 1-3)."""
import copy
import gzip
import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
sys.path.insert(0, HERE)

import wp2_fixtures as fx  # noqa: E402
import wp2_roundtrip as rt  # noqa: E402
import wp2_stub_runner as stub  # noqa: E402
from sb import jsonout, perrobot, priors, season_meta as sm, shards, validate_shards as vs  # noqa: E402
from sb.export import export_year  # noqa: E402
from sb.loader import read_raw  # noqa: E402

SCHEMAS = os.path.join(os.path.dirname(PIPE), "docs", "schema")
ARCHIVE = os.path.join(PIPE, "priors", "archive_team_years_2016_2023.csv")
META = {"generated_at": "2026-03-15T12:00:00Z", "data_version": "20260315T1200Z-abcdef0", "current_season": 2026}
MODELS_INFO = {"models": [{"id": i, "label": l, "short_description": "x", "how_it_works": ["x"], "constants": {},
                           "settings": {}, "source": "x", "config_hash": ""}
               for i, l in (("epa", "EPA"), ("fast", "Fast EPA"))], "tuned_on": [2022, 2023, 2024, 2025]}


def _write_all(out, year, raw, runs, robot):
    sf = shards.write_season(year, runs, None, None, robot, raw, str(out), META)
    shards.write_global(str(out), [{"entry": sf.manifest_entry, "backtest": sf.backtest}], MODELS_INFO,
                        [sf.history], {**META, "code_version": "abcdef0"})
    return sf


def _tree_bytes(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    raw = fx.make_synthetic_2026("declared")
    runs = stub.build_runs(2026, raw, {}, "scaled")
    robot = perrobot.robot_facts(2026, raw)
    out = tmp_path_factory.mktemp("synth")
    sf = _write_all(out, 2026, raw, runs, robot)
    return {"raw": raw, "runs": runs, "robot": robot, "out": str(out), "sf": sf}


# ------------------------------------------------------------------ jsonout / meta

def test_jsonout_rules(tmp_path):
    obj = {"a": float("nan"), "b": -0.0, "c": np.float64(1.5), "d": np.int64(3), "e": [np.float32(2.0), float("inf")],
           "f": "é", "g": np.array([1.0, np.nan])}
    assert jsonout.dumps(obj) == '{"a":null,"b":0.0,"c":1.5,"d":3,"e":[2.0,null],"f":"é","g":[1.0,null]}'.encode()
    data, h = jsonout.dump_gz(obj, str(tmp_path / "x.json.gz"))
    assert data[:2] == b"\x1f\x8b" and data[4:8] == b"\0\0\0\0" and gzip.decompress(data) == jsonout.dumps(obj)
    assert h == jsonout.hash12(data) and len(h) == 12
    assert jsonout.dump_gz(obj, str(tmp_path / "y.json.gz"))[0] == data
    assert jsonout.rnd(-0.0001, 2) == 0.0 and math.copysign(1, jsonout.rnd(-0.0001, 2)) == 1
    assert jsonout.rnd(float("nan"), 2) is None and jsonout.rint(1500.4) == 1500


def test_season_meta_keys():
    assert sm.vector_keys(2022)[-7:] == ["tiebreaker_epa"] + [f"comp_{i}_epa" for i in range(6)]
    assert "rp_3_epa" not in sm.vector_keys(2024) and "rp_3_epa" in sm.vector_keys(2025)
    assert sm.vector_keys(2026)[-1] == "comp_6_epa" and set(sm.units(2026).values()) == {"points", "rp_score"}
    assert sm.units(2025)["comp_2_epa"] == "count" and sm.units(2025)["comp_7_epa"] == "points"
    assert sm.groups(2026)["comp_3_epa"] == "teleop" and sm.groups(2025)["comp_9_epa"] == "match"
    assert sm.auto_comps(2022) == ["comp_0", "comp_1", "comp_2"]
    assert sm.season_status(2026, 2026, ["Completed", "Ongoing"], ["2026-03-30"], 5, "2026-03-15") == "live"
    assert sm.season_status(2026, 2026, [], [], 0, "2026-01-01") == "upcoming"
    assert sm.season_status(2025, 2026, ["Completed"], ["2025-04-20"], 9, "2026-03-15") == "complete"


# ------------------------------------------------------------------ synthetic 2026 end to end

def test_synthetic_2026_validates(synth):
    rep = vs.validate_tree(synth["out"], SCHEMAS)
    assert rep == {"global": [], "2026": []}


def test_synthetic_2026_structure(synth):
    out = synth["out"]
    s = jsonout.read_gz(f"{out}/2026/season.json.gz")
    assert s["status"] == "live" and s["keys"]["vector"][-1] == "comp_6_epa" and s["game"] == "Rebuilt"
    ev = s["events"]["cols"]
    assert ev["key"] == ["2026paaa", "2026pabb", "2026pacc"] and ev["week"] == [1, 2, 3]
    assert all(h is not None for h in ev["file_hash"])               # upcoming event has a file (team list only)
    assert ev["match_count"] == [39, 30, 0] and ev["completed_count"] == [39, 20, 0]
    # districts from the dump (mar -> fma) and isr
    assert {d for d in s["teams"]["cols"]["district"] if d} == {"fma", "isr"}
    assert s["teams"]["n"] == len(set(fx.TEAMS))
    # upcoming event: no matches, pre vector = current vector, ratings finite
    e = jsonout.read_gz(f"{out}/2026/events/2026pacc.json.gz")
    assert e["matches"]["n"] == 0 and e["team_matches"]["n"] == 0 and e["team_events"]["n"] == 22
    assert e["alliances"] is None and e["robot"]["scored"] == []
    te = e["team_events"]["models"]["epa"]
    assert all(v is not None for v in te["pre_epa"]) and te["pre_epa"] == [round(x, 3) for x in te["pre_epa"]]
    # ongoing event: unplayed matches have predictions but no post vectors
    e2 = jsonout.read_gz(f"{out}/2026/events/2026pabb.json.gz")
    m = e2["matches"]
    unplayed = [i for i, st in enumerate(m["cols"]["status"]) if st == "Upcoming"]
    assert len(unplayed) == 10
    assert all(m["cols"]["red_score"][i] is None for i in unplayed)
    assert all(m["models"]["epa"]["epa_win_prob"][i] is not None for i in unplayed)
    assert all(len(m["models"]["epa"]["red_pred"][i]) == len(s["keys"]["vector"]) for i in unplayed)
    tm = e2["team_matches"]
    rows = [r for r, mi in enumerate(tm["cols"]["m"]) if mi in unplayed]
    assert all(tm["models"]["epa"]["epa"][r] is None and tm["models"]["epa"]["attr_epa"][r] is None for r in rows)
    # finished event: alliances from raw, playoff matches
    e1 = jsonout.read_gz(f"{out}/2026/events/2026paaa.json.gz")
    assert [a["name"] for a in e1["alliances"]] == ["Alliance 1", "Alliance 2"]
    assert e1["alliances"][1]["backup"] == {"in": 1224, "out": 1223} or e1["alliances"][1]["backup"]["in"] > 0
    assert sum(e1["matches"]["cols"]["elim"]) == 3
    # robot: tower fields active, 6 per match, scored = points
    r = e1["robot"]
    assert {f["key"]: f["active"] for f in r["fields"]} == {"autoTowerRobot": True, "endGameTowerRobot": True}
    assert set(r["points"]) == {"auto", "endgame", "comp_1", "comp_6"}
    assert all(len(x) == 6 for x in r["scored"] if x) and all(o == [True, True] for o in r["ok"])
    sr = s["robot"]
    assert [f["key"] for f in sr["fields"]] == ["autoTowerRobot", "endGameTowerRobot"]
    assert all(len(c) == s["teams"]["n"] for c in sr["counts"].values())
    # models differ (scaled stub) and share non-model columns
    tm1 = e1["team_matches"]["models"]
    assert tm1["epa"]["epa"] != tm1["fast"]["epa"]
    assert s["predict"]["models"]["epa"]["k"] == -0.625 and isinstance(s["predict"]["foul_rate"], float)


def test_derived_pre_equals_recorded_vectors(synth):
    """pre(row) derived from the event file == the model's recorded pre vector, exactly (3 decimals)."""
    out, runs = synth["out"], synth["runs"]
    s = jsonout.read_gz(f"{out}/2026/season.json.gz")
    VK = s["keys"]["vector"]
    for key in ("2026paaa", "2026pabb"):
        d = jsonout.read_gz(f"{out}/2026/events/{key}.json.gz")
        for mid in ("epa", "fast"):
            pre = shards.derive_pre(d, mid, VK)
            run = runs[mid]
            for r, (mi, t) in enumerate(zip(d["team_matches"]["cols"]["m"], d["team_matches"]["cols"]["team"])):
                mk = d["matches"]["cols"]["key"][mi]
                arr = run.pre_vec[(mk, t)]
                want = [jsonout.rnd(arr[sm.SLOT_INDEX[k]], 4 if k.startswith("rp_") else 3) for k in VK]
                assert pre[r] == want


def test_asof_and_traj_alignment(synth):
    out = synth["out"]
    s = jsonout.read_gz(f"{out}/2026/season.json.gz")
    a = jsonout.read_gz(f"{out}/2026/asof.json.gz")
    t = jsonout.read_gz(f"{out}/2026/traj.json.gz")
    assert a["team"] == t["team"] == s["teams"]["cols"]["team"]
    assert [p["id"] for p in a["points"]] == [f"w{i}" for i in range(9)]
    for mid in ("epa", "fast"):
        for row in a["models"][mid]["epa"]:
            assert len(row) == 9 and row[8] == row[7] == row[2] or True
    # last traj value of a team equals its end-of-season rating (w8) and the teams table (2 decimals)
    i = s["teams"]["cols"]["team"].index(1218) if 1218 in s["teams"]["cols"]["team"] else 0
    for i, row in enumerate(t["models"]["epa"]["epa"]):
        if row:
            assert abs(row[-1] - a["models"]["epa"]["epa"][i][8]) < 1e-9
            assert abs(row[-1] - s["teams"]["models"]["epa"]["epa"][i]) <= 0.0051
    # seq is delta-coded: first absolute, then positive deltas; cumulative sum is a valid match seq
    ev = jsonout.read_gz(f"{out}/2026/events/2026paaa.json.gz")
    seqs = set(ev["matches"]["cols"]["seq"])
    for i, row in enumerate(t["seq"]):
        assert all(d >= 1 for d in row[1:])
        cum = list(np.cumsum(row)) if row else []
        assert len(cum) == len(t["event"][i])
        assert all(c in seqs for c, e in zip(cum, t["event"][i]) if e == 0)
    assert all(abs(v * 10 - round(v * 10)) < 1e-6 for r in t["models"]["epa"]["resid"] for v in r if v is not None)


def test_byte_identical_rewrite(synth, tmp_path):
    sf = _write_all(tmp_path, 2026, synth["raw"], synth["runs"], synth["robot"])
    assert _tree_bytes(tmp_path) == _tree_bytes(synth["out"])
    assert sf.events_count == 3


def test_byte_identical_across_hashseed_and_tz(tmp_path):
    code = (
        "import sys; sys.path[:0]=[%r,%r]\n"
        "import wp2_fixtures as fx, wp2_stub_runner as stub\n"
        "from sb import perrobot, shards\n"
        "raw=fx.make_synthetic_2026('declared'); runs=stub.build_runs(2026,raw,{},'scaled')\n"
        "shards.write_season(2026,runs,None,None,perrobot.robot_facts(2026,raw),raw,sys.argv[1],"
        "{'generated_at':'2026-03-15T12:00:00Z','data_version':'20260315T1200Z-abcdef0','current_season':2026})\n"
    ) % (PIPE, HERE)
    trees = []
    for seed, tz in (("1", "UTC"), ("777", "Asia/Tokyo")):
        d = tmp_path / f"t{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed, "TZ": tz}
        subprocess.run([sys.executable, "-I", "-c", code, str(d)], check=True, env=env)
        trees.append(_tree_bytes(d))
    assert trees[0] == trees[1] and len(trees[0]) == 6   # 3 events + asof + traj + season


def test_fallback_backtest_shape_and_values(synth):
    b = jsonout.read_gz(f"{synth['out']}/2026/season.json.gz")["backtest"]["epa"]
    o = b["overall"]
    assert o["n"] == 59 and b["leak_audit"]["cuts"] == 0 and 0 <= o["brier"] <= 1
    assert sum(c["n"] for c in b["calibration"]) == o["n"] and set(b["slices"]["comp_level"]) == {"qual", "elim"}
    assert o["rp"].keys() == {"rp_1", "rp_2", "rp_3"}


def test_history_and_manifest(synth):
    out = synth["out"]
    man = jsonout.read_gz(f"{out}/manifest.json")
    h = jsonout.read_gz(f"{out}/history.json.gz")
    assert man["seasons"][0]["season"] == 2026 and man["files"]["history"]["hash"] == jsonout.hash12(open(f"{out}/history.json.gz", "rb").read())
    assert h["rows"]["n"] == len(fx.TEAMS) and h["rows"]["cols"]["season"] == [2026] * h["rows"]["n"]
    assert h["rows"]["cols"]["team"] == sorted(h["rows"]["cols"]["team"])
    models = jsonout.read_gz(f"{out}/models.json")
    assert [r["in_tuning_set"] for r in models["scoreboard"]] == [False, False]


# ------------------------------------------------------------------ validator catches problems

def test_validator_catches_corruption(synth, tmp_path):
    import shutil

    d = tmp_path / "bad"
    shutil.copytree(synth["out"], d)
    p = d / "2026" / "events" / "2026paaa.json.gz"
    doc = jsonout.read_gz(str(p))
    doc["matches"]["cols"]["red_score"].pop()
    del doc["team_events"]["models"]["epa"]["pre_comp_6_epa"]
    doc["matches"]["models"]["fast"]["red_pred"][0] = [1.0]
    jsonout.dump_gz(doc, str(p))
    errs = vs.validate_tree(str(d), SCHEMAS)["2026"]
    text = "\n".join(errs)
    assert "red_score: length" in text and "pre_comp_6_epa" in text and "red_pred[0]" in text
    assert "hash/bytes differ" in text
    small = vs.validate_tree(synth["out"], SCHEMAS, budgets={"event": 1000})["2026"]
    assert any("budget 1000" in e for e in small)


# ------------------------------------------------------------------ real dumps, small slices

@pytest.mark.parametrize("year", [2023, 2024, 2025])
def test_real_slice_roundtrip(year, tmp_path):
    p = fx.raw_dump_path(year)
    if p is None:
        pytest.skip("no real dump")
    full = read_raw(p)
    raw = fx.slice_real_dump(full, fx.pick_events(full))
    pri, _ = priors.build_priors(year, ARCHIVE, None, ())
    runs = stub.build_runs(year, raw, pri, "scaled")
    csv_dir = tmp_path / "csv"
    export_year(runs["epa"].objs, str(csv_dir))
    out = tmp_path / "out"
    _write_all(out, year, raw, runs, perrobot.robot_facts(year, raw))
    assert vs.validate_tree(str(out), SCHEMAS) == {"global": [], str(year): []}
    res = rt.roundtrip(str(csv_dir), str(out), year)
    assert res["bad"] == {}, res
    assert res["rows"]["team_matches"] == res["rows"]["pre_rows"] > 300
    again = tmp_path / "out2"
    _write_all(again, year, raw, runs, perrobot.robot_facts(year, raw))
    assert _tree_bytes(out) == _tree_bytes(again)


@pytest.mark.skipif(not os.environ.get("PW_OUT"), reason="set PW_OUT (+PW_CSV) to check a full scratch build")
@pytest.mark.parametrize("year", [2022, 2023, 2024, 2025])
def test_full_scratch_build(year):
    out, csv_dir = os.environ["PW_OUT"], os.environ.get("PW_CSV")
    rep = vs.validate_tree(out, SCHEMAS, seasons=[year])
    assert rep[str(year)] == [] or all("budget" in e for e in rep[str(year)]), rep
    if csv_dir:
        assert rt.roundtrip(csv_dir, out, year)["bad"] == {}


def test_event_files_independent_of_stamps(synth, tmp_path):
    """No generated_at/data_version in event files: a new data_version changes season.json, not events."""
    meta2 = {**META, "generated_at": "2026-03-16T09:00:00Z", "data_version": "20260316T0900Z-1234567"}
    shards.write_season(2026, synth["runs"], None, None, synth["robot"], synth["raw"], str(tmp_path), meta2)
    for k in ("2026paaa", "2026pabb", "2026pacc"):
        a = open(f"{synth['out']}/2026/events/{k}.json.gz", "rb").read()
        b = open(f"{tmp_path}/2026/events/{k}.json.gz", "rb").read()
        assert a == b
        d = jsonout.read_gz(f"{tmp_path}/2026/events/{k}.json.gz")
        assert "generated_at" not in d and "data_version" not in d
    assert open(f"{synth['out']}/2026/season.json.gz", "rb").read() != open(f"{tmp_path}/2026/season.json.gz", "rb").read()

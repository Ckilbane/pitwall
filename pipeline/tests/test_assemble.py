"""assemble.py: cache <-> loader dump (SPEC 2.4), WP1 acceptance 2."""
import copy
import filecmp
import gzip
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
sys.path.insert(0, PIPELINE)
sys.path.insert(0, HERE)

from fetch import assemble, cachelib  # noqa: E402
from wp1_stubs import relabel_dump  # noqa: E402

MINI = os.path.join(HERE, "fixtures", "tba_2023_mini.json.gz")
ARCHIVE = os.path.join(PIPELINE, "priors", "archive_team_years_2016_2023.csv")
RAW_DIR = os.environ.get("PITWALL_ROUNDTRIP_RAW")  # directory with tba_2022..2025.json.gz (slow, opt-in)


def same_dump(a, b, ignore_oprs=True):
    assert a["year"] == b["year"]
    assert a["fetched"] == b["fetched"]
    assert a["events"] == b["events"]  # list order included
    ea = {k: {x: y for x, y in v.items() if not (ignore_oprs and x == "oprs")} for k, v in a["event_data"].items()}
    eb = {k: {x: y for x, y in v.items() if not (ignore_oprs and x == "oprs")} for k, v in b["event_data"].items()}
    assert ea == eb
    for k in ea:  # match order inside each event
        if "matches" in ea[k]:
            assert [m["key"] for m in ea[k]["matches"]] == [m["key"] for m in eb[k]["matches"]]
    ka = sorted(json.dumps(t, sort_keys=True) for t in a.get("teams", []))
    kb = sorted(json.dumps(t, sort_keys=True) for t in b.get("teams", []))
    assert ka == kb


def test_split_and_season_dump_round_trip_mini(tmp_path):
    d = assemble.read_dump_file(MINI)
    c = assemble.split_dump(d, str(tmp_path / "raw"))
    assert c["event_data"] == 2 and c["skipped_keys"] == 0
    d2 = assemble.season_dump(str(tmp_path), 2023)  # parent dir with raw/ also works
    same_dump(d, d2)
    assert "districts" not in d2
    assert d2["fetched"] == d["fetched"]


def test_round_trip_with_districts_and_event_teams(tmp_path):
    d = relabel_dump(assemble.read_dump_file(MINI), 2026)
    d["districts"] = {"fma": [1218, "frc1219"], "pch": []}
    k = next(iter(d["event_data"]))
    d["event_data"][k]["teams"] = [1218, "frc1219", 3]
    assemble.split_dump(d, str(tmp_path / "raw"))
    d2 = assemble.season_dump(str(tmp_path / "raw"), 2026)
    assert d2["districts"] == {"fma": ["frc1218", "frc1219"], "pch": []}
    assert d2["event_data"][k]["teams"] == ["frc1218", "frc1219", "frc3"]
    assert cachelib.season_options(str(tmp_path / "raw"), 2026) == {"districts": True, "event_teams": True}


def test_season_dump_is_deterministic_and_needs_fingerprint(tmp_path):
    raw = str(tmp_path / "raw")
    assemble.split_dump(assemble.read_dump_file(MINI), raw)
    a = json.dumps(assemble.season_dump(raw, 2023), sort_keys=False)
    b = json.dumps(assemble.season_dump(raw, 2023), sort_keys=False)
    assert a == b
    os.remove(cachelib.fingerprint_path(raw, 2023))
    with pytest.raises(FileNotFoundError):
        assemble.season_dump(raw, 2023)
    with pytest.raises(FileNotFoundError):
        assemble.season_dump(raw, 2019)


def test_fingerprint_last_change_is_the_dump_fetch_time(tmp_path):
    raw = str(tmp_path / "raw")
    d = assemble.read_dump_file(MINI)
    assemble.split_dump(d, raw)
    fp = cachelib.load_fingerprint(raw, 2023)
    assert fp["last_change_at"] == d["fetched"]
    h, n = cachelib.compute_inputs_hash(raw, 2023)
    assert fp["inputs_hash"] == h and fp["endpoint_count"] == n


def test_split_refuses_keys_that_could_escape_the_cache(tmp_path):
    d = assemble.read_dump_file(MINI)
    evil = copy.deepcopy(d)
    evil["event_data"]["../../evil"] = {"matches": []}
    evil["event_data"]["2023/../x"] = {"matches": []}
    evil["event_data"]["2024other"] = {"matches": []}  # wrong season
    c = assemble.split_dump(evil, str(tmp_path / "raw"))
    assert c["skipped_keys"] == 3
    assert not os.path.exists(tmp_path / "evil") and not os.path.exists(tmp_path / "x")
    written = [os.path.relpath(os.path.join(p, f), tmp_path) for p, _, fs in os.walk(tmp_path) for f in fs]
    assert all(w.startswith("raw" + os.sep) for w in written)


def test_gz_blobs_are_deterministic_and_rewritten_only_on_change(tmp_path):
    body = b'{"a":[1,2,3]}' * 1000
    assert cachelib.gz_bytes(body) == cachelib.gz_bytes(body)
    assert cachelib.gz_bytes(body)[4:8] == b"\x00\x00\x00\x00"  # mtime 0
    p = str(tmp_path / "x.json.gz")
    assert cachelib.write_if_changed(p, cachelib.gz_bytes(body)) is True
    st = os.stat(p).st_mtime_ns
    assert cachelib.write_if_changed(p, cachelib.gz_bytes(body)) is False
    assert os.stat(p).st_mtime_ns == st


def _tree_equal(a, b):
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(_tree_equal(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)


def _run_py(raw_path, out, year):
    r = subprocess.run([sys.executable, os.path.join(PIPELINE, "run.py"), "--years", str(year),
                        "--raw-dir", os.path.dirname(raw_path), "--raw", f"{year}={raw_path}",
                        "--archive-dir", ARCHIVE, "--out", out],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]


def _roundtrip_outputs_identical(src, year, tmp_path):
    raw = str(tmp_path / "raw")
    assemble.split_dump(assemble.read_dump_file(src), raw)
    rt = str(tmp_path / f"rt_{year}.json.gz")
    assemble.write_dump_file(assemble.season_dump(raw, year), rt)
    _run_py(src, str(tmp_path / "orig"), year)
    _run_py(rt, str(tmp_path / "rtout"), year)
    a, b = str(tmp_path / "orig" / str(year)), str(tmp_path / "rtout" / str(year))
    # run_meta.json records the raw file name: compare everything else byte for byte
    for d in (a, b):
        os.remove(os.path.join(d, "run_meta.json"))
    assert _tree_equal(a, b)


def test_run_py_outputs_byte_identical_after_round_trip_mini(tmp_path):
    _roundtrip_outputs_identical(MINI, 2023, tmp_path)


@pytest.mark.skipif(not RAW_DIR, reason="set PITWALL_ROUNDTRIP_RAW=/dir/with/tba_YYYY.json.gz to run (slow)")
@pytest.mark.parametrize("year", [2022, 2023, 2024, 2025])
def test_run_py_outputs_byte_identical_after_round_trip_full(tmp_path, year):
    _roundtrip_outputs_identical(os.path.join(RAW_DIR, f"tba_{year}.json.gz"), year, tmp_path)


def test_cli_dump_and_split(tmp_path):
    raw = str(tmp_path / "raw")
    assert assemble.main(["--split", MINI, "--cache", raw]) == 0
    out = str(tmp_path / "d.json.gz")
    assert assemble.main(["--dump", "2023", "--cache", raw, "--out", out]) == 0
    same_dump(assemble.read_dump_file(MINI), assemble.read_dump_file(out))

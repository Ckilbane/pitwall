"""pipeline/tools/local_build.py on the 2023 mini fixture: real shards, validated, reports written."""
import json
import os
import shutil
import subprocess
import sys

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(PIPELINE)
FIXTURE = os.path.join(PIPELINE, "tests", "fixtures", "tba_2023_mini.json.gz")


def test_local_build_mini(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(FIXTURE, raw / "tba_2023.json.gz")
    out, rep = tmp_path / "out", tmp_path / "rep"
    r = subprocess.run([sys.executable, "-I", os.path.join(PIPELINE, "tools", "local_build.py"),
                        "--raw-dir", str(raw), "--out", str(out), "--years", "2023", "--report-dir", str(rep)],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    man = json.loads((out / "manifest.json").read_text())
    assert [s["season"] for s in man["seasons"]] == [2023]
    assert (out / "models.json").exists() and (out / "history.json.gz").exists()
    assert (rep / "features_2023_epa.json").exists() and (rep / "features_2023_fast.json").exists()
    v = subprocess.run([sys.executable, "-I", os.path.join(PIPELINE, "sb", "validate_shards.py"), str(out),
                        os.path.join(REPO, "docs", "schema")], capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr

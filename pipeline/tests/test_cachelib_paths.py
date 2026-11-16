import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fetch"))
import cachelib


def test_dump_small_json_bare_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cachelib.dump_small_json({"ok": 1}, "fetch_report.json", indent=1)
    assert (tmp_path / "fetch_report.json").exists()

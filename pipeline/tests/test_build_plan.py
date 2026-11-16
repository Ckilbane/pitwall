"""build.py plan/run/finalize and stage_site.py (SPEC 2.3 to 2.7), WP1 acceptance 1, 3, 4, 5, 6.

The orchestration tests use the real season builder (sb.shards, sb.model_runner, ...) through
wp1_stubs.DevComputer on the 2023 mini fixture (the second season is the same data relabelled to 2024).
"""
import copy
import gzip
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
REPO = os.path.dirname(PIPELINE)
sys.path.insert(0, PIPELINE)
sys.path.insert(0, HERE)

import build  # noqa: E402
from fake_tba_server import FakeTBA, load_dump  # noqa: E402
from fetch import assemble, cachelib, tba_fetch  # noqa: E402
from tools import stage_site  # noqa: E402,F401  (importable as a module too)
from wp1_stubs import relabel_dump  # noqa: E402

MINI = os.path.join(HERE, "fixtures", "tba_2023_mini.json.gz")
KEY = "FAKE-KEY-VALUE-1234-DO-NOT-LEAK"
ARCH = "pipeline/priors/archive_team_years_2016_2023.csv"

# Other work packages edit pipeline/ while these tests run in a shared checkout, and any such edit
# changes the real code hash (rightly: it forces a rebuild).  The orchestration tests therefore pin
# it; the hash function itself is tested on throwaway trees with the real implementation.
_real_compute_code_hash = build.compute_code_hash
PINNED_CODE_HASH = "c0de" * 16


@pytest.fixture(autouse=True)
def pin_code_hash(monkeypatch):
    monkeypatch.setattr(build, "compute_code_hash", lambda repo=REPO: PINNED_CODE_HASH)


# =============================================================================================
# pure rules (acceptance 6)
# =============================================================================================
def good(inputs="i", code="c", norm="n", ok=True):
    return {"inputs_hash": inputs, "code_hash": code, "norm_hash": norm, "built_at": "t", "ok": ok}


def st(**by_year):
    return {"seasons": {k.lstrip("y"): v for k, v in by_year.items()}}


ALL_PUB = {2022: True, 2023: True, 2024: True}


def test_rule_nothing_changed_nothing_rebuilds():
    state = st(y2022=good(), y2023=good(), y2024=good())
    assert build.decide_rebuilds([2022, 2023, 2024], {2022: "i", 2023: "i", 2024: "i"}, state, "c", ALL_PUB) == {}


def test_rule_input_change_rebuilds_that_season_only():
    state = st(y2022=good(), y2023=good(), y2024=good())
    got = build.decide_rebuilds([2022, 2023, 2024], {2022: "i", 2023: "CHANGED", 2024: "i"}, state, "c", ALL_PUB)
    assert got == {2023: ["inputs"]}


def test_rule_code_change_rebuilds_every_season():
    state = st(y2022=good(), y2023=good(), y2024=good())
    got = build.decide_rebuilds([2022, 2023, 2024], {2022: "i", 2023: "i", 2024: "i"}, state, "NEW", ALL_PUB)
    assert got == {2022: ["code"], 2023: ["code"], 2024: ["code"]}


def test_rule_force_all_or_listed():
    state = st(y2022=good(), y2023=good())
    args = ([2022, 2023], {2022: "i", 2023: "i"}, state, "c", {2022: True, 2023: True})
    assert build.decide_rebuilds(*args, force=True) == {2022: ["force"], 2023: ["force"]}
    assert build.decide_rebuilds(*args, force=True, force_seasons=[2023]) == {2023: ["force"]}
    assert build.decide_rebuilds(*args, force=False, force_seasons=[2023]) == {}


def test_rule_failed_last_time_retries_and_missing_shards_rebuild():
    state = st(y2023=good(ok=False), y2024=good())
    got = build.decide_rebuilds([2023, 2024, 2025], {2023: "i", 2024: "i", 2025: "x"}, state, "c",
                                {2023: True, 2024: False, 2025: False})
    assert got == {2023: ["retry"], 2024: ["unpublished"], 2025: ["inputs", "unpublished"]}


def test_rule_norm_change_propagates_to_later_seasons_only():
    todo = {2023: ["inputs"]}
    added = build.propagate_norm([2022, 2023, 2024, 2025], 2023, "old", "new", todo)
    assert added == [2024, 2025]
    assert todo == {2023: ["inputs"], 2024: ["norm:2023"], 2025: ["norm:2023"]}
    # unchanged norm hash: nothing added
    todo2 = {2023: ["inputs"]}
    assert build.propagate_norm([2022, 2023, 2024], 2023, "same", "same", todo2) == []
    assert todo2 == {2023: ["inputs"]}
    # first build (no old hash) counts as changed
    assert build.propagate_norm([2023, 2024], 2023, None, "x", {}) == [2024]
    # a later season already scheduled is not duplicated
    todo3 = {2023: ["inputs"], 2024: ["inputs"]}
    assert build.propagate_norm([2023, 2024], 2023, "a", "b", todo3) == []
    assert todo3[2024] == ["inputs", "norm:2023"]


def test_deploy_decision_truth_table():
    pub = {"data_version": "20260101T0000Z-aaaaaaa"}
    live_same = {"data_version": "20260101T0000Z-aaaaaaa"}
    live_old = {"data_version": "20251231T0000Z-bbbbbbb"}
    assert build.deploy_decision([2026], pub, live_same) == (True, "rebuild")
    assert build.deploy_decision([], pub, live_same) == (False, "none")
    assert build.deploy_decision([], pub, live_old) == (True, "live manifest differs")
    assert build.deploy_decision([], pub, None) == (True, "nothing live yet")
    assert build.deploy_decision([], None, None) == (False, "none")


def test_norm_hash_is_order_independent_and_value_sensitive(tmp_path):
    def w(name, rows):
        p = tmp_path / name
        p.write_text("year,team,norm_epa,epa\n" + "".join(f"2023,{t},{n},1.0\n" for t, n in rows))
        return str(p)

    a = build.norm_hash_of_csv(w("a.csv", [(1, 1500), (2, 1400), (3, 1600)]))
    b = build.norm_hash_of_csv(w("b.csv", [(3, 1600), (1, 1500), (2, 1400)]))
    c = build.norm_hash_of_csv(w("c.csv", [(1, 1500), (2, 1401), (3, 1600)]))
    assert a == b and a != c


def _mini_repo(tmp_path):
    r = tmp_path / "repo"
    for rel, text in {
        "pipeline/a.py": "x = 1\n", "pipeline/vendor/b.py": "y = 2\n", "pipeline/models/fast_epa.json": "{}\n",
        "pipeline/config.json": "{}\n", "docs/perrobot_fields.json": "{}\n", "docs/schema/s.json": "{}\n",
        "pipeline/tests/test_x.py": "t\n", "pipeline/__pycache__/junk.py": "j\n", "pipeline/notes.md": "m\n",
    }.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return r


def test_code_hash_covers_the_right_files(tmp_path):
    r = _mini_repo(tmp_path)
    h0 = _real_compute_code_hash(str(r))
    assert _real_compute_code_hash(str(r)) == h0
    for rel in ("pipeline/a.py", "pipeline/vendor/b.py", "pipeline/models/fast_epa.json", "pipeline/config.json",
                "docs/perrobot_fields.json", "docs/schema/s.json"):
        p = r / rel
        old = p.read_text()
        p.write_text(old + " ")
        assert _real_compute_code_hash(str(r)) != h0, rel
        p.write_text(old)
        assert _real_compute_code_hash(str(r)) == h0
    for rel in ("pipeline/tests/test_x.py", "pipeline/__pycache__/junk.py", "pipeline/notes.md"):
        (r / rel).write_text("changed")
        assert _real_compute_code_hash(str(r)) == h0, rel
    (r / "pipeline" / "new.py").write_text("z")
    assert _real_compute_code_hash(str(r)) != h0


# =============================================================================================
# plan on a real cache layout
# =============================================================================================
def seed(tmp, years=(2023, 2024)):
    """cache/raw seeded from the mini dump for each year (later years are relabelled copies)."""
    base = assemble.read_dump_file(MINI)
    cache = str(tmp / "cache")
    for y in years:
        assemble.split_dump(base if y == 2023 else relabel_dump(base, y), os.path.join(cache, "raw"))
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"seasons": list(years), "current_season": max(years), "archive_priors": ARCH,
                               "default_region": "fma", "default_watchlist": [1218, 1219], "default_model": "epa"}))
    return cache, str(cfg)


def report(tmp, **kw):
    p = tmp / "fetch_report.json"
    p.write_text(json.dumps({"mode": "daily", "error_ratio": 0.0, "requested_endpoints": 100, "aborted": None, **kw}))
    return str(p)


def plan(tmp, cache, cfg, live=None, force=False, seasons=None, rep=None, name="plan.json"):
    out = str(tmp / name)
    args = ["plan", "--cache", cache, "--config", cfg, "--fetch-report", rep or report(tmp), "--out", out]
    if live:
        args += ["--live-manifest", live]
    if force:
        args.append("--force")
    if seasons:
        args += ["--seasons", *map(str, seasons)]
    assert build.main(args) == 0
    return json.load(open(out)), out


def test_plan_first_run_rebuilds_everything_present(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    cache, cfg = seed(tmp_path)
    p, _ = plan(tmp_path, cache, cfg)
    assert p["rebuild"] == [2023, 2024] and p["deploy"] is True and p["abort"] is None
    assert p["reasons"]["2023"] == ["inputs", "unpublished"]
    assert p["seasons_present"] == [2023, 2024]
    assert p["last_change_at"]["2023"].endswith("Z") and "." not in p["last_change_at"]["2023"]  # second precision


def test_plan_aborts_when_more_than_20_percent_of_endpoints_failed(tmp_path):
    cache, cfg = seed(tmp_path)
    p, _ = plan(tmp_path, cache, cfg, rep=report(tmp_path, error_ratio=0.21, requested_endpoints=100))
    assert p["abort"] and "21%" in p["abort"]["reason"] and p["rebuild"] == [] and p["deploy"] is False
    p, _ = plan(tmp_path, cache, cfg, rep=report(tmp_path, error_ratio=0.2))
    assert p["abort"] is None and p["rebuild"] == [2023, 2024]
    p, _ = plan(tmp_path, cache, cfg, rep=report(tmp_path, aborted="auth"))
    assert p["abort"] and p["deploy"] is False


def test_plan_writes_github_outputs_and_summary(tmp_path):
    cache, cfg = seed(tmp_path)
    go, gs = tmp_path / "out.txt", tmp_path / "sum.md"
    assert build.main(["plan", "--cache", cache, "--config", cfg, "--fetch-report", report(tmp_path),
                       "--out", str(tmp_path / "p.json"), "--github-output", str(go), "--summary", str(gs)]) == 0
    kv = dict(l.split("=", 1) for l in go.read_text().splitlines())
    assert kv["has_work"] == "true" and kv["abort"] == "false" and kv["rebuild"] == "2023 2024"
    assert "rebuild" in gs.read_text()


# =============================================================================================
# orchestration with the real builders
# =============================================================================================
pytest.importorskip("sb.shards")


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("PITWALL_ALLOW_STUB", "1")
    for k in ("PITWALL_FAIL_SEASONS", "PITWALL_INVALID_SEASONS", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(k, raising=False)


COUNTER = [0]


def run(tmp, cache, cfg, plan_path, extra_env=None, monkeypatch=None, out_name="staged"):
    """build.py run with the DevComputer; returns (rc, run_result)."""
    COUNTER[0] += 1
    if monkeypatch:
        monkeypatch.setenv("PITWALL_NOW", f"2026-11-16T04:{COUNTER[0] % 60:02d}:00Z")
        for k, v in (extra_env or {}).items():
            monkeypatch.setenv(k, v)
    work = str(tmp / "work")
    staged = str(tmp / out_name)
    rc = build.main(["run", "--plan", plan_path, "--cache", cache, "--work", work, "--out-data", staged,
                     "--config", cfg, "--computer", "wp1_stubs:DevComputer"])
    return rc, json.load(open(os.path.join(work, "run_result.json"))), staged, work


def finalize(cache, staged, work):
    return build.main(["finalize", "--cache", cache, "--staged", staged, "--run-result",
                       os.path.join(work, "run_result.json")])


def digest_tree(root):
    out = {}
    for p, _, fs in os.walk(root):
        for f in fs:
            fp = os.path.join(p, f)
            out[os.path.relpath(fp, root)] = open(fp, "rb").read()
    return out


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A cache after one full build of 2023 and 2024 (published/, out/, state.json present)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("PITWALL_ALLOW_STUB", "1")
    mp.setattr(build, "compute_code_hash", lambda repo=REPO: PINNED_CODE_HASH)
    tmp = tmp_path_factory.mktemp("world")
    cache, cfg = seed(tmp)
    p, pp = plan(tmp, cache, cfg)
    rc, res, staged, work = run(tmp, cache, cfg, pp, monkeypatch=mp)
    assert rc == 0 and res["rebuilt"] == [2023, 2024] and not res["failed"], res
    assert finalize(cache, staged, work) == 0
    mp.undo()
    return {"tmp": tmp, "cache": cache, "cfg": cfg, "res": res}


def clone(world, tmp_path):
    dst = tmp_path / "cache"
    shutil.copytree(world["cache"], dst)
    return str(dst)


def edit_matches(cache, year, fn):
    """Edit one event's cached matches body (as if TBA changed) and refresh the fingerprint."""
    raw = os.path.join(cache, "raw")
    key = sorted(k for k in cachelib.iter_event_keys(raw, year))[0]
    rel = f"{year}/event/{key}/matches.json.gz"
    ok, body = cachelib.read_body(cachelib.abs_path(raw, rel))
    fn(body)
    assemble._put(raw, rel, body)
    cachelib.refresh_fingerprint(raw, year, "2026-11-16T05:00:00Z", True)


def edit_rankings(cache, year):
    raw = os.path.join(cache, "raw")
    key = sorted(k for k in cachelib.iter_event_keys(raw, year))[0]
    rel = f"{year}/event/{key}/rankings.json.gz"
    ok, body = cachelib.read_body(cachelib.abs_path(raw, rel))
    body["rankings"][0]["rank"] = 99
    assemble._put(raw, rel, body)
    cachelib.refresh_fingerprint(raw, year, "2026-11-16T05:00:00Z", True)


def test_first_build_publishes_valid_tree_with_computed_prior_chain(world):
    cache = world["cache"]
    assert sorted(os.listdir(os.path.join(cache, "published"))) == ["2023", "2024", "history.json.gz", "manifest.json", "models.json"]
    assert sorted(os.listdir(os.path.join(cache, "out"))) == ["2023", "2024"]
    state = json.load(open(os.path.join(cache, "state.json")))
    assert set(state["seasons"]) == {"2023", "2024"} and all(v["ok"] for v in state["seasons"].values())
    man = json.load(open(os.path.join(cache, "published", "manifest.json")))
    assert [s["season"] for s in man["seasons"]] == [2024, 2023]
    assert all(s["stale"] is False for s in man["seasons"])
    from sb import validate_shards as vs
    rep = vs.validate_tree(os.path.join(cache, "published"), os.path.join(REPO, "docs", "schema"))
    assert all(not e for e in rep.values()), rep


def test_no_change_plan_has_no_work_unless_live_site_differs(world, tmp_path):
    cache = clone(world, tmp_path)
    live = tmp_path / "live.json"
    shutil.copy(os.path.join(cache, "published", "manifest.json"), live)
    p, _ = plan(tmp_path, cache, world["cfg"], live=str(live))
    assert p["rebuild"] == [] and p["deploy"] is False and p["reasons"] == {}
    p, _ = plan(tmp_path, cache, world["cfg"])  # no live manifest: first deploy / site down
    assert p["rebuild"] == [] and p["deploy"] is True and p["deploy_reason"] == "nothing live yet"
    m = json.load(open(live))
    m["data_version"] = "20200101T0000Z-0000000"
    live.write_text(json.dumps(m))
    p, _ = plan(tmp_path, cache, world["cfg"], live=str(live))
    assert p["deploy"] is True and p["deploy_reason"] == "live manifest differs"


def test_redeploy_without_rebuild_copies_published_byte_for_byte(world, tmp_path, monkeypatch):
    cache = clone(world, tmp_path)
    p, pp = plan(tmp_path, cache, world["cfg"])
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch)
    assert rc == 0 and res["rebuilt"] == [] and res["deploy_ok"] and not res["degraded"]
    assert digest_tree(staged) == digest_tree(os.path.join(cache, "published"))
    assert res["data_version"] == json.load(open(os.path.join(cache, "published", "manifest.json")))["data_version"]


def test_input_change_rebuilds_only_that_season_and_others_stay_byte_identical(world, tmp_path, monkeypatch):
    cache = clone(world, tmp_path)
    before = digest_tree(os.path.join(cache, "published"))
    edit_matches(cache, 2024, lambda ms: ms[0]["alliances"]["red"].__setitem__("score", ms[0]["alliances"]["red"]["score"] + 9))
    live = tmp_path / "live.json"
    shutil.copy(os.path.join(cache, "published", "manifest.json"), live)
    p, pp = plan(tmp_path, cache, world["cfg"], live=str(live))
    assert p["rebuild"] == [2024] and p["reasons"] == {"2024": ["inputs"]} and p["deploy"] is True
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch)
    assert rc == 0 and res["rebuilt"] == [2024] and not res["degraded"]
    after = digest_tree(staged)
    same23 = {k for k in before if k.startswith("2023" + os.sep)}
    assert same23 and all(after[k] == before[k] for k in same23)   # 2023 shards untouched: same hashes, caches stay valid
    s24 = [k for k in before if k.startswith("2024" + os.sep)]
    assert any(after[k] != before[k] for k in s24)
    man = json.load(open(os.path.join(staged, "manifest.json")))
    assert man["data_version"] != json.load(open(os.path.join(cache, "published", "manifest.json")))["data_version"]
    assert finalize(cache, staged, work) == 0
    st_ = json.load(open(os.path.join(cache, "state.json")))
    assert st_["seasons"]["2024"]["inputs_hash"] == p["inputs_hash"]["2024"]


def test_changing_priors_propagates_but_a_rankings_change_does_not(world, tmp_path, monkeypatch):
    # rankings feed no rating: 2023 rebuilds, its norm hash is unchanged, 2024 is left alone
    cache = clone(world, tmp_path)
    edit_rankings(cache, 2023)
    p, pp = plan(tmp_path, cache, world["cfg"])
    assert p["rebuild"] == [2023]
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch)
    assert rc == 0 and res["rebuilt"] == [2023]
    # scores feed ratings: 2023's norm hash changes, so 2024 rebuilds too (rule 3)
    cache2 = str(tmp_path / "cache2")
    shutil.copytree(world["cache"], cache2)
    def bump(ms):
        for m in ms[:20]:
            m["alliances"]["red"]["score"] += 60
    edit_matches(cache2, 2023, bump)
    p2, pp2 = plan(tmp_path, cache2, world["cfg"], name="plan2.json")
    assert p2["rebuild"] == [2023]
    rc, res2, staged2, work2 = run(tmp_path, cache2, world["cfg"], pp2, monkeypatch=monkeypatch, out_name="staged2")
    assert rc == 0 and res2["rebuilt"] == [2023, 2024], res2
    assert finalize(cache2, staged2, work2) == 0


def test_force_rebuilds_listed_seasons(world, tmp_path):
    cache = clone(world, tmp_path)
    p, _ = plan(tmp_path, cache, world["cfg"], force=True, seasons=[2023])
    assert p["rebuild"] == [2023] and p["reasons"] == {"2023": ["force"]}
    p, _ = plan(tmp_path, cache, world["cfg"], force=True)
    assert p["rebuild"] == [2023, 2024]


def test_failure_injection_serves_last_good_with_stale_flag_and_still_deploys(world, tmp_path, monkeypatch, capsys):
    cache = clone(world, tmp_path)
    before = digest_tree(os.path.join(cache, "published"))
    edit_rankings(cache, 2023)                       # 2023 will rebuild fine
    edit_matches(cache, 2024, lambda ms: ms[0]["alliances"]["blue"].__setitem__("score", 77))
    p, pp = plan(tmp_path, cache, world["cfg"])
    assert p["rebuild"] == [2023, 2024]
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch,
                                extra_env={"PITWALL_FAIL_SEASONS": "2024"})
    assert rc == 0                                   # the job fails later, in the workflow's last step
    assert res["degraded"] is True and res["deploy_ok"] is True and res["rebuilt"] == [2023]
    assert list(res["failed"]) == ["2024"] and "injected failure" in res["failed"]["2024"]
    after = digest_tree(staged)
    s24 = [k for k in before if k.startswith("2024" + os.sep)]
    assert s24 and all(after[k] == before[k] for k in s24)           # 2024 is the last good data, byte for byte
    man = json.load(open(os.path.join(staged, "manifest.json")))
    e24 = next(s for s in man["seasons"] if s["season"] == 2024)
    e23 = next(s for s in man["seasons"] if s["season"] == 2023)
    assert e24["stale"] is True and "injected failure" in e24["stale_reason"]
    assert e23["stale"] is False
    from sb import validate_shards as vs
    rep = vs.validate_tree(staged, os.path.join(REPO, "docs", "schema"))
    assert all(not e for e in rep.values()), rep
    assert finalize(cache, staged, work) == 0
    state = json.load(open(os.path.join(cache, "state.json")))
    assert state["seasons"]["2024"]["ok"] is False and state["seasons"]["2023"]["ok"] is True
    # the next run (no failure injected) retries 2024
    p2, _ = plan(tmp_path, cache, world["cfg"], name="plan2.json")
    assert p2["rebuild"] == [2024] and "retry" in p2["reasons"]["2024"]


def test_validation_failure_is_replaced_by_last_good_and_later_seasons_do_not_use_its_priors(world, tmp_path, monkeypatch):
    cache = clone(world, tmp_path)
    edit_rankings(cache, 2023)
    edit_rankings(cache, 2024)
    p, pp = plan(tmp_path, cache, world["cfg"])
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch,
                                extra_env={"PITWALL_INVALID_SEASONS": "2023"})
    assert rc == 0 and res["rebuilt"] == [] and set(res["failed"]) == {"2023", "2024"}
    assert "validation failed" in res["failed"]["2023"] and "2023" in res["failed"]["2024"]
    pub, got = digest_tree(os.path.join(cache, "published")), digest_tree(staged)
    assert got.keys() == pub.keys()
    assert all(got[k] == pub[k] for k in pub if k[:4].isdigit())   # season shards are the last good ones
    man = json.load(open(os.path.join(staged, "manifest.json")))
    assert all(s["stale"] for s in man["seasons"])
    assert "priors come from it" in res["failed"]["2024"]


def test_first_run_with_nothing_published_and_a_failing_build_does_not_deploy(world, tmp_path, monkeypatch):
    cache, cfg = seed(tmp_path)
    p, pp = plan(tmp_path, cache, cfg)
    rc, res, staged, work = run(tmp_path, cache, cfg, pp, monkeypatch=monkeypatch,
                                extra_env={"PITWALL_FAIL_SEASONS": "2023,2024"})
    assert rc == 1 and res["deploy_ok"] is False and res["fatal"]
    assert finalize(cache, staged, work) == 1
    assert not os.path.exists(os.path.join(cache, "published")) and not os.path.exists(os.path.join(cache, "state.json"))


def test_first_run_one_season_fails_the_other_still_publishes(world, tmp_path, monkeypatch):
    cache, cfg = seed(tmp_path)
    p, pp = plan(tmp_path, cache, cfg)
    rc, res, staged, work = run(tmp_path, cache, cfg, pp, monkeypatch=monkeypatch,
                                extra_env={"PITWALL_FAIL_SEASONS": "2024"})
    assert rc == 0 and res["rebuilt"] == [2023] and res["degraded"] and res["deploy_ok"]
    man = json.load(open(os.path.join(staged, "manifest.json")))
    assert [s["season"] for s in man["seasons"]] == [2023]   # 2024 had nothing published to fall back to


def test_github_outputs_of_run(world, tmp_path, monkeypatch):
    cache = clone(world, tmp_path)
    edit_matches(cache, 2024, lambda ms: ms[0]["alliances"]["blue"].__setitem__("score", 78))
    p, pp = plan(tmp_path, cache, world["cfg"])
    go = tmp_path / "go.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(go))
    rc, res, staged, work = run(tmp_path, cache, world["cfg"], pp, monkeypatch=monkeypatch,
                                extra_env={"PITWALL_FAIL_SEASONS": "2024"})
    kv = dict(l.split("=", 1) for l in go.read_text().splitlines())
    assert rc == 0 and kv["degraded"] == "true" and kv["deploy_ok"] == "true" and kv["failed_seasons"] == "2024"
    assert kv["data_version"] == res["data_version"]


# =============================================================================================
# the data branch: one commit, stable blobs (acceptance 5)  -- runs the workflow's own commands
# =============================================================================================
def _save_script():
    import yaml

    wf = yaml.safe_load(open(os.path.join(REPO, ".github", "workflows", "update.yml")))
    step = next(s for s in wf["jobs"]["update"]["steps"] if s.get("id") == "save")
    return step["run"]


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                            "GIT_TERMINAL_PROMPT": "0"})
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def test_data_branch_is_one_commit_and_unchanged_blobs_keep_their_ids(tmp_path):
    pytest.importorskip("yaml")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    script = _save_script()
    url = "file://" + str(remote)

    def save(work, version):
        env = {**os.environ, "DEPLOY_OK": "false", "DATA_VERSION": version, "GIT_CONFIG_GLOBAL": "/dev/null",
               "GIT_CONFIG_SYSTEM": "/dev/null"}
        r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=work, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    # run 1: no data branch yet (the "Start an empty cache" case)
    w1 = tmp_path / "w1"
    (w1 / "cache" / "raw" / "2026").mkdir(parents=True)
    git(w1, "init", "-q", "cache")
    git(w1 / "cache", "remote", "add", "origin", url)
    for n, body in (("a.json.gz", b"alpha"), ("b.json.gz", b"beta")):
        (w1 / "cache" / "raw" / "2026" / n).write_bytes(body)
    (w1 / "cache" / "state.json").write_text("{}\n")
    save(w1, "v1")
    # run 2: checkout of the data branch (shallow), one body changes
    w2 = tmp_path / "w2"
    w2.mkdir()
    git(w2, "clone", "-q", "--depth", "1", "--branch", "data", url, "cache")
    (w2 / "cache" / "raw" / "2026" / "b.json.gz").write_bytes(b"beta CHANGED")
    save(w2, "v2")
    # run 3: nothing changed at all
    w3 = tmp_path / "w3"
    w3.mkdir()
    git(w3, "clone", "-q", "--depth", "1", "--branch", "data", url, "cache")
    save(w3, "v3")
    assert git(remote, "rev-list", "--count", "data") == "1"             # exactly one commit after three runs
    assert git(remote, "log", "-1", "--format=%s", "data") == "data v3"
    assert git(remote, "rev-list", "--max-parents=0", "data") == git(remote, "rev-parse", "data")  # a root commit
    # blob ids: a.json.gz identical across runs; b.json.gz changed content has a new id
    blobs = {}
    for label, w in (("1", w1), ("2", w2)):
        blobs[label] = {n: git(w / "cache", "hash-object", str(w / "cache" / "raw" / "2026" / n)) for n in ("a.json.gz", "b.json.gz")}
    assert blobs["1"]["a.json.gz"] == blobs["2"]["a.json.gz"] and blobs["1"]["b.json.gz"] != blobs["2"]["b.json.gz"]
    assert git(remote, "rev-parse", "data:raw/2026/a.json.gz") == blobs["1"]["a.json.gz"]


# =============================================================================================
# stage_site.py
# =============================================================================================
def make_site(tmp, with_sw=False, precache=True):
    s = tmp / "site"
    for rel, txt in {"index.html": "<html></html>", "js/app.js": "1", "css/a.css": "a", "brand/m.svg": "<svg/>",
                     "tests/x.test.mjs": "t", "tools/build_tokens.py": "t", ".DS_Store": "x"}.items():
        p = s / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    if with_sw:
        (s / "sw.js").write_text("//")
    if precache:
        (s / "tools" / "build_precache.py").write_text(
            "import sys, json, os\nout = sys.argv[1]\n"
            "json.dump({'version': 'x', 'files': sorted(os.listdir(out))}, open(os.path.join(out, 'sw-precache.json'), 'w'))\n")
    return str(s)


def test_stage_site_copies_site_and_data_and_runs_precache(world, tmp_path):
    site = make_site(tmp_path, with_sw=True)
    out = str(tmp_path / "_site")
    rc = stage_site.main(["--site", site, "--data", os.path.join(world["cache"], "published"), "--out", out])
    assert rc == 0
    names = sorted(os.listdir(out))
    assert "index.html" in names and "sw-precache.json" in names and "data" in names and "sw.js" in names
    assert not os.path.exists(os.path.join(out, "tests")) and not os.path.exists(os.path.join(out, "tools"))
    assert not os.path.exists(os.path.join(out, ".DS_Store"))
    assert os.path.exists(os.path.join(out, "data", "manifest.json"))
    assert os.path.exists(os.path.join(out, "data", "2024", "events"))


def test_stage_site_refuses_unexpected_data_files_and_missing_pieces(world, tmp_path):
    site = make_site(tmp_path)
    data = tmp_path / "data"
    shutil.copytree(os.path.join(world["cache"], "published"), data)
    (data / "raw").mkdir()
    (data / "raw" / "tba_2023.json.gz").write_bytes(b"x")
    assert stage_site.main(["--site", site, "--data", str(data), "--out", str(tmp_path / "o1")]) == 1
    shutil.rmtree(data / "raw")
    (data / "2024" / "events" / "evil.json.gz").write_bytes(b"x")
    assert stage_site.main(["--site", site, "--data", str(data), "--out", str(tmp_path / "o2")]) == 1
    os.remove(data / "2024" / "events" / "evil.json.gz")
    os.remove(data / "manifest.json")
    assert stage_site.main(["--site", site, "--data", str(data), "--out", str(tmp_path / "o3")]) == 1
    # no index.html
    os.remove(os.path.join(site, "index.html"))
    assert stage_site.main(["--site", site, "--data", os.path.join(world["cache"], "published"),
                            "--out", str(tmp_path / "o4")]) == 1


def test_stage_site_needs_precache_script_when_site_has_a_service_worker(world, tmp_path):
    site = make_site(tmp_path, with_sw=True, precache=False)
    assert stage_site.main(["--site", site, "--data", os.path.join(world["cache"], "published"),
                            "--out", str(tmp_path / "o")]) == 1
    other = tmp_path / "other"
    other.mkdir()
    site2 = make_site(other, with_sw=False, precache=False)   # no service worker (before WP6): precache is skipped
    assert stage_site.main(["--site", site2, "--data", os.path.join(world["cache"], "published"),
                            "--out", str(tmp_path / "o2")]) == 0


# =============================================================================================
# the whole chain against the fake TBA server (acceptance 1 and 3)
# =============================================================================================
E2E_DUMPS = [MINI] + ([os.environ["PITWALL_E2E_DUMP"]] if os.environ.get("PITWALL_E2E_DUMP") else [])


@pytest.mark.parametrize("dump_path", E2E_DUMPS, ids=lambda p: os.path.basename(p))
def test_end_to_end_fetch_plan_run_finalize_stage_and_the_key_never_leaks(tmp_path, monkeypatch, capsys, dump_path):
    dump = load_dump(dump_path)
    year = int(dump["year"])
    srv = FakeTBA(dump, KEY)
    url = srv.start()
    try:
        monkeypatch.setenv("TBA_KEY", KEY)
        monkeypatch.setenv("PITWALL_ALLOW_STUB", "1")
        for k in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
            monkeypatch.delenv(k, raising=False)
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"seasons": [year], "current_season": year, "archive_priors": ARCH,
                                   "default_region": "fma", "default_watchlist": [1218, 1219], "default_model": "epa"}))
        cache = str(tmp_path / "cache")

        def fetch(name):
            rep = str(tmp_path / name)
            assert tba_fetch.main(["--cache", os.path.join(cache, "raw"), "--config", str(cfg), "--mode", "daily",
                                   "--base-url", url, "--report", rep, "--min-interval", "0",
                                   "--backoff-base", "0.001", "--today", "2030-01-01"]) == 0
            return rep

        # run 1: everything is fetched, built, published
        rep1 = fetch("fetch1.json")
        p1, pp1 = plan(tmp_path, cache, str(cfg), rep=rep1)
        assert p1["rebuild"] == [year] and p1["deploy"] is True
        rc, res, staged, work = run(tmp_path, cache, str(cfg), pp1, monkeypatch=monkeypatch)
        assert rc == 0 and res["rebuilt"] == [year] and not res["degraded"]
        assert finalize(cache, staged, work) == 0
        site = make_site(tmp_path)
        out = str(tmp_path / "_site")
        assert stage_site.main(["--site", site, "--data", staged, "--out", out]) == 0

        # run 2, no TBA change: only conditional requests, all 304, nothing rebuilt, no deploy if the live site matches
        srv.reset_log()
        t0 = time.monotonic()
        rep2 = fetch("fetch2.json")
        live = tmp_path / "live_manifest.json"
        shutil.copy(os.path.join(cache, "published", "manifest.json"), live)
        p2, pp2 = plan(tmp_path, cache, str(cfg), live=str(live), rep=rep2, name="plan2.json")
        elapsed = time.monotonic() - t0
        r2 = json.load(open(rep2))
        assert r2["changed_endpoints"] == 0 and r2["errors_total"] == 0
        assert not [r for r in srv.log if r["status"] == 200]
        assert all(r["inm"] for r in srv.log if r["status"] == 304)
        assert p2["rebuild"] == [] and p2["deploy"] is False
        assert elapsed < 60

        # run 3: one event changes on TBA -> only it is refetched, only this season is planned
        victim = next(iter(dump["event_data"]))
        srv.mutate(lambda d: d["event_data"][victim]["matches"][0]["alliances"]["red"].__setitem__(
            "score", d["event_data"][victim]["matches"][0]["alliances"]["red"]["score"] + 5))
        srv.reset_log()
        rep3 = fetch("fetch3.json")
        assert [r["path"] for r in srv.log if r["status"] == 200] == [f"event/{victim}/matches"]
        p3, _ = plan(tmp_path, cache, str(cfg), live=str(live), rep=rep3, name="plan3.json")
        assert p3["rebuild"] == [year] and p3["reasons"][str(year)] == ["inputs"]

        # the key reached TBA only as the header, and is nowhere on disk or in any output
        assert set(srv.keys_received) == {KEY}
        needle = KEY.encode()
        hits = []
        for dirpath, _, files in os.walk(str(tmp_path)):
            for fn in files:
                p = os.path.join(dirpath, fn)
                raw = open(p, "rb").read()
                if p.endswith(".gz"):
                    try:
                        raw += gzip.decompress(raw)
                    except OSError:
                        pass
                if needle in raw:
                    hits.append(p)
        assert hits == []
        cap = capsys.readouterr()
        assert KEY not in cap.out and KEY not in cap.err
    finally:
        srv.stop()

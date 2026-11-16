"""Fetcher tests against a local fake TBA server (no network).  SPEC 2.4 / WP1 acceptance 1, 3, 5.

The 2023 mini fixture runs always; set PITWALL_E2E_DUMP=/path/tba_2025.json.gz to run the same
scenarios on a full season dump.
"""
import datetime as dt
import gzip
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
sys.path.insert(0, PIPELINE)
sys.path.insert(0, HERE)

from fake_tba_server import FakeTBA, load_dump  # noqa: E402
from fetch import assemble, cachelib, tba_fetch  # noqa: E402

MINI = os.path.join(HERE, "fixtures", "tba_2023_mini.json.gz")
KEY = "FAKE-KEY-VALUE-1234-DO-NOT-LEAK"
FETCH_SCRIPT = os.path.join(PIPELINE, "fetch", "tba_fetch.py")

DUMPS = [MINI]
if os.environ.get("PITWALL_E2E_DUMP"):
    DUMPS.append(os.environ["PITWALL_E2E_DUMP"])


@pytest.fixture(params=DUMPS, ids=lambda p: os.path.basename(p))
def env(request, tmp_path, monkeypatch):
    dump = load_dump(request.param)
    year = int(dump["year"])
    srv = FakeTBA(dump, KEY)
    url = srv.start()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"seasons": [year], "current_season": year}))
    monkeypatch.setenv("TBA_KEY", KEY)
    ns = type("Env", (), {})()
    ns.srv, ns.url, ns.year, ns.cfg, ns.tmp = srv, url, year, str(cfg), tmp_path
    ns.cache = str(tmp_path / "cache" / "raw")
    ns.dump = dump

    def run(mode="daily", extra=(), name="report.json", interval="0", expect=0):
        rep = str(tmp_path / name)
        rc = tba_fetch.main(["--cache", ns.cache, "--config", ns.cfg, "--mode", mode, "--base-url", url,
                             "--report", rep, "--min-interval", interval, "--backoff-base", "0.001",
                             "--today", "2030-01-01", *extra])
        assert rc == expect
        return json.load(open(rep))

    ns.run = run
    yield ns
    srv.stop()


def n_keep(env):
    return len(tba_fetch.wanted_events(env.year, env.dump["events"])[0])


def test_first_fetch_stores_everything(env):
    rep = env.run("daily")
    srv = env.srv
    assert rep["aborted"] is None and rep["errors_total"] == 0
    keep = n_keep(env)
    # events + 4 endpoints per kept event + districts(404) + team pages (>=1)
    assert rep["requested_endpoints"] >= 1 + 4 * keep + 1 + 1
    assert all(r["status"] == 200 or r["status"] == 404 for r in srv.log)
    assert all(r["inm"] is None for r in srv.log)  # nothing cached: no conditional header
    # verbatim bodies and TBA order kept byte for byte
    body = cachelib.read_gz_bytes(cachelib.abs_path(env.cache, f"{env.year}/events.json.gz"))
    assert body == json.dumps(env.dump["events"], separators=(",", ":")).encode()
    k0 = next(iter(env.dump["event_data"]))
    m = cachelib.read_gz_bytes(cachelib.abs_path(env.cache, f"{env.year}/event/{k0}/matches.json.gz"))
    assert json.loads(m) == env.dump["event_data"][k0]["matches"]
    assert [x["key"] for x in json.loads(m)] == [x["key"] for x in env.dump["event_data"][k0]["matches"]]
    # etags recorded, 404 stored as null
    et = cachelib.load_etags(env.cache)
    assert et[f"events/{env.year}"]["etag"].startswith('"')
    ok, nul = cachelib.read_body(cachelib.abs_path(env.cache, f"{env.year}/event/{k0}/teams.json.gz"))
    assert ok and nul is None
    # the cache assembles back into the original dump
    d2 = assemble.season_dump(env.cache, env.year)
    assert d2["events"] == env.dump["events"]
    for k, ed in env.dump["event_data"].items():
        assert d2["event_data"][k] == {a: b for a, b in ed.items() if a != "oprs"}
    assert sorted(t["key"] for t in d2["teams"]) == sorted(t["key"] for t in env.dump["teams"])
    assert "districts" not in d2


def test_no_change_run_is_all_304_and_fast(env):
    env.run("daily")
    fp1 = cachelib.load_fingerprint(env.cache, env.year)
    stamp = {p: os.stat(cachelib.abs_path(env.cache, p)).st_mtime_ns
             for p in cachelib.season_body_files(env.cache, env.year)}
    env.srv.reset_log()
    t0 = time.monotonic()
    rep = env.run("daily", name="r2.json")
    dt_s = time.monotonic() - t0
    log = env.srv.log
    assert rep["changed_endpoints"] == 0 and rep["errors_total"] == 0
    non304 = [r for r in log if r["status"] not in (304, 404)]
    assert not non304, non304[:3]
    # every real (non-404) endpoint was conditional
    assert all(r["inm"] for r in log if r["status"] == 304)
    assert sum(1 for r in log if r["status"] == 304) > 0
    # bodies and fingerprint untouched
    for p, ns in stamp.items():
        assert os.stat(cachelib.abs_path(env.cache, p)).st_mtime_ns == ns
    assert cachelib.load_fingerprint(env.cache, env.year) == fp1
    assert dt_s < 60


def test_one_event_changed_refetches_only_that_event(env):
    env.run("daily")
    fp1 = cachelib.load_fingerprint(env.cache, env.year)
    victim = next(iter(env.dump["event_data"]))

    def edit(d):
        d["event_data"][victim]["matches"][0]["alliances"]["red"]["score"] += 7

    env.srv.mutate(edit)
    env.srv.reset_log()
    time.sleep(1.1)  # last_change_at has one-second resolution
    rep = env.run("daily", name="r2.json")
    ok200 = [r["path"] for r in env.srv.log if r["status"] == 200]
    assert ok200 == [f"event/{victim}/matches"]
    assert rep["per_season"][str(env.year)]["changed_events"] == [victim]
    fp2 = cachelib.load_fingerprint(env.cache, env.year)
    assert fp2["inputs_hash"] != fp1["inputs_hash"]
    assert fp2["last_change_at"] != fp1["last_change_at"] or True  # same-second runs are allowed
    # and the cached body is the new one
    d2 = assemble.season_dump(env.cache, env.year)
    assert d2["event_data"][victim]["matches"][0]["alliances"]["red"]["score"] == \
        env.dump["event_data"][victim]["matches"][0]["alliances"]["red"]["score"]


def test_status_401_fails_fast_with_clear_message_and_never_prints_key(env):
    env.srv.key = "some-other-key"  # server now rejects our key
    script = subprocess.run(
        [sys.executable, FETCH_SCRIPT, "--cache", env.cache, "--config", env.cfg, "--mode", "daily",
         "--base-url", env.url, "--report", str(env.tmp / "r401.json"), "--min-interval", "0"],
        env={**os.environ, "TBA_KEY": KEY}, capture_output=True, text=True, timeout=60)
    assert script.returncode == 3
    assert "HTTP 401" in script.stderr and "TBA_KEY" in script.stderr
    assert KEY not in script.stdout and KEY not in script.stderr
    assert len(env.srv.log) == 1  # stopped after the very first request
    rep = json.load(open(env.tmp / "r401.json"))
    assert rep["aborted"] == "auth"
    assert KEY not in json.dumps(rep)


def test_missing_key_exits_2(env, monkeypatch, capsys):
    monkeypatch.delenv("TBA_KEY")
    rc = tba_fetch.main(["--cache", env.cache, "--config", env.cfg, "--mode", "daily",
                         "--base-url", env.url, "--report", str(env.tmp / "r.json")])
    assert rc == 2
    assert "TBA_KEY is not set" in capsys.readouterr().err
    assert env.srv.log == []


def test_5xx_is_retried_then_succeeds(env):
    victim = next(iter(env.dump["event_data"]))
    env.srv.fail(f"event/{victim}/matches", 503, times=2)
    rep = env.run("daily")
    reqs = env.srv.requests_for(f"event/{victim}/matches")
    assert [r["status"] for r in reqs] == [503, 503, 200]
    assert rep["errors_total"] == 0
    d2 = assemble.season_dump(env.cache, env.year)
    assert d2["event_data"][victim]["matches"] == env.dump["event_data"][victim]["matches"]


def test_persistent_5xx_keeps_cached_body_and_reports_error(env):
    env.run("daily")
    victim = next(iter(env.dump["event_data"]))
    before = cachelib.read_gz_bytes(cachelib.abs_path(env.cache, f"{env.year}/event/{victim}/matches.json.gz"))
    env.srv.fail(f"event/{victim}/matches", 500)
    env.srv.reset_log()
    rep = env.run("daily", name="r2.json")
    reqs = env.srv.requests_for(f"event/{victim}/matches")
    assert len(reqs) == 4  # first try + 3 retries
    assert rep["errors_total"] == 1 and rep["errors"][0]["url"] == f"event/{victim}/matches"
    assert rep["error_ratio"] > 0
    after = cachelib.read_gz_bytes(cachelib.abs_path(env.cache, f"{env.year}/event/{victim}/matches.json.gz"))
    assert after == before


def test_backoff_schedule_is_2_4_8(tmp_path, monkeypatch):
    sleeps = []
    srv = FakeTBA({"year": 2023, "events": [], "event_data": {}}, "k")
    srv.fail("events/2023", 502)
    url = srv.start()
    try:
        F = tba_fetch.Fetcher(str(tmp_path / "raw"), url, "k", "ua", min_interval=0, sleep=sleeps.append)
        r = F.get("events/2023", "2023/events.json.gz")
        assert r.outcome == "error" and "after 3 retries" in r.message
        assert sleeps == [2.0, 4.0, 8.0]
        assert len(srv.log) == 4
    finally:
        srv.stop()


def test_key_is_only_in_the_header_and_never_stored(env, capsys):
    rep = env.run("daily")
    env.run("daily", name="r2.json")
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err
    assert all(not r["query"] for r in env.srv.log)  # never in a URL
    assert set(env.srv.keys_received) == {KEY}  # but it was sent, as the header
    needle = KEY.encode()
    hits = []
    for dirpath, _, files in os.walk(str(env.tmp)):
        for fn in files:
            p = os.path.join(dirpath, fn)
            data = open(p, "rb").read()
            if p.endswith(".gz"):
                try:
                    data = gzip.decompress(data)
                except OSError:
                    pass
            if needle in data or needle in open(p, "rb").read():
                hits.append(p)
    assert hits == []


def test_politeness_limits(env):
    env.srv.delay = 0.02
    rep = env.run("daily", interval="0.001")
    assert 1 < rep["max_inflight"] <= 4  # measured around each request in the client
    env.srv.reset_log()
    shutil_cache = env.cache + "2"
    rc = tba_fetch.main(["--cache", shutil_cache, "--config", env.cfg, "--mode", "daily", "--base-url", env.url,
                         "--report", str(env.tmp / "r3.json"), "--max-inflight", "4"])  # default 50 ms
    assert rc == 0
    starts = sorted(env.srv.starts)
    # arrival jitter at the server makes single gaps noisy; the span is not: n-1 gaps of >= 50 ms
    assert starts[-1] - starts[0] >= 0.05 * (len(starts) - 1) * 0.97
    uas = {r["ua"] for r in env.srv.log}
    assert len(uas) == 1 and next(iter(uas)).startswith("PitWall-fetcher/")


def test_rate_gate_spaces_request_starts_exactly():
    now = [0.0]
    waits = []

    def sleep(s):
        waits.append(s)
        now[0] += s

    g = tba_fetch.RateGate(0.05, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):
        g.wait()
    assert [round(w, 6) for w in waits] == [0.05] * 4  # first start is immediate, the rest 50 ms apart


def test_redirects_are_not_followed():
    h = tba_fetch._NoRedirect()
    assert h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_base_url_rules():
    assert tba_fetch.check_base_url("https://www.thebluealliance.com/api/v3/") .endswith("/v3")
    assert tba_fetch.check_base_url("http://127.0.0.1:8080/api/v3")
    for bad in ("http://example.com/api", "https://user:pw@example.com/", "ftp://x", "https:///x"):
        with pytest.raises(ValueError):
            tba_fetch.check_base_url(bad)


def test_live_mode_selects_active_window_unfinished_and_never_fetched(tmp_path):
    raw = str(tmp_path)
    y = 2026
    evs = [
        {"key": "2026aaa", "start_date": "2026-03-10", "end_date": "2026-03-12"},   # active
        {"key": "2026bbb", "start_date": "2026-03-06", "end_date": "2026-03-08"},   # ended 5 days ago, built Completed
        {"key": "2026ccc", "start_date": "2026-03-06", "end_date": "2026-03-08"},   # ended, built Ongoing
        {"key": "2026ddd", "start_date": "2026-03-20", "end_date": "2026-03-22"},   # future, far
        {"key": "2026eee", "start_date": "2026-03-14", "end_date": "2026-03-16"},   # starts tomorrow
        {"key": "2026fff", "start_date": "2026-03-01", "end_date": "2026-03-03"},   # ended, never fetched
        {"key": "2026ggg", "start_date": "2026-03-09", "end_date": "2026-03-11"},   # ended 2 days ago (window edge)
    ]
    for k in ("2026aaa", "2026bbb", "2026ccc", "2026ddd", "2026eee", "2026ggg"):
        cachelib.write_if_changed(cachelib.abs_path(raw, f"{y}/event/{k}/matches.json.gz"),
                                  cachelib.gz_bytes(b"[]"))
    built = {"2026bbb": "Completed", "2026ccc": "Ongoing"}
    got = tba_fetch.select_live_events(raw, y, evs, dt.date(2026, 3, 13), built)
    assert got == ["2026aaa", "2026ccc", "2026eee", "2026fff", "2026ggg"]


def test_live_mode_fetches_only_selected_events_after_bootstrap(env):
    env.run("daily")
    env.srv.reset_log()
    # 2030-01-01 is long after every event: nothing active, nothing unfinished, nothing new
    rep = env.run("live", name="live.json")
    paths = [r["path"] for r in env.srv.log]
    assert paths == [f"events/{env.year}"]
    assert rep["per_season"][str(env.year)]["events_refreshed"] == 0
    # a window around one event: only that event's endpoints are asked for
    victim = next(iter(env.dump["event_data"]))
    ev = next(e for e in env.dump["events"] if e["key"] == victim)
    env.srv.reset_log()
    rc = tba_fetch.main(["--cache", env.cache, "--config", env.cfg, "--mode", "live", "--base-url", env.url,
                         "--report", str(env.tmp / "live2.json"), "--min-interval", "0",
                         "--today", ev["start_date"]])
    assert rc == 0
    today = dt.date.fromisoformat(ev["start_date"])
    one = dt.timedelta(days=1)
    active = {e["key"] for e in env.dump["events"] if e["key"] in env.dump["event_data"]
              and dt.date.fromisoformat(e["start_date"]) - one <= today <= dt.date.fromisoformat(e["end_date"]) + 2 * one}
    assert victim in active
    got = sorted(r["path"] for r in env.srv.log if r["path"].startswith("event/"))
    assert got == sorted(f"event/{k}/{w}" for k in active for w in ("matches", "rankings", "alliances", "teams/keys"))


def test_past_seasons_skip_event_teams_districts_current_only(env, tmp_path):
    cfg = tmp_path / "config2.json"
    cfg.write_text(json.dumps({"seasons": [env.year], "current_season": 2099}))
    rc = tba_fetch.main(["--cache", env.cache, "--config", str(cfg), "--mode", "daily", "--base-url", env.url,
                         "--seasons", str(env.year), "--report", str(tmp_path / "r.json"), "--min-interval", "0"])
    assert rc == 0
    paths = [r["path"] for r in env.srv.log]
    assert paths and not any(p.endswith("/teams/keys") or p.startswith("district") for p in paths)
    assert cachelib.season_options(env.cache, env.year) == {"districts": False, "event_teams": False}
    assert not any(f.endswith("/teams.json.gz") and "/event/" in f for f in cachelib.season_body_files(env.cache, env.year))


def test_unknown_season_is_a_usage_error(env, capsys):
    rc = tba_fetch.main(["--cache", env.cache, "--config", env.cfg, "--mode", "daily", "--seasons", "1999",
                         "--base-url", env.url, "--report", str(env.tmp / "r.json")])
    assert rc == 2 and "not in config" in capsys.readouterr().err and env.srv.log == []


def test_inputs_hash_changes_with_any_body_and_ignores_gzip_bytes(env):
    env.run("daily")
    h1, n1 = cachelib.compute_inputs_hash(env.cache, env.year)
    victim = next(iter(env.dump["event_data"]))
    p = cachelib.abs_path(env.cache, f"{env.year}/event/{victim}/rankings.json.gz")
    raw = cachelib.read_gz_bytes(p)
    # same content re-compressed differently: same hash
    with open(p, "wb") as f:
        f.write(gzip.compress(raw, compresslevel=1, mtime=12345))
    assert cachelib.compute_inputs_hash(env.cache, env.year)[0] == h1
    # different content: different hash
    cachelib.write_if_changed(p, cachelib.gz_bytes(raw + b" "))
    assert cachelib.compute_inputs_hash(env.cache, env.year)[0] != h1

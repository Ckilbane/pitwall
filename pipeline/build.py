#!/usr/bin/env python3
"""Plan and run the PitWall data build (SPEC 2.3 steps 5-7 and 10, 2.5, 2.7).

    build.py plan     --cache cache --config pipeline/config.json --fetch-report fetch_report.json \
                      --live-manifest live_manifest.json [--force] [--seasons 2025 2026] --out plan.json
    build.py run      --plan plan.json --cache cache --work work --out-data staged_data
    build.py finalize --cache cache --staged staged_data --run-result work/run_result.json

`cache` is the checkout of the `data` branch (cache/raw, cache/out, cache/published, cache/state.json).

plan      decides which seasons to rebuild (inputs hash, code hash, retry, missing shards, --force) and
          whether to deploy.  Nothing is computed.
run       builds the planned seasons in ascending order (priors chain per SPEC 2.6), validates each
          one, replaces a failing season by its last good shards (manifest entry `stale: true`),
          copies untouched seasons from cache/published, writes models.json, history.json.gz and
          manifest.json.  It never touches cache/published or cache/state.json.
finalize  promotes the result into the cache: published/ := staged data, out/{y} for rebuilt seasons,
          state.json.  Run it only after `run` succeeded.

Exit codes: 0 ok (also when seasons fell back to last-good data: see run_result.json `degraded`);
1 nothing deployable / fatal; 2 usage.

SPEC deviations are listed in the WP1 report; the ones that live here: `code_hash` skips
pipeline/tests/ (test edits must not force a rebuild), and validation happens inside `run` (per
season, right after each build) instead of as a separate workflow step.
"""
import argparse
import csv
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fetch import assemble, cachelib  # noqa: E402

SCHEMA = 1
ABORT_ERROR_RATIO = 0.20  # SPEC 2.7: more than 20% of requested endpoints failed -> no compute, no deploy
LEAK_CUTS_FULL = 3        # SPEC 4.5.4: `full` mode runs 3 cuts per season

CODE_HASH_SKIP_DIRS = {"__pycache__", ".pycache", "tests"}


# =============================================================================================
# small helpers
# =============================================================================================
def utc_now() -> dt.datetime:
    env = os.environ.get("PITWALL_NOW")
    if env:
        return dt.datetime.strptime(env, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: str, default: Any = None) -> Any:
    return cachelib.load_json_file(path, default)


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")


def one_line(s: Any, n: int = 300) -> str:
    return " ".join(str(s).split())[:n]


def append_lines(path: Optional[str], lines: List[str]) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def gh_output(path: Optional[str], **kv: Any) -> None:
    """Write `key=value` lines to $GITHUB_OUTPUT (values are single-line)."""
    append_lines(path, [f"{k}={one_line(v, 500)}" for k, v in kv.items()])


def short_code_version(code_hash: str) -> str:
    sha = (os.environ.get("GITHUB_SHA") or "").strip().lower()
    if len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha[:7]):
        return sha[:7]
    return code_hash[:7]


def data_version_for(now: dt.datetime, code_hash: str) -> str:
    return f"{now.strftime('%Y%m%dT%H%MZ')}-{short_code_version(code_hash)}"


# =============================================================================================
# code hash (SPEC 2.5 rule 2)
# =============================================================================================
def compute_code_hash(repo: str = REPO) -> str:
    """SHA-256 over sorted "{relative_path} {sha256(content)}" lines for pipeline/**/*.py (vendored
    tree included, tests excluded), pipeline/models/*.json, pipeline/config.json,
    docs/perrobot_fields.json and docs/schema/*.json."""
    files: List[str] = []
    pdir = os.path.join(repo, "pipeline")
    for dirpath, dirnames, filenames in os.walk(pdir):
        dirnames[:] = sorted(d for d in dirnames if d not in CODE_HASH_SKIP_DIRS)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            if fn.endswith(".py"):
                files.append(rel)
            elif rel.startswith("pipeline/models/") and rel.count("/") == 2 and fn.endswith(".json"):
                files.append(rel)
    files.append("pipeline/config.json")
    files.append("docs/perrobot_fields.json")
    sdir = os.path.join(repo, "docs", "schema")
    if os.path.isdir(sdir):
        files += ["docs/schema/" + fn for fn in os.listdir(sdir) if fn.endswith(".json")]
    lines = []
    for rel in sorted(set(files)):
        p = os.path.join(repo, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, "rb") as f:
                lines.append(f"{rel} {hashlib.sha256(f.read()).hexdigest()}")
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def norm_hash_of_csv(path: str) -> str:
    """SHA-256 of sorted "team,norm_epa" pairs of a team_years.csv (SPEC 2.5 rule 3)."""
    pairs = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pairs.append(f"{row['team']},{row['norm_epa']}")
    pairs.sort()
    return hashlib.sha256(("\n".join(pairs) + "\n").encode("utf-8")).hexdigest()


# =============================================================================================
# state
# =============================================================================================
def state_path(cache: str) -> str:
    return os.path.join(cache, "state.json")


def load_state(cache: str) -> Dict[str, Any]:
    st = load_json(state_path(cache), None)
    if not isinstance(st, dict):
        st = {}
    st.setdefault("data_version", None)
    st.setdefault("code_hash", None)
    if not isinstance(st.get("seasons"), dict):
        st["seasons"] = {}
    return st


# =============================================================================================
# plan (pure decision functions are unit-tested in tests/test_build_plan.py)
# =============================================================================================
def decide_rebuilds(
    seasons: List[int],
    inputs: Dict[int, str],
    state: Dict[str, Any],
    code_hash: str,
    published: Dict[int, bool],
    force: bool = False,
    force_seasons: Optional[List[int]] = None,
) -> Dict[int, List[str]]:
    """{season: [reasons]} for every season to rebuild, from the rules known before building.

    Rules (SPEC 2.5): 1 inputs hash differs from state; 2 code hash differs; 4 --force (with
    --seasons only those, else all).  Added: `retry` when the last build failed (state ok == false,
    SPEC 2.7) and `unpublished` when no shards exist in cache/published.  Rule 3 (norm hash
    propagation) depends on a build result and is applied by `propagate_norm` during `run`."""
    out: Dict[int, List[str]] = {}
    sts = state.get("seasons", {})
    for y in sorted(seasons):
        st = sts.get(str(y))
        why: List[str] = []
        if not st or st.get("inputs_hash") != inputs.get(y):
            why.append("inputs")
        if st and st.get("code_hash") != code_hash:
            why.append("code")
        if st and st.get("ok") is False:
            why.append("retry")
        if not published.get(y, False):
            why.append("unpublished")
        if force and (not force_seasons or y in force_seasons):
            why.append("force")
        if why:
            out[y] = why
    return out


def propagate_norm(
    seasons: List[int],
    built: int,
    old_norm_hash: Optional[str],
    new_norm_hash: str,
    todo: Dict[int, List[str]],
) -> List[int]:
    """Rule 3: season `built` was rebuilt and its norm hash changed -> every later season (present)
    rebuilds.  Adds to `todo` in place, returns the seasons newly added."""
    if old_norm_hash == new_norm_hash:
        return []
    added = []
    for z in sorted(seasons):
        if z > built:
            if z not in todo:
                todo[z] = []
                added.append(z)
            if f"norm:{built}" not in todo[z]:
                todo[z].append(f"norm:{built}")
    return added


def deploy_decision(rebuild: List[int], published_manifest: Optional[Dict[str, Any]],
                    live_manifest: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """SPEC 2.5: deploy when something is rebuilt, or when the published manifest's data_version
    differs from the live site's (a previous deploy failed, or first deploy)."""
    if rebuild:
        return True, "rebuild"
    if published_manifest:
        pv = published_manifest.get("data_version")
        lv = (live_manifest or {}).get("data_version")
        if pv and pv != lv:
            return True, "live manifest differs" if live_manifest else "nothing live yet"
    return False, "none"


def _second_precision(ts: Optional[str]) -> Optional[str]:
    """2026-11-11T23:34:24.819Z -> 2026-11-11T23:34:24Z (the schemas want second precision)."""
    return re.sub(r"\.[0-9]+Z$", "Z", ts) if isinstance(ts, str) else ts


def make_plan(cache: str, config: Dict[str, Any], fetch_report: Optional[Dict[str, Any]],
              live_manifest: Optional[Dict[str, Any]], force: bool = False,
              force_seasons: Optional[List[int]] = None, repo: str = REPO,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    now = now or utc_now()
    raw_dir = os.path.join(cache, "raw")
    configured = [int(s) for s in config["seasons"]]
    present = [y for y in sorted(configured) if os.path.isfile(cachelib.abs_path(raw_dir, cachelib.endpoint_events(y)[1]))]
    inputs: Dict[int, str] = {}
    last_change: Dict[int, Optional[str]] = {}
    for y in present:
        fp = cachelib.load_fingerprint(raw_dir, y)
        if fp is None:  # e.g. a hand-seeded cache: compute, do not write
            h, n = cachelib.compute_inputs_hash(raw_dir, y)
            fp = {"inputs_hash": h, "endpoint_count": n, "last_change_at": None}
        inputs[y] = fp["inputs_hash"]
        last_change[y] = _second_precision(fp.get("last_change_at"))
    code_hash = compute_code_hash(repo)
    state = load_state(cache)
    pub_dir = os.path.join(cache, "published")
    published = {y: os.path.isfile(os.path.join(pub_dir, str(y), "season.json.gz")) for y in present}
    reasons = decide_rebuilds(present, inputs, state, code_hash, published, force, force_seasons)

    abort = None
    if fetch_report:
        if fetch_report.get("aborted"):
            abort = {"reason": f"fetch aborted ({fetch_report['aborted']})"}
        elif float(fetch_report.get("error_ratio") or 0.0) > ABORT_ERROR_RATIO:
            abort = {"reason": "TBA fetch failed for {:.0%} of {} requested endpoints (limit {:.0%})".format(
                float(fetch_report["error_ratio"]), fetch_report.get("requested_endpoints"), ABORT_ERROR_RATIO)}
    pub_manifest = load_json(os.path.join(pub_dir, "manifest.json"), None)
    rebuild = [] if abort else sorted(reasons)
    deploy, deploy_reason = (False, "aborted") if abort else deploy_decision(rebuild, pub_manifest, live_manifest)
    return {
        "schema": SCHEMA,
        "generated_at": iso(now),
        "mode": (fetch_report or {}).get("mode"),
        "current_season": int(config["current_season"]),
        "seasons_present": present,
        "inputs_hash": {str(y): inputs[y] for y in present},
        "last_change_at": {str(y): last_change[y] for y in present},
        "code_hash": code_hash,
        "force": bool(force),
        "rebuild": rebuild,
        "reasons": {str(y): reasons[y] for y in rebuild},
        "deploy": bool(deploy),
        "deploy_reason": deploy_reason,
        "abort": abort,
    }


def cmd_plan(a: argparse.Namespace) -> int:
    config = load_json(a.config)
    plan = make_plan(a.cache, config, load_json(a.fetch_report, None) if a.fetch_report else None,
                     load_json(a.live_manifest, None) if a.live_manifest else None,
                     bool(a.force), a.seasons or None)
    write_json(a.out, plan)
    has_work = bool(plan["rebuild"]) or plan["deploy"]
    print(f"plan: rebuild={plan['rebuild']} reasons={plan['reasons']} deploy={plan['deploy']} "
          f"({plan['deploy_reason']}) abort={plan['abort']}")
    gh_output(a.github_output, has_work=str(has_work and not plan["abort"]).lower(),
              abort=str(bool(plan["abort"])).lower(), abort_reason=(plan["abort"] or {}).get("reason", ""),
              plan_deploy=str(plan["deploy"]).lower(), rebuild=" ".join(map(str, plan["rebuild"])))
    lines = ["### Plan", f"- mode: `{plan['mode']}`; seasons in cache: {plan['seasons_present']}",
             f"- rebuild: {plan['rebuild'] or 'none'} {plan['reasons'] or ''}",
             f"- deploy: {plan['deploy']} ({plan['deploy_reason']})"]
    if plan["abort"]:
        lines.append(f"- **abort: {plan['abort']['reason']}**; nothing was rebuilt or deployed")
    elif not has_work:
        lines.append("- no changes: no TBA inputs changed and the live site is current")
    append_lines(a.summary, lines)
    return 0


# =============================================================================================
# computers: the only place that touches WP2/WP3b/WP3c
# =============================================================================================
class SeasonOutput:
    """What `Computer.compute_season` returns."""

    def __init__(self, entry: Dict[str, Any], backtest: Dict[str, Any], history: Dict[str, Any],
                 notes: Optional[List[str]] = None):
        self.entry, self.backtest, self.history, self.notes = entry, backtest, history, notes or []


class RealComputer:
    """The production season builder: raw dump -> EPA + Fast EPA runs -> features -> backtest ->
    shards, using the APIs in SPEC 4 (sb.model_runner, sb.backtest, sb.perrobot, sb.features,
    sb.shards, sb.model_info, sb.export)."""

    def __init__(self, config: Dict[str, Any], leak_cuts: int = 0, published_dir: Optional[str] = None):
        self.config = config
        self.leak_cuts = leak_cuts
        self.published_dir = published_dir
        self.fast_config = None  # None = pipeline/models/fast_epa.json (sb.fast_epa.load_config)

    @staticmethod
    def _optional(name: str):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name == name:
                return None
            raise

    def compute_season(self, year: int, dump: Dict[str, Any], priors: Any, new_out: str, staged: str,
                       meta: Dict[str, Any]) -> SeasonOutput:
        import sb  # noqa: F401
        from sb import backtest, export, model_runner, perrobot, shards

        notes: List[str] = []
        base = model_runner.load_base(year, dump)
        facts = perrobot.robot_facts(year, dump)
        epa = model_runner.run_model(year, base, priors, "epa")
        fast = model_runner.run_model(year, base, priors, "fast", config=self.fast_config, robot_facts=facts)
        runs = {"epa": epa, "fast": fast}

        # priors for later seasons: team_years.csv + run_meta.json of the EPA run only
        tmp = os.path.join(new_out, "_export")
        export.export_year(epa.objs, tmp)
        dst = os.path.join(new_out, str(year))
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(os.path.join(tmp, str(year), "team_years.csv"), os.path.join(dst, "team_years.csv"))
        write_json(os.path.join(dst, "run_meta.json"), {"year": year, "inputs_hash": meta.get("inputs_hash"),
                                                         "code_hash": meta.get("code_hash")})
        shutil.rmtree(tmp, ignore_errors=True)

        features = None
        feat_mod = self._optional("sb.features")
        if feat_mod is not None:
            cfg = getattr(feat_mod, "CONFIG", {})
            features = {m: feat_mod.compute(r, r.objs[2], cfg) for m, r in runs.items()}
        else:
            notes.append("sb.features not present: feature columns are null")

        la = self._leak_audits(year, base, priors, facts, backtest, runs)
        backtests = {m: backtest.score_season(r, la.get(m)) for m, r in runs.items()}
        files = shards.write_season(year, runs, features, backtests, facts, dump, staged, meta)
        return SeasonOutput(files.manifest_entry, files.backtest, files.history, notes + list(files.notes))

    def _leak_audits(self, year, base, priors, facts, backtest, runs) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if self.leak_cuts > 0:
            for m, r in runs.items():
                cfg = r.config if m == "fast" else None
                out[m] = backtest.leak_audit(year, base, priors, m, cfg, facts if m == "fast" else None,
                                             cuts=self.leak_cuts, full_run=r)
            return out
        # other runs copy the last result (SPEC 4.5.4)
        if self.published_dir:
            doc = _read_gz_json(os.path.join(self.published_dir, str(year), "season.json.gz"))
            for m in runs:
                la = ((doc or {}).get("backtest") or {}).get(m, {}).get("leak_audit")
                if la:
                    out[m] = la
        return out

    def write_global(self, staged: str, seasons_meta: List[Dict[str, Any]], history_parts: List[Dict[str, Any]],
                     manifest_meta: Dict[str, Any]) -> Dict[str, Any]:
        import sb  # noqa: F401
        from sb import model_info, shards

        models = [model_info.describe("epa"), model_info.describe("fast")]
        tuned = list(((models[1].get("tuning") or {}).get("tuned_on")) or [])
        return shards.write_global(staged, seasons_meta, {"models": models, "tuned_on": tuned},
                                   history_parts, manifest_meta)

    def history_part(self, season_doc: Dict[str, Any]) -> Dict[str, Any]:
        from sb import shards

        return shards.history_part(season_doc)

    def validate_season(self, staged: str, year: int, schema_dir: str) -> List[str]:
        from sb import validate_shards as vs

        try:
            return vs.validate_season(staged, year, vs.Schemas(schema_dir))
        except Exception as e:  # noqa: BLE001  (an unreadable file is a validation failure, not a crash)
            return [f"{year}: unreadable shard: {type(e).__name__}: {e}"]

    def validate_tree(self, staged: str, schema_dir: str) -> List[str]:
        from sb import validate_shards as vs

        try:
            rep = vs.validate_tree(staged, schema_dir)
        except Exception as e:  # noqa: BLE001
            return [f"unreadable data tree: {type(e).__name__}: {e}"]
        return [f"[{k}] {e}" for k, errs in rep.items() for e in errs]


def _read_gz_json(path: str) -> Any:
    import gzip

    try:
        with gzip.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def make_computer(spec: str, config: Dict[str, Any], leak_cuts: int, published_dir: str):
    if spec == "real":
        return RealComputer(config, leak_cuts, published_dir)
    if os.environ.get("PITWALL_ALLOW_STUB") != "1":
        raise SystemExit("--computer other than 'real' is for tests and needs PITWALL_ALLOW_STUB=1")
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)(config, leak_cuts, published_dir)


# =============================================================================================
# run
# =============================================================================================
def _copytree(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _read_published_entry(published: str, year: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(manifest seasonEntry, season.json document) of a published season, or (None, None)."""
    man = load_json(os.path.join(published, "manifest.json"), None) or {}
    entry = next((s for s in man.get("seasons", []) if s.get("season") == year), None)
    doc = _read_gz_json(os.path.join(published, str(year), "season.json.gz"))
    return entry, doc


def cmd_run(a: argparse.Namespace) -> int:
    t_start = time.monotonic()
    plan = load_json(a.plan)
    config = load_json(a.config)
    cache = a.cache
    published = os.path.join(cache, "published")
    state = load_state(cache)
    now = utc_now()
    generated_at = iso(now)
    code_hash = plan["code_hash"]
    data_version = data_version_for(now, code_hash)
    schema_dir = os.path.join(REPO, "docs", "schema")
    leak_cuts = LEAK_CUTS_FULL if plan.get("mode") == "full" else 0
    computer = make_computer(a.computer, config, leak_cuts, published)

    staged = a.out_data
    work = a.work
    for d in (staged, work):
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d)
    out_view = os.path.join(work, "out_view")          # priors source: cache/out + seasons built in this run
    new_out = os.path.join(work, "out_new")            # promoted by finalize for successfully built seasons
    os.makedirs(new_out)
    if os.path.isdir(os.path.join(cache, "out")):
        shutil.copytree(os.path.join(cache, "out"), out_view)
    else:
        os.makedirs(out_view)

    seasons = [int(y) for y in plan["seasons_present"]]
    todo: Dict[int, List[str]] = {int(y): list(r) for y, r in plan["reasons"].items() if int(y) in seasons}
    archive = os.path.join(REPO, config["archive_priors"])
    summary: List[str] = ["### Build"]
    rebuilt: List[int] = []
    failed: Dict[int, str] = {}
    stale: Dict[int, str] = {}
    new_seasons: Dict[str, Dict[str, Any]] = {}
    outputs: Dict[int, SeasonOutput] = {}
    reused: Dict[int, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    broken_priors_from: Optional[int] = None

    for y in seasons:
        prev = state["seasons"].get(str(y), {})
        if y not in todo:
            continue_ok = _reuse(published, y, reused)
            if continue_ok:
                new_seasons[str(y)] = dict(prev) if prev else {}
                shutil.copytree(os.path.join(published, str(y)), os.path.join(staged, str(y)))
                continue
            todo[y] = ["unpublished"]  # state says built but the shards are gone: build it
        t0 = time.monotonic()
        try:
            if broken_priors_from is not None:
                raise RuntimeError(f"not rebuilt: season {broken_priors_from} failed validation in this run "
                                   "and this season's priors come from it")
            from sb import priors as sbpriors  # noqa: WPS433

            dump = assemble.season_dump(cache, y)
            computed = {z for z in seasons if z < y}
            priors, sources = sbpriors.build_priors(y, archive, out_view, computed, False)
            meta = {
                "generated_at": generated_at, "data_version": data_version,
                "current_season": int(config["current_season"]), "updated_at": generated_at,
                "last_tba_change_at": (plan.get("last_change_at") or {}).get(str(y)),
                "inputs_hash": plan["inputs_hash"][str(y)], "code_hash": code_hash,
                "today": generated_at[:10],
            }
            sdir = os.path.join(staged, str(y))
            if os.path.exists(sdir):
                shutil.rmtree(sdir)
            out = computer.compute_season(y, dump, priors, new_out, staged, meta)
            errs = computer.validate_season(staged, y, schema_dir)
            if errs:
                if y < max(seasons):
                    broken_priors_from = y
                raise ValueError("validation failed: " + errs[0] + (f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""))
            outputs[y] = out
            rebuilt.append(y)
            nh = norm_hash_of_csv(os.path.join(new_out, str(y), "team_years.csv"))
            shutil.copytree(os.path.join(new_out, str(y)), os.path.join(out_view, str(y)), dirs_exist_ok=True)
            new_seasons[str(y)] = {"inputs_hash": plan["inputs_hash"][str(y)], "code_hash": code_hash,
                                   "norm_hash": nh, "built_at": generated_at, "ok": True}
            added = propagate_norm(seasons, y, prev.get("norm_hash"), nh, todo)
            summary.append(f"- {y}: built in {time.monotonic() - t0:.0f}s ({', '.join(todo[y])}); priors {sources}"
                           + (f"; norm changed, also rebuilding {added}" if added else ""))
            print(f"[{y}] built {time.monotonic() - t0:.1f}s reasons={todo[y]} priors={sources}", flush=True)
        except Exception as e:  # noqa: BLE001  (any failure falls back to the last good shards)
            traceback.print_exc()
            reason = one_line(f"{type(e).__name__}: {e}")
            failed[y] = reason
            sdir = os.path.join(staged, str(y))
            if os.path.exists(sdir):
                shutil.rmtree(sdir)
            ok = _reuse(published, y, reused)
            if ok:
                shutil.copytree(os.path.join(published, str(y)), sdir)
                stale[y] = reason
            new_seasons[str(y)] = {**prev, "ok": False}
            summary.append(f"- {y}: FAILED ({reason}); " + ("serving last good shards" if ok else "nothing published to fall back to"))
            print(f"[{y}] FAILED {reason}", flush=True)

    result: Dict[str, Any] = {
        "schema": SCHEMA, "data_version": data_version, "generated_at": generated_at,
        "rebuilt": rebuilt, "failed": {str(k): v for k, v in failed.items()}, "stale": sorted(stale),
        "out_new": new_out, "deploy_ok": False, "degraded": bool(failed), "fatal": None,
        "elapsed_s": round(time.monotonic() - t_start, 1),
    }
    published_seasons = sorted(int(d) for d in os.listdir(staged) if d.isdigit())
    if not published_seasons:
        result["fatal"] = "no season could be built and nothing was published before: not deploying"
        return _finish_run(a, result, summary, 1)

    if not rebuilt and not failed:
        # redeploy of the already published tree (live manifest differs): byte-identical copy
        shutil.rmtree(staged)
        shutil.copytree(published, staged)
        result["data_version"] = (load_json(os.path.join(published, "manifest.json"), {}) or {}).get("data_version")
        result["deploy_ok"] = True
        summary.append(f"- nothing to build; re-deploying published data {result['data_version']}")
        new_state = dict(state)
        new_state["seasons"] = {**state["seasons"], **{k: v for k, v in new_seasons.items() if v}}
        result["new_state"] = new_state
        return _finish_run(a, result, summary, 0)

    # models.json, history.json.gz, manifest.json
    seasons_meta: List[Dict[str, Any]] = []
    history_parts: List[Dict[str, Any]] = []
    for y in published_seasons:
        if y in outputs:
            o = outputs[y]
            entry, bt, hist = o.entry, o.backtest, o.history
        else:
            entry, doc = reused[y] if y in reused else _read_published_entry(published, y)
            entry = dict(entry)
            bt = doc.get("backtest") or {}
            hist = computer.history_part(doc)
        if y in stale:
            entry["stale"] = True
            entry["stale_reason"] = stale[y]
        seasons_meta.append({"entry": entry, "backtest": bt})
        history_parts.append(hist)
    manifest_meta = {
        "data_version": data_version, "generated_at": generated_at, "code_version": short_code_version(code_hash),
        "default_season": int(config["current_season"]), "default_model": config["default_model"],
        "default_region": config["default_region"], "default_watchlist": list(config["default_watchlist"]),
    }
    computer.write_global(staged, seasons_meta, history_parts, manifest_meta)
    errs = computer.validate_tree(staged, schema_dir)
    if errs:
        result["fatal"] = "final validation of the staged tree failed: " + one_line(errs[0])
        summary.append(f"- **final validation failed**: {one_line(errs[0])}")
        return _finish_run(a, result, summary, 1)

    new_state = {"data_version": data_version, "code_hash": code_hash,
                 "seasons": {**state["seasons"], **{k: v for k, v in new_seasons.items() if v}}}
    result["new_state"] = new_state
    result["deploy_ok"] = True
    summary.append(f"- data_version `{data_version}`; rebuilt {rebuilt or 'none'}; "
                   + (f"stale (last good served): {sorted(stale)}" if stale else "no stale seasons"))
    return _finish_run(a, result, summary, 0)


def _reuse(published: str, year: int, cache_: Dict[int, Any]) -> bool:
    """True if the published tree holds shards for `year` (and remember its entry + doc)."""
    entry, doc = _read_published_entry(published, year)
    if entry is None or doc is None or not os.path.isdir(os.path.join(published, str(year))):
        return False
    cache_[year] = (entry, doc)
    return True


def _finish_run(a: argparse.Namespace, result: Dict[str, Any], summary: List[str], rc: int) -> int:
    write_json(os.path.join(a.work, "run_result.json"), result)
    gh_output(a.github_output, degraded=str(bool(result["degraded"] or result["fatal"])).lower(),
              deploy_ok=str(bool(result["deploy_ok"])).lower(), data_version=result["data_version"] or "",
              failed_seasons=" ".join(result["failed"]), run_fatal=result["fatal"] or "")
    if result["fatal"]:
        summary.append(f"- **fatal: {result['fatal']}**")
        print(f"run: FATAL {result['fatal']}", file=sys.stderr)
    append_lines(a.summary, summary)
    return rc


# =============================================================================================
# finalize
# =============================================================================================
def cmd_finalize(a: argparse.Namespace) -> int:
    res = load_json(a.run_result)
    if not res or res.get("fatal") or not res.get("deploy_ok"):
        print("finalize: run did not produce a deployable tree; cache left untouched", file=sys.stderr)
        return 1
    cache = a.cache
    pub = os.path.join(cache, "published")
    tmp = pub + ".new"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    shutil.copytree(a.staged, tmp)
    if os.path.exists(pub):
        shutil.rmtree(pub)
    os.replace(tmp, pub)
    out_new = res.get("out_new")
    for y in res.get("rebuilt", []):
        src = os.path.join(out_new, str(y))
        if os.path.isdir(src):
            _copytree(src, os.path.join(cache, "out", str(y)))
    write_json(state_path(cache), res["new_state"])
    print(f"finalize: published/ = {res['data_version']}, state.json written, out/ updated for {res.get('rebuilt')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--cache", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--fetch-report")
    p.add_argument("--live-manifest")
    p.add_argument("--force", action="store_true")
    p.add_argument("--seasons", nargs="*", type=int)
    p.add_argument("--out", required=True)
    p.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--cache", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--out-data", required=True)
    r.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    r.add_argument("--computer", default="real", help="'real' (default); tests pass module:Class with PITWALL_ALLOW_STUB=1")
    r.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    r.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    r.set_defaults(fn=cmd_run)

    f = sub.add_parser("finalize")
    f.add_argument("--cache", required=True)
    f.add_argument("--staged", required=True)
    f.add_argument("--run-result", required=True)
    f.set_defaults(fn=cmd_finalize)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build REAL shards locally from raw TBA dumps (for site development; not part of the Action).

    python3 -I pipeline/tools/local_build.py --raw-dir DIR --out /home/claude/pw_real \
            --years 2022 2023 2024 2025 [--leak-cuts 0] [--report-dir DIR] [--fast-config PATH]

Reads {raw-dir}/tba_{year}.json.gz (untrusted data; the only thing read from that directory), then
runs exactly what the Action's `build.py run` runs per season: build.RealComputer.compute_season
(EPA + tuned Fast EPA via sb.model_runner and pipeline/models/fast_epa.json, per-robot facts, sb.features,
sb.backtest.score_season, shards.write_season), build.RealComputer.validate_season, then
write_global (models.json via sb.model_info, history.json.gz, manifest.json) and validate_tree.
Priors chain per SPEC 2.6: archive for 2022 (and any season whose predecessor is not built here),
computed seasons otherwise.

Leak audit: with --leak-cuts 0 (default) the stored audit of pipeline/models/fast_epa_backtest.json is
copied when its config hash matches the run (SPEC 4.5.4: "other runs copy the last result"); with
N > 0 the audit is run for real with N cuts per model and season.

Also writes feature calibration reports (WP3c) to --report-dir (default: OUT.reports, a sibling of
the data tree, never inside it): features_{year}_{model}.json.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(TOOLS)
REPO = os.path.dirname(PIPE)
for p in (PIPE,):
    if p not in sys.path:
        sys.path.insert(0, p)

import build  # noqa: E402  (pipeline/build.py, WP1)
from sb import features as sbfeatures  # noqa: E402


class _FeatureTap:
    """Stands in for `sb.features` inside RealComputer: same CONFIG/compute, plus it keeps the
    calibration diagnostics of every (season, model) it computes."""

    CONFIG = sbfeatures.CONFIG

    def __init__(self):
        self.reports = {}

    def compute(self, run, events, config):
        feats = sbfeatures.compute(run, events, config)
        rep = sbfeatures.diagnostics(run, feats)
        rep["change_examples"] = sbfeatures.change_examples(run, feats)
        self.reports[(int(run.year), run.model_id)] = rep
        return feats


class LocalComputer(build.RealComputer):
    def __init__(self, config, leak_cuts, fast_config, stored_audits):
        super().__init__(config, leak_cuts, None)
        self.fast_config = fast_config
        self.tap = _FeatureTap()
        self.stored_audits = stored_audits
        self.audit_notes = []

    def _optional(self, name):  # noqa: D401
        if name == "sb.features":
            return self.tap
        return super()._optional(name)

    def _leak_audits(self, year, base, priors, facts, backtest, runs):
        if self.leak_cuts > 0:
            return super()._leak_audits(year, base, priors, facts, backtest, runs)
        out = {}
        for m, r in runs.items():
            st = ((self.stored_audits.get(str(year)) or {}).get("summary") or {}).get(m) or {}
            if st.get("leak_audit") and (st.get("config_hash") or "") == (r.config_hash or ""):
                out[m] = st["leak_audit"]
            else:
                self.audit_notes.append(f"{year} {m}: no stored leak audit for this config hash; cuts=0")
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--leak-cuts", type=int, default=0)
    ap.add_argument("--report-dir")
    ap.add_argument("--fast-config", help="alternative Fast EPA config JSON (default pipeline/models/fast_epa.json)")
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args(argv)

    from sb import priors as sbpriors

    config = build.load_json(os.path.join(PIPE, "config.json"))
    schema_dir = os.path.join(REPO, "docs", "schema")
    out = os.path.abspath(a.out)
    report_dir = os.path.abspath(a.report_dir or out.rstrip(os.sep) + ".reports")
    stored = (build.load_json(os.path.join(PIPE, "models", "fast_epa_backtest.json"), {}) or {}).get("seasons", {})
    fast_cfg = build.load_json(a.fast_config) if a.fast_config else None

    years = sorted(set(a.years))
    paths = {y: os.path.join(a.raw_dir, f"tba_{y}.json.gz") for y in years}
    missing = [p for p in paths.values() if not os.path.isfile(p)]
    if missing:
        print("raw dump not found: " + ", ".join(missing), file=sys.stderr)
        return 2

    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    os.makedirs(report_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="pw_local_")
    new_out = os.path.join(work, "out")          # team_years.csv of built seasons = priors source
    os.makedirs(new_out)

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    generated_at = build.iso(now)
    code_hash = build.compute_code_hash(REPO)
    data_version = build.data_version_for(now, code_hash)
    archive = os.path.join(REPO, config["archive_priors"])
    computer = LocalComputer(config, a.leak_cuts, fast_cfg, stored)

    from sb.loader import read_raw

    outputs, built = {}, set()
    for y in years:
        t0 = time.monotonic()
        with open(paths[y], "rb") as f:
            inputs_hash = hashlib.sha256(f.read()).hexdigest()
        dump = read_raw(paths[y])
        priors, sources = sbpriors.build_priors(y, archive, new_out, built, False)
        meta = {"generated_at": generated_at, "data_version": data_version,
                "current_season": int(config["current_season"]), "updated_at": generated_at,
                "last_tba_change_at": None, "inputs_hash": inputs_hash, "code_hash": code_hash,
                "today": generated_at[:10]}
        o = computer.compute_season(y, dump, priors, new_out, out, meta)
        errs = computer.validate_season(out, y, schema_dir)
        if errs:
            print(f"[{y}] VALIDATION FAILED: {errs[:5]}", file=sys.stderr)
            return 1
        outputs[y] = o
        built.add(y)
        for m in ("epa", "fast"):
            rep = computer.tap.reports.get((y, m))
            if rep:
                with open(os.path.join(report_dir, f"features_{y}_{m}.json"), "w", encoding="utf-8") as f:
                    json.dump(rep, f, indent=1, sort_keys=True)
                    f.write("\n")
        print(f"[{y}] built {time.monotonic() - t0:.0f}s priors={sources} notes={o.notes}", flush=True)
        del dump

    seasons_meta = [{"entry": outputs[y].entry, "backtest": outputs[y].backtest} for y in years]
    history = [outputs[y].history for y in years]
    manifest_meta = {"data_version": data_version, "generated_at": generated_at,
                     "code_version": build.short_code_version(code_hash),
                     "default_season": int(config["current_season"]), "default_model": config["default_model"],
                     "default_region": config["default_region"],
                     "default_watchlist": list(config["default_watchlist"])}
    computer.write_global(out, seasons_meta, history, manifest_meta)
    errs = computer.validate_tree(out, schema_dir)
    if errs:
        print("final validation failed: " + "; ".join(errs[:5]), file=sys.stderr)
        return 1
    for n in computer.audit_notes:
        print("note: " + n)
    if not a.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    total = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(out) for fn in fns)
    print(f"ok: {len(years)} seasons -> {out} ({total / 1e6:.1f} MB), reports in {report_dir}, "
          f"data_version {data_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

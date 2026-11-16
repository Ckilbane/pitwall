#!/usr/bin/env python3
"""WP2 scratch build: seasons -> CSV export + data shards in a scratch directory.

    python3 -I pipeline/tests/wp2_scratch_build.py --raw-dir DIR --out /home/claude/pw_out \
        --csv-out /home/claude/pw_csv --years 2022 2023 2024 2025 [--variant copy|scaled]

Uses the stub model runner (tests/wp2_stub_runner.py) until WP3b lands, and the fallback backtest.
Priors follow SPEC 2.6 (2022 from the archive; later seasons chain from our computed seasons).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
sys.path.insert(0, HERE)

import wp2_stub_runner as stub  # noqa: E402
from sb import perrobot, priors, shards  # noqa: E402
from sb.export import export_year  # noqa: E402
from sb.loader import read_raw  # noqa: E402

ARCHIVE = os.path.join(PIPE, "priors", "archive_team_years_2016_2023.csv")


def stub_models_info():
    def m(i, label):
        return {"id": i, "label": label, "short_description": "stub", "how_it_works": ["stub"],
                "constants": {}, "settings": {}, "source": "stub", "config_hash": ""}
    return {"models": [m("epa", "EPA"), m("fast", "Fast EPA")], "tuned_on": [2022, 2023, 2024, 2025]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv-out", required=True)
    ap.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    ap.add_argument("--variant", default="scaled", choices=["copy", "scaled"])
    ap.add_argument("--archive-2022-for-2023", action="store_true",
                    help="seed 2023 from the archive 2022 (the validated way) instead of computed 2022")
    ap.add_argument("--no-robot", action="store_true")
    a = ap.parse_args(argv)

    done, seasons_meta, hist = set(), [], []
    gen = "2026-11-16T12:00:00Z"
    for y in sorted(a.years):
        raw = read_raw(os.path.join(a.raw_dir, f"tba_{y}.json.gz"))
        comp = set(done)
        if y == 2023 and a.archive_2022_for_2023:
            comp.discard(2022)
        pri, src = priors.build_priors(y, ARCHIVE, a.csv_out, comp)
        t0 = time.time()
        runs = stub.build_runs(y, raw, pri, a.variant)
        t1 = time.time()
        export_year(runs["epa"].objs, a.csv_out)
        facts = None if a.no_robot else perrobot.robot_facts(y, raw)
        meta = {"generated_at": gen, "data_version": "20261116T1200Z-0000000", "current_season": 2026}
        sf = shards.write_season(y, runs, None, None, facts, raw, a.out, meta)
        t2 = time.time()
        print(f"[{y}] priors {src}  runs {t1 - t0:.1f}s  shards {t2 - t1:.1f}s  events {sf.events_count} "
              f"event bytes {sf.events_bytes}  season {sf.files['season']['bytes']} asof {sf.files['asof']['bytes']} "
              f"traj {sf.files['traj']['bytes']}  max event {max(v['bytes'] for v in sf.events.values())}")
        done.add(y)
        seasons_meta.append({"entry": sf.manifest_entry, "backtest": sf.backtest})
        hist.append(sf.history)
    shards.write_global(a.out, seasons_meta, stub_models_info(), hist,
                        {"data_version": "20261116T1200Z-0000000", "generated_at": gen,
                         "code_version": "0000000"})
    return 0


if __name__ == "__main__":
    sys.exit(main())

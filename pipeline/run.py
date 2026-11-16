#!/usr/bin/env python3
"""Run the offline Statbotics EPA chain season by season.

    python3 run.py --years 2023 2024 2025 [2026] --raw-dir DIR --archive-dir DIR --out out/

Seasons run in ascending order.  Priors for each season (sb.priors): seasons computed in this
run, or already present in --out from an earlier run and listed in --use-computed, come from our
own out/{year}/team_years.csv; everything else from the archive team_years.csv.
Raw dumps are read from {raw-dir}/tba_{year}.json.gz (override one with --raw YEAR=PATH).
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sb.engine import run_year  # noqa: E402
from sb.export import export_year  # noqa: E402
from sb.priors import build_priors  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--archive-dir", required=True,
                    help="directory holding the archive team_years.csv, or the archive CSV file itself "
                         "(pipeline/priors/archive_team_years_2016_2023.csv)")
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--raw", action="append", default=[], metavar="YEAR=PATH",
                    help="explicit dump path for one season (e.g. 2026=/path/tba_2026.json.gz)")
    ap.add_argument("--use-computed", type=int, nargs="*", default=[],
                    help="earlier seasons already in --out to use as priors instead of the archive")
    ap.add_argument("--archive-priors", type=int, nargs="*", default=[],
                    help="force these prior seasons to come from the archive even if computed here")
    ap.add_argument("--include-offseason-priors", action="store_true",
                    help="keep archive team_years flagged offseason (default: dropped)")
    args = ap.parse_args(argv)

    raw_over = {}
    for s in args.raw:
        y, p = s.split("=", 1)
        raw_over[int(y)] = p

    computed = set(args.use_computed)
    os.makedirs(args.out, exist_ok=True)
    for year in sorted(set(args.years)):
        raw_path = raw_over.get(year) or os.path.join(args.raw_dir, f"tba_{year}.json.gz")
        if not os.path.exists(raw_path):
            print(f"[{year}] raw dump not found: {raw_path}", file=sys.stderr)
            return 2
        prior_computed = computed - set(args.archive_priors)
        priors, sources = build_priors(
            year, args.archive_dir, args.out, prior_computed, args.include_offseason_priors
        )
        t0 = time.time()
        objs = run_year(year, raw_path, priors)
        counts = export_year(objs, args.out)
        meta = {
            "year": year,
            "raw": os.path.basename(raw_path),
            "prior_sources": {str(k): v for k, v in sources.items()},
            "prior_team_counts": {str(k): len(v) for k, v in priors.items()},
            "include_offseason_priors": bool(args.include_offseason_priors),
            "rows": counts,
        }
        with open(os.path.join(args.out, str(year), "run_meta.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
            f.write("\n")
        tys = list(objs[1].values())
        best = max(tys, key=lambda t: (t.auto_epa if t.auto_epa is not None else -1e9, -t.team))
        print(f"[{year}] priors {sources}  teams {len(tys)}  matches {len(objs[4])}  "
              f"max auto_epa {float(best.auto_epa)} ({best.team})  {time.time() - t0:.1f}s")
        computed.add(year)
    return 0


if __name__ == "__main__":
    sys.exit(main())

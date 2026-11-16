#!/usr/bin/env python3
"""2023 replay mismatch experiments (no vendor edits; variants are input/monkeypatch only).

    python3 validate/experiments_2023.py --raw DIR/tba_2023.json.gz --archive-dir DIR --out out

E0 baseline        archive priors 2019/2020/2022 without offseason-flagged team_years (run.py default)
E1 +offseason      archive priors including offseason-flagged team_years (what the archive DB had)
E2 norm pool       E1, then norm_epa recomputed with upstream get_epa_to_norm_epa_func on our
                   epa list + the archive's 125 offseason-only 2023 team_years' epa (the archive
                   fitted its norm distribution on all 3409 team_years)
Then: residual split by whether the team played an archive week-0/in-season offseason event before
its first official match (those matches are in the archive's EPA stream but not in our dumps).
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "validate"))

from compare_archive import rd, stats  # noqa: E402
from sb.engine import run_year  # noqa: E402
from sb.priors import build_priors  # noqa: E402
import src.models.epa.unitless as U  # noqa: E402
from src.utils.utils import r  # noqa: E402

F = ["epa", "auto_epa", "teleop_epa", "endgame_epa", "norm_epa", "epa_start"]


def frame(objs):
    return pd.DataFrame([{**{k: float(getattr(t, k)) for k in F}, "team": t.team}
                         for t in objs[1].values()]).set_index("team")


def report(name, df, arch, subset=None):
    idx = df.index.intersection(arch.index)
    if subset is not None:
        idx = idx.intersection(subset)
    parts = []
    for k in F:
        s = stats(df.loc[idx, k], arch.loc[idx, k])
        parts.append(f"{k}: MAE {s['mae']:.4f} max {s['max']:.2f} exact {s['exact']:.3f}")
    print(f"{name} (n={len(idx)})\n   " + "\n   ".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--archive-dir", required=True)
    a = ap.parse_args()
    aty_all = rd(os.path.join(a.archive_dir, "team_years.csv"))
    a23 = aty_all[aty_all.year == 2023]
    arch = a23[a23.offseason == "f"].set_index("team")

    res = {}
    for name, inc in [("E0 baseline", False), ("E1 +offseason priors", True)]:
        P, src = build_priors(2023, a.archive_dir, None, (), include_offseason=inc)
        objs = run_year(2023, a.raw, P)
        res[name] = (objs, frame(objs))
        report(name, res[name][1], arch)

    objs, df = res["E1 +offseason priors"]
    epas = [float(t.epa) for t in objs[1].values()]
    extra = a23[a23.offseason == "t"].epa.astype(float).tolist()
    f = U.get_epa_to_norm_epa_func(epas + extra)
    df2 = df.copy()
    df2["norm_epa"] = [r(f(float(objs[1][f"{t}_2023"].epa))) for t in df2.index]
    report("E2 E1 + norm fit on 3409 team_years", df2, arch)

    # week-0 / in-season offseason participation in the archive stream
    ev = rd(os.path.join(a.archive_dir, "events.csv"))
    off = ev[(ev.year == 2023) & (ev.offseason == "t")]
    champs_end = ev[(ev.year == 2023) & (ev.type == "einstein")].time.max()
    early = set(off[off.time < champs_end].key)
    atm = rd(os.path.join(a.archive_dir, "team_matches.csv"))
    atm = atm[atm.year == 2023]
    w0 = set(atm[atm.event.isin(early)].team)
    print(f"\nteams with archive offseason matches before Einstein (week-0 scrimmages etc.): {len(w0)} "
          f"in {len(early)} events: {sorted(early)}")
    clean = df.index.difference(pd.Index(sorted(w0)))
    report("E1 restricted to teams WITHOUT early offseason matches", df, arch, clean)
    report("E1 restricted to teams WITH early offseason matches", df, arch, pd.Index(sorted(w0)))
    report("E2 restricted to teams WITHOUT early offseason matches", df2, arch, clean)


if __name__ == "__main__":
    main()

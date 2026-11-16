#!/usr/bin/env python3
"""Compare our computed seasons with the March-2024 Statbotics archive.

    python3 -I validate/compare_archive.py --out out --archive-dir DIR [--year 2023] [--section A C D]

A: team_years (non-offseason archive rows) field errors + per-match pre-match auto_epa error by
   match index (archive team_matches.epa/auto_epa are PRE-match values: first row == epa_start).
C: 2024 epa_start vs archive 2024 (week-1 snapshot): raw and implied prior norm_epa.
D: internal identities on our outputs (all years present in --out).
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

FIELDS = ["epa", "auto_epa", "teleop_epa", "endgame_epa", "norm_epa", "unitless_epa",
          "epa_start", "epa_pre_champs", "epa_max"]


def rd(path, **kw):
    df = pd.read_csv(path, na_values=["NULL", ""], keep_default_na=False, low_memory=False, **kw)
    if "team" in df.columns and not pd.api.types.is_numeric_dtype(df["team"]):
        # archive has a few non-numeric team ids (e.g. "9999B" offseason B-teams): drop them
        df["team"] = pd.to_numeric(df["team"], errors="coerce")
        df = df[df["team"].notna()].copy()
        df["team"] = df["team"].astype("int64")
    return df


def stats(a, b):
    m = a.notna() & b.notna()
    a, b = a[m].astype(float), b[m].astype(float)
    e = (a - b).abs()
    return dict(n=int(m.sum()), mae=e.mean(), p95=e.quantile(0.95), max=e.max(),
                bias=(a - b).mean(), corr=np.corrcoef(a, b)[0, 1] if len(a) > 2 else np.nan,
                exact=(e < 0.005).mean())


def fmt_table(rows, cols):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(
            (f"{v:.4f}" if isinstance(v, float) else str(v)) for v in r) + " |")
    return "\n".join(out)


def section_a(out, arch, year):
    ours = rd(os.path.join(out, str(year), "team_years.csv")).set_index("team")
    aty = rd(os.path.join(arch, "team_years.csv"))
    aty = aty[(aty.year == year) & (aty.offseason == "f")].set_index("team")
    common = ours.index.intersection(aty.index)
    print(f"\n## A. {year} team_years vs archive (non-offseason)")
    print(f"ours {len(ours)}, archive {len(aty)}, common {len(common)}, "
          f"only ours {len(ours.index.difference(aty.index))}, only archive {len(aty.index.difference(ours.index))}")
    rows = []
    for f in FIELDS:
        s = stats(ours.loc[common, f], aty.loc[common, f])
        rows.append([f, s["n"], s["mae"], s["p95"], s["max"], s["bias"], s["corr"], s["exact"],
                     float(ours[f].max()), float(aty[f].max())])
    print(fmt_table(rows, ["field", "n", "MAE", "p95", "max", "bias(ours-arch)", "corr",
                           "frac |e|<.005", "our max", "arch max"]))

    # per match: our pre_* vs archive team_matches (pre-match values)
    atm = rd(os.path.join(arch, "team_matches.csv"))
    atm = atm[atm.year == year]
    ev = rd(os.path.join(arch, "events.csv"))
    off = set(ev[(ev.year == year) & (ev.offseason == "t")].key)
    tm = rd(os.path.join(out, str(year), "team_matches.csv"))
    tm = tm[["seq", "match", "team", "week", "pre_epa", "pre_auto_epa", "auto_epa", "epa"]]
    tm["k"] = tm.groupby("team").cumcount() + 1
    j = tm.merge(atm[["team", "match", "epa", "auto_epa"]].rename(
        columns={"epa": "a_pre_epa", "auto_epa": "a_pre_auto"}), on=["team", "match"], how="inner")
    j["e_auto"] = (j.pre_auto_epa - j.a_pre_auto).abs()
    j["e_epa"] = (j.pre_epa - j.a_pre_epa).abs()
    print(f"\nper-match join: ours {len(tm)} rows, archive on-season "
          f"{(~atm.event.isin(off)).sum()} rows, joined {len(j)}")
    bins = [0, 1, 2, 3, 5, 8, 12, 20, 40, 1000]
    j["kb"] = pd.cut(j.k, bins)
    g = j.groupby("kb", observed=True).agg(n=("e_auto", "size"), auto_mae=("e_auto", "mean"),
                                           auto_p95=("e_auto", lambda x: x.quantile(.95)),
                                           epa_mae=("e_epa", "mean"))
    print("\npre-match error by team match index k (k=1 is the prior, before any update)")
    print(g.to_string())
    gw = j.groupby("week").agg(n=("e_auto", "size"), auto_mae=("e_auto", "mean"), epa_mae=("e_epa", "mean"))
    print("\nby week\n" + gw.to_string())
    # did the archive see offseason matches before a team's on-season match?
    atm_s = atm.sort_values(["team", "time", "match"], kind="stable")
    atm_s["is_off"] = atm_s.event.isin(off)
    atm_s["off_before"] = atm_s.groupby("team").is_off.cumsum()
    on = atm_s[~atm_s.is_off]
    j2 = j.merge(on[["team", "match", "off_before"]], on=["team", "match"])
    print("\nmean |pre auto err| by whether archive already processed offseason matches for the team:")
    print(j2.groupby(j2.off_before > 0).e_auto.agg(["size", "mean"]).to_string())
    # sample teams
    print("\nsample teams (k: ours pre_auto / archive pre_auto):")
    for t in [254, 1323, 6328, 1690, 2056, 1218, 118, 4414]:
        s = j[j.team == t].sort_values("k")
        if len(s) == 0:
            continue
        pick = s[s.k.isin([1, 2, 5, 10, 20, s.k.max()])]
        print(f"  {t}: " + "  ".join(f"k{int(r.k)} {r.pre_auto_epa:.2f}/{r.a_pre_auto:.2f}" for r in pick.itertuples()))
    return j


def section_c(out, arch, year=2024):
    p = os.path.join(out, str(year), "team_years.csv")
    if not os.path.exists(p):
        return
    ours = rd(p).set_index("team")
    oy = rd(os.path.join(out, str(year), "years.csv")).iloc[0]
    aty = rd(os.path.join(arch, "team_years.csv"))
    aty = aty[aty.year == year].set_index("team")
    ay = rd(os.path.join(arch, "years.csv")).set_index("year").loc[year]
    common = ours.index.intersection(aty.index)
    print(f"\n## C. {year} epa_start (prior) vs archive week-1 snapshot")
    print(f"ours {len(ours)}, archive {len(aty)}, common {len(common)}")
    print(f"year means used by init: ours no_foul_mean {oy.no_foul_mean:.2f} score_sd {oy.score_sd:.2f}; "
          f"archive no_foul_mean {ay.no_foul_mean:.2f} score_sd {ay.score_sd:.2f} ({int(ay['count'])} matches)")
    s = stats(ours.loc[common, "epa_start"], aty.loc[common, "epa_start"])
    print("raw epa_start: " + ", ".join(f"{k} {v:.4f}" for k, v in s.items()))
    # implied prior norm epa: epa_start = nf/3 + sd * (norm - 1500)/250  (unless floored at 0)
    zo = (ours.loc[common, "epa_start"] - oy.no_foul_mean / 3) / oy.score_sd
    za = (aty.loc[common, "epa_start"] - ay.no_foul_mean / 3) / ay.score_sd
    no, na = 1500 + 250 * zo, 1500 + 250 * za
    s = stats(no, na)
    print("implied prior norm_epa (removes year-mean difference): " + ", ".join(f"{k} {v:.4f}" for k, v in s.items()))
    d = (no - na).abs()
    print(f"  |diff| <= 1 (rounding of epa_start) : {(d <= 1).mean():.4f};  > 5 : {(d > 5).sum()} teams")
    big = d.sort_values(ascending=False).head(8)
    print("  largest: " + ", ".join(f"{t}: ours {no[t]:.0f} arch {na[t]:.0f}" for t in big.index))
    return no, na


def section_d(out):
    print("\n## D. internal identities")
    for y in sorted(int(x) for x in os.listdir(out) if x.isdigit()):
        ty = rd(os.path.join(out, str(y), "team_years.csv"))
        names = json.load(open(os.path.join(out, str(y), "comp_names.json")))
        acomps = names["auto_comps"]
        e1 = (ty.auto_epa + ty.teleop_epa + ty.endgame_epa - ty.epa).abs()
        sa = sum(ty[f"{c}_epa"] for c in acomps)
        e2 = (sa - ty.auto_epa)
        tm = rd(os.path.join(out, str(y), "team_matches.csv"))
        tm = tm[tm.epa.notna()]
        e3 = (tm.auto_epa + tm.teleop_epa + tm.endgame_epa - tm.epa).abs()
        ma = rd(os.path.join(out, str(y), "match_auto.csv"))
        ma = ma[ma.status == "Completed"]
        cols = [names["names"][c] for c in acomps]
        e4 = (ma[cols].sum(axis=1) - ma.auto_points)
        print(f"{y}: auto comps {acomps} = {cols}")
        print(f"   team_years  |auto+teleop+endgame-epa|: mean {e1.mean():.4f} max {e1.max():.3f}")
        print(f"   team_matches |auto+teleop+endgame-epa|: mean {e3.mean():.4f} max {e3.max():.3f}")
        print(f"   team_years  sum(auto comps)-auto_epa: mean {e2.mean():.3f} mean|.| {e2.abs().mean():.3f} max|.| {e2.abs().max():.3f}")
        print(f"   match_auto  sum(auto comps)-auto_points: mean {e4.mean():.3f} share exact {(e4.abs()<1e-9).mean():.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--section", nargs="*", default=["A", "C", "D"])
    a = ap.parse_args()
    pd.set_option("display.width", 200)
    if "A" in a.section:
        section_a(a.out, a.archive_dir, a.year)
    if "C" in a.section:
        section_c(a.out, a.archive_dir)
    if "D" in a.section:
        section_d(a.out)


if __name__ == "__main__":
    main()

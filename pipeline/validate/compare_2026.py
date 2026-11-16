#!/usr/bin/env python3
"""Compare our out/2026 with the real Statbotics 2026 exports.

    python3 -I validate/compare_2026.py --out out --insights DIR/2026_insights.csv \
        --breakdown DIR/2026_epa_breakdown.csv

Column mapping (reference -> ours):
  2026_insights.csv   num -> team; total_epa -> epa; norm_epa; unitless_epa; auto_epa; teleop_epa;
                      endgame_epa; rp_1_epa; rp_2_epa; rp_3_epa; epa_rank -> total_epa_rank;
                      winrate; record "W-L-T" -> wins-losses-ties; next_event_key (info only)
  2026_epa_breakdown  total_points -> epa; auto_points -> auto_epa; teleop_points -> teleop_epa;
                      endgame_points -> endgame_epa; auto_fuel -> comp_0_epa; auto_tower -> comp_1_epa;
                      transition_fuel -> comp_2_epa; first_shift_fuel -> comp_3_epa;
                      second_shift_fuel -> comp_4_epa; endgame_fuel -> comp_5_epa;
                      endgame_tower -> comp_6_epa;
                      derived: teleop_fuel -> comp_2+comp_3+comp_4 (the export's teleop_fuel equals
                      teleop_points; NOTE vendored breakdown.py derived_breakdown[2026]["teleop_fuel"]
                      also adds endgame_fuel, which does not match the export),
                      total_fuel -> comp_0+comp_2+comp_3+comp_4+comp_5, total_tower -> comp_1+comp_6.
The reference values are rounded to 0.1 (norm/unitless to 1), so MAE has a floor of ~0.025.
The insights export lists next_event = a champs division, i.e. it may be a snapshot taken before
or during champs; every field is therefore compared twice: against our end-of-season vector
(team_years) and against our last post-match vector before week 8 (pre-champs, from
team_matches.csv, same rule as upstream agg.process_year_epas).
"""
import argparse
import os

import numpy as np
import pandas as pd

INS = {"total_epa": "epa", "norm_epa": "norm_epa", "unitless_epa": "unitless_epa",
       "auto_epa": "auto_epa", "teleop_epa": "teleop_epa", "endgame_epa": "endgame_epa",
       "rp_1_epa": "rp_1_epa", "rp_2_epa": "rp_2_epa", "rp_3_epa": "rp_3_epa",
       "epa_rank": "total_epa_rank", "winrate": "winrate"}
BD = {"total_points": "epa", "auto_points": "auto_epa", "teleop_points": "teleop_epa",
      "endgame_points": "endgame_epa", "auto_fuel": "comp_0_epa", "auto_tower": "comp_1_epa",
      "transition_fuel": "comp_2_epa", "first_shift_fuel": "comp_3_epa",
      "second_shift_fuel": "comp_4_epa", "endgame_fuel": "comp_5_epa",
      "endgame_tower": "comp_6_epa", "teleop_fuel": "teleop_fuel", "total_fuel": "total_fuel",
      "total_tower": "total_tower"}
PRE_CHAMPS_OK = {"epa", "auto_epa", "teleop_epa", "endgame_epa", "rp_1_epa", "rp_2_epa",
                 "rp_3_epa", "teleop_fuel", "total_fuel", "total_tower"} | {f"comp_{i}_epa" for i in range(10)}


def derive(df):
    df = df.copy()
    df["teleop_fuel"] = df.comp_2_epa + df.comp_3_epa + df.comp_4_epa
    df["total_fuel"] = df.comp_0_epa + df.comp_2_epa + df.comp_3_epa + df.comp_4_epa + df.comp_5_epa
    df["total_tower"] = df.comp_1_epa + df.comp_6_epa
    return df


def stat_rows(ref, ours, mapping, label):
    rows = []
    common = ref.index.intersection(ours.index)
    for rc, oc in mapping.items():
        if rc not in ref.columns or oc not in ours.columns:
            continue
        a = pd.to_numeric(ref.loc[common, rc], errors="coerce")
        b = pd.to_numeric(ours.loc[common, oc], errors="coerce")
        m = a.notna() & b.notna()
        if m.sum() == 0:
            continue
        e = (b[m] - a[m]).abs()
        corr = np.corrcoef(a[m], b[m])[0, 1] if m.sum() > 2 else float("nan")
        rows.append((label, rc, oc, int(m.sum()), e.mean(), e.quantile(.95), e.max(), (b[m] - a[m]).mean(), corr))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--insights", required=True)
    ap.add_argument("--breakdown", required=True)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    d = os.path.join(a.out, "2026")
    ty = pd.read_csv(os.path.join(d, "team_years.csv")).set_index("team")
    ty = derive(ty)
    tm = pd.read_csv(os.path.join(d, "team_matches.csv"), low_memory=False)
    tm = tm[tm.epa.notna() & (tm.week < 8)]
    pre = derive(tm.groupby("team").last())  # rows are in processing (time) order

    ins = pd.read_csv(a.insights, encoding="utf-8-sig").set_index("num")
    bd = pd.read_csv(a.breakdown, encoding="utf-8-sig").set_index("num")
    rec = ins.record.astype(str).str.split("-", expand=True).astype(int)
    print(f"reference teams: insights {len(ins)}, breakdown {len(bd)}; ours {len(ty)}; "
          f"missing from ours: {sorted(set(ins.index) - set(ty.index))}")
    w = ty.reindex(ins.index)
    print(f"record W/L/T exact match: {((w.wins == rec[0]) & (w.losses == rec[1]) & (w.ties == rec[2])).mean():.3f}")

    rows = []
    for label, ours in (("end", ty), ("pre_champs", pre)):
        rows += stat_rows(ins, ours, {k: v for k, v in INS.items() if label == "end" or v in PRE_CHAMPS_OK}, label + " insights")
        rows += stat_rows(bd, ours, {k: v for k, v in BD.items() if label == "end" or v in PRE_CHAMPS_OK}, label + " breakdown")
    out = pd.DataFrame(rows, columns=["vs", "ref col", "our col", "n", "MAE", "p95", "max", "bias", "corr"])
    pd.set_option("display.width", 200)
    print(out.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    ref_top = ins.sort_values(["auto_epa"], ascending=False).head(a.top)
    our_top = ty.sort_values(["auto_epa"], ascending=False).head(a.top)
    print(f"\ntop-{a.top} auto_epa (reference covers {len(ins)} teams only)")
    print(f"{'#':>3} {'ref team':>8} {'ref':>6} {'ours(same team)':>15} {'pre_ch':>7} | {'our team':>8} {'ours':>6} {'ref(same)':>9}")
    for i in range(a.top):
        rt = ref_top.index[i] if i < len(ref_top) else None
        ot = our_top.index[i]
        rv = ref_top.auto_epa.iloc[i] if rt is not None else float("nan")
        ov_same = ty.auto_epa.get(rt, float("nan"))
        pv_same = pre.auto_epa.get(rt, float("nan"))
        rv_same = ins.auto_epa.get(ot, float("nan"))
        print(f"{i+1:>3} {rt!s:>8} {rv:>6.1f} {ov_same:>15.2f} {pv_same:>7.2f} | {ot:>8} {our_top.auto_epa.iloc[i]:>6.2f} {rv_same:>9.1f}")


if __name__ == "__main__":
    main()

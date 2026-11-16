"""Round-trip checker: shards in data_dir/{year} against the CSV export in csv_dir/{year} (SPEC 3, WP2 acceptance 2).

    roundtrip(csv_dir, data_dir, year, model="epa") -> {"rows": {...}, "bad": {column: count}}

Compares every value of team_years, events, team_events, matches and team_matches with the CSV
(`round(csv_value, nd)` against the shard value, nd = the SPEC 3.1 decimals), reconstructs every
team_matches `pre_*` with `shards.derive_pre` (event file only) and compares to the CSV `pre_*`,
and rebuilds match_auto.csv from the event files.  Empty `bad` means exact.
Only the `epa` model has a CSV; `fast` columns are checked structurally elsewhere.
"""
import csv
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sb import shards  # noqa: E402
from sb.jsonout import read_gz  # noqa: E402

TEXT = {"name", "team_name", "country", "state", "district", "next_event_key", "key", "comp_level",
        "status", "video", "winner", "epa_winner", "type", "start_date", "end_date", "match", "event",
        "alliance"}


def _rd(csv_dir: str, year: int, name: str) -> List[Dict[str, str]]:
    with open(os.path.join(csv_dir, str(year), f"{name}.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _eq(col: str, c: str, v: Any, nd: Optional[int]) -> bool:
    if c == "":
        return v is None
    if v is None:
        return False
    if col in TEXT:
        return str(v) == c
    if c in ("true", "false"):
        return v is (c == "true")
    try:
        f = int(c)
        if nd is None:
            return f == v
        return round(float(f), nd) == v
    except ValueError:
        pass
    try:
        f = float(c)
    except ValueError:
        return str(v) == c
    if nd is None:
        return f == v
    return round(f, nd) == v


def _near(col: str, c: str, v: Any) -> bool:
    """Pre/post vectors are exported with 3 decimals (rp 4); the CSV holds upstream's 2-decimal rounding of
    the same number, so equal up to double rounding (<= 0.0051) and exact for rp."""
    if c == "" or v is None:
        return c == "" and v is None
    if col.startswith("rp_"):
        return round(float(c), 4) == v
    return abs(float(c) - v) <= 0.0051 and round(v, 3) == v


def roundtrip(csv_dir: str, data_dir: str, year: int, model: str = "epa") -> Dict[str, Any]:
    bad: Dict[str, int] = {}
    rows: Dict[str, int] = {}

    def note(k: str) -> None:
        bad[k] = bad.get(k, 0) + 1

    sdir = os.path.join(data_dir, str(year))
    s = read_gz(os.path.join(sdir, "season.json.gz"))
    VK, BK = s["keys"]["vector"], s["keys"]["breakdown"]
    vnd = lambda k: 4 if k.startswith("rp_") else 2  # noqa: E731

    # ---- team_years
    T = s["teams"]
    idx = {t: i for i, t in enumerate(T["cols"]["team"])}
    ty = _rd(csv_dir, year, "team_years")
    rows["team_years"] = len(ty)
    if len(ty) != len(idx):
        note("ty.count")
    M = T["models"][model]
    for r in ty:
        i = idx[int(r["team"])]
        for k in ("name", "country", "state", "district", "rookie_year", "wins", "losses", "ties", "count",
                  "next_event_key", "next_event_week"):
            if not _eq(k, r[k], T["cols"][k][i], None):
                note("ty." + k)
        if not _eq("winrate", r["winrate"], T["cols"]["winrate"][i], 4):
            note("ty.winrate")
        for k in ["epa_start", "epa_pre_champs", "epa_max"] + VK:
            if not _eq(k, r[k], M[k][i], vnd(k)):
                note("ty." + k)
        for k in ("unitless_epa", "norm_epa"):
            if not _eq(k, r[k], M[k][i], 0):
                note("ty." + k)
        for k in shards._TY_RANK_FIELDS:
            if not _eq(k, r[k], M[k][i], 4 if k.endswith("percentile") else None):
                note("ty." + k)
    # ---- events
    E = s["events"]
    ei = {k: i for i, k in enumerate(E["cols"]["key"])}
    ev = _rd(csv_dir, year, "events")
    rows["events"] = len(ev)
    if len(ev) != len(ei):
        note("ev.count")
    for r in ev:
        i = ei[r["key"]]
        for k in ("name", "week", "type", "district", "state", "country", "start_date", "end_date", "status",
                  "num_teams", "qual_matches", "current_match", "video"):
            if not _eq(k, r[k], E["cols"][k][i], None):
                note("ev." + k)
        for k in shards._EVENT_MODEL_FIELDS:
            nd = 2 if k in ("epa_max", "epa_top_8", "epa_top_24", "epa_mean", "epa_sd") else shards._metric_nd(k)
            if not _eq(k, r[k], E["models"][model][k][i], nd):
                note("ev." + k)
    # ---- per event
    by_te = {(int(r["team"]), r["event"]): r for r in _rd(csv_dir, year, "team_events")}
    by_m = {r["key"]: r for r in _rd(csv_dir, year, "matches")}
    tm_by_ev: Dict[str, List[Dict[str, str]]] = {}
    for r in _rd(csv_dir, year, "team_matches"):
        tm_by_ev.setdefault(r["event"], []).append(r)
    ma_by_ev: Dict[str, Dict] = {}
    for r in _rd(csv_dir, year, "match_auto"):
        ma_by_ev.setdefault(r["event"], {})[(r["match"], r["alliance"])] = r
    names, ac = s["keys"]["names"], s["keys"]["auto_comps"]
    n_te = n_m = n_tm = n_ma = n_pre = 0
    for key, i in ei.items():
        if E["cols"]["file_hash"][i] is None:
            if tm_by_ev.get(key) or any(k[1] == key for k in by_te):
                note("event.missing_file")
            continue
        d = read_gz(os.path.join(sdir, "events", f"{key}.json.gz"))
        te = d["team_events"]
        for j, t in enumerate(te["cols"]["team"]):
            n_te += 1
            r = by_te[(t, key)]
            for k in ("team_name", "first_event", "wins", "losses", "ties", "count", "qual_wins", "qual_losses",
                      "qual_ties", "qual_count", "rps", "rank", "num_teams"):
                if not _eq(k, r[k], te["cols"][k][j], None):
                    note("te." + k)
            for k in ("winrate", "qual_winrate", "rps_per_match"):
                if not _eq(k, r[k], te["cols"][k][j], 4):
                    note("te." + k)
            for k in ["epa_start", "epa_pre_elim", "epa_mean", "epa_max"] + VK:
                if not _eq(k, r[k], te["models"][model][k][j], vnd(k)):
                    note("te." + k)
            for k in ("unitless_epa", "norm_epa"):
                if not _eq(k, r[k], te["models"][model][k][j], 0):
                    note("te." + k)
        m = d["matches"]
        C = m["cols"]
        for j, mk in enumerate(C["key"]):
            n_m += 1
            r = by_m[mk]
            for k in ("comp_level", "set_number", "match_number", "elim", "time", "predicted_time", "status",
                      "video", "seq", "winner"):
                if not _eq(k, r[k], C[k][j], None):
                    note("m." + k)
            for a in ("red", "blue"):
                if [int(r[f"{a}_{n}"]) for n in (1, 2, 3) if r[f"{a}_{n}"] != ""] != C[a][j]:
                    note("m." + a)
                for suffix in ("dq", "surrogate"):
                    if [int(x) for x in r[f"{a}_{suffix}"].split(",") if x] != C[f"{a}_{suffix}"][j]:
                        note(f"m.{suffix}")
                for bk in BK:
                    c = r[f"{a}_{bk}"]
                    if bk == "score" and c == "-1":
                        c = ""
                    if not _eq(f"{a}_{bk}", c, C[f"{a}_{bk}"][j], None):
                        note("m." + bk)
            MM = m["models"][model]
            specs = [("epa_winner", None), ("epa_win_prob", 4), ("epa_red_score_pred", 2), ("epa_blue_score_pred", 2)]
            specs += [(f"epa_{a}_rp_{n}_pred", 4) for a in ("red", "blue") for n in (1, 2, 3)]
            for k, nd in specs:
                if not _eq(k, r[k], MM[k][j], nd):
                    note("m." + k)
        tm = d["team_matches"]
        rws = tm_by_ev.get(key, [])
        if len(rws) != tm["n"]:
            note("tm.n")
            continue
        pre = shards.derive_pre(d, model, VK)
        for j, r in enumerate(rws):
            n_tm += 1
            if int(r["team"]) != tm["cols"]["team"][j] or r["match"] != C["key"][tm["cols"]["m"][j]]:
                note("tm.identity")
            for q, k in enumerate(VK):
                if not _near(k, r[k], tm["models"][model][k][j]):
                    note("tm.post." + k)
                if not _near(k, r["pre_" + k], pre[j][q]):
                    note("tm.pre." + k)
            n_pre += 1
        # match_auto
        pre_by = {(C["key"][tm["cols"]["m"][j]], tm["cols"]["alliance"][j], tm["cols"]["slot"][j]): pre[j]
                  for j in range(tm["n"])}
        mar = ma_by_ev.get(key, {})
        for j, mk in enumerate(C["key"]):
            for ai, a in enumerate(("red", "blue")):
                r = mar[(mk, a)]
                n_ma += 1
                auto = C[f"{a}_auto"][j]
                if not _eq("auto_points", r["auto_points"], auto, None):
                    note("ma.auto_points")
                cv = [C[f"{a}_{c}"][j] for c in ac]
                for c, v in zip(ac, cv):
                    if not _eq(names[c], r[names[c]], v, None):
                        note("ma." + c)
                rest = None if auto is None else auto - sum(v or 0 for v in cv)
                if not _eq("auto_not_in_comps", r["auto_not_in_comps"], rest, None):
                    note("ma.auto_not_in_comps")
                pa = [pre_by[(mk, ai, sl)][1] for sl in (1, 2, 3) if (mk, ai, sl) in pre_by]
                pred = round(sum(pa), 2) if pa and all(x is not None for x in pa) else None
                if (pred is None) != (r["pred_auto"] == "") or (
                        pred is not None and abs(float(r["pred_auto"]) - pred) > 0.0201):
                    note("ma.pred_auto")
    rows.update({"team_events": n_te, "matches": n_m, "team_matches": n_tm, "pre_rows": n_pre,
                 "match_auto": n_ma})
    return {"rows": rows, "bad": bad}


if __name__ == "__main__":
    import json

    csv_dir, data_dir = sys.argv[1], sys.argv[2]
    for y in [int(a) for a in sys.argv[3:]]:
        print(y, json.dumps(roundtrip(csv_dir, data_dir, y)))

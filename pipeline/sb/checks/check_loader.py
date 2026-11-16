"""Acceptance checks for sb.loader (run: python3 -I sb/checks/check_loader.py [--raw DIR] [--arch DIR]).

Reads the real Statbotics archive (older comp_1..comp_18 schema) and compares only
schema-independent fields.  Prints a report; exit status 0 always (it is a report, not a test).
"""
import argparse
import ast
import hashlib
import json
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from sb.loader import load_year_full  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--raw", default="/mnt/user-data/uploads/IDM/sbrebuild/raw")
ap.add_argument("--arch", default="/mnt/user-data/uploads/Downloads/statbotics_v3_csvs")
ap.add_argument("--seed", type=int, default=1218)
ap.add_argument("--only", default="")
args = ap.parse_args()
want = set(args.only.split(",")) if args.only else {"stats", "counts", "sample", "years", "ties", "det", "models", "district", "synthetic"}


def raw_path(y):
    return f"{args.raw}/tba_{y}.json.gz"


def objs_digest(objs, teams):
    """Stable digest of everything load_year produces."""
    h = hashlib.sha256()
    year, tys, evs, tes, ms, _ = objs
    h.update(repr(year).encode())
    for d in (tys, evs, tes, ms):
        for k, v in d.items():  # insertion order is part of the contract
            h.update(k.encode())
            h.update(repr(v).encode())
    for t in teams:
        h.update(repr(t).encode())
    return h.hexdigest()


R = load_year_full(2023, raw_path(2023))
year, tys, evs, tes, ms, _ = R.objs
print("== 2023 loaded: team_years=%d events=%d team_events=%d matches=%d teams=%d" % (len(tys), len(evs), len(tes), len(ms), len(R.teams)))
print("deviation log:")
for d in R.deviations:
    print("  -", d)
print("clean_breakdown ERROR lines:", len(R.clean_breakdown_errors))

# --------------------------------------------------------------------------- 1. year stats
if "stats" in want:
    print("\n== 1. Year stats vs archive years.csv (2023)")
    ay = pd.read_csv(f"{args.arch}/years.csv")
    ay = ay[ay.year == 2023].iloc[0]
    for f in ["score_mean", "score_sd", "foul_mean", "no_foul_mean", "auto_mean", "teleop_mean", "endgame_mean"]:
        a, b = float(ay[f]), float(getattr(year, f))
        print(f"  {f:14s} loader={b:8.2f} archive={a:8.2f} diff={b-a:+.4f} {'OK' if abs(a-b) <= 0.01 else 'MISMATCH'}")
    print("  (other loader means) rp_1=%.2f rp_2=%.2f rp_3=%.2f tiebreaker=%.2f" % (year.rp_1_mean, year.rp_2_mean, year.rp_3_mean, year.tiebreaker_mean))
    print("  archive rp_1=%.2f rp_2=%.2f tiebreaker=%.2f" % (ay.rp_1_mean, ay.rp_2_mean, ay.tiebreaker_mean))

# --------------------------------------------------------------------------- 2. counts / keys
am = None
if {"counts", "sample"} & want:
    am = pd.read_csv(f"{args.arch}/matches.csv", low_memory=False)
    am = am[am.year == 2023].copy()
    am["offseason"] = am["offseason"].astype(str).str.lower().isin(["t", "true", "1"])
if "counts" in want:
    print("\n== 2. Counts vs archive (2023)")
    from src.tba.constants import DISTRICT_MAPPING
    ae = pd.read_csv(f"{args.arch}/events.csv", low_memory=False)
    ae = ae[ae.year == 2023].copy()
    ae["district"] = ae["district"].map(lambda d: DISTRICT_MAPPING.get(d, d) if isinstance(d, str) else d)  # archive predates chs->fch
    ae["offseason"] = ae["offseason"].astype(str).str.lower().isin(["t", "true", "1"])
    ae_non = ae[~ae.offseason]
    print("  archive events total=%d non-offseason=%d (types %s)" % (len(ae), len(ae_non), ae_non.type.value_counts().to_dict()))
    print("  loader  events=%d (types %s)" % (len(evs), pd.Series([e.type.value for e in evs.values()]).value_counts().to_dict()))
    ek_a, ek_l = set(ae_non.key), set(evs)
    print("  event keys only in archive:", sorted(ek_a - ek_l), " only in loader:", sorted(ek_l - ek_a))
    # event fields
    m = ae_non.set_index("key")
    cmp = {"week": 0, "time": 0, "type": 0, "district": 0, "state": 0, "country": 0, "start_date": 0, "name": 0, "num_teams": 0, "qual_matches": 0}
    nev = 0
    for k, e in evs.items():
        if k not in m.index:
            continue
        nev += 1
        a = m.loc[k]
        for f in cmp:
            av, lv = a[f], getattr(e, f)
            if hasattr(lv, "value"):
                lv = lv.value
            if pd.isna(av) and lv is None:
                continue
            if f in ("week", "time", "num_teams", "qual_matches") and not pd.isna(av) and lv == int(av):
                continue
            if av == lv:
                continue
            cmp[f] += 1
    print("  event field mismatches over %d common events: %s" % (nev, cmp))
    print("  (num_teams/qual_matches archive = March-2024 DB; num_teams counts match participants here)")

    am_non = am[~am.offseason]
    print("  archive matches total=%d non-offseason=%d | loader matches=%d" % (len(am), len(am_non), len(ms)))
    ka, kl = set(am_non.key), set(ms)
    only_a, only_l = sorted(ka - kl), sorted(kl - ka)
    print("  match keys only in archive (non-offseason): %d %s" % (len(only_a), only_a[:15]))
    print("  match keys only in loader: %d %s" % (len(only_l), only_l[:15]))
    if only_a:
        ev_of = pd.Series([k.split("_")[0] for k in only_a]).value_counts().to_dict()
        print("    archive-only by event:", ev_of)
    if only_l:
        ev_of = pd.Series([k.split("_")[0] for k in only_l]).value_counts().to_dict()
        print("    loader-only by event:", ev_of)
    dups = am_non.key.duplicated().sum()
    print("  archive duplicate keys:", dups)
    print("  loader matches per event with no event_data / zero matches:", [k for k, e in evs.items() if e.status.value == "Invalid"])

# --------------------------------------------------------------------------- 3. sample of 50
if "sample" in want:
    print("\n== 3. Match-level comparison vs archive")
    am_i = am.set_index("key")
    fields = [
        ("red_score", lambda m: m.red_score), ("blue_score", lambda m: m.blue_score),
        ("red_no_foul", lambda m: m.red_no_foul), ("blue_no_foul", lambda m: m.blue_no_foul),
        ("red_auto", lambda m: m.red_auto), ("blue_auto", lambda m: m.blue_auto),
        ("red_teleop", lambda m: m.red_teleop), ("blue_teleop", lambda m: m.blue_teleop),
        ("red_endgame", lambda m: m.red_endgame), ("blue_endgame", lambda m: m.blue_endgame),
        ("red_teams", lambda m: m.get_red()), ("blue_teams", lambda m: m.get_blue()),
        ("elim", lambda m: m.elim), ("week", lambda m: m.week), ("time", lambda m: m.time),
        ("winner", lambda m: m.winner.value if m.winner else None),
        ("comp_level", lambda m: m.comp_level.value), ("red_dq", lambda m: m.red_dq), ("red_surrogate", lambda m: m.red_surrogate),
        ("red_rp_1", lambda m: m.red_rp_1), ("red_rp_2", lambda m: m.red_rp_2), ("red_foul", lambda m: m.red_foul),
        ("red_tiebreaker", lambda m: m.red_tiebreaker),
    ]

    def archive_val(a, f):
        if f in ("red_teams", "blue_teams"):
            c = f[:-6]
            return [int(a[f"{c}_{i}"]) for i in (1, 2, 3) if not pd.isna(a[f"{c}_{i}"])]
        v = a[f]
        if f == "elim":
            return str(v).lower() in ("t", "true", "1")
        if f in ("red_dq", "red_surrogate"):
            # archive csv round-trips single team ids through float ("8773.0")
            return "" if pd.isna(v) else ",".join(str(int(float(x))) for x in str(v).split(",") if x != "")
        if pd.isna(v):
            return None
        if f in ("red_rp_1", "red_rp_2"):
            return str(v).lower() in ("t", "true", "1")
        if f in ("winner", "comp_level"):
            return str(v)
        return int(v)

    def compare(keys, label):
        bad = {f: 0 for f, _ in fields}
        examples = {}
        for k in keys:
            a, m = am_i.loc[k], ms[k]
            for f, g in fields:
                av, lv = archive_val(a, f), g(m)
                if f.endswith("_rp_1") or f.endswith("_rp_2"):
                    av, lv = bool(av), bool(lv)
                if av != lv:
                    bad[f] += 1
                    examples.setdefault(f, (k, av, lv))
        print(f"  {label}: n={len(keys)} mismatches per field: " + str({f: n for f, n in bad.items() if n} or "none"))
        for f, (k, av, lv) in list(examples.items())[:8]:
            print(f"      e.g. {f} {k}: archive={av!r} loader={lv!r}")

    common = sorted(set(am_i.index[~am_i.offseason]) & set(ms))
    rnd = random.Random(args.seed)
    sample = rnd.sample(common, 50)
    compare(sample, "random 50 (seed %d)" % args.seed)
    compare(common, "ALL common matches")
    print("  sample keys:", sample[:10], "...")

# --------------------------------------------------------------------------- districts
if "district" in want:
    print("\n== 3b. Derived TeamYear.district vs archive team_years (2023)")
    from src.tba.constants import DISTRICT_MAPPING
    aty = pd.read_csv(f"{args.arch}/team_years.csv", usecols=["year", "team", "district"], dtype=str)
    aty = aty[aty.year == "2023"].set_index("team").district
    aty = aty.map(lambda d: DISTRICT_MAPPING.get(d, d) if isinstance(d, str) else d)  # archive predates chs->fch
    same = diff = 0
    ex = []
    for ty in tys.values():
        if str(ty.team) not in aty.index:
            continue
        a = aty.loc[str(ty.team)]
        a = None if pd.isna(a) else a
        if a == ty.district:
            same += 1
        else:
            diff += 1
            if len(ex) < 8:
                ex.append((ty.team, a, ty.district))
    print(f"  agree={same} disagree={diff} ({same/(same+diff):.1%} agree)  e.g. (team, archive, derived)={ex}")
    if "kinds" not in globals():
        from collections import Counter as _C
        kinds = _C()
        for ty in tys.values():
            if str(ty.team) not in aty.index:
                continue
            a = aty.loc[str(ty.team)]
            a = None if pd.isna(a) else a
            if a != ty.district:
                kinds[("archive=None" if a is None else "archive=" + a) + " vs " + ("derived=None" if ty.district is None else "derived=" + ty.district)] += 1
        print("  disagreement kinds (top):", kinds.most_common(12))

# --------------------------------------------------------------------------- 4. other years
if "years" in want:
    print("\n== 4. Other seasons")
    for y in (2022, 2023, 2024, 2025):
        r = load_year_full(y, raw_path(y))
        yo, tys_, evs_, tes_, ms_, _ = r.objs
        st = {}
        for m in ms_.values():
            st[m.status.value] = st.get(m.status.value, 0) + 1
        print(f"  {y}: events={len(evs_)} (invalid={sum(1 for e in evs_.values() if e.status.value=='Invalid')}) matches={len(ms_)} {st} team_years={len(tys_)} team_events={len(tes_)} breakdown_ERROR_lines={len(r.clean_breakdown_errors)}")
        print("       means: score=%.2f sd=%.2f foul=%.2f no_foul=%.2f auto=%.2f teleop=%.2f endgame=%.2f rp1=%.3f rp2=%.3f rp3=%.3f tb=%.3f" % (yo.score_mean, yo.score_sd, yo.foul_mean, yo.no_foul_mean, yo.auto_mean, yo.teleop_mean, yo.endgame_mean, yo.rp_1_mean, yo.rp_2_mean, yo.rp_3_mean, yo.tiebreaker_mean))
        print("       comps:", [getattr(yo, f"comp_{i}_mean") for i in range(10)])
        if r.clean_breakdown_errors:
            print("       first ERROR lines:", r.clean_breakdown_errors[:3])

# --------------------------------------------------------------------------- 4b. time ties
if "ties" in want:
    print("\n== 4b. Match.time ties (EPA stage uses a *stable* sort on time; ties fall back to dict insertion order)")
    from collections import defaultdict
    for y in (2022, 2023, 2024, 2025):
        o = R.objs if y == 2023 else load_year_full(y, raw_path(y)).objs
        by_t = defaultdict(list)
        for m in o[4].values():
            by_t[m.time].append(m)
        groups = [g for g in by_t.values() if len(g) > 1]
        overlap = [g for g in groups if sum(len(set(m.get_red() + m.get_blue())) for m in g) != len(set(t for m in g for t in m.get_red() + m.get_blue()))]
        same_event = [g for g in groups if len({m.event for m in g}) < len(g)]
        print(f"  {y}: tie groups={len(groups)} (matches in them={sum(len(g) for g in groups)}), groups with a shared TEAM (order-sensitive)={len(overlap)}, groups with 2+ matches in one event={len(same_event)}")
        for g in overlap[:5]:
            print("      order-sensitive:", [(m.key, m.time) for m in g])

# --------------------------------------------------------------------------- 5. determinism
if "det" in want:
    print("\n== 5. Determinism")
    d1 = objs_digest(R.objs, R.teams)
    R2 = load_year_full(2023, raw_path(2023))
    d2 = objs_digest(R2.objs, R2.teams)
    # load a different season in between (exercises the empty_breakdown leak isolation)
    load_year_full(2018 if os.path.exists(raw_path(2018)) else 2025, raw_path(2018 if os.path.exists(raw_path(2018)) else 2025))
    R3 = load_year_full(2023, raw_path(2023))
    d3 = objs_digest(R3.objs, R3.teams)
    print("  in-process: run1==run2:", d1 == d2, " run1==run3 (after loading another season):", d1 == d3)
    outs = set()
    for hs in ("0", "1234"):
        env = dict(os.environ, PYTHONHASHSEED=hs, TZ="Asia/Tokyo")
        p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--only", "digestonly", "--raw", args.raw], capture_output=True, text=True, env=env)
        outs.add(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:])
    print("  separate processes (different PYTHONHASHSEED and machine TZ) digests:", outs, "same as in-process:", outs == {d1})
    print("  digest:", d1)

if args.only == "digestonly":
    print(objs_digest(R.objs, R.teams))

# --------------------------------------------------------------------------- 2026-shaped dump smoke test
if "synthetic" in want:
    print("\n== 7. Synthetic 2026-shaped dump (2025 data relabelled; exercises districts/teams/event-teams paths)")
    import copy
    from sb.loader import read_raw
    raw = copy.deepcopy(read_raw(raw_path(2025)))
    raw["year"] = 2026
    # districts: from derived TeamYear.district of a normal 2025 load, as {abbr: [frcNNN, ...]}
    r25 = load_year_full(2025, raw_path(2025))
    dist = {}
    for ty in r25.objs[1].values():
        if ty.district:
            dist.setdefault("mar" if ty.district == "fma" else ty.district, []).append("frc%d" % ty.team)
    raw["districts"] = dist
    keys = list(raw["event_data"])
    # event 0: ongoing (quals only); event 1: upcoming (no matches, but has a team list); event 2: untouched
    q = raw["event_data"][keys[0]]["matches"]
    raw["event_data"][keys[0]]["matches"] = [m for m in q if m["comp_level"] == "qm"]
    teams1 = sorted({t for m in raw["event_data"][keys[1]]["matches"] for a in ("red", "blue") for t in m["alliances"][a]["team_keys"]})
    raw["event_data"][keys[1]]["matches"] = []
    raw["event_data"][keys[1]]["teams"] = teams1
    r = load_year_full(2026, "<synthetic>", raw=raw)
    o = r.objs
    st = {e.key: e.status.value for e in o[2].values() if e.key in keys[:3]}
    print("  event statuses:", st, "| team_events for upcoming event:", sum(1 for te in o[3].values() if te.event == keys[1]), "of", len(teams1))
    print("  districts applied (TeamYear.district from dump):", sum(1 for t in o[1].values() if t.district), "| deviations:", [d for d in r.deviations if "district" in d or "teams" in d])
    print("  isr teams:", sum(1 for t in o[1].values() if t.district == "isr"), "fma(from 'mar'):", sum(1 for t in o[1].values() if t.district == "fma"))

# --------------------------------------------------------------------------- models vs upstream
if "models" in want:
    print("\n== 6. sb.models fields/defaults vs upstream ORM declarations (ast-parsed)")
    import sb.models as M
    V = os.path.join(ROOT, "vendor/backend/src/db/models/")
    ok = True
    for fname, orm, cls in [("year.py", "YearORM", "Year"), ("team_year.py", "TeamYearORM", "TeamYear"), ("event.py", "EventORM", "Event"), ("team_event.py", "TeamEventORM", "TeamEvent"), ("match.py", "MatchORM", "Match"), ("team.py", "TeamORM", "Team"), ("etag.py", "ETagORM", "ETag")]:
        tree = ast.parse(open(V + fname).read())
        up = []
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name == orm:
                for s in n.body:
                    if isinstance(s, ast.AnnAssign) and isinstance(s.value, ast.Call) and getattr(s.value.func, "id", "") == "mapped_column":
                        d = None
                        for kw in s.value.keywords:
                            if kw.arg == "default":
                                d = ast.literal_eval(kw.value)
                        up.append((s.target.id, d))
        obj = getattr(M, cls)()
        mine = [(f, getattr(obj, f)) for f in getattr(M, cls).__slots__]
        same = up == mine
        ok &= same
        print(f"  {cls}: {len(up)} fields, identical names/order/defaults: {same}")

#!/usr/bin/env python3
"""Build the prediction fixtures that site/js/model/predict.js is tested against (WP3).

    python3 -I pipeline/tests/make_predict_fixtures.py \
        --raw-dir DIR --archive pipeline/priors/archive_team_years_2016_2023.csv \
        --computed-out /home/claude/sbrebuild/out --years 2022 2023 2024 2025 \
        --out pipeline/tests/fixtures [--full DIR] [--check-csv]

For every season it replays the vendored EPA model exactly as sb.engine.run_year does (same
loader, same priors, same upstream calc -> agg -> metrics), with a recording subclass of the
upstream EPA whose predict_match first copies each team's rating vector (`self.epas[t].mean`)
and then calls the unmodified upstream method. Nothing in vendor/ is changed and no state is
touched, so the season's outputs are the pipeline's outputs.

Priors: if `{computed-out}/{year}/run_meta.json` exists, its `prior_sources` decide which prior
seasons come from computed team_years.csv and which from the archive, so the replay equals the
reference run in `computed-out` (checked with --check-csv against matches.csv/team_matches.csv).

Files written
  {out}/predict_{year}.json.gz   2,000 matches: 1,000 playoff + 1,000 qual (seeded 1218, kept
                                 in processing order). Every match has the pre-match vectors
                                 rounded as exported (3.1: 2 decimals, rp 4) and the exported
                                 (rounded) predictions; 100 of them (50 + 50) also carry the
                                 full-precision vectors and the full-precision predictions.
  {out}/predict_synthetic.json   seasons 2022-2026 with random and edge-case vectors (2023 grid
                                 overflow branches, 2025 processor algae, 2026 three RPs), with
                                 expectations computed by calling the vendored
                                 EPA.predict_match directly.
  {full}/predict_full_{year}.json.gz  (optional, not committed) every match of the season with
                                 full-precision vectors and predictions.
"""
import argparse
import csv
import gzip
import json
import math
import os
import random
import sys
import tempfile
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pipeline/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import sb  # noqa: E402,F401
import sb.loader as loader  # noqa: E402  (installs the shim)
from sb.models import Year  # noqa: E402
from sb.priors import build_priors  # noqa: E402

import src.data.epa.calc as _calc  # noqa: E402
import src.data.epa.main as _epa_main  # noqa: E402
import src.models.epa.init as _epa_init  # noqa: E402
from src.breakdown import key_to_name  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402

SLOT_INDEX = {"epa": 0, "auto_epa": 1, "teleop_epa": 2, "endgame_epa": 3, "rp_1_epa": 4,
              "rp_2_epa": 5, "rp_3_epa": 6, "tiebreaker_epa": 7}
SLOT_INDEX.update({f"comp_{i}_epa": 8 + i for i in range(10)})

N_ELIM, N_QUAL, N_EXACT_EACH, N_SYNTH, SEED = 1000, 1000, 50, 40, 1218


def vector_keys(year: int) -> List[str]:
    """VK per SPEC 3.2."""
    names = key_to_name[year]
    vk = ["epa", "auto_epa", "teleop_epa", "endgame_epa", "rp_1_epa", "rp_2_epa"]
    if "rp_3" in names:
        vk.append("rp_3_epa")
    vk.append("tiebreaker_epa")
    vk += [f"comp_{i}_epa" for i in range(10) if f"comp_{i}" in names]
    return vk


def is_rp(k: str) -> bool:
    return k.startswith("rp_")


def to_vk(vec18, vk: List[str]) -> List[float]:
    return [float(vec18[SLOT_INDEX[k]]) for k in vk]


def round_vk(vals: List[float], vk: List[str]) -> List[float]:
    # SPEC 3.1: ratings 2 decimals, rp_*_epa 4 decimals; this is also what pre_record_team stores
    return [round(float(v), 4 if is_rp(k) else 2) for v, k in zip(vals, vk)]


def r(x: float, nd: int) -> float:
    return round(float(x), nd)


class RecordingEPA(EPA):
    """Upstream EPA plus a read-only record of each predict_match call."""

    records: Dict[str, Dict[str, Any]] = {}

    def predict_match(self, match, event):
        red = match.get_red()[: self.num_teams]
        blue = match.get_blue()[: self.num_teams]
        pre = {"red": [np.array(self.epas[t].mean, dtype=float).copy() for t in red],
               "blue": [np.array(self.epas[t].mean, dtype=float).copy() for t in blue]}
        win_prob, red_pred, blue_pred = super().predict_match(match, event)
        RecordingEPA.records[match.key] = {
            "red_teams": list(red), "blue_teams": list(blue), "pre": pre,
            "win_prob": float(win_prob),
            "red_score": float(red_pred.score), "blue_score": float(blue_pred.score),
            "red_rp": [float(red_pred.rp_1 or 0), float(red_pred.rp_2 or 0), float(red_pred.rp_3 or 0)],
            "blue_rp": [float(blue_pred.rp_1 or 0), float(blue_pred.rp_2 or 0), float(blue_pred.rp_3 or 0)],
            "red_bd": np.array(red_pred.breakdown, dtype=float).copy(),
            "blue_bd": np.array(blue_pred.breakdown, dtype=float).copy(),
        }
        return win_prob, red_pred, blue_pred


def run_season(year: int, raw_path: str, priors) -> tuple:
    objs = loader.load_year(year, raw_path)
    RecordingEPA.records = {}
    saved = _calc.EPA
    _epa_init.get_constants.cache_clear()
    try:
        _calc.EPA = RecordingEPA
        objs = _epa_main.process_year(objs, priors)
    finally:
        _calc.EPA = saved
        _epa_init.get_constants.cache_clear()
    return objs, dict(RecordingEPA.records)


def season_predict(year_obj: Year) -> Dict[str, Any]:
    k = float(EPA.k_func(year_obj.year))
    return {"num_teams": 3, "score_sd": float(year_obj.score_sd),
            "foul_rate": float(year_obj.get_foul_rate()),
            "models": {"epa": {"k": k}, "fast": {"k": k}}}


def match_entry(m, rec, vk, exact: bool) -> Dict[str, Any]:
    e: Dict[str, Any] = {
        "key": m.key, "elim": bool(m.elim), "comp_level": str(m.comp_level.value
                                                            if hasattr(m.comp_level, "value")
                                                            else m.comp_level),
        "red": rec["red_teams"], "blue": rec["blue_teams"],
        "red_vecs": [round_vk(to_vk(v, vk), vk) for v in rec["pre"]["red"]],
        "blue_vecs": [round_vk(to_vk(v, vk), vk) for v in rec["pre"]["blue"]],
        # exported values (upstream record_match rounding; red_pred/blue_pred per SPEC 3.1)
        "export": {
            "win_prob": m.epa_win_prob,
            "red_score": m.epa_red_score_pred, "blue_score": m.epa_blue_score_pred,
            "red_rp": [m.epa_red_rp_1_pred, m.epa_red_rp_2_pred, m.epa_red_rp_3_pred],
            "blue_rp": [m.epa_blue_rp_1_pred, m.epa_blue_rp_2_pred, m.epa_blue_rp_3_pred],
            "red_pred": round_vk(to_vk(rec["red_bd"], vk), vk),
            "blue_pred": round_vk(to_vk(rec["blue_bd"], vk), vk),
        },
    }
    for side in ("red_rp", "blue_rp"):
        e["export"][side] = [None if x is None else float(x) for x in e["export"][side]]
    for f in ("win_prob", "red_score", "blue_score"):
        e["export"][f] = float(e["export"][f])
    if exact:
        e["exact"] = exact_entry(rec, vk)
    return e


def exact_entry(rec, vk) -> Dict[str, Any]:
    return {
        "red_vecs": [to_vk(v, vk) for v in rec["pre"]["red"]],
        "blue_vecs": [to_vk(v, vk) for v in rec["pre"]["blue"]],
        "win_prob": rec["win_prob"], "red_score": rec["red_score"], "blue_score": rec["blue_score"],
        "red_rp": rec["red_rp"], "blue_rp": rec["blue_rp"],
        "red_pred": to_vk(rec["red_bd"], vk), "blue_pred": to_vk(rec["blue_bd"], vk),
    }


def check_csv(year: int, order, recs, ref_dir: str) -> List[str]:
    """Replay == reference run: matches.csv epa_* and team_matches.csv pre_* equal."""
    problems: List[str] = []
    mpath = os.path.join(ref_dir, str(year), "matches.csv")
    tpath = os.path.join(ref_dir, str(year), "team_matches.csv")
    if not os.path.exists(mpath):
        print(f"[{year}] check-csv: no reference {mpath}, skipped")
        return []
    rows = list(csv.DictReader(open(mpath, newline="", encoding="utf-8")))
    if [x["key"] for x in rows] != [m.key for m in order]:
        problems.append(f"{year}: processing order differs from matches.csv")
    by_key = {m.key: m for m in order}
    cols = ["epa_win_prob", "epa_red_score_pred", "epa_blue_score_pred", "epa_red_rp_1_pred",
            "epa_red_rp_2_pred", "epa_red_rp_3_pred", "epa_blue_rp_1_pred", "epa_blue_rp_2_pred",
            "epa_blue_rp_3_pred"]
    bad = 0
    for row in rows:
        m = by_key.get(row["key"])
        for c in cols:
            v = getattr(m, c)
            want = row[c]
            got = "" if v is None else repr(float(v))
            if (want == "" or got == "") and want != got:
                bad += 1
            elif want != "" and float(want) != float(got):
                bad += 1
    if bad:
        problems.append(f"{year}: {bad} epa_* values differ from matches.csv")
    pre_cols = [f"pre_{k}" for k in SLOT_INDEX]
    bad = 0
    n = 0
    for row in csv.DictReader(open(tpath, newline="", encoding="utf-8")):
        rec = recs[row["match"]]
        side = rec["pre"][row["alliance"]]
        slot = int(row["slot"]) - 1
        if slot >= len(side):
            continue
        n += 1
        vec = side[slot]
        for c in pre_cols:
            k = c[4:]
            nd = 4 if is_rp(k) else 2
            if row[c] == "":
                continue
            if float(row[c]) != round(float(vec[SLOT_INDEX[k]]), nd):
                bad += 1
    if bad:
        problems.append(f"{year}: {bad} pre_* values differ from team_matches.csv rounding")
    print(f"[{year}] check-csv: {len(rows)} matches, {n} team rows compared, "
          f"{'OK' if not problems else '; '.join(problems)}")
    return problems


def priors_for(year: int, archive_path: str, computed_out: str):
    computed = set()
    meta_path = os.path.join(computed_out, str(year), "run_meta.json") if computed_out else ""
    if meta_path and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        computed = {int(y) for y, s in meta.get("prior_sources", {}).items() if s == "computed"}
    if os.path.isdir(archive_path):
        archive_dir = archive_path
    else:  # sb.priors reads {dir}/team_years.csv
        archive_dir = tempfile.mkdtemp(prefix="pw_archive_")
        os.symlink(os.path.abspath(archive_path), os.path.join(archive_dir, "team_years.csv"))
    return build_priors(year, archive_dir, computed_out, computed, False)


def build_season(year: int, args) -> List[str]:
    raw = os.path.join(args.raw_dir, f"tba_{year}.json.gz")
    priors, sources = priors_for(year, args.archive, args.computed_out)
    objs, recs = run_season(year, raw, priors)
    year_obj, matches = objs[0], objs[4]
    order = sorted(matches.values(), key=lambda m: m.time)
    vk = vector_keys(year)
    problems: List[str] = []
    if args.check_csv:
        problems += check_csv(year, order, recs, args.computed_out)

    rng = random.Random(SEED + year)
    elims = [m.key for m in order if m.elim]
    quals = [m.key for m in order if not m.elim]
    pick_e = set(rng.sample(elims, min(N_ELIM, len(elims))))
    pick_q = set(rng.sample(quals, min(N_QUAL, len(quals))))
    exact = set(rng.sample(sorted(pick_e), min(N_EXACT_EACH, len(pick_e))))
    exact |= set(rng.sample(sorted(pick_q), min(N_EXACT_EACH, len(pick_q))))

    head = {"schema": 1, "kind": "predict_fixture", "season": year, "model": "epa",
            "prior_sources": {str(k): v for k, v in sources.items()},
            "keys": {"vector": vk, "names": dict(key_to_name[year])},
            "predict": season_predict(year_obj)}
    sel = [m for m in order if m.key in pick_e or m.key in pick_q]
    fx = dict(head, matches=[match_entry(m, recs[m.key], vk, m.key in exact) for m in sel])
    write_json(os.path.join(args.out, f"predict_{year}.json.gz"), fx)
    print(f"[{year}] priors {sources}; fixture {len(sel)} matches "
          f"({len(pick_e)} playoff, {len(exact)} exact)")
    if args.full:
        os.makedirs(args.full, exist_ok=True)
        full = dict(head, kind="predict_full", matches=[
            dict(key=m.key, elim=bool(m.elim), red=recs[m.key]["red_teams"],
                 blue=recs[m.key]["blue_teams"], exact=exact_entry(recs[m.key], vk))
            for m in order])
        write_json(os.path.join(args.full, f"predict_full_{year}.json.gz"), full)
        print(f"[{year}] full fixture: {len(order)} matches")
    return problems


# ---------- synthetic cases: call the vendored EPA.predict_match directly ----------

class _FakeMatch:
    def __init__(self, key, red, blue, elim):
        self.key, self._red, self._blue, self.elim = key, red, blue, elim

    def get_red(self):
        return list(self._red)

    def get_blue(self):
        return list(self._blue)


def synthetic(args) -> None:
    rng = np.random.default_rng(SEED)
    seasons = []
    for year in (2022, 2023, 2024, 2025, 2026):
        vk = vector_keys(year)
        model_year_k = EPA.k_func(year)
        yobj = Year(year=year, score_sd=float(30 + year % 7), foul_mean=4.25 + year % 3,
                    no_foul_mean=70.0 + year % 11)
        model = EPA()
        model.year_obj, model.year_num, model.num_teams = yobj, year, 3
        model.k = model_year_k
        cases = []
        for i in range(N_SYNTH):
            base = rng.normal(10, 6, size=(6, 18))
            base[:, 4:7] = rng.normal(0.15, 0.35, size=(6, 3))
            if year == 2023 and i % 3 == 0:  # push the grid over 9 per row in various ways
                base[:, 8 + 3:8 + 6] = rng.uniform(0, 6, size=(6, 3))
                base[:, 8 + 6:8 + 8] = rng.uniform(-2, 20, size=(6, 2))
                base[:, 8 + 8] = rng.uniform(0, 5, size=6)
                base[:, 8 + 9] = rng.uniform(0, 14, size=6)
            if year == 2023 and i == 3:  # grid points <= 0: cube/cone not rescaled
                base[:, 8 + 6:8 + 8] = -1.0
            if year == 2025:
                base[:, 8 + 6] = rng.uniform(-1, 4, size=6)
            if i == 0:
                base[:] = 0.0
            # only VK slots are meaningful; others are what an exported vector cannot carry
            for s in range(18):
                if s not in [SLOT_INDEX[k] for k in vk]:
                    base[:, s] = 0.0
            teams = list(range(1, 7))
            model.epas = {t: _Rating(base[t - 1].copy()) for t in teams}
            elim = bool(i % 4 == 1)
            m = _FakeMatch(f"{year}syn_{i}", teams[:3], teams[3:], elim)
            wp, rp_, bp_ = model.predict_match(m, None)
            cases.append({
                "key": m.key, "elim": elim,
                "red_vecs": [to_vk(base[j], vk) for j in range(3)],
                "blue_vecs": [to_vk(base[j], vk) for j in range(3, 6)],
                "win_prob": float(wp), "red_score": float(rp_.score), "blue_score": float(bp_.score),
                "red_rp": [float(rp_.rp_1 or 0), float(rp_.rp_2 or 0), float(rp_.rp_3 or 0)],
                "blue_rp": [float(bp_.rp_1 or 0), float(bp_.rp_2 or 0), float(bp_.rp_3 or 0)],
                "red_pred": to_vk(rp_.breakdown, vk), "blue_pred": to_vk(bp_.breakdown, vk),
            })
        k = float(EPA.k_func(year))
        seasons.append({"season": year, "keys": {"vector": vk, "names": dict(key_to_name[year])},
                        "predict": {"num_teams": 3, "score_sd": float(yobj.score_sd),
                                    "foul_rate": float(yobj.get_foul_rate()),
                                    "models": {"epa": {"k": k}, "fast": {"k": k}}},
                        "cases": cases})
    write_json(os.path.join(args.out, "predict_synthetic.json"),
               {"schema": 1, "kind": "predict_synthetic", "seasons": seasons})
    print("synthetic: " + ", ".join(f"{s['season']}:{len(s['cases'])}" for s in seasons))


class _Rating:
    def __init__(self, mean):
        self.mean = mean


def write_json(path: str, obj: Any) -> None:
    def clean(x):
        if isinstance(x, float) and not math.isfinite(x):
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    text = json.dumps(clean(obj), separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    data = text.encode("utf-8")
    if path.endswith(".gz"):  # deterministic: level 9, mtime 0, no file name
        data = gzip.compress(data, compresslevel=9, mtime=0)
    with open(path, "wb") as f:
        f.write(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--archive", default=os.path.join(ROOT, "priors", "archive_team_years_2016_2023.csv"))
    ap.add_argument("--computed-out", default="")
    ap.add_argument("--years", type=int, nargs="*", default=[2023, 2024, 2025])
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "fixtures"))
    ap.add_argument("--full", default="", help="also write every match, full precision, here")
    ap.add_argument("--check-csv", action="store_true",
                    help="compare the replay with {computed-out}/{year}/matches.csv, team_matches.csv")
    ap.add_argument("--no-synthetic", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    problems: List[str] = []
    for year in sorted(args.years):
        problems += build_season(year, args)
    if not args.no_synthetic:
        synthetic(args)
    for p in problems:
        print("PROBLEM", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

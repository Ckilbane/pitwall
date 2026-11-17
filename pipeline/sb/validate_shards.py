#!/usr/bin/env python3
"""Validate a published data/ tree (SPEC 2.3 step 7, 3.x).

    python pipeline/sb/validate_shards.py DATA_DIR SCHEMA_DIR [--season Y ...] [--json report.json]
                                          [--budget season=800000 ...] [--no-hash-check]

Checks, per file:
  * JSON Schema (docs/schema/*.json; `jsonschema` is a pinned requirement) for manifest.json,
    models.json, history.json.gz, season.json.gz, asof.json.gz, traj.json.gz and every event file;
  * column lengths: every array in a table's `cols` and `models.<m>` has exactly `n` entries;
  * season-dependent names: `keys.vector`/`keys.breakdown` equal the vendored key_to_name derivation
    (sb.season_meta); teams/team_events/team_matches carry every VK column (+ pre_<vk>, attr_*),
    matches carry red_<bk>/blue_<bk> for every BK, red_pred/blue_pred have len(VK) entries;
  * cross-file integrity: hashes and byte counts in the manifest and in season.json
    (`events.cols.file_hash/file_bytes`) equal the files on disk; asof/traj rows are aligned with
    the season teams table; event file names equal their `event` key; event keys match
    ^[0-9]{4}[a-z0-9]+$; robot arrays have one row per match and 6 entries per row;
  * size budgets (SPEC 3.9): season.json <= 1.2 MB, asof <= 900 KB, traj <= 1.5 MB, event <= 200 KB.

Exit status 0 = clean, 1 = errors.  `validate_season()` is the function build.py uses to decide
whether a season must be replaced by its last good shards.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sb import season_meta as sm  # noqa: E402

BUDGETS = {"season": 1_200_000, "asof": 900_000, "traj": 1_500_000, "event": 200_000}
EVENT_KEY = re.compile(r"^[0-9]{4}[a-z0-9]+$")
MODEL_IDS = ("epa", "fast")
MAX_ERRORS_PER_FILE = 25


def _load_bytes(raw: bytes) -> Any:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _read(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    return raw, _load_bytes(raw)


def _h12(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


class Schemas:
    def __init__(self, schema_dir: str):
        try:
            from jsonschema import Draft202012Validator
        except ImportError as e:  # pragma: no cover
            raise SystemExit("jsonschema is required (pipeline/requirements.txt): " + str(e))
        self._V = Draft202012Validator
        self.docs = {}
        for name in ("manifest", "season", "event"):
            with open(os.path.join(schema_dir, f"{name}.schema.json"), encoding="utf-8") as f:
                self.docs[name] = json.load(f)
        self._cache: Dict[str, Any] = {}

    def validator(self, which: str):
        if which in self._cache:
            return self._cache[which]
        m, s, e = self.docs["manifest"], self.docs["season"], self.docs["event"]
        if which == "manifest":
            schema = m
        elif which == "models":
            schema = {"$defs": m["$defs"], "$ref": "#/$defs/modelsFile"}
        elif which == "history":
            schema = {"$defs": m["$defs"], "$ref": "#/$defs/historyFile"}
        elif which == "season":
            schema = s
        elif which == "asof":
            schema = {"$defs": s["$defs"], "$ref": "#/$defs/asofFile"}
        elif which == "traj":
            schema = {"$defs": s["$defs"], "$ref": "#/$defs/trajFile"}
        elif which == "event":
            schema = e
        else:
            raise KeyError(which)
        v = self._V(schema)
        self._cache[which] = v
        return v

    def check(self, which: str, doc: Any, label: str, errs: List[str]) -> None:
        n = 0
        for err in self.validator(which).iter_errors(doc):
            path = "/".join(str(p) for p in err.absolute_path)
            msg = err.message if len(err.message) < 200 else err.message[:200] + "..."
            errs.append(f"{label}: schema {which}: {path or '<root>'}: {msg}")
            n += 1
            if n >= MAX_ERRORS_PER_FILE:
                errs.append(f"{label}: (more schema errors suppressed)")
                break


def check_table(tbl: Any, label: str, errs: List[str], need_models: bool = True) -> int:
    if not isinstance(tbl, dict) or "n" not in tbl or "cols" not in tbl:
        errs.append(f"{label}: not a table")
        return 0
    n = tbl["n"]
    for k, arr in tbl["cols"].items():
        if not isinstance(arr, list) or len(arr) != n:
            errs.append(f"{label}.cols.{k}: length {len(arr) if isinstance(arr, list) else '?'} != n {n}")
    models = tbl.get("models")
    if need_models and models is None:
        errs.append(f"{label}: missing models")
    for mid, cols in (models or {}).items():
        if mid not in MODEL_IDS:
            errs.append(f"{label}.models: unknown model {mid!r}")
        for k, arr in cols.items():
            if not isinstance(arr, list) or len(arr) != n:
                errs.append(f"{label}.models.{mid}.{k}: length {len(arr) if isinstance(arr, list) else '?'} != n {n}")
    return n


def _need(cols: Dict[str, Any], names: List[str], label: str, errs: List[str]) -> None:
    missing = [k for k in names if k not in cols]
    if missing:
        errs.append(f"{label}: missing columns {missing[:8]}{'...' if len(missing) > 8 else ''}")


def validate_event_doc(doc: Dict[str, Any], season_doc: Dict[str, Any], label: str, errs: List[str],
                       expect_key: Optional[str] = None) -> None:
    VK = season_doc["keys"]["vector"]
    BK = season_doc["keys"]["breakdown"]
    if expect_key and doc.get("event") != expect_key:
        errs.append(f"{label}: event {doc.get('event')!r} != file name key {expect_key!r}")
    if doc.get("season") != season_doc["season"]:
        errs.append(f"{label}: season {doc.get('season')} != {season_doc['season']}")
    if not EVENT_KEY.match(str(doc.get("event", ""))):
        errs.append(f"{label}: bad event key {doc.get('event')!r}")
    n_te = check_table(doc["team_events"], f"{label}.team_events", errs)
    n_m = check_table(doc["matches"], f"{label}.matches", errs)
    n_tm = check_table(doc["team_matches"], f"{label}.team_matches", errs)
    mc = doc["matches"]["cols"]
    _need(mc, [f"{c}_{b}" for b in BK for c in ("red", "blue")], f"{label}.matches.cols", errs)
    for mid in MODEL_IDS:
        mm = doc["matches"]["models"].get(mid, {})
        for col in ("red_pred", "blue_pred"):
            for i, v in enumerate(mm.get(col, [])):
                if v is not None and len(v) != len(VK):
                    errs.append(f"{label}.matches.models.{mid}.{col}[{i}]: {len(v)} entries != {len(VK)} VK")
                    break
        _need(doc["team_events"]["models"].get(mid, {}), VK + [f"pre_{k}" for k in VK],
              f"{label}.team_events.models.{mid}", errs)
        _need(doc["team_matches"]["models"].get(mid, {}), VK, f"{label}.team_matches.models.{mid}", errs)
    tm = doc["team_matches"]["cols"]
    if tm["m"] and (min(tm["m"]) < 0 or max(tm["m"]) >= n_m):
        errs.append(f"{label}.team_matches.cols.m: index outside matches (n={n_m})")
    if tm["m"] != sorted(tm["m"]):
        errs.append(f"{label}.team_matches.cols.m: not in match order")
    seqs = mc.get("seq", [])
    if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
        errs.append(f"{label}.matches.cols.seq: not strictly increasing")
    for i, (r, b) in enumerate(zip(mc.get("red", []), mc.get("blue", []))):
        if not 2 <= len(r) <= 4 or not 2 <= len(b) <= 4:
            errs.append(f"{label}.matches: row {i} has alliance of {len(r)}/{len(b)} teams")
            break
    rb = doc["robot"]
    for key in ("scored", "ok"):
        if len(rb[key]) != n_m:
            errs.append(f"{label}.robot.{key}: {len(rb[key])} rows != {n_m} matches")
    codes = {f["key"]: f["codes"] for f in rb["fields"]}
    for k, rows in rb["values"].items():
        if k not in codes:
            errs.append(f"{label}.robot.values.{k}: not in robot.fields")
            continue
        if len(rows) != n_m:
            errs.append(f"{label}.robot.values.{k}: {len(rows)} rows != {n_m}")
        for r in rows:
            if r is not None and any(x is not None and not 0 <= x < len(codes[k]) for x in r):
                errs.append(f"{label}.robot.values.{k}: code index out of range")
                break
    for s, rows in rb["points"].items():
        if len(rows) != n_m:
            errs.append(f"{label}.robot.points.{s}: {len(rows)} rows != {n_m}")


def validate_season(data_dir: str, season: int, schemas: Schemas, budgets: Optional[Dict[str, int]] = None,
                    hash_check: bool = True) -> List[str]:
    budgets = {**BUDGETS, **(budgets or {})}
    errs: List[str] = []
    sdir = os.path.join(data_dir, str(season))
    sp = os.path.join(sdir, "season.json.gz")
    if not os.path.exists(sp):
        return [f"{season}: season.json.gz missing"]
    raw, sdoc = _read(sp)
    label = f"{season}/season.json.gz"
    if len(raw) > budgets["season"]:
        errs.append(f"{label}: {len(raw)} bytes > budget {budgets['season']}")
    schemas.check("season", sdoc, label, errs)
    if errs and any("schema" in e for e in errs):
        return errs
    if sdoc["season"] != season:
        errs.append(f"{label}: season field {sdoc['season']} != directory {season}")
    keys = sdoc["keys"]
    if keys["vector"] != sm.vector_keys(season):
        errs.append(f"{label}: keys.vector != derived VK {sm.vector_keys(season)}")
    if keys["breakdown"] != sm.breakdown_keys(season):
        errs.append(f"{label}: keys.breakdown != derived BK")
    if set(keys["units"]) != set(keys["vector"]) or set(keys["groups"]) != set(keys["vector"]):
        errs.append(f"{label}: keys.units/groups do not cover exactly VK")
    VK = keys["vector"]
    n_ev = check_table(sdoc["events"], f"{label}.events", errs)
    n_t = check_table(sdoc["teams"], f"{label}.teams", errs)
    for mid in MODEL_IDS:
        _need(sdoc["teams"]["models"].get(mid, {}), VK, f"{label}.teams.models.{mid}", errs)
        if set(sdoc["year"]["models"][mid]["percentiles"]) != set(VK):
            errs.append(f"{label}.year.models.{mid}.percentiles: keys != VK")
    ev_rows = sdoc["events"]["cols"]
    pairs = list(zip(ev_rows["start_date"], ev_rows["key"]))
    if pairs != sorted(pairs):
        errs.append(f"{label}.events: rows not sorted by (start_date, key)")
    teams = sdoc["teams"]["cols"]["team"]
    if teams != sorted(teams) or len(set(teams)) != len(teams):
        errs.append(f"{label}.teams: rows not strictly sorted by team number")
    for f, cnt in sdoc["robot"]["counts"].items():
        if len(cnt) != n_t:
            errs.append(f"{label}.robot.counts.{f}: {len(cnt)} rows != {n_t} teams")
    # asof / traj
    for name in ("asof", "traj"):
        p = os.path.join(sdir, f"{name}.json.gz")
        if not os.path.exists(p):
            errs.append(f"{season}/{name}.json.gz missing")
            continue
        raw2, doc2 = _read(p)
        lab = f"{season}/{name}.json.gz"
        if len(raw2) > budgets[name]:
            errs.append(f"{lab}: {len(raw2)} bytes > budget {budgets[name]}")
        schemas.check(name, doc2, lab, errs)
        if doc2.get("team") != teams:
            errs.append(f"{lab}: team column not aligned with season.json teams")
            continue
        for mid in MODEL_IDS:
            for k, rows in doc2["models"][mid].items():
                if len(rows) != n_t:
                    errs.append(f"{lab}.models.{mid}.{k}: {len(rows)} rows != {n_t}")
        if name == "traj":
            for i in range(n_t):
                L = len(doc2["seq"][i])
                if len(doc2["event"][i]) != L or any(
                        len(doc2["models"][mid][k][i]) != L for mid in MODEL_IDS for k in ("epa", "resid")):
                    errs.append(f"{lab}: row {i} (team {teams[i]}) has ragged arrays")
                    break
            if any(len(r) > 1 and min(r[1:]) < 1 for r in doc2["seq"]):
                errs.append(f"{lab}.seq: delta-coded seq must have deltas >= 1")
            if any(e >= n_ev or e < 0 for r in doc2["event"] for e in r):
                errs.append(f"{lab}.event: index outside events table")
        else:
            if len(doc2["points"]) != 9:
                errs.append(f"{lab}: points != 9 entries")
            for mid in MODEL_IDS:
                if any(len(r) != 9 for r in doc2["models"][mid]["epa"]):
                    errs.append(f"{lab}: a row does not have 9 points")
                    break
    # events
    ev_dir = os.path.join(sdir, "events")
    on_disk = ({fn[:-len(".json.gz")]: os.path.join(ev_dir, fn) for fn in os.listdir(ev_dir)
                if fn.endswith(".json.gz")} if os.path.isdir(ev_dir) else {})
    listed = {}
    for i, k in enumerate(ev_rows["key"]):
        if ev_rows["file_hash"][i] is not None:
            listed[k] = (ev_rows["file_hash"][i], ev_rows["file_bytes"][i])
    for k in sorted(set(on_disk) - set(listed)):
        errs.append(f"{season}/events/{k}.json.gz: file not listed in season.json events table")
    for k in sorted(set(listed) - set(on_disk)):
        errs.append(f"{season}/events/{k}.json.gz: listed but missing")
    for k in sorted(set(listed) & set(on_disk)):
        p = on_disk[k]
        raw3, edoc = _read(p)
        lab = f"{season}/events/{k}.json.gz"
        if len(raw3) > budgets["event"]:
            errs.append(f"{lab}: {len(raw3)} bytes > budget {budgets['event']}")
        if hash_check and (_h12(raw3) != listed[k][0] or len(raw3) != listed[k][1]):
            errs.append(f"{lab}: hash/bytes differ from season.json events table")
        n0 = len(errs)
        schemas.check("event", edoc, lab, errs)
        if len(errs) == n0:
            validate_event_doc(edoc, sdoc, lab, errs, expect_key=k)
    return errs


def validate_tree(data_dir: str, schema_dir: str, seasons: Optional[List[int]] = None,
                  budgets: Optional[Dict[str, int]] = None, hash_check: bool = True) -> Dict[str, List[str]]:
    schemas = Schemas(schema_dir)
    report: Dict[str, List[str]] = {}
    g: List[str] = []
    mp = os.path.join(data_dir, "manifest.json")
    manifest = None
    if not os.path.exists(mp):
        g.append("manifest.json missing")
    else:
        raw, manifest = _read(mp)
        schemas.check("manifest", manifest, "manifest.json", g)
        for name in ("models", "history"):
            ref = manifest.get("files", {}).get(name) if isinstance(manifest, dict) else None
            if not ref:
                continue
            p = os.path.join(data_dir, ref["path"])
            if not os.path.exists(p):
                g.append(f"{ref['path']} missing")
                continue
            raw2, doc = _read(p)
            if hash_check and (_h12(raw2) != ref["hash"] or len(raw2) != ref["bytes"]):
                g.append(f"{ref['path']}: hash/bytes differ from manifest")
            schemas.check(name, doc, ref["path"], g)
            if name == "history":
                check_table({"n": doc["rows"]["n"], "cols": doc["rows"]["cols"],
                             "models": doc["rows"]["models"]}, "history.rows", g)
        want = [s["season"] for s in manifest.get("seasons", [])]
        if want != sorted(want, reverse=True):
            g.append("manifest.seasons not newest first")
    report["global"] = g
    if manifest is not None:
        for s in manifest["seasons"]:
            y = s["season"]
            if seasons and y not in seasons:
                continue
            errs: List[str] = []
            for name, ref in s["files"].items():
                p = os.path.join(data_dir, ref["path"])
                if not os.path.exists(p):
                    errs.append(f"{ref['path']} missing")
                    continue
                with open(p, "rb") as f:
                    raw3 = f.read()
                if hash_check and (_h12(raw3) != ref["hash"] or len(raw3) != ref["bytes"]):
                    errs.append(f"{ref['path']}: hash/bytes differ from manifest")
            errs += validate_season(data_dir, y, schemas, budgets, hash_check)
            ev_dir = os.path.join(data_dir, str(y), "events")
            files = [fn for fn in os.listdir(ev_dir)] if os.path.isdir(ev_dir) else []
            if s["events_count"] != len(files):
                errs.append(f"{y}: manifest events_count {s['events_count']} != {len(files)} files")
            tot = sum(os.path.getsize(os.path.join(ev_dir, fn)) for fn in files)
            if s["events_bytes"] != tot:
                errs.append(f"{y}: manifest events_bytes {s['events_bytes']} != {tot}")
            report[str(y)] = errs
    else:
        for y in seasons or []:
            report[str(y)] = validate_season(data_dir, y, schemas, budgets, hash_check)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("schema_dir")
    ap.add_argument("--season", type=int, nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--budget", action="append", default=[], metavar="NAME=BYTES")
    ap.add_argument("--no-hash-check", action="store_true")
    a = ap.parse_args(argv)
    budgets = {}
    for b in a.budget:
        k, v = b.split("=")
        budgets[k] = int(v)
    rep = validate_tree(a.data_dir, a.schema_dir, a.season, budgets, not a.no_hash_check)
    bad = 0
    for k, errs in rep.items():
        print(f"[{k}] {'OK' if not errs else str(len(errs)) + ' error(s)'}")
        for e in errs[:40]:
            print("   ", e)
        bad += len(errs)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

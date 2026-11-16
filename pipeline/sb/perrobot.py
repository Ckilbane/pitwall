"""Per-robot facts and robot-attributed points (SPEC 3.5.7, 4.4).

Loads `docs/perrobot_fields.json` and applies its rules to a raw TBA season dump.

    facts = robot_facts(year, raw)
    facts.by_match[match_key] -> (red: AllianceFacts | None, blue: AllianceFacts | None)
    facts.active_parts           parts whose season match rate >= 0.99
    facts.match_rate["{field}:{slot}"]   per part id (a second part on the same field and slot
                                         gets "{field}:{slot}:{check_field}" so ids are unique)

Build-time reconciliation switch: for every part the sum of the robots' points is compared with
the alliance `check_field` on every alliance that has a score_breakdown.  A part is ACTIVE only
if the field exists in the data and that match rate is >= 0.99.  Inactive parts are still listed
(`fields[].active = false`) but their points are never exported or used for attribution.  This is
what gates the unverified 2026 fields.

2026 discovery (fields inferred, SPEC Q5): if a field's TBA name is not in the data, breakdown keys
ending in Robot1..3 are searched for the field's words; for unverified `map` parts whose declared
points do not reproduce the alliance total, points per value are fitted by least squares (rounded
to integers) and used only if that reproduces the total.  Everything found/fitted is recorded in
`RobotFacts.notes` and `RobotFacts.fitted`.

Robot index: `RobotN` = `alliances.<color>.team_keys[N-1]`.  A 4th team gets no per-robot data.
Input is untrusted: only dict/list/str values are read, nothing is executed or used in a path.
"""
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import sb  # noqa: F401
from sb.season_meta import BK_SLOT

FIELDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs", "perrobot_fields.json",
)
ACTIVE_MIN_RATE = 0.99

_AGG_SLOT = {"no_foul": 0, "auto": 1, "teleop": 2, "endgame": 3}
_NONE_VALUES = (None, "None", "No", "no", "none", "")


def load_spec(path: str = FIELDS_PATH) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class AllianceFacts:
    values: Dict[str, List[Optional[str]]]          # field key -> 3 raw values (None if absent)
    points: np.ndarray                              # (3, 18) robot points per rating slot, NaN = not robot-attributable
    ok: bool                                        # every active part matched its check_field exactly
    event_points: Dict[str, np.ndarray] = field(default_factory=dict)   # 'auto'|'endgame'|'comp_i' -> (3,)
    scored: np.ndarray = field(default_factory=lambda: np.zeros(3))     # (3,) each point once


@dataclass
class RobotFacts:
    year: int
    by_match: Dict[str, Tuple[Optional[AllianceFacts], Optional[AllianceFacts]]]
    active_parts: List[Dict[str, Any]]
    match_rate: Dict[str, float]
    fields: List[Dict[str, Any]]                    # [{key, codes, active, match_rate}] (shard `robot.fields`)
    part_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)   # id -> {exact, n}
    notes: List[str] = field(default_factory=list)
    fitted: Dict[str, Dict[str, float]] = field(default_factory=dict)
    resolved: Dict[str, str] = field(default_factory=dict)  # field key -> TBA base name used

    @property
    def any_active(self) -> bool:
        return bool(self.active_parts)


def _truthy(x: Any) -> bool:
    return x is True


def _as_float(x: Any) -> Optional[float]:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _parse_split_total(expr: str) -> Tuple[float, str, str, float]:
    """'8 if autoBridgeState != 'Level' else 12' -> (8, 'autoBridgeState', 'Level', 12)."""
    m = re.match(r"^\s*(\d+) if (\w+) != '(\w+)' else (\d+)\b", expr)  # trailing prose allowed
    if not m:
        raise ValueError(f"unsupported split_once alliance_points expression: {expr!r}")
    return float(m.group(1)), m.group(2), m.group(3), float(m.group(4))


def _part_id(field_key: str, part: Dict[str, Any], taken: Dict[str, int]) -> str:
    return f"{field_key}:{part['slot']}"


def _evaluate(part: Dict[str, Any], vals: List[Optional[str]], bd: Dict[str, Any],
              pmap: Optional[Dict[str, float]] = None) -> Optional[np.ndarray]:
    """Points per robot (3,) for one part on one alliance; None if an input is missing."""
    rule = part["rule"]
    pts = np.zeros(3)
    if rule == "map":
        m = pmap if pmap is not None else part["points"]
        for i, v in enumerate(vals):
            pts[i] = float(m.get(v, 0)) if v is not None else 0.0
        return pts
    if rule == "split_once":
        a, state_field, level, b = _parse_split_total(part["alliance_points"])
        qual = [i for i, v in enumerate(vals) if v in part["qualifying"]]
        if not qual:
            return pts
        state = bd.get(state_field)
        if state is None:
            return None
        total = b if state == level else a
        for i in qual:
            pts[i] = total / len(qual)
        return pts
    if rule == "map_bridge":
        state = bd.get(part["state_field"])
        if state is None:
            return None
        m = part["points_level"] if state == part["level_value"] else part["points_unlevel"]
        for i, v in enumerate(vals):
            pts[i] = float(m.get(v, 0)) if v is not None else 0.0
        return pts
    if rule == "chain_share":
        total = _as_float(bd.get(part["check_field"]))
        if total is None:
            return None
        locs = part["locations"]
        counts: Dict[str, int] = {}
        for v in vals:
            if v in locs:
                counts[v] = counts.get(v, 0) + 1
        sharing = [i for i, v in enumerate(vals) if v in locs and counts[v] >= 2]
        for i in sharing:
            pts[i] = total / len(sharing)
        return pts
    if rule == "location_flag":
        for i, v in enumerate(vals):
            if v in part["locations"] and _truthy(bd.get(part["flag_prefix"] + v)):
                pts[i] = float(part["points_per_robot"])
        return pts
    if rule == "location_credit":
        locs = part["locations"]
        onstage = [i for i, v in enumerate(vals) if v in locs]
        for loc in locs:
            if not _truthy(bd.get(part["flag_prefix"] + loc)):
                continue
            here = [i for i in onstage if vals[i] == loc] or onstage
            for i in here:
                pts[i] += part["points_per_flag"] / len(here)
        return pts
    raise ValueError(f"unknown rule {rule!r}")


def _camel_words(key: str) -> List[str]:
    words = [w.lower() for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", key)]
    words = [w for w in words if w != "robot"]
    out: List[str] = []
    for w in words:  # "end" "game" -> "endgame"
        if out and out[-1] == "end" and w == "game":
            out[-1] = "endgame"
        else:
            out.append(w)
    return out


def _iter_alliances(raw: Dict[str, Any]):
    for ed in (raw.get("event_data") or {}).values():
        if not isinstance(ed, dict):
            continue
        for m in ed.get("matches") or []:
            if not isinstance(m, dict) or not isinstance(m.get("key"), str):
                continue
            als = m.get("alliances") or {}
            sbd = m.get("score_breakdown") or {}
            for color in ("red", "blue"):
                a = als.get(color) or {}
                tk = a.get("team_keys") or []
                bd = sbd.get(color) if isinstance(sbd, dict) else None
                yield m["key"], color, (bd if isinstance(bd, dict) else None), len(tk)


def _robot_bases(raw: Dict[str, Any]) -> Dict[str, int]:
    """base names (key minus trailing 1-3) that appear as <base>1..3 in any breakdown -> count."""
    seen: Dict[str, int] = {}
    for _, _, bd, _ in _iter_alliances(raw):
        if bd is None:
            continue
        for k in bd:
            if len(k) > 1 and k[-1] in "123" and k[:-1].endswith("Robot"):
                seen[k[:-1]] = seen.get(k[:-1], 0) + 1
        if sum(seen.values()) > 3000:
            break
    return seen


def robot_facts(year: int, raw: Dict[str, Any], spec: Optional[Dict[str, Any]] = None) -> RobotFacts:
    spec = spec or load_spec()
    season = (spec.get("seasons") or {}).get(str(year))
    if season is None:
        return RobotFacts(year=year, by_match={}, active_parts=[], match_rate={}, fields=[],
                          notes=[f"no per-robot field map for {year}"])
    notes: List[str] = []
    alliances = [(mk, c, bd, n) for mk, c, bd, n in _iter_alliances(raw)]
    with_bd = [(mk, c, bd, n) for mk, c, bd, n in alliances if bd]
    present = _robot_bases(raw)

    # --- resolve field names (declared name, else discovery for unverified fields) ---
    fields = season["fields"]
    resolved: Dict[str, Optional[str]] = {}
    used: set = set()
    for f in fields:
        base = f["tba"].replace("{n}", "")
        if base in present:
            resolved[f["key"]] = base
            used.add(base)
    for f in fields:
        if f["key"] in resolved:
            continue
        resolved[f["key"]] = None
        words = _camel_words(f["key"])
        cands = [b for b in present if b not in used and all(w in b.lower() for w in words)]
        if cands and not f.get("verified", True):
            best = sorted(cands, key=lambda b: (len(b), b))[0]
            resolved[f["key"]] = best
            used.add(best)
            notes.append(f"{f['key']}: declared name {f['tba']} not in data; using discovered field {best}1..3")
        else:
            notes.append(f"{f['key']}: field not found in data (looked for {f['tba']})")

    # --- parts ---
    parts: List[Dict[str, Any]] = []
    id_count: Dict[str, int] = {}
    for f in fields:
        for p in f["parts"]:
            pid = _part_id(f["key"], p, id_count)
            id_count[pid] = id_count.get(pid, 0) + 1
            parts.append({"id": pid, "field": f["key"], "base": resolved.get(f["key"]), "spec": p,
                          "fspec": f})
    for p in parts:  # disambiguate duplicate ids with the check field
        if id_count[p["id"]] > 1:
            p["id"] = f"{p['id']}:{p['spec']['check_field']}"

    # --- first pass: evaluate every part on every alliance with a breakdown ---
    def robot_values(bd: Dict[str, Any], base: str) -> List[Optional[str]]:
        out = []
        for i in range(3):
            v = bd.get(f"{base}{i + 1}")
            out.append(v if isinstance(v, str) else None)
        return out

    all_values: Dict[str, set] = {f["key"]: set() for f in fields}
    stats: Dict[str, Dict[str, int]] = {p["id"]: {"exact": 0, "n": 0} for p in parts}
    results: Dict[Tuple[str, str], Dict[str, Tuple[np.ndarray, bool]]] = {}
    vals_cache: Dict[Tuple[str, str], Dict[str, List[Optional[str]]]] = {}

    # observed values per field
    for mk, c, bd, _ in with_bd:
        vc = {}
        for f in fields:
            base = resolved.get(f["key"])
            if base is None:
                continue
            v = robot_values(bd, base)
            vc[f["key"]] = v
            all_values[f["key"]].update(x for x in v if x is not None)
        vals_cache[(mk, c)] = vc

    fitted: Dict[str, Dict[str, float]] = {}
    pmaps: Dict[str, Optional[Dict[str, float]]] = {p["id"]: None for p in parts}

    def run_part(p: Dict[str, Any], pmap: Optional[Dict[str, float]], store: bool) -> Tuple[int, int]:
        n = ex = 0
        spec_p = p["spec"]
        for mk, c, bd, _ in with_bd:
            vc = vals_cache[(mk, c)]
            if p["base"] is None or p["field"] not in vc:
                continue
            chk = _as_float(bd.get(spec_p["check_field"]))
            if chk is None:
                continue
            if all(v is None for v in vc[p["field"]]):
                continue  # per-robot values absent on this alliance
            pts = _evaluate(spec_p, vc[p["field"]], bd, pmap)
            if pts is None:
                continue
            n += 1
            exact = abs(float(pts.sum()) - chk) < 1e-6
            ex += int(exact)
            if store:
                results.setdefault((mk, c), {})[p["id"]] = (pts, exact)
        return ex, n

    for p in parts:
        ex, n = run_part(p, None, False)
        rate = ex / n if n else 0.0
        if (n and rate < ACTIVE_MIN_RATE and p["spec"]["rule"] == "map"
                and not p["spec"].get("verified", True) and p["base"] is not None):
            fit = _fit_map(p, with_bd, vals_cache)
            if fit is not None:
                ex2, n2 = run_part(p, fit, False)
                if n2 and ex2 / n2 > rate:
                    pmaps[p["id"]] = fit
                    fitted[p["id"]] = fit
                    ex, n = ex2, n2
                    notes.append(f"{p['id']}: declared points did not reproduce {p['spec']['check_field']} "
                                 f"(rate {rate:.3f}); fitted {fit} (rate {ex / n:.3f})")
        stats[p["id"]] = {"exact": ex, "n": n}

    match_rate = {pid: (s["exact"] / s["n"] if s["n"] else float("nan")) for pid, s in stats.items()}
    for p in parts:
        s = stats[p["id"]]
        p["rate"] = (s["exact"] / s["n"]) if s["n"] else None
        p["active"] = bool(s["n"] and p["rate"] >= ACTIVE_MIN_RATE)
        if p["base"] is None:
            p["active"] = False
        if not p["active"] and p["base"] is not None:
            notes.append(f"{p['id']}: inactive, match rate {0 if p['rate'] is None else p['rate']:.4f} "
                         f"({s['exact']} of {s['n']}) < {ACTIVE_MIN_RATE}")
    active = [p for p in parts if p["active"]]

    # second pass for active parts (re-run with the chosen point map so results are stored)
    results.clear()
    for p in active:
        run_part(p, pmaps[p["id"]], True)

    # --- build AllianceFacts per match ---
    by_match: Dict[str, List[Optional[AllianceFacts]]] = {}
    for mk, c, bd, _ in alliances:
        slot = by_match.setdefault(mk, [None, None])
        if not bd:
            continue
        vc = vals_cache.get((mk, c), {})
        pts18 = np.full((3, 18), np.nan)
        evp: Dict[str, np.ndarray] = {}
        scored = np.zeros(3)
        ok = True
        res = results.get((mk, c), {})
        for p in active:
            r = res.get(p["id"])
            if r is None:
                ok = False
                continue
            pts, exact = r
            ok = ok and exact
            sp = p["spec"]
            scored = scored + pts
            s = sp["slot"]
            evp[s] = evp.get(s, np.zeros(3)) + pts
            for k in sp.get("also_in", []):
                if k in ("auto", "teleop", "endgame") and k != s:
                    evp[k] = evp.get(k, np.zeros(3)) + pts
            if s in BK_SLOT:  # a rating slot (comp_i)
                si = BK_SLOT[s]
                pts18[:, si] = np.where(np.isnan(pts18[:, si]), 0.0, pts18[:, si]) + pts
                for k in sp.get("also_in", []):
                    ai = _AGG_SLOT.get(k)
                    if ai is not None:
                        pts18[:, ai] = np.where(np.isnan(pts18[:, ai]), 0.0, pts18[:, ai]) + pts
        # facts: every field with a resolved name, whether or not its parts are active
        slot[0 if c == "red" else 1] = AllianceFacts(
            values={k: list(v) for k, v in vc.items()}, points=pts18, ok=bool(ok and active),
            event_points=evp, scored=scored)
    by_match_t = {mk: (v[0], v[1]) for mk, v in by_match.items()}

    # --- shard `fields` ---
    out_fields = []
    for f in fields:
        declared = list(f.get("values", []))
        extra = sorted(all_values.get(f["key"], set()) - set(declared))
        fparts = [p for p in parts if p["field"] == f["key"]]
        rates = [p["rate"] for p in fparts if p["rate"] is not None]
        out_fields.append({
            "key": f["key"],
            "codes": declared + extra,
            "active": any(p["active"] for p in fparts),
            "match_rate": (min(rates) if rates else None),
        })
    return RobotFacts(
        year=year, by_match=by_match_t,
        active_parts=[{**p["spec"], "id": p["id"], "field": p["field"], "base": p["base"],
                       "rate": p["rate"], "points": (pmaps[p["id"]] or p["spec"].get("points"))}
                      for p in active],
        match_rate={k: (None if math.isnan(v) else v) for k, v in match_rate.items()},
        fields=out_fields, part_stats=stats, notes=notes, fitted=fitted,
        resolved={k: v for k, v in resolved.items() if v},
    )


def _fit_map(p: Dict[str, Any], with_bd, vals_cache) -> Optional[Dict[str, float]]:
    """Least-squares points per value for a `map` part (2026 discovery); integers or None."""
    spec_p = p["spec"]
    vs = sorted({v for (mk, c), vc in vals_cache.items() for v in vc.get(p["field"], [])
                 if v not in _NONE_VALUES})
    if not vs or len(vs) > 8:
        return None
    rows, ys = [], []
    for mk, c, bd, _ in with_bd:
        vc = vals_cache[(mk, c)].get(p["field"])
        chk = _as_float(bd.get(spec_p["check_field"]))
        if vc is None or chk is None:
            continue
        rows.append([sum(1 for v in vc if v == x) for x in vs])
        ys.append(chk)
    if len(rows) < 10:
        return None
    sol, *_ = np.linalg.lstsq(np.array(rows, float), np.array(ys, float), rcond=None)
    fit = {v: float(round(s)) for v, s in zip(vs, sol)}
    return fit


def summarize(facts: RobotFacts) -> Dict[str, Any]:
    """Human-readable reconciliation report (used by the build log and the WP2 report)."""
    return {
        "year": facts.year,
        "parts": {pid: {"exact": s["exact"], "n": s["n"], "rate": facts.match_rate.get(pid)}
                  for pid, s in facts.part_stats.items()},
        "active": [p["id"] for p in facts.active_parts],
        "notes": facts.notes,
    }

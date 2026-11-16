"""Flat-file export of one computed season to out/{year}/.

Files
  years.csv         one row, every upstream Year field (means, EPA percentiles, metrics)
  events.csv        every Event field, sorted by key
  team_years.csv    every TeamYear field except the team_matches list, sorted by team
  team_events.csv   every TeamEvent field, sorted by (team, time, event)
  matches.csv       every scalar Match field (results, cleaned breakdown, EPA predictions),
                    in EPA processing order (stable sort on time = upstream order)
  team_matches.csv  one row per team per match, processing order, red then blue:
                    pre-match vector (pre_*, from match.pre_epas) and post-match vector
                    (epa, auto_epa, ... comp_9_epa, from match.epas; empty if the match was not
                    played / the team was beyond the 3rd slot)
  match_auto.csv    one row per alliance per match: identifiers, teams, auto points, every auto
                    component (comp slots whose breakdown.py name starts with "auto_"), named by
                    breakdown.py, auto_not_in_comps = auto_points - sum(auto comps) (2025: auto
                    leave points; 2023: auto grid points minus the auto piece count, since auto
                    grid points have no slot of their own; 2024/2026: 0), and
                    pred_auto = sum of the alliance's pre-match auto_epa
  comp_names.json   breakdown.py key_to_name for the year (comp_i -> name, plus standard slots)
                    and "auto_comps": [comp_i, ...]

Determinism: fixed row and column order, floats written with repr(float(x)), enums by value,
None as empty, booleans as true/false, "\n" line endings.
"""
import csv
import dataclasses
import json
import math
import os
from enum import Enum
from typing import Any, Dict, Iterable, List, Sequence

import sb  # noqa: F401
from sb.models import Event, Match, TeamEvent, TeamYear, Year

from src.breakdown import key_to_name  # noqa: E402

EPA_KEYS_2016 = (
    ["epa", "auto_epa", "teleop_epa", "endgame_epa", "rp_1_epa", "rp_2_epa", "rp_3_epa",
     "tiebreaker_epa"]
    + [f"comp_{i}_epa" for i in range(10)]
)


def fmt(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, Enum):
        return str(x.value)
    if isinstance(x, bool):
        return "true" if x else "false"
    if hasattr(x, "item") and not isinstance(x, (list, dict, str)):
        x = x.item()  # numpy scalar
        if isinstance(x, bool):
            return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if math.isnan(x):
            return ""
        if x == int(x) and abs(x) < 1e15:
            return repr(float(x))
        return repr(x)
    return str(x)


def _write(path: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(list(header))
        for row in rows:
            w.writerow([fmt(v) for v in row])
            n += 1
    return n


def _fields(cls: type, exclude: Sequence[str] = ()) -> List[str]:
    return [f.name for f in dataclasses.fields(cls) if f.name not in exclude]


def comp_names(year: int) -> Dict[str, str]:
    return dict(key_to_name.get(year, {}))


def auto_comps(year: int) -> List[str]:
    """comp slots that are auto components: breakdown.py name starts with 'auto_'."""
    return [k for k, v in comp_names(year).items() if k.startswith("comp_") and v.startswith("auto_")]


def processing_order(matches: Dict[str, Match]) -> List[Match]:
    # identical to src/data/epa/calc.py: sorted(matches.values(), key=lambda m: m.time)
    return sorted(matches.values(), key=lambda m: m.time)


def export_year(objs, out_dir: str) -> Dict[str, int]:
    year_obj, team_years, events, team_events, matches, _ = objs
    year = year_obj.year
    d = os.path.join(out_dir, str(year))
    os.makedirs(d, exist_ok=True)
    counts: Dict[str, int] = {}

    yf = _fields(Year)
    counts["years"] = _write(os.path.join(d, "years.csv"), yf, [[getattr(year_obj, k) for k in yf]])

    ef = _fields(Event)
    counts["events"] = _write(
        os.path.join(d, "events.csv"), ef,
        ([getattr(e, k) for k in ef] for e in sorted(events.values(), key=lambda e: e.key)),
    )

    tyf = _fields(TeamYear, exclude=("team_matches",))
    counts["team_years"] = _write(
        os.path.join(d, "team_years.csv"), tyf,
        ([getattr(t, k) for k in tyf] for t in sorted(team_years.values(), key=lambda t: t.team)),
    )

    tef = _fields(TeamEvent)
    counts["team_events"] = _write(
        os.path.join(d, "team_events.csv"), tef,
        ([getattr(t, k) for k in tef]
         for t in sorted(team_events.values(), key=lambda t: (t.team, t.time or 0, t.event))),
    )

    order = processing_order(matches)

    mf = _fields(Match, exclude=("pre_epas", "epas"))
    counts["matches"] = _write(
        os.path.join(d, "matches.csv"), ["seq"] + mf,
        ([i] + [getattr(m, k) for k in mf] for i, m in enumerate(order)),
    )

    epa_keys = EPA_KEYS_2016 if year >= 2016 else ["epa"]
    tm_head = (
        ["seq", "match", "event", "week", "comp_level", "elim", "status", "time", "alliance",
         "slot", "team"]
        + [f"pre_{k}" for k in epa_keys]
        + epa_keys
    )

    def tm_rows():
        for i, m in enumerate(order):
            pre = m.pre_epas or {}
            post = m.epas or {}
            for alliance, teams in (("red", m.get_red()), ("blue", m.get_blue())):
                for slot, t in enumerate(teams, 1):
                    p = pre.get(str(t), {})
                    q = post.get(str(t), {})
                    yield (
                        [i, m.key, m.event, m.week, m.comp_level, m.elim, m.status, m.time,
                         alliance, slot, t]
                        + [p.get(k) for k in epa_keys]
                        + [q.get(k) for k in epa_keys]
                    )

    counts["team_matches"] = _write(os.path.join(d, "team_matches.csv"), tm_head, tm_rows())

    names = comp_names(year)
    acomps = auto_comps(year)
    ma_head = (
        ["seq", "match", "event", "week", "comp_level", "set_number", "match_number", "elim",
         "status", "time", "alliance", "team_1", "team_2", "team_3", "teams", "auto_points"]
        + [names[c] for c in acomps]
        + ["auto_not_in_comps", "pred_auto"]
    )

    def ma_rows():
        for i, m in enumerate(order):
            pre = m.pre_epas or {}
            for alliance, teams in (("red", m.get_red()), ("blue", m.get_blue())):
                t3 = (teams + [None, None, None])[:3]
                pa = [pre.get(str(t), {}).get("auto_epa") for t in teams[:3]]
                pred = round(sum(pa), 2) if pa and all(x is not None for x in pa) else None
                auto = getattr(m, f"{alliance}_auto")
                cv = [getattr(m, f"{alliance}_{c}") for c in acomps]
                rest = None if auto is None else auto - sum(v or 0 for v in cv)
                yield (
                    [i, m.key, m.event, m.week, m.comp_level, m.set_number, m.match_number,
                     m.elim, m.status, m.time, alliance, *t3,
                     " ".join(str(t) for t in teams), auto]
                    + cv
                    + [rest, pred]
                )

    counts["match_auto"] = _write(os.path.join(d, "match_auto.csv"), ma_head, ma_rows())

    with open(os.path.join(d, "comp_names.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"year": year, "names": names, "auto_comps": acomps}, f, indent=1, sort_keys=True)
        f.write("\n")
    return counts

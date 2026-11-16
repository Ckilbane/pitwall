"""WP2 test fixtures: deterministic synthetic TBA dumps and slices of the real dumps.

`make_synthetic_2026(variant)` builds a small 2026-shaped dump (no real 2026 data exists yet):
three FMA/Israel-district events (one finished, one ongoing with unplayed matches, one upcoming
with only a team list), 2026 score breakdowns (hubScore.*, autoTowerPoints, endGameTowerPoints),
`districts` and per-event `teams` lists, and per-robot tower fields in one of these variants:

  declared  fields named as docs/perrobot_fields.json guesses (autoTowerRobotN, endGameTowerRobotN),
            points 15 / 10-20-30
  renamed   same data under other names (autoTowerStateRobotN, endGameTowerStateRobotN) -> found by
            discovery
  fitted    declared names, different point values (Level1 8, Level2 16, Level3 24 endgame) -> the
            declared points fail the 99% check and are replaced by the least-squares fit
  absent    no per-robot fields at all -> "field not found", nothing active
  wrong     declared names but values that cannot reproduce the totals -> parts inactive

`slice_real_dump(raw, keys)` keeps a few events of a real dump (order preserved).
"""
import copy
import random
from typing import Any, Dict, List, Optional, Sequence

TEAMS = list(range(1200, 1230)) + [4000, 4001, 5000, 5001]


def _bd(rng: random.Random, variant: str, robots: Dict[str, List[str]]) -> Dict[str, Any]:
    lvl_auto = {"None": 0, "Level1": 15 if variant != "fitted" else 15}
    pts_end = {"None": 0, "Level1": 10, "Level2": 20, "Level3": 30}
    if variant == "fitted":
        pts_end = {"None": 0, "Level1": 8, "Level2": 16, "Level3": 24}
    a_vals = robots["auto"]
    e_vals = robots["end"]
    auto_tower = sum(lvl_auto[v] for v in a_vals)
    end_tower = sum(pts_end[v] for v in e_vals)
    if variant == "wrong":
        end_tower = rng.choice([0, 10, 20, 30, 40])
    hub = {
        "autoPoints": rng.randint(0, 25), "transitionPoints": rng.randint(0, 12),
        "shift1Points": rng.randint(0, 12), "shift2Points": rng.randint(0, 12),
        "shift3Points": rng.randint(0, 12), "shift4Points": rng.randint(0, 12),
        "endgamePoints": rng.randint(0, 10),
    }
    d: Dict[str, Any] = {
        "hubScore": hub, "autoTowerPoints": auto_tower, "endGameTowerPoints": end_tower,
        "energizedAchieved": rng.random() < 0.3, "superchargedAchieved": rng.random() < 0.1,
        "traversalAchieved": rng.random() < 0.2,
        "foulPoints": rng.choice([0, 0, 4, 8]), "adjustPoints": 0,
    }
    names = {"declared": ("autoTowerRobot", "endGameTowerRobot"), "fitted": ("autoTowerRobot", "endGameTowerRobot"),
             "wrong": ("autoTowerRobot", "endGameTowerRobot"), "renamed": ("autoTowerStateRobot", "endGameTowerStateRobot")}
    if variant != "absent":
        an, en = names[variant]
        for i in range(3):
            d[f"{an}{i + 1}"] = a_vals[i]
            d[f"{en}{i + 1}"] = e_vals[i]
    return d


def _alliance_score(bd: Dict[str, Any]) -> int:
    h = bd["hubScore"]
    fuel = sum(h.values())
    return int(fuel + bd["autoTowerPoints"] + bd["endGameTowerPoints"] + bd["foulPoints"])


def make_synthetic_2026(variant: str = "declared", seed: int = 7) -> Dict[str, Any]:
    rng = random.Random(seed)
    def ev(key, name, start, end, week, district, etype=1):
        return {"key": key, "name": name, "event_type": etype, "district": {"abbreviation": district},
                "year": 2026, "week": week, "city": "X", "state_prov": "PA", "country": "USA",
                "start_date": start, "end_date": end, "webcasts": [], "timezone": "America/New_York"}
    events = [
        ev("2026paaa", "Finished District Event", "2026-03-05", "2026-03-07", 0, "mar"),
        ev("2026pabb", "Ongoing District Event", "2026-03-14", "2026-03-16", 1, "mar"),
        ev("2026pacc", "Upcoming District Event", "2026-03-28", "2026-03-30", 2, "isr"),
    ]
    teams_raw = [{"key": f"frc{t}", "nickname": f"Team {t}", "city": "X", "state_prov": "PA",
                  "country": "USA", "rookie_year": 2000 + (t % 25)} for t in TEAMS]
    event_data: Dict[str, Any] = {}
    t0 = 1_772_700_000  # 2026-03-05

    def make_match(key, evk, level, sn, mn, red, blue, tm, played):
        robots = {c: {"auto": [rng.choice(["None", "Level1"]) for _ in range(3)],
                      "end": [rng.choice(["None", "None", "Level1", "Level2", "Level3"]) for _ in range(3)]}
                  for c in ("red", "blue")}
        m = {"key": key, "event_key": evk, "comp_level": level, "set_number": sn, "match_number": mn,
             "time": tm, "predicted_time": tm + 30, "videos": [],
             "alliances": {c: {"team_keys": [f"frc{t}" for t in ts], "score": -1,
                               "surrogate_team_keys": [], "dq_team_keys": []}
                           for c, ts in (("red", red), ("blue", blue))},
             "winning_alliance": "", "score_breakdown": None}
        if played:
            bds = {c: _bd(rng, variant, robots[c]) for c in ("red", "blue")}
            sc = {c: _alliance_score(bds[c]) for c in bds}
            for c in sc:
                m["alliances"][c]["score"] = sc[c]
            m["winning_alliance"] = "red" if sc["red"] > sc["blue"] else ("blue" if sc["blue"] > sc["red"] else "")
            m["score_breakdown"] = bds
        return m

    def quals(evk, pool, n, t_start, played_upto):
        out = []
        for i in range(1, n + 1):
            six = rng.sample(pool, 6)
            out.append(make_match(f"{evk}_qm{i}", evk, "qm", 1, i, six[:3], six[3:], t_start + 600 * i, i <= played_upto))
        return out

    pool1, pool2, pool3 = TEAMS[:24], TEAMS[8:32], TEAMS[12:36]
    m1 = quals("2026paaa", pool1, 36, t0, 36)
    # three playoff matches
    for sn, (r, b) in enumerate([(pool1[:3], pool1[3:6]), (pool1[6:9], pool1[9:12])], 1):
        m1.append(make_match(f"2026paaa_sf{sn}m1", "2026paaa", "sf", sn, 1, r, b, t0 + 40000 + sn * 900, True))
    m1.append(make_match("2026paaa_f1m1", "2026paaa", "f", 1, 1, pool1[:3], pool1[6:9], t0 + 50000, True))
    event_data["2026paaa"] = {
        "matches": m1,
        "rankings": {"rankings": [{"rank": i + 1, "team_key": f"frc{t}"} for i, t in enumerate(pool1)]},
        "alliances": [{"name": "Alliance 1", "picks": [f"frc{t}" for t in pool1[:3]], "declines": [],
                       "status": {"level": "f", "status": "won", "record": {"wins": 2, "losses": 0, "ties": 0}}},
                      {"name": "Alliance 2", "picks": [f"frc{t}" for t in pool1[6:9]], "declines": [],
                       "backup": {"in": f"frc{pool1[12]}", "out": f"frc{pool1[11]}"},
                       "status": {"level": "f", "status": "eliminated", "record": {"wins": 0, "losses": 2, "ties": 0}}}],
    }
    event_data["2026pabb"] = {
        "matches": quals("2026pabb", pool2, 30, t0 + 9 * 86400, 20),
        "rankings": {"rankings": []}, "alliances": [],
        "teams": [f"frc{t}" for t in pool2],
    }
    event_data["2026pacc"] = {"matches": [], "rankings": {"rankings": []}, "alliances": None,
                              "teams": [f"frc{t}" for t in pool3]}
    districts = {"mar": [f"frc{t}" for t in TEAMS[:28]], "isr": [f"frc{t}" for t in TEAMS[28:]]}
    return {"year": 2026, "fetched": "2026-03-15T12:00:00.000Z", "events": events,
            "event_data": event_data, "teams": teams_raw, "districts": districts}


def slice_real_dump(raw: Dict[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    """A dump with only `keys` events (and the teams that appear in them)."""
    keys = list(keys)
    out = {k: v for k, v in raw.items() if k not in ("events", "event_data", "teams")}
    out["events"] = [e for e in raw["events"] if e["key"] in keys]
    out["event_data"] = {k: copy.deepcopy(raw["event_data"][k]) for k in raw["event_data"] if k in keys}
    used = set()
    for ed in out["event_data"].values():
        for m in ed.get("matches") or []:
            for a in ("red", "blue"):
                used.update(m["alliances"][a]["team_keys"])
    out["teams"] = [t for t in raw.get("teams", []) if t["key"] in used]
    return out


def pick_events(raw: Dict[str, Any]) -> List[str]:
    """One week-1 event (so Year means exist), one later district event, one champs division
    (type 3), one district championship: deterministic by dump order."""
    want = {"w1": None, "district": None, "champs_div": None, "dcmp": None}
    for e in raw["events"]:
        ed = raw["event_data"].get(e["key"]) or {}
        if not ed.get("matches"):
            continue
        t, w = e.get("event_type"), e.get("week")
        if t in (0, 1) and w == 0 and want["w1"] is None and len(ed["matches"]) < 140:
            want["w1"] = e["key"]
        elif t == 1 and w is not None and w >= 2 and want["district"] is None and len(ed["matches"]) < 140:
            want["district"] = e["key"]
        elif t == 3 and want["champs_div"] is None:
            want["champs_div"] = e["key"]
        elif t == 2 and want["dcmp"] is None:
            want["dcmp"] = e["key"]
    return [k for k in want.values() if k]


def raw_dump_path(year: int) -> Optional[str]:
    """Real dump for `year` if available: $PW_RAW_DIR, the shared upload dir, or tests/fixtures."""
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.environ.get("PW_RAW_DIR"), "/mnt/user-data/uploads/IDM/sbrebuild/raw",
             "/home/claude/sbrebuild/raw", os.path.join(here, "fixtures")]
    for d in cands:
        if d:
            p = os.path.join(d, f"tba_{year}.json.gz")
            if os.path.exists(p):
                return p
    return None

"""WP2: per-robot facts (SPEC 4.4): evidence counts 2022-2025, rule unit tests, 2026 discovery paths."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from sb import perrobot  # noqa: E402
from sb.loader import read_raw  # noqa: E402
import wp2_fixtures as fx  # noqa: E402

SPEC = perrobot.load_spec()

# evidence in docs/perrobot_fields.json: part id -> (exact, n)
EVIDENCE = {
    2022: {"taxiRobot:comp_0": (29290, 29290), "endgameRobot:comp_5": (29290, 29290)},
    2023: {"mobilityRobot:comp_0": (32632, 32632), "autoChargeStationRobot:comp_0": (32632, 32632),
           "endGameChargeStationRobot:comp_9:endGameParkPoints": (32632, 32632),
           "endGameChargeStationRobot:comp_9:endGameChargeStationPoints": (32632, 32632)},
    2024: {"autoLineRobot:comp_0": (33954, 33954), "endGameRobot:comp_5": (33954, 33954),
           "endGameRobot:comp_6": (33954, 33954), "endGameRobot:comp_9": (33954, 33954),
           "endGameRobot:comp_7": (33954, 33954),
           # alliance total is 5 x lit flags (exact), but 49 alliances scored a trap with no robot onstage
           "endGameRobot:comp_8": (33905, 33954)},
    2025: {"autoLineRobot:auto": (35685, 35692), "endGameRobot:comp_9": (35692, 35692)},
}


@pytest.mark.parametrize("year", [2022, 2023, 2024, 2025])
def test_evidence_counts_real_dumps(year):
    p = fx.raw_dump_path(year)
    if p is None:
        pytest.skip("no real dump")
    f = perrobot.robot_facts(year, read_raw(p))
    got = {pid: (s["exact"], s["n"]) for pid, s in f.part_stats.items()}
    assert got == EVIDENCE[year]
    assert all(r >= 0.99 for r in f.match_rate.values())
    assert len(f.active_parts) == len(EVIDENCE[year])
    assert not f.notes
    # every robot fact row has 3 robots and (3, 18) points with NaN where not robot-credited
    mk, (red, blue) = next((k, v) for k, v in f.by_match.items() if v[0] is not None)
    assert red.points.shape == (3, 18) and red.scored.shape == (3,)
    credited = {int(i) for i in np.where(~np.isnan(red.points).all(axis=0))[0]}
    assert credited == {perrobot.BK_SLOT[p["slot"]] for p in f.active_parts if p["slot"] in perrobot.BK_SLOT} | \
        {a for p in f.active_parts if p["slot"] in perrobot.BK_SLOT
         for a in (perrobot._AGG_SLOT[k] for k in p.get("also_in", []) if k in perrobot._AGG_SLOT)}


def _part(year, key, slot):
    for f in SPEC["seasons"][str(year)]["fields"]:
        for p in f["parts"]:
            if f["key"] == key and p["slot"] == slot:
                return p
    raise KeyError((year, key, slot))


def test_rule_map_and_bridge_2023():
    park = _part(2023, "endGameChargeStationRobot", "comp_9")  # first = park map
    assert list(perrobot._evaluate(park, ["Park", "Docked", "None"], {}, None)) == [2, 0, 0]
    bridge = [p for f in SPEC["seasons"]["2023"]["fields"] for p in f["parts"] if p["rule"] == "map_bridge"][0]
    lvl = perrobot._evaluate(bridge, ["Docked", "Docked", "Park"], {"endGameBridgeState": "Level"}, None)
    unl = perrobot._evaluate(bridge, ["Docked", "Docked", "Park"], {"endGameBridgeState": "NotLevel"}, None)
    assert list(lvl) == [10, 10, 0] and list(unl) == [6, 6, 0]
    assert perrobot._evaluate(bridge, ["Docked", "None", "None"], {}, None) is None   # missing state -> not evaluable


def test_rule_split_once_2023_auto_dock():
    p = _part(2023, "autoChargeStationRobot", "comp_0")
    one = perrobot._evaluate(p, ["Docked", "None", "None"], {"autoBridgeState": "Level"}, None)
    two = perrobot._evaluate(p, ["Docked", "Docked", "None"], {"autoBridgeState": "NotLevel"}, None)
    none = perrobot._evaluate(p, ["None", "None", "None"], {"autoBridgeState": "Level"}, None)
    assert list(one) == [12, 0, 0] and list(two) == [4, 4, 0] and list(none) == [0, 0, 0]


def test_rules_2024_stage():
    chain = _part(2024, "endGameRobot", "comp_7")
    bd = {"endGameHarmonyPoints": 2}
    assert list(perrobot._evaluate(chain, ["StageLeft", "StageLeft", "None"], bd, None)) == [1, 1, 0]
    assert list(perrobot._evaluate(chain, ["StageLeft", "StageRight", "None"], {"endGameHarmonyPoints": 0}, None)) == [0, 0, 0]
    spot = _part(2024, "endGameRobot", "comp_9")
    bd = {"micStageLeft": True, "micCenterStage": False, "micStageRight": False}
    assert list(perrobot._evaluate(spot, ["StageLeft", "CenterStage", "Parked"], bd, None)) == [1, 0, 0]
    trap = _part(2024, "endGameRobot", "comp_8")
    bd = {"trapStageRight": True, "trapCenterStage": False, "trapStageLeft": False}
    # credited to the robot at that location; else shared by onstage robots; else residual
    assert list(perrobot._evaluate(trap, ["StageRight", "StageLeft", "None"], bd, None)) == [5, 0, 0]
    assert np.allclose(perrobot._evaluate(trap, ["StageLeft", "CenterStage", "None"], bd, None), [2.5, 2.5, 0])
    assert list(perrobot._evaluate(trap, ["Parked", "None", "None"], bd, None)) == [0, 0, 0]


# ---------------------------------------------------------------- 2026 (synthetic dump)

def _facts(variant):
    return perrobot.robot_facts(2026, fx.make_synthetic_2026(variant))


def test_2026_declared_names_are_active_and_reconcile():
    f = _facts("declared")
    assert {p["id"] for p in f.active_parts} == {"autoTowerRobot:comp_1", "endGameTowerRobot:comp_6"}
    assert all(v == 1.0 for v in f.match_rate.values())
    assert not f.fitted and not f.notes
    red, blue = next(v for v in f.by_match.values() if v[0] is not None)
    assert red.ok and red.points.shape == (3, 18)
    # comp_1 = auto tower (also in auto), comp_6 = endgame tower (also in endgame and total)
    assert not np.isnan(red.points[:, 9]).any() and not np.isnan(red.points[:, 14]).any()
    assert np.allclose(red.scored, red.event_points["comp_1"] + red.event_points["comp_6"])
    assert np.allclose(red.event_points["auto"], red.event_points["comp_1"])
    assert np.allclose(red.event_points["endgame"], red.event_points["comp_6"])
    assert np.allclose(red.points[:, 0], red.scored)


def test_2026_renamed_fields_found_by_discovery():
    f = _facts("renamed")
    assert f.resolved == {"autoTowerRobot": "autoTowerStateRobot", "endGameTowerRobot": "endGameTowerStateRobot"}
    assert len(f.active_parts) == 2 and all(r == 1.0 for r in f.match_rate.values())
    assert any("using discovered field" in n for n in f.notes)


def test_2026_wrong_points_are_fitted_when_possible():
    f = _facts("fitted")
    assert f.fitted == {"endGameTowerRobot:comp_6": {"Level1": 8.0, "Level2": 16.0, "Level3": 24.0}}
    assert {p["id"] for p in f.active_parts} == {"autoTowerRobot:comp_1", "endGameTowerRobot:comp_6"}
    assert all(r >= 0.99 for r in f.match_rate.values())


def test_2026_absent_fields_report_not_found_and_nothing_is_active():
    f = _facts("absent")
    assert f.active_parts == [] and all(not x["active"] for x in f.fields)
    assert sum("field not found" in n for n in f.notes) == 2
    assert all(v is None or v == 0 for v in f.match_rate.values()) or not f.match_rate


def test_2026_inconsistent_values_switch_parts_off():
    f = _facts("wrong")
    ids = {p["id"] for p in f.active_parts}
    assert "endGameTowerRobot:comp_6" not in ids and "autoTowerRobot:comp_1" in ids
    assert f.match_rate["endGameTowerRobot:comp_6"] < 0.99
    assert any("inactive" in n for n in f.notes)
    # facts (values) are still exported for the inactive field; points are not
    red, _ = next(v for v in f.by_match.values() if v[0] is not None)
    assert "endGameTowerRobot" in red.values and np.isnan(red.points[:, 14]).all()


def test_unknown_season_has_no_facts():
    f = perrobot.robot_facts(2019, {"events": [], "event_data": {}})
    assert f.by_match == {} and f.active_parts == [] and f.notes


def test_untrusted_input_is_data_only():
    raw = fx.make_synthetic_2026("declared")
    m = raw["event_data"]["2026paaa"]["matches"][0]
    m["score_breakdown"]["red"]["autoTowerRobot1"] = {"__import__": "os"}   # wrong type: ignored
    m["score_breakdown"]["red"]["endGameTowerRobot2"] = "__import__('os')"  # unknown string: scores 0
    f = perrobot.robot_facts(2026, raw)
    assert f.by_match[m["key"]][0].values["autoTowerRobot"][0] is None

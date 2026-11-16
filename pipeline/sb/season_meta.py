"""Season-dependent keys and labels for the shards (SPEC 3.2, 3.3, 3.6).

Everything here is derived from the vendored `src.breakdown.key_to_name` (never retyped) plus the
few rules the spec lists.  The only literals are the game names, the 18-slot rating vector layout
(`SLOT_INDEX`, = `Year.get_mean_components` order) and the 2026 units exception.
"""
from typing import Any, Dict, List, Optional

import sb  # noqa: F401

from src.breakdown import key_to_name  # noqa: E402

SEASONS = (2022, 2023, 2024, 2025, 2026)

GAMES = {2022: "Rapid React", 2023: "Charged Up", 2024: "Crescendo", 2025: "Reefscape", 2026: "Rebuilt"}

SLOT_INDEX: Dict[str, int] = {
    "epa": 0, "auto_epa": 1, "teleop_epa": 2, "endgame_epa": 3,
    "rp_1_epa": 4, "rp_2_epa": 5, "rp_3_epa": 6, "tiebreaker_epa": 7,
    **{f"comp_{i}_epa": 8 + i for i in range(10)},
}

# breakdown key -> slot index in the same 18-vector (score/foul keys have none)
BK_SLOT: Dict[str, int] = {
    "no_foul": 0, "auto": 1, "teleop": 2, "endgame": 3, "rp_1": 4, "rp_2": 5, "rp_3": 6,
    "tiebreaker": 7, **{f"comp_{i}": 8 + i for i in range(10)},
}

WEEK_LABELS = ["Preseason"] + [f"After week {i}" for i in range(1, 8)] + ["End of season"]

_PHASE_PREFIX = {
    "auto_": "auto", "teleop_": "teleop", "endgame_": "endgame",
    # 2026 shifts are teleop
    "transition_": "teleop", "first_shift_": "teleop", "second_shift_": "teleop",
}


def names(year: int) -> Dict[str, str]:
    """key_to_name for the year (slot -> season name), in upstream order."""
    return dict(key_to_name.get(year, {}))


def _named_comps(year: int) -> List[int]:
    n = names(year)
    return [i for i in range(10) if f"comp_{i}" in n]


def has_rp3(year: int) -> bool:
    return "rp_3" in names(year)


def vector_keys(year: int) -> List[str]:
    """VK: rating vector keys (upstream TeamYear field names), SPEC 3.2."""
    vk = ["epa", "auto_epa", "teleop_epa", "endgame_epa", "rp_1_epa", "rp_2_epa"]
    if has_rp3(year):
        vk.append("rp_3_epa")
    vk.append("tiebreaker_epa")
    vk += [f"comp_{i}_epa" for i in _named_comps(year)]
    return vk


def breakdown_keys(year: int) -> List[str]:
    """BK: per-alliance result keys; match columns are red_<bk>, blue_<bk>."""
    bk = ["score", "no_foul", "foul", "auto", "teleop", "endgame", "rp_1", "rp_2"]
    if has_rp3(year):
        bk.append("rp_3")
    bk.append("tiebreaker")
    bk += [f"comp_{i}" for i in _named_comps(year)]
    return bk


def comp_of(vk: str) -> Optional[int]:
    if vk.startswith("comp_") and vk.endswith("_epa"):
        return int(vk[5:-4])
    return None


def slot_name(vk: str) -> str:
    """VK key -> key_to_name slot key (epa -> 'no_foul' has no name; others strip _epa)."""
    return vk[:-4] if vk.endswith("_epa") and vk != "epa" else vk


def units(year: int) -> Dict[str, str]:
    n = names(year)
    out: Dict[str, str] = {}
    for vk in vector_keys(year):
        c = comp_of(vk)
        if vk.startswith("rp_"):
            out[vk] = "rp_score"
        elif c is None:
            out[vk] = "points"
        elif year == 2026:
            out[vk] = "points"  # clean_breakdown_2026 reads *Points fields (SPEC 3.2)
        else:
            out[vk] = "points" if n[f"comp_{c}"].endswith("_points") else "count"
    return out


def groups(year: int) -> Dict[str, str]:
    n = names(year)
    out: Dict[str, str] = {}
    for vk in vector_keys(year):
        c = comp_of(vk)
        if vk == "epa":
            out[vk] = "total"
        elif vk in ("auto_epa", "teleop_epa", "endgame_epa"):
            out[vk] = vk[:-4]
        elif vk.startswith("rp_"):
            out[vk] = "rp"
        elif vk == "tiebreaker_epa":
            out[vk] = "tiebreaker"
        else:
            nm = n[f"comp_{c}"]
            g = "match"
            for pre, ph in _PHASE_PREFIX.items():
                if nm.startswith(pre):
                    g = ph
                    break
            out[vk] = g
    return out


def auto_comps(year: int) -> List[str]:
    from sb.export import auto_comps as _ac

    return _ac(year)


def keys_block(year: int) -> Dict[str, Any]:
    """season.json `keys` object, in schema order."""
    return {
        "vector": vector_keys(year),
        "breakdown": breakdown_keys(year),
        "names": names(year),
        "units": units(year),
        "groups": groups(year),
        "auto_comps": auto_comps(year),
    }


def season_status(
    year: int, current_season: Optional[int], event_statuses: List[str], end_dates: List[str],
    completed_matches: int, today: str,
) -> str:
    """upcoming / live / complete (SPEC 3.3)."""
    if completed_matches == 0:
        return "upcoming"
    if current_season is not None and year == current_season:
        if any(s != "Completed" for s in event_statuses if s != "Invalid"):
            return "live"
        if end_dates and today <= max(end_dates):
            return "live"
    return "complete"

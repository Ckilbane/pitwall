"""Model descriptions for data/models.json (WP3, SPEC 4.7).

    from sb.model_info import describe
    entry = describe("epa")                 # or describe("fast"), describe("epa", year_obj)

Returns one `models[]` entry of manifest.schema.json#/$defs/modelsFile:
`id, label, short_description, how_it_works[], constants, settings, source, config_hash,
[tuning]`.

EPA constants are read from the vendored modules at call time (never retyped):
`src.models.epa.constants` (NORM_MEAN, NORM_SD, INIT_PENALTY, YEAR_ONE_WEIGHT, MEAN_REVERSION,
ELIM_WEIGHT), `EPA.k_func(2026)` and `EPA.percent_func` sampled at x = 0, 6, 9, 12. Fast EPA
reads pipeline/models/fast_epa.json (WP3b) through `sb.fast_epa`; its hash is
`sb.model_runner.config_hash`, the same function the model runner stamps on each ModelRun.

With `year_obj` (an upstream Year), the season's prediction constants are added
(`score_sd`, `foul_rate`): the numbers site/js/model/predict.js uses from season.predict.
"""
import json
import os
from typing import Any, Dict, List, Optional

import sb  # noqa: F401  (vendored `src` on sys.path)
import sb.loader  # noqa: F401  (installs the sys.modules shim the vendored modules need)

import src.models.epa.constants as C  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM_FILE = os.path.join(PIPELINE, "vendor", "UPSTREAM")
K_YEAR = 2026
PERCENT_SAMPLES = (0, 6, 9, 12)


def _upstream_commit() -> str:
    try:
        with open(UPSTREAM_FILE, encoding="utf-8") as f:
            line = f.read().split("\n", 1)[0]
        repo, _, rest = line.partition(" @ ")
        return f"{repo.strip()} @ {rest.split()[0][:7]}" if rest else line.strip()
    except OSError:
        return "avgupta456/statbotics"


def _num(x: Any) -> Any:
    """Plain Python number (json-safe)."""
    return x.item() if hasattr(x, "item") else x


def _pct(x: float) -> str:
    s = f"{100 * x:.1f}".rstrip("0").rstrip(".")
    return s + "%"


def _year_constants(year_obj) -> Dict[str, Dict[str, Any]]:
    if year_obj is None:
        return {}
    return {
        "score_sd": {"value": _num(float(year_obj.score_sd)),
                     "meaning": f"{year_obj.year} score SD from week-1 matches (points); scales "
                                "the win probability"},
        "foul_rate": {"value": _num(float(year_obj.get_foul_rate())),
                      "meaning": f"{year_obj.year} mean foul points / mean non-foul points; "
                                 "predicted scores are multiplied by 1 + foul_rate"},
    }


def _epa_constants(year_obj=None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {
        "NORM_MEAN": {"value": _num(C.NORM_MEAN), "meaning": "Normalized EPA mean (unitless)"},
        "NORM_SD": {"value": _num(C.NORM_SD), "meaning": "Normalized EPA standard deviation (unitless)"},
        "INIT_PENALTY": {"value": _num(C.INIT_PENALTY),
                         "meaning": "New teams start this many SD below the mean"},
        "YEAR_ONE_WEIGHT": {"value": _num(C.YEAR_ONE_WEIGHT),
                            "meaning": "Weight of last season in the starting rating (the season "
                                       "before gets the rest)"},
        "MEAN_REVERSION": {"value": _num(C.MEAN_REVERSION),
                           "meaning": "Share of the starting rating pulled back to the new-team level"},
        "ELIM_WEIGHT": {"value": _num(C.ELIM_WEIGHT),
                        "meaning": "Weight of a playoff match update relative to a qual match"},
        "k": {"value": _num(EPA.k_func(K_YEAR)),
              "meaning": "Win probability = 1 / (1 + 10^(k * (red - blue) / score_sd))"},
    }
    for x in PERCENT_SAMPLES:
        out[f"percent_{x}"] = {
            "value": _num(EPA.percent_func(K_YEAR, x)),
            "meaning": f"Update step after {x} counted qual matches (share of the error)",
        }
    out.update(_year_constants(year_obj))
    return out


def _epa_how() -> List[str]:
    p = {x: EPA.percent_func(K_YEAR, x) for x in PERCENT_SAMPLES}
    init_norm = C.NORM_MEAN - C.INIT_PENALTY * C.NORM_SD
    k = EPA.k_func(K_YEAR)
    return [
        "EPA (expected points added) estimates the points a team adds to its alliance's score in "
        "a typical match. It also rates each scoring component (auto, teleop, endgame and the "
        "season's game pieces) and each ranking point. This is the Statbotics model, run unchanged.",
        f"A team's season starts from its last two seasons: {_pct(C.YEAR_ONE_WEIGHT)} of last "
        f"season's normalized EPA and {_pct(1 - C.YEAR_ONE_WEIGHT)} of the season before, then "
        f"{_pct(C.MEAN_REVERSION)} of the way back to the new-team level "
        f"({init_norm:g}, i.e. {C.INIT_PENALTY:g} SD below the {C.NORM_MEAN:g} mean). New teams "
        "start at that level.",
        "After each match the alliance's error (actual minus predicted, per component) is split "
        "equally among its three teams, and each rating moves part of the way toward its share: "
        f"{_pct(p[0])} of the error through a team's first 6 qual matches, "
        f"{_pct(p[9])} at 9 and {_pct(p[12])} from 12 on. Playoff matches count "
        f"{C.ELIM_WEIGHT:.2f} as much and do not change ranking-point ratings.",
        "A match prediction adds the three teams' ratings on each alliance. Win probability = "
        f"1 / (1 + 10^(k × (red − blue) / score SD)) with k = {k:g} and the season's score SD; "
        "the predicted scores then add the season's average foul points.",
        "Ranking points: each alliance's summed RP rating goes through a sigmoid centred on 0.5 "
        "to give a probability. Season rules: in 2023 a grid row holds at most 9 pieces (overflow "
        "moves down a row); in 2025 a processor algae counts 6 points for you and 3 for the "
        "opponent's net.",
        f"Normalized EPA rescales a season's ratings to mean {C.NORM_MEAN:g}, SD {C.NORM_SD:g}, "
        "so seasons compare. Season constants (means, score SD, foul rate) come from week 1, "
        "including the week-1 matches being predicted.",
    ]


def _fast_config(config_path: Optional[str]):
    """(raw config or None, merged settings, hash). Uses WP3b's loader and hash when present."""
    try:
        from sb import fast_epa as F  # WP3b
        path = config_path or F.CONFIG_PATH
        raw = F.load_config(path) if os.path.exists(path) else None
        settings = F.settings_of(raw)
    except ImportError:  # WP3b not landed: read the file directly, no defaults
        path = config_path or os.path.join(PIPELINE, "models", "fast_epa.json")
        raw = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        settings = dict((raw or {}).get("settings", {}))
    try:
        from sb.model_runner import config_hash  # WP3b
        h = config_hash(raw) if raw else ""
    except ImportError:
        import hashlib
        h = hashlib.sha256(json.dumps((raw or {}).get("settings", raw or {}), sort_keys=True,
                                      separators=(",", ":"), ensure_ascii=True).encode()).hexdigest() if raw else ""
    return raw, settings, h


def _fast_constants(s: Dict[str, Any], year_obj=None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    def add(name: str, value: Any, meaning: str) -> None:
        if value is not None:
            out[name] = {"value": _num(value), "meaning": meaning}

    pc = s.get("percent", {}) or {}
    add("percent_first_n", pc.get("first_n"), "Qual matches that use the first-match step")
    add("percent_first", pc.get("first"), "Update step for those first matches (share of the error)")
    add("percent_start", pc.get("start"), "Update step at the start of the ramp")
    add("percent_end", pc.get("end"), "Update step after the ramp")
    add("percent_ramp_start", pc.get("ramp_start"), "Qual match count where the ramp starts")
    add("percent_ramp_len", pc.get("ramp_len"), "Qual matches the ramp takes")
    add("elim_weight", s.get("elim_weight"), "Weight of a playoff match update relative to a qual match")
    add("mean_reversion", s.get("mean_reversion"), "Share of the starting rating pulled back to the new-team level")
    add("year_one_weight", s.get("year_one_weight"), "Weight of last season in the starting rating")
    add("robot_weight", s.get("robot_weight"), "Weight of per-robot attribution against the equal split")
    add("k", s.get("win_k"), "Win probability = 1 / (1 + 10^(k * (red - blue) / score_sd))")
    add("NORM_MEAN", C.NORM_MEAN, "Normalized EPA mean (unitless)")
    add("NORM_SD", C.NORM_SD, "Normalized EPA standard deviation (unitless)")
    out.update(_year_constants(year_obj))
    return out


def _fast_how(s: Dict[str, Any], tuned_on: List[int]) -> List[str]:
    pc = s.get("percent", {}) or {}
    pb = s.get("passback", {}) or {}
    lines = [
        "Fast EPA is a PitWall variant of EPA. It predicts matches the same way (same sums, "
        "sigmoids and season rules) but moves ratings faster early in a season and can credit "
        "points a single robot earned to that robot.",
    ]
    if pc:
        first_n = int(pc.get("first_n", 0) or 0)
        ramp = (f"{_pct(pc.get('start', 0))} of the error, falling to {_pct(pc.get('end', 0))} "
                f"over {pc.get('ramp_len')} matches from match {pc.get('ramp_start')}")
        elim = f"Playoff matches count {float(s.get('elim_weight', 0)):.2f} as much."
        if first_n > 0:
            lines.append(f"Update step: {_pct(pc.get('first', 0))} of the error for a team's first "
                         f"{first_n} qual matches, then {ramp}. {elim}")
        else:
            lines.append(f"Update step: {ramp}. {elim}")
    if s.get("robot_attribution"):
        lines.append("Where TBA reports a field per robot (for example leave, park or climb), that "
                     "robot gets its own points and error; the rest of the alliance error is split "
                     f"{'equally' if s.get('residual_split') == 'equal' else 'by predicted share'}. "
                     "Without per-robot data the error is split equally, as in EPA.")
    else:
        lines.append("The alliance error is split equally among the three teams, as in EPA.")
    if pb.get("enabled"):
        lines.append("Between events a team's qual matches at its previous event are replayed once "
                     f"with current ratings at {_pct(pb.get('percent_scale', 0))} of the normal step. "
                     "Only earlier matches are used, never the match being predicted.")
    seed = s.get("seed")
    lines.append("Starting ratings come from "
                 + ("last season's piece-count rating" if seed == "pieces" else "normalized EPA, as in EPA")
                 + f"; win probability uses k = {float(s.get('win_k', EPA.k_func(K_YEAR))):g}.")
    if tuned_on:
        lines.append(f"Settings were tuned on {', '.join(str(y) for y in tuned_on)} (win-probability "
                     "Brier score) and frozen; later seasons are out-of-sample checks.")
    else:
        lines.append("Settings are not tuned yet; treat Fast EPA numbers as provisional.")
    return lines


def describe(model_id: str, year_obj=None, config_path: Optional[str] = None) -> Dict[str, Any]:
    """models.json entry for `model_id` ("epa" | "fast")."""
    if model_id == "epa":
        return {
            "id": "epa",
            "label": "EPA",
            "short_description": "Statbotics EPA, reproduced exactly: points a team adds per match.",
            "how_it_works": _epa_how(),
            "constants": _epa_constants(year_obj),
            "settings": {},
            "source": f"Statbotics backend (MIT), {_upstream_commit()}, src/models/epa",
            "config_hash": "",
        }
    if model_id == "fast":
        raw, settings, h = _fast_config(config_path)
        tuning = (raw or {}).get("tuning")
        tuned_on = list((tuning or {}).get("tuned_on") or [])
        entry: Dict[str, Any] = {
            "id": "fast",
            "label": "Fast EPA",
            "short_description": "EPA with faster early-season updates and per-robot credit where "
                                 "TBA reports it.",
            "how_it_works": _fast_how(settings, tuned_on),
            "constants": _fast_constants(settings, year_obj),
            "settings": settings,
            "source": "PitWall pipeline/sb/fast_epa.py, config pipeline/models/fast_epa.json",
            "config_hash": h,
        }
        if tuning is not None:
            entry["tuning"] = tuning
        return entry
    raise ValueError(f"unknown model id {model_id!r}")

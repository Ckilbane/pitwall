"""Model runner (SPEC 4.1): one loader pass per season, any number of model runs on copies.

    base = load_base(2023, raw)                       # sb.loader.load_year, once
    epa  = run_model(2023, base, priors, "epa")
    fast = run_model(2023, base, priors, "fast", config=cfg, robot_facts=facts)

Mechanism (vendored files untouched): deep-copy the loaded objs, clear the `get_constants`
cache, point `src.data.epa.calc.EPA` at a zero-argument factory that returns OUR model instance,
run upstream `src.data.epa.main.process_year` (calc -> agg -> metrics, exactly upstream), restore
`calc.EPA` in `finally` and clear the cache again.  agg/metrics then fill ranks, norm_epa and the
Year/Event metric fields for whichever model ran, so every EPA-named field of `ModelRun.objs`
holds THIS model's values.

Recording (`RecordingMixin`) wraps predict_match / attribute_match / update_team, always calls
super() first and never changes model state:
  alliance_pred[match_key] = (red, blue) breakdown after post_process_breakdown (18-slot order)
  attrib[match_key][team]  = attribution vector (target the rating moved toward), 18 slots
  updated                  = {(match_key, team)} where update_team ran
  win_prob[match_key]      = unrounded win probability (match.epa_win_prob is rounded to 4 dp)
  pre_vec / post_vec[(match_key, team)] = unrounded rating vector before / after the match
                             (pre: every team of the match, as upstream pre_epas; post: every
                             attributed team, as upstream epas)
  final_vec[team]          = unrounded rating at the end of the data (every team of the season,
                             including teams with no matches yet)
"""
import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple, Union

import numpy as np

import sb  # noqa: F401
import sb.loader as loader  # installs the shim

import src.data.epa.calc as _calc  # noqa: E402
import src.data.epa.main as _epa_main  # noqa: E402
import src.models.epa.init as _epa_init  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402

MODEL_IDS = ("epa", "fast")


class RecordingMixin:
    """Records predictions, attributions and updates.  Put it FIRST in the bases."""

    def _rec_init(self) -> None:
        self.rec_alliance_pred: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self.rec_attrib: Dict[str, Dict[int, np.ndarray]] = {}
        self.rec_updated: Set[Tuple[str, int]] = set()
        self.rec_win_prob: Dict[str, float] = {}
        self.rec_pre_vec: Dict[Tuple[str, int], np.ndarray] = {}
        self.rec_post_vec: Dict[Tuple[str, int], np.ndarray] = {}
        self._rec_cur: Optional[str] = None
        self._rec_off = 0  # >0 while a model replays history internally (Fast EPA passback)

    def process_match(self, match, event, team_events, team_years):
        self._rec_cur = match.key
        try:
            return super().process_match(match, event, team_events, team_years)
        finally:
            self._rec_cur = None

    def pre_record_team(self, team, te, ty):
        if self._rec_cur is not None and not self._rec_off:
            self.rec_pre_vec[(self._rec_cur, int(team))] = np.array(
                self.epas[team].mean, dtype=float, copy=True)
        return super().pre_record_team(team, te, ty)

    def post_record_team(self, team, te, ty):
        if self._rec_cur is not None and not self._rec_off:
            self.rec_post_vec[(self._rec_cur, int(team))] = np.array(
                self.epas[team].mean, dtype=float, copy=True)
        return super().post_record_team(team, te, ty)

    def predict_match(self, match, event):
        out = super().predict_match(match, event)
        if not self._rec_off:
            win_prob, red_pred, blue_pred = out
            self.rec_alliance_pred[match.key] = (
                np.array(red_pred.breakdown, dtype=float, copy=True),
                np.array(blue_pred.breakdown, dtype=float, copy=True),
            )
            self.rec_win_prob[match.key] = float(win_prob)
        return out

    def attribute_match(self, match, red_pred, blue_pred):
        out = super().attribute_match(match, red_pred, blue_pred)
        if not self._rec_off:
            self.rec_attrib[match.key] = {
                int(t): np.array(a.epa, dtype=float, copy=True) for t, a in out.items()
            }
        return out

    def update_team(self, team, attrib, match):
        out = super().update_team(team, attrib, match)
        if not self._rec_off:
            self.rec_updated.add((match.key, int(team)))
        return out


class RecordingEPA(RecordingMixin, EPA):
    """The vendored EPA, unchanged, plus recording."""

    def __init__(self) -> None:
        super().__init__()
        self._rec_init()


@dataclass
class ModelRun:
    model_id: str  # "epa" | "fast"
    year: int
    objs: tuple  # upstream objs_type (deep copy); every EPA field holds THIS model's values
    alliance_pred: Dict[str, Tuple[np.ndarray, np.ndarray]]
    attrib: Dict[str, Dict[int, np.ndarray]]
    updated: Set[Tuple[str, int]]
    config_hash: str  # "" for EPA; sha256 of the canonical Fast EPA config
    win_prob: Dict[str, float] = field(default_factory=dict)
    k: float = -5 / 8
    config: Optional[dict] = None
    # unrounded 18-slot rating vectors (exporters write 3 decimals from these)
    pre_vec: Dict[Tuple[str, int], np.ndarray] = field(default_factory=dict)   # before match
    post_vec: Dict[Tuple[str, int], np.ndarray] = field(default_factory=dict)  # after match
    final_vec: Dict[int, np.ndarray] = field(default_factory=dict)  # current state, every team


def config_hash(config: Optional[dict]) -> str:
    if not config:
        return ""
    settings = config.get("settings", config)
    blob = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_base(year: int, raw: Union[dict, str]) -> tuple:
    """sb.loader.load_year once per season.  `raw` is a parsed dump or a path."""
    if isinstance(raw, dict):
        return loader.load_year(year, "<dict>", raw=raw)
    return loader.load_year(year, raw)


def make_model(model_id: str, config: Optional[dict] = None, robot_facts=None):
    if model_id == "epa":
        return RecordingEPA()
    if model_id == "fast":
        from sb.fast_epa import FastEPA, load_config

        return FastEPA(config if config is not None else load_config(), robot_facts)
    raise ValueError(f"unknown model id {model_id!r}")


def run_model(
    year: int,
    base_objs: tuple,
    all_team_years,
    model_id: str,
    config: Optional[dict] = None,
    robot_facts=None,
    *,
    stages: str = "full",
    copy_objs: bool = True,
) -> ModelRun:
    """Run one model over a season.

    stages: "full" = upstream calc -> agg -> metrics (what the exporters need);
            "calc" = calc only (predictions, ratings and recordings; used by tuning/backtests
                     that never read agg/metrics fields).
    copy_objs=False runs on `base_objs` itself (caller already made a private copy).
    """
    if int(base_objs[0].year) != int(year):
        raise ValueError(f"objs are for {base_objs[0].year}, asked for {year}")
    objs = copy.deepcopy(base_objs) if copy_objs else base_objs
    model = make_model(model_id, config, robot_facts)

    _epa_init.get_constants.cache_clear()
    prev = _calc.EPA
    _calc.EPA = lambda: model  # process_year calls EPA() with no arguments
    try:
        if stages == "full":
            objs = _epa_main.process_year(objs, all_team_years)
        elif stages == "calc":
            objs = _calc.process_year(objs, all_team_years)
        else:
            raise ValueError(f"stages must be 'full' or 'calc', not {stages!r}")
    finally:
        _calc.EPA = prev
        _epa_init.get_constants.cache_clear()

    cfg = None
    if model_id == "fast":
        cfg = model.config
    return ModelRun(
        model_id=model_id,
        year=int(year),
        objs=objs,
        alliance_pred=model.rec_alliance_pred,
        attrib=model.rec_attrib,
        updated=model.rec_updated,
        config_hash=config_hash(cfg) if model_id == "fast" else "",
        win_prob=model.rec_win_prob,
        k=float(model.k),
        config=cfg,
        pre_vec=model.rec_pre_vec,
        post_vec=model.rec_post_vec,
        final_vec={int(t): np.array(rt.mean, dtype=float, copy=True)
                   for t, rt in model.epas.items()},
    )

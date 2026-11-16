"""WP2 test/build support: a minimal stand-in for WP3b's `sb.model_runner` (SPEC 4.1).

WP3b owns the real runner.  Until it lands, WP2's tests and scratch builds need ModelRun objects, so
this file implements the same mechanism in ~100 lines: deep-copy the loader's objs, run the
vendored `src.data.epa.main.process_year` with `calc.EPA` temporarily replaced by a recording
subclass of the vendored EPA, and keep (alliance_pred, attrib, updated).  The `fast` stand-in is
either a copy of the EPA run (`variant="copy"`, the spec's fallback) or EPA with 1.3x larger update
steps (`variant="scaled"`, used only to prove that shards keep the two models' columns apart).

Not part of the shipped pipeline; `pipeline/sb/shards.py` only needs duck-typed ModelRun attributes
(model_id, year, objs, alliance_pred, attrib, updated, config_hash).
"""
import copy
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import sb.loader as loader  # noqa: E402,F401  (installs the shim)
import src.data.epa.calc as _calc  # noqa: E402
import src.data.epa.main as _epa_main  # noqa: E402
import src.models.epa.init as _epa_init  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402


@dataclass
class ModelRun:
    model_id: str
    year: int
    objs: tuple
    alliance_pred: Dict[str, Tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    attrib: Dict[str, Dict[int, np.ndarray]] = field(default_factory=dict)
    updated: Set[Tuple[str, int]] = field(default_factory=set)
    config_hash: str = ""
    k: Optional[float] = None
    # unrounded rating vectors (18 slots) for 3-decimal export; WP3b's ModelRun must provide the same
    pre_vec: Dict[Tuple[str, int], np.ndarray] = field(default_factory=dict)
    post_vec: Dict[Tuple[str, int], np.ndarray] = field(default_factory=dict)
    final_vec: Dict[int, np.ndarray] = field(default_factory=dict)


class RecordingEPA(EPA):
    def __init__(self, run_store: Dict[str, Any], step_scale: float = 1.0):
        super().__init__()
        self._store = run_store
        self._scale = step_scale

    _cur = None

    def process_match(self, match, event, team_events, team_years):
        self._cur = match.key
        try:
            super().process_match(match, event, team_events, team_years)
        finally:
            self._cur = None

    def pre_record_team(self, team, te, ty):
        if self._cur is not None:
            self._store["pre_vec"][(self._cur, team)] = np.array(self.epas[team].mean, copy=True)
        return super().pre_record_team(team, te, ty)

    def post_record_team(self, team, te, ty):
        if self._cur is not None:
            self._store["post_vec"][(self._cur, team)] = np.array(self.epas[team].mean, copy=True)
        return super().post_record_team(team, te, ty)

    def predict_match(self, match, event):
        out = super().predict_match(match, event)
        self._store["pred"][match.key] = (np.array(out[1].breakdown, copy=True),
                                          np.array(out[2].breakdown, copy=True))
        return out

    def attribute_match(self, match, red_pred, blue_pred):
        out = super().attribute_match(match, red_pred, blue_pred)
        self._store["attrib"][match.key] = {t: np.array(a.epa, copy=True) for t, a in out.items()}
        return out

    def update_team(self, team, attrib, match):
        if self._scale != 1.0:
            from src.models.types import Attribution
            mu = self.epas[team].mean
            attrib = Attribution(mu + self._scale * (np.asarray(attrib.epa) - mu))
        super().update_team(team, attrib, match)
        self._store["updated"].add((match.key, team))


def load_base(year: int, raw) -> tuple:
    if isinstance(raw, dict):
        return loader.load_year(year, "", raw=raw)
    return loader.load_year(year, raw)


def run_model(year: int, base_objs: tuple, all_team_years, model_id: str = "epa",
              step_scale: float = 1.0) -> ModelRun:
    store = {"pred": {}, "attrib": {}, "updated": set(), "pre_vec": {}, "post_vec": {}, "model": None}
    objs = copy.deepcopy(base_objs)
    _epa_init.get_constants.cache_clear()
    prev = _calc.EPA
    def _factory():
        store["model"] = RecordingEPA(store, step_scale)
        return store["model"]

    _calc.EPA = _factory  # type: ignore
    try:
        objs = _epa_main.process_year(objs, all_team_years)
    finally:
        _calc.EPA = prev  # type: ignore
        _epa_init.get_constants.cache_clear()
    return ModelRun(model_id=model_id, year=year, objs=objs, alliance_pred=store["pred"],
                    attrib=store["attrib"], updated=store["updated"], k=EPA.k_func(year),
                    pre_vec=store["pre_vec"], post_vec=store["post_vec"],
                    final_vec={t: np.array(r.mean, copy=True) for t, r in store["model"].epas.items()})


def build_runs(year: int, raw: dict, priors, variant: str = "copy") -> Dict[str, ModelRun]:
    base = load_base(year, raw)
    epa = run_model(year, base, priors, "epa")
    if variant == "copy":
        fast = ModelRun(model_id="fast", year=year, objs=copy.deepcopy(epa.objs),
                        alliance_pred=epa.alliance_pred, attrib=epa.attrib, updated=set(epa.updated),
                        config_hash="stub-copy", k=epa.k, pre_vec=epa.pre_vec, post_vec=epa.post_vec,
                        final_vec=epa.final_vec)
    else:
        fast = run_model(year, base, priors, "fast", step_scale=1.3)
        fast.config_hash = "stub-scaled"
    return {"epa": epa, "fast": fast}

"""Tests for pipeline/sb/model_info.py (WP3). Run: python -m pytest pipeline/tests -q"""
import json
import os
import sys

import pytest

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(PIPELINE)
if PIPELINE not in sys.path:
    sys.path.insert(0, PIPELINE)

import jsonschema  # noqa: E402

from sb.model_info import describe  # noqa: E402
from sb.models import Year  # noqa: E402
import src.models.epa.constants as C  # noqa: E402
from src.models.epa.main import EPA  # noqa: E402

SCHEMA_PATH = os.path.join(REPO, "docs", "schema", "manifest.schema.json")


def models_validator():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        root = json.load(f)
    schema = {"$ref": "#/$defs/modelsFile", "$defs": root["$defs"]}
    return jsonschema.Draft202012Validator(schema)


def models_file(entries):
    return {"schema": 1, "kind": "models", "generated_at": "2026-11-16T12:07:00Z",
            "models": entries, "scoreboard": []}


def test_validates_against_models_schema():
    doc = models_file([describe("epa"), describe("fast")])
    # serialisable exactly as jsonout writes (no NaN, no numpy)
    json.dumps(doc, allow_nan=False)
    errors = sorted(models_validator().iter_errors(doc), key=str)
    assert not errors, [e.message for e in errors]


def test_epa_constants_are_read_from_vendored_modules():
    c = describe("epa")["constants"]
    assert c["NORM_MEAN"]["value"] == C.NORM_MEAN
    assert c["NORM_SD"]["value"] == C.NORM_SD
    assert c["INIT_PENALTY"]["value"] == C.INIT_PENALTY
    assert c["YEAR_ONE_WEIGHT"]["value"] == C.YEAR_ONE_WEIGHT
    assert c["MEAN_REVERSION"]["value"] == C.MEAN_REVERSION
    assert c["ELIM_WEIGHT"]["value"] == C.ELIM_WEIGHT
    assert c["k"]["value"] == EPA.k_func(2026) == -0.625
    for x in (0, 6, 9, 12):
        assert c[f"percent_{x}"]["value"] == EPA.percent_func(2026, x)


def test_epa_constants_follow_monkeypatched_vendor(monkeypatch):
    # proves the values are read at call time, not retyped
    monkeypatch.setattr(C, "MEAN_REVERSION", 0.25)
    d = describe("epa")
    assert d["constants"]["MEAN_REVERSION"]["value"] == 0.25
    assert any("25% of the way back" in p for p in d["how_it_works"])


def test_year_constants_optional():
    y = Year(year=2025, score_sd=31.5, foul_mean=5.0, no_foul_mean=80.0)
    c = describe("epa", y)["constants"]
    assert c["score_sd"]["value"] == 31.5
    assert c["foul_rate"]["value"] == y.get_foul_rate() == 5.0 / 80.0
    assert "score_sd" not in describe("epa")["constants"]


def test_how_it_works_voice():
    for m in ("epa", "fast"):
        how = describe(m)["how_it_works"]
        assert 3 <= len(how) <= 6
        for p in how:
            assert "!" not in p
            assert p[0].isupper()
            assert all(ord(ch) < 0x2190 or ch in "×−" for ch in p), p  # no emoji or symbols


def test_fast_reads_config_file(tmp_path):
    cfg = {
        "id": "fast", "version": 1,
        "settings": {
            "percent": {"first_n": 3, "first": 0.6, "start": 0.4, "end": 0.2, "ramp_start": 3, "ramp_len": 6},
            "elim_weight": 0.3333, "mean_reversion": 0.4, "year_one_weight": 0.7, "seed": "norm_epa",
            "robot_attribution": True, "robot_weight": 1.0, "residual_split": "equal",
            "passback": {"enabled": True, "percent_scale": 0.5, "scope": "quals"}, "win_k": -0.7,
        },
        "tuning": {"tuned_on": [2022, 2023, 2024, 2025], "tuned_at": "2026-11-16T00:00:00Z",
                   "objective": "x", "search": {}, "baseline": {}, "chosen": {}, "top": []},
    }
    p = tmp_path / "fast_epa.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    d = describe("fast", config_path=str(p))
    assert d["constants"]["k"]["value"] == -0.7
    assert d["settings"]["percent"]["first"] == 0.6
    assert d["tuning"]["tuned_on"] == [2022, 2023, 2024, 2025]
    assert len(d["config_hash"]) == 64
    text = " ".join(d["how_it_works"])
    assert "60% of the error for a team's first 3 qual matches" in text
    assert "replayed" in text and "per robot" in text
    assert "2022, 2023, 2024, 2025" in text
    try:
        from sb.model_runner import config_hash
    except ImportError:
        config_hash = None
    if config_hash is not None:
        assert d["config_hash"] == config_hash(cfg)
    errors = list(models_validator().iter_errors(models_file([describe("epa"), d])))
    assert not errors, [e.message for e in errors]


def test_deterministic_and_unknown_model():
    assert json.dumps(describe("epa"), sort_keys=True) == json.dumps(describe("epa"), sort_keys=True)
    with pytest.raises(ValueError):
        describe("opr")

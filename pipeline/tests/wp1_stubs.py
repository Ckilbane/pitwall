"""Test doubles for the orchestration tests (WP1).  Not used by the Action.

DevComputer is the real season builder with two test hooks, selected with
`build.py run --computer wp1_stubs:DevComputer` and PITWALL_ALLOW_STUB=1:

* PITWALL_FAIL_SEASONS=2024,2025        compute raises (failure injection);
* PITWALL_INVALID_SEASONS=2024          compute succeeds but leaves an invalid asof file, so validation fails;
* PITWALL_BUDGETS='{"traj": 2000000}' relaxes shard size budgets for validation (the full 2025 dump's traj
  file is 1.16 MB against the 1 MB budget; that is WP2's to fix, the e2e test must still run);
* the Fast EPA config falls back to the EPA-equivalent defaults while pipeline/models/fast_epa.json
  does not exist yet (WP3b), so these tests run before that file lands.
"""
import json
import os
import sys
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
if PIPELINE not in sys.path:
    sys.path.insert(0, PIPELINE)

import build  # noqa: E402


def _years(name: str):
    return {int(x) for x in os.environ.get(name, "").split(",") if x.strip()}


class DevComputer(build.RealComputer):
    def __init__(self, config, leak_cuts=0, published_dir=None):
        super().__init__(config, leak_cuts, published_dir)
        if not os.path.exists(os.path.join(PIPELINE, "models", "fast_epa.json")):
            self.fast_config = {}

    def validate_season(self, staged, year, schema_dir):
        budgets = json.loads(os.environ.get("PITWALL_BUDGETS", "{}"))
        if not budgets:
            return super().validate_season(staged, year, schema_dir)
        from sb import validate_shards as vs

        return vs.validate_season(staged, year, vs.Schemas(schema_dir), budgets)

    def compute_season(self, year, dump, priors, new_out, staged, meta):
        if year in _years("PITWALL_FAIL_SEASONS"):
            raise RuntimeError(f"injected failure for {year}")
        out = super().compute_season(year, dump, priors, new_out, staged, meta)
        if year in _years("PITWALL_INVALID_SEASONS"):
            with open(os.path.join(staged, str(year), "asof.json.gz"), "wb") as f:
                f.write(b"not gzip, not json")
        return out


def relabel_dump(dump: Dict[str, Any], year: int) -> Dict[str, Any]:
    """Copy of a season dump relabelled to another year: every string starting with the old year
    (event keys, match keys, dates) and every `year` field moves to `year`.  Test data only."""
    old = str(dump["year"])
    new = str(year)

    def fix(x: Any) -> Any:
        if isinstance(x, str):
            return new + x[len(old):] if x.startswith(old) else x
        if isinstance(x, list):
            return [fix(i) for i in x]
        if isinstance(x, dict):
            return {fix(k): (year if k == "year" and v == int(old) else fix(v)) for k, v in x.items()}
        return x

    return fix(dump)

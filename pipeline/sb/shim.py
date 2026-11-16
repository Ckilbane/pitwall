"""Import redirection so that vendored upstream code runs against ORM-free models and a
raw-dump "TBA".

`install()` registers stand-in modules in sys.modules (never touching vendor/):

    src.db.models     -> sb.models            (Year/TeamYear/Event/TeamEvent/Match/Team/ETag,
                                               match_dict_to_objs)
    src.db.functions  -> no-op stubs          (remove_teams_with_no_events, update_team_districts)
    src.data.utils    -> objs_type, create_objs, Timer (no DB / GCS imports)
    src.tba.main      -> get_tba(url, etag, cache) served from the raw dump of the *active*
                         DumpSource (see `use_source`); no network, no `requests`

After `install()`, these upstream modules import and run unmodified:
    src.tba.read_tba, src.tba.breakdown, src.tba.clean_data, src.tba.constants,
    src.data.tba (process_year, load_teams), src.data.avg, src.data.wins,
    src.data.epa.{calc,agg,metrics,main}, src.models.template, src.models.epa.*

The stand-in for `get_tba` reproduces the TBA API v3 URLs upstream calls:
    events/{year}                     <- dump["events"]
    event/{key}/matches               <- dump["event_data"][key]["matches"]
    event/{key}/rankings              <- ...["rankings"]
    event/{key}/alliances             <- ...["alliances"]
    event/{key}/teams/simple          <- dump["event_data"][key]["teams"]  (when present)
    districts/{year}                  <- dump["districts"].keys()           (when present)
    district/{year}{abbr}/teams       <- dump["districts"][abbr]            (when present)
    teams/{page}                      <- dump["teams"] bucketed by team number // 500
Anything not available returns (False, None), exactly what upstream's `_get_tba` returns for a
non-200/non-304 response, so upstream's own "missing data" paths run.
Every call returns a deep copy: upstream mutates the event dicts it receives.
"""
import copy
import sys
import types
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union

import sb  # noqa: F401  (sys.path bootstrap)

_ACTIVE: Dict[str, Any] = {"source": None}
_PRISTINE_EMPTY_BREAKDOWN: Dict[str, Any] = {}


def _team_num(team: Union[str, int]) -> int:
    s = str(team)
    return int(s[3:]) if s.startswith("frc") else int(s)


class DumpSource:
    """Serves TBA v3 GET urls out of one season's raw dump (see sb.loader for the shape)."""

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.year: int = int(raw["year"]) if "year" in raw else -1
        self.events = raw.get("events") or []
        self.event_data = raw.get("event_data") or {}
        self.has_districts = isinstance(raw.get("districts"), dict)
        self.has_teams = isinstance(raw.get("teams"), list)
        self.calls: Dict[str, int] = {}

    def _ed(self, key: str) -> Optional[Dict[str, Any]]:
        return self.event_data.get(key)

    def get(self, url: str) -> Union[Any, bool]:
        parts = url.split("/")
        self.calls[parts[0]] = self.calls.get(parts[0], 0) + 1

        if parts[0] == "events" and len(parts) == 2:
            return copy.deepcopy(self.events)

        if parts[0] == "event" and len(parts) >= 3:
            ed = self._ed(parts[1])
            if ed is None:
                return False
            tail = "/".join(parts[2:])
            if tail == "matches":
                return copy.deepcopy(ed.get("matches", []))
            if tail == "rankings":
                return copy.deepcopy(ed["rankings"]) if "rankings" in ed else False
            if tail == "alliances":
                return copy.deepcopy(ed["alliances"]) if "alliances" in ed else False
            if tail == "teams/simple":
                if "teams" not in ed:
                    return False
                return [{"key": "frc" + str(_team_num(t))} for t in ed["teams"]]
            return False

        if parts[0] == "districts" and len(parts) == 2:
            if not self.has_districts:
                return False
            return [
                {"key": f"{parts[1]}{abbr}", "abbreviation": abbr}
                for abbr in self.raw["districts"].keys()
            ]

        if parts[0] == "district" and len(parts) == 3 and parts[2] == "teams":
            if not self.has_districts:
                return False
            abbr = parts[1][4:]  # key is "<year><abbr>"
            return [
                {"key": "frc" + str(_team_num(t))}
                for t in self.raw["districts"].get(abbr, [])
            ]

        if parts[0] == "teams" and len(parts) == 2:
            if not self.has_teams:
                return False
            page = int(parts[1])
            out = []
            for t in self.raw["teams"]:
                num = _team_num(t["key"])
                if num // 500 != page:
                    continue
                t = copy.deepcopy(t)
                # /teams/simple objects lack rookie_year; upstream indexes it directly
                for k in ("nickname", "rookie_year", "country", "state_prov"):
                    t.setdefault(k, None)
                out.append(t)
            return out
        return False


@contextmanager
def use_source(source: Optional[DumpSource]):
    prev = _ACTIVE["source"]
    _ACTIVE["source"] = source
    try:
        yield
    finally:
        _ACTIVE["source"] = prev


def _stub_get_tba(
    url: str, etag: Optional[str] = None, cache: bool = True
) -> Tuple[Union[Any, bool], Optional[str]]:
    src = _ACTIVE["source"]
    if src is None:
        raise RuntimeError("no active DumpSource: wrap calls in sb.shim.use_source(...)")
    return src.get(url), None


def _mod(name: str, **attrs: Any) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


_installed = False


def install() -> None:
    """Idempotently register the stand-in modules."""
    global _installed
    if _installed:
        return

    import sb.models as models

    sys.modules["src.db.models"] = models

    sys.modules["src.db.functions"] = _mod(
        "src.db.functions",
        remove_teams_with_no_events=lambda *a, **k: None,
        update_team_districts=lambda *a, **k: None,
        clear_year=lambda *a, **k: None,
    )

    objs_type = Tuple[
        models.Year,
        Dict[str, models.TeamYear],
        Dict[str, models.Event],
        Dict[str, models.TeamEvent],
        Dict[str, models.Match],
        Dict[str, models.ETag],
    ]

    def create_objs(year: int) -> objs_type:  # verbatim from upstream data/utils.py
        return (models.Year(year=year), {}, {}, {}, {}, {})

    class Timer:  # verbatim from upstream data/utils.py
        def __init__(self):
            self.start = datetime.now()

        def print(self, label: str) -> None:
            end = datetime.now()
            print(label, "\t", end - self.start)
            self.start = end

    def _no_db(*a: Any, **k: Any):
        raise NotImplementedError("DB access is not available in the flat-file port")

    sys.modules["src.data.utils"] = _mod(
        "src.data.utils",
        objs_type=objs_type,
        create_objs=create_objs,
        Timer=Timer,
        read_objs=_no_db,
        write_objs=_no_db,
    )

    sys.modules["src.tba.main"] = _mod("src.tba.main", get_tba=_stub_get_tba)

    # remember the pristine value of the module-level dict that upstream's clean_breakdown
    # returns (and post_clean_breakdown mutates) for matches with no breakdown
    from src.tba.types import empty_breakdown

    _PRISTINE_EMPTY_BREAKDOWN.clear()
    _PRISTINE_EMPTY_BREAKDOWN.update(copy.deepcopy(empty_breakdown))

    _installed = True


def reset_empty_breakdown() -> None:
    """Restore upstream's shared `empty_breakdown` dict to its import-time contents.

    Upstream `clean_breakdown` returns that module-level dict itself for matches without a
    breakdown, and `post_clean_breakdown` (2018, 2025) writes into it, so state leaks between
    matches and (in a long-lived process) between seasons.  Resetting between seasons makes
    load_year independent of what was loaded earlier in the process.  See NOTES.md.
    """
    from src.tba.types import empty_breakdown

    empty_breakdown.clear()
    empty_breakdown.update(copy.deepcopy(_PRISTINE_EMPTY_BREAKDOWN))

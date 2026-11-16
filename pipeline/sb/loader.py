"""LOADER stage: raw TBA season dump -> upstream `objs_type` tuple, offline.

    from sb.loader import load_year
    objs = load_year(2023, "/path/tba_2023.json.gz")
    year, team_years, events, team_events, matches, etags = objs

`objs` has exactly the upstream shape (src/data/utils.py::objs_type), i.e. the tuple the EPA
stage (src/data/epa/main.py::process_year) receives after upstream's
    process_year_tba -> process_year_avg -> process_year_wins
have run.  Index 5 (etags) is always an empty dict (kept so `(year_obj, *objs[1:])` and the
other upstream unpackings work unchanged).

The work is done by *running the vendored upstream code unmodified* against a dump-backed
stand-in for the TBA client (see sb/shim.py): src.data.tba.process_year,
src.tba.read_tba.get_events / get_event_matches / get_event_rankings / get_event_alliances,
src.tba.breakdown.clean_breakdown*, src.data.avg.process_year, src.data.wins.process_year.
Only deviations are listed in sb/NOTES.md and in `LoadResult.deviations`.

Raw dump shape (one season; untrusted data, parsed with json only):
    {"year": int, "fetched": str,
     "events": [TBA /events/{year} objects],
     "event_data": {event_key: {"matches": [...], "rankings": {...}, "alliances": [...],
                                "oprs": {...}, "teams": [team keys]  <- optional (2026+)}},
     "teams": [TBA team objects: key, nickname, city, state_prov, country, rookie_year]
                                                                        <- optional
     "districts": {abbr: [team keys or numbers]}                        <- optional (2026+)}
"""
import gzip
import io
import json
import os
import time
from collections import Counter, defaultdict
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import sb  # noqa: F401  (sys.path bootstrap)
from sb import shim

shim.install()

import src.data.avg as _avg  # noqa: E402
import src.data.tba as _tba  # noqa: E402
import src.data.wins as _wins  # noqa: E402
from sb.models import (  # noqa: E402
    Event,
    Match,
    Team,
    TeamEvent,
    TeamYear,
    Year,
)
from src.data.utils import create_objs, objs_type  # noqa: E402
from src.tba.constants import DISTRICT_MAPPING  # noqa: E402
from src.types.enums import EventStatus, EventType  # noqa: E402

# The archive of real Statbotics output (built March 2024) has event.time values that are
# local midnight in US Eastern (offset 4h/5h from UTC depending on DST), i.e. upstream's
# time.mktime() ran with TZ=America/New_York.  The time only matters for event.time /
# team_event.time and for the fallback match time of matches TBA has no timestamp for.
DEFAULT_TZ = "America/New_York"


@dataclass
class LoadResult:
    objs: objs_type
    teams: List[Team]  # upstream `teams` list after process_year (known + new placeholder teams)
    deviations: List[str] = field(default_factory=list)
    clean_breakdown_errors: List[str] = field(default_factory=list)  # lines upstream printed
    source_calls: Dict[str, int] = field(default_factory=dict)


@contextmanager
def _tz(name: Optional[str]):
    if name is None:
        yield
        return
    prev = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


@contextmanager
def _fixed_now(today: str):
    """Make `datetime.now()` inside src.data.wins return `today` (YYYY-MM-DD).

    wins.process_year uses now() to decide TeamYear.next_event_*.  Pinning it makes the output
    independent of the wall clock.
    """
    fixed = datetime.strptime(today, "%Y-%m-%d")

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore
            return fixed

    prev = _wins.datetime
    _wins.datetime = _FixedDT  # type: ignore
    try:
        yield
    finally:
        _wins.datetime = prev  # type: ignore


def read_raw(raw_path: str) -> Dict[str, Any]:
    """Read a (possibly gzipped) JSON dump.  json only: nothing from the file is executed."""
    opener = gzip.open if raw_path.endswith(".gz") else open
    with opener(raw_path, "rt", encoding="utf-8") as f:  # type: ignore
        raw = json.load(f)
    if not isinstance(raw, dict) or "events" not in raw or "event_data" not in raw:
        raise ValueError(f"{raw_path}: not a TBA season dump (need 'events' and 'event_data')")
    return raw


def _derive_team_districts(
    objs: objs_type, deviations: List[str]
) -> None:
    """Fallback for dumps without a "districts" key.

    Upstream sets TeamYear.district from TBA's /district/{key}/teams lists.  Without them we use
    the district of the DISTRICT-type events (then DISTRICT_CMP events) the team played: most
    frequent wins, ties broken alphabetically.  Teams with only regional / champs events keep None.
    """
    _, team_years, events, team_events, _, _ = objs
    by_team: Dict[int, Dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for te in team_events.values():
        ev = events[te.event]
        if ev.district is None:
            continue
        if ev.type == EventType.DISTRICT:
            by_team[te.team]["district"][ev.district] += 1
        elif ev.type == EventType.DISTRICT_CMP:
            by_team[te.team]["cmp"][ev.district] += 1

    n = 0
    for ty in team_years.values():
        if ty.district is not None:
            continue
        c = by_team.get(ty.team, {})
        pool = c.get("district") or c.get("cmp")
        if not pool:
            continue
        best = sorted(pool.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        ty.district = best
        n += 1
    deviations.append(
        f"no 'districts' in dump: TeamYear.district derived from district-event attendance "
        f"for {n} of {len(team_years)} team_years"
    )


def load_year_full(
    year: int,
    raw_path: str,
    *,
    tz: Optional[str] = DEFAULT_TZ,
    today: Optional[str] = None,
    with_wins: bool = True,
    raw: Optional[Dict[str, Any]] = None,
) -> LoadResult:
    """Build the upstream objs tuple for one season; see module docstring.

    tz:        timezone upstream's time.mktime() used for event.time (see DEFAULT_TZ).
    today:     YYYY-MM-DD used for next_event_* (default: the dump's `fetched` date, so output
               is a pure function of the file).
    with_wins: also run upstream data/wins.py (needed: it fills TeamEvent.qual_count/records,
               Event.num_teams/current_match/qual_matches that the EPA stage reads).
    raw:       already-parsed dump (skips reading raw_path).
    """
    if raw is None:
        raw = read_raw(raw_path)
    if "year" in raw and int(raw["year"]) != year:
        raise ValueError(f"{raw_path}: dump is for {raw['year']}, asked for {year}")
    raw = dict(raw)
    raw.setdefault("year", year)

    if today is None:
        today = str(raw.get("fetched") or datetime.now().isoformat())[:10]

    deviations: List[str] = []
    source = shim.DumpSource(raw)

    if not source.has_teams:
        deviations.append(
            "no 'teams' in dump: all teams get upstream's placeholder Team(name=str(team))"
        )
    if not source.has_districts:
        deviations.append("no 'districts' in dump: district team lists unavailable")
    if not any("teams" in ed for ed in source.event_data.values()):
        deviations.append(
            "no per-event 'teams': teams come from match participants only "
            "(upstream also reads /event/{key}/teams/simple, only for events with zero matches in "
            "the current year, i.e. Upcoming events)"
        )

    buf = io.StringIO()
    with shim.use_source(source), _tz(tz), redirect_stdout(buf):
        shim.reset_empty_breakdown()
        try:
            teams = _tba.load_teams(cache=True)
            objs = create_objs(year)
            new_teams, objs = _tba.process_year(
                year, teams, objs, partial=False, cache=True
            )
            teams = teams + new_teams

            if not source.has_districts:
                _derive_team_districts(objs, deviations)

            year_obj = _avg.process_year(objs[0], list(objs[4].values()))
            objs = (year_obj, *objs[1:])

            if with_wins:
                with _fixed_now(today):
                    objs = _wins.process_year(objs)
        finally:
            shim.reset_empty_breakdown()

    errors = [ln for ln in buf.getvalue().splitlines() if ln.strip()]

    n_invalid = sum(1 for e in objs[2].values() if e.status == EventStatus.INVALID)
    if n_invalid:
        deviations.append(
            f"{n_invalid} of {len(objs[2])} events that pass upstream's event filter have no "
            f"matches in the dump (status Invalid, kept in events dict exactly as upstream does)"
        )

    if source.has_teams:
        meta = {t.team for t in teams if t.name != str(t.team) or t.rookie_year is not None}
        nometa = sorted({ty.team for ty in objs[1].values()} - meta)
        if nometa:
            deviations.append(
                f"{len(nometa)} played teams are absent from dump['teams'] -> placeholder Team"
            )

    return LoadResult(
        objs=objs,
        teams=teams,
        deviations=deviations,
        clean_breakdown_errors=errors,
        source_calls=dict(source.calls),
    )


def load_year(
    year: int,
    raw_path: str,
    **kwargs: Any,
) -> objs_type:
    """Upstream `objs_type` for `year` built purely from the raw dump at `raw_path`.

    Includes year means (data/avg.py) and win/record/event counters (data/wins.py).
    Keyword args are those of `load_year_full`.
    """
    return load_year_full(year, raw_path, **kwargs).objs


def year_stats(objs: objs_type) -> Year:
    """(Re)compute Year.score_mean/score_sd/foul/no_foul/auto/teleop/endgame/rp/tiebreaker/comp
    means with the unmodified upstream data/avg.py::process_year."""
    return _avg.process_year(objs[0], list(objs[4].values()))


def index_team_years(objs: objs_type) -> Dict[int, TeamYear]:
    """{team number: TeamYear}.  This is the inner dict of upstream's
    `all_team_years[year]` that models/epa/main.py::start_season reads for prior seasons."""
    return {ty.team: ty for ty in objs[1].values()}

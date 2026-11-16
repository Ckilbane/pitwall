"""EPA ENGINE stage: raw dump + priors -> upstream objs with every EPA field filled.

Call order is upstream src/data/main.py::process_year exactly:
    process_year_tba -> process_year_avg -> process_year_wins     (sb.loader.load_year)
    process_year_epa = data/epa/main.process_year:
        calc.process_year   (EPA.start_season with all_team_years, then process_match per match
                             sorted stably by time, then post_record_team for unplayed team_events)
        agg.process_year    (TeamYear epa/epa_pre_champs/epa_max/norm_epa/unitless_epa/ranks,
                             Year percentiles, TeamEvent epa_start/mean/max/pre_elim/norm/unitless,
                             Event epa_max/mean/sd/top_8/top_24)
        metrics.process_year
Nothing else in upstream produces season-level EPA fields; data/epa/main.post_process only fills
Team-level (career) norm_epa aggregates, which are not season outputs (see `team_norm_summary`).

All upstream code runs unmodified via the sys.modules shim installed by sb.loader.
"""
from typing import Any, Dict, List, Optional

import sb  # noqa: F401
import sb.loader as loader  # installs shim
from sb.models import Team, TeamYear

import src.data.epa.main as _epa_main  # noqa: E402
import src.models.epa.init as _epa_init  # noqa: E402


def run_year(
    year: int,
    raw_path: str,
    all_team_years: Dict[int, Dict[int, TeamYear]],
    **loader_kwargs: Any,
):
    """Return upstream objs_type for `year` with EPA computed.

    all_team_years: {season: {team: TeamYear}} for the prior seasons (see sb.priors).
    loader_kwargs:  passed to sb.loader.load_year (tz, today, raw, ...).
    """
    objs = loader.load_year(year, raw_path, **loader_kwargs)
    # get_constants is lru_cached on Year (hash = year number): stale means from an earlier run
    # of the same season in this process would otherwise be reused (NOTES.md surprise 8).
    _epa_init.get_constants.cache_clear()
    try:
        objs = _epa_main.process_year(objs, all_team_years)
    finally:
        _epa_init.get_constants.cache_clear()
    return objs


def team_norm_summary(
    all_team_years: Dict[int, Dict[int, TeamYear]]
) -> List[Team]:
    """Upstream data/epa/main.post_process on the given seasons (Team.norm_epa, _recent, _mean,
    _max).  Provided for completeness; not used by the season chain."""
    teams = sorted({t for d in all_team_years.values() for t in d})
    objs = [Team(team=t) for t in teams]
    return _epa_main.post_process(objs, all_team_years)

"""Priors for upstream EPA `start_season`: the `all_team_years` structure.

Upstream (src/models/epa/main.py::EPA.start_season) looks at seasons year-1 .. year-4, keeps the
team_years that exist (2021 never does: no season), and passes the FIRST TWO found to
`get_init_epa`, which reads only `.norm_epa` of each (None -> rookie value 1450).  So a season
needs up to four prior seasons: 2023 -> {2022, 2020, 2019}, 2024 -> {2023, 2022, 2020},
2025 -> {2024, 2023, 2022}, 2026 -> {2025, 2024, 2023, 2022}.

Each prior season comes from one of two sources:
  * "archive":  the March-2024 Statbotics archive team_years.csv (seasons we do not compute);
  * "computed": our own out/{year}/team_years.csv (norm_epa is an integer there, so the CSV
                round-trip is exact and an in-process chain equals a file chain).

The returned objects are `sb.models.TeamYear` with year/team/norm_epa set (all upstream reads).
"""
import csv
import os
from typing import Dict, Iterable, List, Optional, Tuple

import sb  # noqa: F401
from sb.models import TeamYear

NO_SEASON = {2021}
LOOKBACK = 4  # upstream: range(year - 1, year - 5, -1)

AllTeamYears = Dict[int, Dict[int, TeamYear]]


def prior_years(year: int) -> List[int]:
    """Seasons upstream may read for `year` (newest first), without 2021."""
    return [y for y in range(year - 1, year - 1 - LOOKBACK, -1) if y not in NO_SEASON and y >= 2002]


def _num(x: Optional[str]) -> Optional[float]:
    if x is None:
        return None
    x = x.strip()
    if x == "" or x.upper() == "NULL" or x.lower() == "nan":
        return None
    return float(x)


def archive_csv_path(archive: str) -> str:
    """`archive` may be the CSV file itself (pipeline/priors/archive_team_years_2016_2023.csv, which
    needs only the columns year, team, offseason, norm_epa) or a directory holding team_years.csv
    (the full Statbotics v3 export)."""
    if os.path.isdir(archive):
        return os.path.join(archive, "team_years.csv")
    return archive


def load_archive_years(
    archive_dir: str, years: Iterable[int], include_offseason: bool = False
) -> AllTeamYears:
    """{year: {team: TeamYear(norm_epa)}} from the archive team_years.csv.

    `archive_dir`: a directory containing team_years.csv, or the CSV file itself (archive_csv_path).

    include_offseason=False (default) drops rows with offseason == 't' (teams that only played
    offseason events that season).  Current upstream drops offseason events entirely, so those
    team_years would not exist in an upstream DB built today.
    """
    want = set(int(y) for y in years)
    out: AllTeamYears = {y: {} for y in sorted(want)}
    path = archive_csv_path(archive_dir)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                y = int(row["year"])
            except (TypeError, ValueError):
                continue
            if y not in want:
                continue
            if not include_offseason and row.get("offseason") == "t":
                continue
            try:
                team = int(row["team"])
            except (TypeError, ValueError):
                continue  # offseason "B" teams such as 1023B: never in upstream's team_years
            ne = _num(row.get("norm_epa"))
            out[y][team] = TeamYear(year=y, team=team, norm_epa=ne)
    return out


def load_computed_year(out_dir: str, year: int) -> Dict[int, TeamYear]:
    path = os.path.join(out_dir, str(year), "team_years.csv")
    res: Dict[int, TeamYear] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            team = int(row["team"])
            ne = _num(row.get("norm_epa"))
            if ne is not None and ne == int(ne):
                ne = int(ne)
            res[team] = TeamYear(year=year, team=team, norm_epa=ne)
    return res


def build_priors(
    year: int,
    archive_dir: Optional[str],
    out_dir: Optional[str],
    computed: Iterable[int] = (),
    include_offseason: bool = False,
) -> Tuple[AllTeamYears, Dict[int, str]]:
    """all_team_years for `year`, plus {prior_year: source} for logging.

    A prior season is taken from our outputs if it is in `computed` (and the file exists),
    otherwise from the archive.  Missing everywhere -> absent (teams get rookie priors).
    """
    computed = set(computed)
    res: AllTeamYears = {}
    sources: Dict[int, str] = {}
    need_archive = []
    for y in prior_years(year):
        if y in computed and out_dir and os.path.exists(
            os.path.join(out_dir, str(y), "team_years.csv")
        ):
            res[y] = load_computed_year(out_dir, y)
            sources[y] = "computed"
        else:
            need_archive.append(y)
    if need_archive and archive_dir:
        arch = load_archive_years(archive_dir, need_archive, include_offseason)
        for y in need_archive:
            if arch.get(y):
                res[y] = arch[y]
                sources[y] = "archive" + ("+offseason" if include_offseason else "")
            else:
                sources[y] = "missing"
    for y in need_archive:
        sources.setdefault(y, "missing")
    return dict(sorted(res.items())), dict(sorted(sources.items()))

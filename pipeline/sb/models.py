"""ORM-free equivalents of the upstream Statbotics db models (Year, TeamYear, Event,
TeamEvent, Match, Team, ETag).

GENERATED from vendor/backend/src/db/models/*.py (field names, order and defaults come from
the SQLAlchemy column declarations; helper-method bodies are copied verbatim from the
upstream subclasses).  Differences from upstream:
  * base classes are plain dataclasses(slots=True) instead of attr classes built from the ORM
  * `Model.to_dict` uses dataclasses.asdict instead of attr.asdict (all concrete models
    override to_dict anyway)
  * `match_dict_to_objs` is copied verbatim from db/models/create.py
Everything else (pk/sort/__str__/__hash__/get_* helpers) is upstream text.

Importing this module puts vendor/backend on sys.path (see sb/__init__.py), so
`from src.breakdown import ...` resolves to the vendored upstream modules.
"""
import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

import numpy as np

import sb  # noqa: F401  (adds vendor/backend to sys.path)
from src.breakdown import derived_breakdown, key_to_name
from src.constants import CURR_YEAR
from src.tba.types import MatchDict
from src.types.enums import CompLevel, EventStatus, EventType, MatchStatus, MatchWinner
from src.utils.utils import get_match_name


class Model:
    T1 = TypeVar("T1")

    @classmethod
    def from_dict(cls: Type[T1], dict: Dict[str, Any]) -> T1:
        dict = {k: dict.get(k, None) for k in cls.__slots__}  # type: ignore
        return cls(**dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)  # type: ignore

    def sort(self) -> Any:
        raise NotImplementedError()

    def pk(self) -> str:
        raise NotImplementedError()

    def __hash__(self) -> int:
        return hash(self.pk())

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Model):
            return False
        return self.pk() == other.pk()

    def __str__(self):
        return self.__repr__()



@dataclass(slots=True, eq=False)
class _Year:
    year: int = None
    score_mean: float = 0
    score_sd: float = 0
    foul_mean: float = 0
    no_foul_mean: float = 0
    auto_mean: float = 0
    teleop_mean: float = 0
    endgame_mean: float = 0
    rp_1_mean: float = 0
    rp_2_mean: float = 0
    rp_3_mean: float = 0
    tiebreaker_mean: float = 0
    comp_0_mean: float = 0
    comp_1_mean: float = 0
    comp_2_mean: float = 0
    comp_3_mean: float = 0
    comp_4_mean: float = 0
    comp_5_mean: float = 0
    comp_6_mean: float = 0
    comp_7_mean: float = 0
    comp_8_mean: float = 0
    comp_9_mean: float = 0
    epa_99p: Optional[float] = None
    epa_90p: Optional[float] = None
    epa_75p: Optional[float] = None
    epa_25p: Optional[float] = None
    auto_epa_99p: Optional[float] = None
    auto_epa_90p: Optional[float] = None
    auto_epa_75p: Optional[float] = None
    auto_epa_25p: Optional[float] = None
    teleop_epa_99p: Optional[float] = None
    teleop_epa_90p: Optional[float] = None
    teleop_epa_75p: Optional[float] = None
    teleop_epa_25p: Optional[float] = None
    endgame_epa_99p: Optional[float] = None
    endgame_epa_90p: Optional[float] = None
    endgame_epa_75p: Optional[float] = None
    endgame_epa_25p: Optional[float] = None
    rp_1_epa_99p: Optional[float] = None
    rp_1_epa_90p: Optional[float] = None
    rp_1_epa_75p: Optional[float] = None
    rp_1_epa_25p: Optional[float] = None
    rp_2_epa_99p: Optional[float] = None
    rp_2_epa_90p: Optional[float] = None
    rp_2_epa_75p: Optional[float] = None
    rp_2_epa_25p: Optional[float] = None
    rp_3_epa_99p: Optional[float] = None
    rp_3_epa_90p: Optional[float] = None
    rp_3_epa_75p: Optional[float] = None
    rp_3_epa_25p: Optional[float] = None
    comp_0_epa_99p: Optional[float] = None
    comp_0_epa_90p: Optional[float] = None
    comp_0_epa_75p: Optional[float] = None
    comp_0_epa_25p: Optional[float] = None
    comp_1_epa_99p: Optional[float] = None
    comp_1_epa_90p: Optional[float] = None
    comp_1_epa_75p: Optional[float] = None
    comp_1_epa_25p: Optional[float] = None
    comp_2_epa_99p: Optional[float] = None
    comp_2_epa_90p: Optional[float] = None
    comp_2_epa_75p: Optional[float] = None
    comp_2_epa_25p: Optional[float] = None
    comp_3_epa_99p: Optional[float] = None
    comp_3_epa_90p: Optional[float] = None
    comp_3_epa_75p: Optional[float] = None
    comp_3_epa_25p: Optional[float] = None
    comp_4_epa_99p: Optional[float] = None
    comp_4_epa_90p: Optional[float] = None
    comp_4_epa_75p: Optional[float] = None
    comp_4_epa_25p: Optional[float] = None
    comp_5_epa_99p: Optional[float] = None
    comp_5_epa_90p: Optional[float] = None
    comp_5_epa_75p: Optional[float] = None
    comp_5_epa_25p: Optional[float] = None
    comp_6_epa_99p: Optional[float] = None
    comp_6_epa_90p: Optional[float] = None
    comp_6_epa_75p: Optional[float] = None
    comp_6_epa_25p: Optional[float] = None
    comp_7_epa_99p: Optional[float] = None
    comp_7_epa_90p: Optional[float] = None
    comp_7_epa_75p: Optional[float] = None
    comp_7_epa_25p: Optional[float] = None
    comp_8_epa_99p: Optional[float] = None
    comp_8_epa_90p: Optional[float] = None
    comp_8_epa_75p: Optional[float] = None
    comp_8_epa_25p: Optional[float] = None
    comp_9_epa_99p: Optional[float] = None
    comp_9_epa_90p: Optional[float] = None
    comp_9_epa_75p: Optional[float] = None
    comp_9_epa_25p: Optional[float] = None
    count: int = 0
    epa_conf: Optional[float] = None
    epa_acc: Optional[float] = None
    epa_mse: Optional[float] = None
    champs_count: int = 0
    epa_champs_conf: Optional[float] = None
    epa_champs_acc: Optional[float] = None
    epa_champs_mse: Optional[float] = None
    epa_score_rmse: Optional[float] = None
    epa_score_error: Optional[float] = None
    epa_champs_score_rmse: Optional[float] = None
    epa_champs_score_error: Optional[float] = None
    rp_count: int = 0
    epa_rp_1_error: Optional[float] = None
    epa_rp_1_acc: Optional[float] = None
    epa_rp_2_error: Optional[float] = None
    epa_rp_2_acc: Optional[float] = None
    epa_rp_3_error: Optional[float] = None
    epa_rp_3_acc: Optional[float] = None
    champs_rp_count: int = 0
    epa_champs_rp_1_error: Optional[float] = None
    epa_champs_rp_1_acc: Optional[float] = None
    epa_champs_rp_2_error: Optional[float] = None
    epa_champs_rp_2_acc: Optional[float] = None
    epa_champs_rp_3_error: Optional[float] = None
    epa_champs_rp_3_acc: Optional[float] = None


class Year(_Year, Model):
    def pk(self: "Year") -> str:
        return str(self.year)

    def __hash__(self: "Year") -> int:
        return hash(self.pk())

    def __str__(self: "Year") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join([str(self.year), str(self.count)])

    def get_foul_rate(self: "Year") -> float:
        return (self.foul_mean or 0) / (self.no_foul_mean or 1)

    def get_mean_components(self: "Year") -> Any:
        if self.year < 2016:
            return np.array([self.score_mean])
        return np.array(
            [
                self.no_foul_mean,
                self.auto_mean,
                self.teleop_mean,
                self.endgame_mean,
                self.rp_1_mean,
                self.rp_2_mean,
                self.rp_3_mean,
                self.tiebreaker_mean,
                self.comp_0_mean,
                self.comp_1_mean,
                self.comp_2_mean,
                self.comp_3_mean,
                self.comp_4_mean,
                self.comp_5_mean,
                self.comp_6_mean,
                self.comp_7_mean,
                self.comp_8_mean,
                self.comp_9_mean,
            ]
        )

    def to_dict(self: "Year") -> Dict[str, Any]:
        clean: Dict[str, Any] = {
            "year": self.year,
            "score_mean": self.score_mean,
            "score_sd": self.score_sd,
            "percentiles": {
                "total_points": {
                    "p99": self.epa_99p,
                    "p90": self.epa_90p,
                    "p75": self.epa_75p,
                    "p25": self.epa_25p,
                },
            },
            "breakdown": {},
            "metrics": {
                "win_prob": {
                    "season": {
                        "count": self.count,
                        "conf": self.epa_conf,
                        "acc": self.epa_acc,
                        "mse": self.epa_mse,
                    },
                    "champs": {
                        "count": self.champs_count,
                        "conf": self.epa_champs_conf,
                        "acc": self.epa_champs_acc,
                        "mse": self.epa_champs_mse,
                    },
                },
                "score_pred": {
                    "season": {
                        "count": 2 * self.count,
                        "rmse": self.epa_score_rmse,
                        "error": self.epa_score_error,
                    },
                    "champs": {
                        "count": 2 * self.champs_count,
                        "rmse": self.epa_champs_score_rmse,
                        "error": self.epa_champs_score_error,
                    },
                },
            },
        }

        clean["breakdown"] = {
            "total_points_mean": self.score_mean,
        }
        clean["percentiles"]["total_points"] = {
            "p99": self.epa_99p,
            "p90": self.epa_90p,
            "p75": self.epa_75p,
            "p25": self.epa_25p,
        }
        if self.year >= 2016:
            clean["breakdown"] = {
                "total_points_mean": self.score_mean,
                "foul_mean": self.foul_mean,
                "no_foul_mean": self.no_foul_mean,
            }
            pairs = list(key_to_name[self.year].items())
            pairs += [("rp_1", "rp_1"), ("rp_2", "rp_2")]
            if self.year >= 2025:
                pairs += [("rp_3", "rp_3")]
            for key, name in pairs:
                clean["breakdown"][f"{name}_mean"] = getattr(self, f"{key}_mean")
                if key != "tiebreaker":
                    clean["percentiles"][name] = {
                        "p99": getattr(self, f"{key}_epa_99p"),
                        "p90": getattr(self, f"{key}_epa_90p"),
                        "p75": getattr(self, f"{key}_epa_75p"),
                        "p25": getattr(self, f"{key}_epa_25p"),
                    }

            rp_1_name = key_to_name[self.year]["rp_1"]
            rp_2_name = key_to_name[self.year]["rp_2"]
            clean["metrics"]["rp_pred"] = {
                "season": {
                    "count": self.rp_count,
                    rp_1_name: {"error": self.epa_rp_1_error, "acc": self.epa_rp_1_acc},
                    rp_2_name: {"error": self.epa_rp_2_error, "acc": self.epa_rp_2_acc},
                },
                "champs": {
                    "count": self.champs_rp_count,
                    rp_1_name: {
                        "error": self.epa_champs_rp_1_error,
                        "acc": self.epa_champs_rp_1_acc,
                    },
                    rp_2_name: {
                        "error": self.epa_champs_rp_2_error,
                        "acc": self.epa_champs_rp_2_acc,
                    },
                },
            }

        if self.year >= 2025:
            rp_3_name = key_to_name[self.year]["rp_3"]
            clean["metrics"]["rp_pred"]["season"][rp_3_name] = (
                {
                    "error": self.epa_rp_3_error,
                    "acc": self.epa_rp_3_acc,
                },
            )

            clean["metrics"]["rp_pred"]["champs"][rp_3_name] = (
                {
                    "error": self.epa_champs_rp_3_error,
                    "acc": self.epa_champs_rp_3_acc,
                },
            )

        return clean


@dataclass(slots=True, eq=False)
class _TeamYear:
    id: Optional[int] = None
    year: int = None
    team: int = None
    name: str = None
    country: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    rookie_year: Optional[int] = None
    next_event_key: Optional[str] = None
    next_event_name: Optional[str] = None
    next_event_week: Optional[int] = None
    next_event_start_date: Optional[str] = None
    epa_start: float = 0
    epa_pre_champs: float = 0
    epa_max: float = 0
    epa: float = 0
    auto_epa: Optional[float] = None
    teleop_epa: Optional[float] = None
    endgame_epa: Optional[float] = None
    rp_1_epa: Optional[float] = None
    rp_2_epa: Optional[float] = None
    rp_3_epa: Optional[float] = None
    tiebreaker_epa: Optional[float] = None
    comp_0_epa: Optional[float] = None
    comp_1_epa: Optional[float] = None
    comp_2_epa: Optional[float] = None
    comp_3_epa: Optional[float] = None
    comp_4_epa: Optional[float] = None
    comp_5_epa: Optional[float] = None
    comp_6_epa: Optional[float] = None
    comp_7_epa: Optional[float] = None
    comp_8_epa: Optional[float] = None
    comp_9_epa: Optional[float] = None
    unitless_epa: float = 0
    norm_epa: Optional[float] = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    count: int = 0
    winrate: float = 0
    total_epa_rank: Optional[int] = None
    total_epa_percentile: Optional[float] = None
    total_team_count: Optional[int] = None
    country_epa_rank: Optional[int] = None
    country_epa_percentile: Optional[float] = None
    country_team_count: Optional[int] = None
    state_epa_rank: Optional[int] = None
    state_epa_percentile: Optional[float] = None
    state_team_count: Optional[int] = None
    district_epa_rank: Optional[int] = None
    district_epa_percentile: Optional[float] = None
    district_team_count: Optional[int] = None
    team_matches: Optional[List[Any]] = None


class TeamYear(_TeamYear, Model):
    def sort(self: "TeamYear") -> Tuple[int, int]:
        return (self.team, self.year)

    def pk(self: "TeamYear") -> str:
        return f"{self.team}_{self.year}"

    def __hash__(self: "TeamYear") -> int:
        return hash(self.pk())

    def __str__(self: "TeamYear") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join(
            [str(self.team), str(self.year), str(self.count), str(self.epa)]
        )

    def to_dict(self: "TeamYear") -> Dict[str, Any]:
        clean: Dict[str, Any] = {
            "team": self.team,
            "year": self.year,
            "name": self.name,
            "country": self.country,
            "state": self.state,
            "district": self.district,
            "rookie_year": self.rookie_year,
            "epa": {
                "total_points": self.epa,
                "unitless": self.unitless_epa,
                "norm": self.norm_epa,
                "breakdown": {},
                "stats": {
                    "start": self.epa_start,
                    "pre_champs": self.epa_pre_champs,
                    "max": self.epa_max,
                },
                "ranks": {
                    "total": {
                        "rank": self.total_epa_rank,
                        "percentile": self.total_epa_percentile,
                        "team_count": self.total_team_count,
                    },
                    "country": {
                        "rank": self.country_epa_rank,
                        "percentile": self.country_epa_percentile,
                        "team_count": self.country_team_count,
                    },
                    "state": {
                        "rank": self.state_epa_rank,
                        "percentile": self.state_epa_percentile,
                        "team_count": self.state_team_count,
                    },
                    "district": {
                        "rank": self.district_epa_rank,
                        "percentile": self.district_epa_percentile,
                        "team_count": self.district_team_count,
                    },
                },
            },
            "record": {
                "wins": self.wins,
                "losses": self.losses,
                "ties": self.ties,
                "count": self.count,
                "winrate": self.winrate,
            },
        }

        bd = clean["epa"]["breakdown"]
        bd["total_points"] = self.epa
        if self.year >= 2016:
            pairs = list(key_to_name[self.year].items())
            pairs += [("rp_1", "rp_1"), ("rp_2", "rp_2")]
            if self.year >= 2025:
                pairs += [("rp_3", "rp_3")]
            for key, name in pairs:
                bd[name] = getattr(self, f"{key}_epa")
            for derived_name, sources in derived_breakdown.get(self.year, {}).items():
                bd[derived_name] = sum(bd.get(src) or 0 for src in sources)

        if self.year == CURR_YEAR:
            clean["competing"] = {
                "next_event_key": self.next_event_key,
                "next_event_name": self.next_event_name,
                "next_event_week": self.next_event_week,
                "next_event_start_date": self.next_event_start_date,
            }

        return clean


@dataclass(slots=True, eq=False)
class _Event:
    key: str = None
    year: int = None
    name: str = None
    time: int = None
    country: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    start_date: str = None
    end_date: str = None
    type: EventType = None
    week: int = None
    video: Optional[str] = None
    status: EventStatus = None
    num_teams: int = 0
    current_match: Optional[int] = None
    qual_matches: Optional[int] = None
    epa_max: Optional[float] = None
    epa_top_8: Optional[float] = None
    epa_top_24: Optional[float] = None
    epa_mean: Optional[float] = None
    epa_sd: Optional[float] = None
    count: int = 0
    epa_conf: Optional[float] = None
    epa_acc: Optional[float] = None
    epa_mse: Optional[float] = None
    epa_score_rmse: Optional[float] = None
    epa_score_error: Optional[float] = None
    rp_count: int = 0
    epa_rp_1_error: Optional[float] = None
    epa_rp_1_acc: Optional[float] = None
    epa_rp_2_error: Optional[float] = None
    epa_rp_2_acc: Optional[float] = None
    epa_rp_3_error: Optional[float] = None
    epa_rp_3_acc: Optional[float] = None


class Event(_Event, Model):
    def pk(self: "Event") -> str:
        return self.key

    def __hash__(self: "Event") -> int:
        return hash(self.pk())

    def __str__(self: "Event") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join(
            [
                self.key,
                self.status,
                str(self.num_teams),
                str(self.current_match),
                str(self.qual_matches),
            ]
        )

    def get_event_status_str(self: "Event") -> str:
        if self.status != "Ongoing":
            return self.status

        if self.qual_matches == 0:
            return "Scheduled Unreleased"
        elif self.current_match == 0:
            return "Schedule Released"
        elif (self.current_match or -1) < (self.qual_matches or -1):
            return "Qual " + str(self.current_match)
        elif self.current_match == self.qual_matches:
            return "Quals Over"
        else:
            return "Elims Ongoing"

    def to_dict(self: "Event") -> Dict[str, Any]:
        clean: Dict[str, Any] = {
            "key": self.key,
            "year": self.year,
            "name": self.name,
            "time": self.time,
            "country": self.country,
            "state": self.state,
            "district": self.district,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "type": self.type,
            "week": self.week,
            "video": self.video,
            "status": self.status,
            "status_str": self.get_event_status_str(),
            "num_teams": self.num_teams,
            "current_match": self.current_match,
            "qual_matches": self.qual_matches,
            "epa": {
                "max": self.epa_max,
                "top_8": self.epa_top_8,
                "top_24": self.epa_top_24,
                "mean": self.epa_mean,
                "sd": self.epa_sd,
            },
            "metrics": {
                "win_prob": {
                    "count": self.count,
                    "conf": self.epa_conf,
                    "acc": self.epa_acc,
                    "mse": self.epa_mse,
                },
                "score_pred": {
                    "count": 2 * self.count,
                    "rmse": self.epa_score_rmse,
                    "error": self.epa_score_error,
                },
            },
        }

        if self.year >= 2016:
            clean["metrics"]["rp_pred"] = {
                "count": self.rp_count,
                key_to_name[self.year]["rp_1"]: {
                    "error": self.epa_rp_1_error,
                    "acc": self.epa_rp_1_acc,
                },
                key_to_name[self.year]["rp_2"]: {
                    "error": self.epa_rp_2_error,
                    "acc": self.epa_rp_2_acc,
                },
            }
            if self.year >= 2025:
                clean["metrics"]["rp_pred"][key_to_name[self.year]["rp_3"]] = (
                    {
                        "error": self.epa_rp_3_error,
                        "acc": self.epa_rp_3_acc,
                    },
                )

        return clean


@dataclass(slots=True, eq=False)
class _TeamEvent:
    id: Optional[int] = None
    team: int = None
    year: int = None
    event: str = None
    time: int = None
    team_name: str = None
    event_name: str = None
    country: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    type: EventType = None
    week: int = None
    status: EventStatus = None
    first_event: bool = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    count: int = 0
    winrate: float = 0
    qual_wins: int = 0
    qual_losses: int = 0
    qual_ties: int = 0
    qual_count: int = 0
    qual_winrate: float = 0
    rps: int = 0
    rps_per_match: float = 0
    rank: Optional[int] = None
    num_teams: Optional[int] = None
    elim_alliance: Optional[str] = None
    is_captain: Optional[bool] = None
    epa_start: float = 0
    epa_pre_elim: float = 0
    epa_mean: float = 0
    epa_max: float = 0
    epa: float = 0
    auto_epa: Optional[float] = None
    teleop_epa: Optional[float] = None
    endgame_epa: Optional[float] = None
    rp_1_epa: Optional[float] = None
    rp_2_epa: Optional[float] = None
    rp_3_epa: Optional[float] = None
    tiebreaker_epa: Optional[float] = None
    comp_0_epa: Optional[float] = None
    comp_1_epa: Optional[float] = None
    comp_2_epa: Optional[float] = None
    comp_3_epa: Optional[float] = None
    comp_4_epa: Optional[float] = None
    comp_5_epa: Optional[float] = None
    comp_6_epa: Optional[float] = None
    comp_7_epa: Optional[float] = None
    comp_8_epa: Optional[float] = None
    comp_9_epa: Optional[float] = None
    unitless_epa: float = 0
    norm_epa: Optional[float] = 0


class TeamEvent(_TeamEvent, Model):
    def sort(self: "TeamEvent") -> Tuple[int, int]:
        return (self.team, self.time)

    def pk(self: "TeamEvent") -> str:
        return f"{self.team}_{self.event}"

    def __hash__(self: "TeamEvent") -> int:
        return hash(self.pk())

    def __str__(self: "TeamEvent") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join(
            [
                str(self.team),
                self.event,
                str(self.status),
                str(self.count),
                str(self.epa),
                str(self.rank),
            ]
        )

    def to_dict(self: "TeamEvent") -> Dict[str, Any]:
        elim_wins = self.wins - self.qual_wins
        elim_losses = self.losses - self.qual_losses
        elim_ties = self.ties - self.qual_ties
        elim_count = self.count - self.qual_count
        elim_winrate = 0
        if elim_count > 0:
            elim_winrate = (elim_wins + elim_ties / 2) / elim_count

        clean: Dict[str, Any] = {
            "team": self.team,
            "year": self.year,
            "event": self.event,
            "time": self.time,
            "team_name": self.team_name,
            "event_name": self.event_name,
            "country": self.country,
            "state": self.state,
            "district": self.district,
            "type": self.type,
            "week": self.week,
            "status": self.status,
            "first_event": self.first_event,
            "epa": {
                "total_points": self.epa,
                "unitless": self.unitless_epa,
                "norm": self.norm_epa,
                "breakdown": {},
                "stats": {
                    "start": self.epa_start,
                    "pre_elim": self.epa_pre_elim,
                    "mean": self.epa_mean,
                    "max": self.epa_max,
                },
            },
            "record": {
                "qual": {
                    "wins": self.qual_wins,
                    "losses": self.qual_losses,
                    "ties": self.qual_ties,
                    "count": self.qual_count,
                    "winrate": self.qual_winrate,
                    "rps": self.rps,
                    "rps_per_match": self.rps_per_match,
                    "rank": self.rank,
                    "num_teams": self.num_teams,
                },
                "elim": {
                    "wins": elim_wins,
                    "losses": elim_losses,
                    "ties": elim_ties,
                    "count": elim_count,
                    "winrate": elim_winrate,
                    "alliance": self.elim_alliance,
                    "is_captain": self.is_captain,
                },
                "total": {
                    "wins": self.wins,
                    "losses": self.losses,
                    "ties": self.ties,
                    "count": self.count,
                    "winrate": self.winrate,
                },
            },
        }

        bd = clean["epa"]["breakdown"]
        bd["total_points"] = self.epa
        if self.year >= 2016:
            pairs = list(key_to_name[self.year].items())
            pairs += [("rp_1", "rp_1"), ("rp_2", "rp_2")]
            if self.year >= 2025:
                pairs += [("rp_3", "rp_3")]
            for key, name in pairs:
                bd[name] = getattr(self, f"{key}_epa")
            for derived_name, sources in derived_breakdown.get(self.year, {}).items():
                bd[derived_name] = sum(bd.get(src) or 0 for src in sources)

        return clean


@dataclass(slots=True, eq=False)
class _Match:
    key: str = None
    year: int = None
    event: str = None
    week: int = None
    elim: bool = None
    comp_level: CompLevel = None
    set_number: int = None
    match_number: int = None
    time: int = None
    predicted_time: Optional[int] = None
    status: MatchStatus = None
    video: Optional[str] = None
    red_1: int = None
    red_2: int = None
    red_3: Optional[int] = None
    red_dq: str = None
    red_surrogate: str = None
    blue_1: int = None
    blue_2: int = None
    blue_3: Optional[int] = None
    blue_dq: str = None
    blue_surrogate: str = None
    winner: Optional[MatchWinner] = None
    red_score: Optional[int] = None
    red_no_foul: Optional[int] = None
    red_foul: Optional[int] = None
    red_auto: Optional[int] = None
    red_teleop: Optional[int] = None
    red_endgame: Optional[int] = None
    red_rp_1: Optional[bool] = None
    red_rp_2: Optional[bool] = None
    red_rp_3: Optional[bool] = None
    red_tiebreaker: Optional[int] = None
    red_comp_0: Optional[float] = None
    red_comp_1: Optional[float] = None
    red_comp_2: Optional[float] = None
    red_comp_3: Optional[float] = None
    red_comp_4: Optional[float] = None
    red_comp_5: Optional[float] = None
    red_comp_6: Optional[float] = None
    red_comp_7: Optional[float] = None
    red_comp_8: Optional[float] = None
    red_comp_9: Optional[float] = None
    blue_score: Optional[int] = None
    blue_no_foul: Optional[int] = None
    blue_foul: Optional[int] = None
    blue_auto: Optional[int] = None
    blue_teleop: Optional[int] = None
    blue_endgame: Optional[int] = None
    blue_rp_1: Optional[bool] = None
    blue_rp_2: Optional[bool] = None
    blue_rp_3: Optional[bool] = None
    blue_tiebreaker: Optional[int] = None
    blue_comp_0: Optional[float] = None
    blue_comp_1: Optional[float] = None
    blue_comp_2: Optional[float] = None
    blue_comp_3: Optional[float] = None
    blue_comp_4: Optional[float] = None
    blue_comp_5: Optional[float] = None
    blue_comp_6: Optional[float] = None
    blue_comp_7: Optional[float] = None
    blue_comp_8: Optional[float] = None
    blue_comp_9: Optional[float] = None
    epa_winner: Optional[MatchWinner] = None
    epa_win_prob: Optional[float] = None
    epa_red_score_pred: Optional[float] = None
    epa_blue_score_pred: Optional[float] = None
    epa_red_rp_1_pred: Optional[float] = None
    epa_red_rp_2_pred: Optional[float] = None
    epa_red_rp_3_pred: Optional[float] = None
    epa_blue_rp_1_pred: Optional[float] = None
    epa_blue_rp_2_pred: Optional[float] = None
    epa_blue_rp_3_pred: Optional[float] = None
    pre_epas: Optional[Dict[str, Any]] = None
    epas: Optional[Dict[str, Any]] = None


class Match(_Match, Model):
    def sort(self: "Match") -> int:
        return self.time or 0

    def pk(self: "Match") -> str:
        return self.key

    def __hash__(self: "Match") -> int:
        return hash(self.pk())

    def __str__(self: "Match") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join(
            [
                self.key,
                self.status,
                str(self.red_score),
                str(self.blue_score),
                str(self.red_teleop),
                str(self.blue_teleop),
                str(self.epa_red_score_pred),
                str(self.epa_blue_score_pred),
                str(self.predicted_time),
            ]
        )

    """HELPER FUNCTIONS"""

    def get_red(self: "Match") -> List[int]:
        return [x for x in [self.red_1, self.red_2, self.red_3] if x is not None]

    def get_blue(self: "Match") -> List[int]:
        return [x for x in [self.blue_1, self.blue_2, self.blue_3] if x is not None]

    def get_red_surrogates(self: "Match") -> List[int]:
        return [int(x) for x in self.red_surrogate.split(",") if x != ""]

    def get_blue_surrogates(self: "Match") -> List[int]:
        return [int(x) for x in self.blue_surrogate.split(",") if x != ""]

    def get_red_dqs(self: "Match") -> List[int]:
        return [int(x) for x in self.red_dq.split(",") if x != ""]

    def get_blue_dqs(self: "Match") -> List[int]:
        return [int(x) for x in self.blue_dq.split(",") if x != ""]

    def get_teams(self: "Match") -> List[List[int]]:
        return [self.get_red(), self.get_blue()]

    def get_winner(self: "Match") -> Optional[MatchWinner]:
        # For calculating win prediction metrics
        if self.winner is not None:
            return self.winner

        if self.red_score is None or self.blue_score is None:
            return None

        if self.red_score > self.blue_score:
            return MatchWinner.RED
        elif self.blue_score > self.red_score:
            return MatchWinner.BLUE
        else:
            return MatchWinner.TIE

    def get_breakdown(self: "Match", alliance: str) -> Any:
        if self.year < 2016:
            return np.array([getattr(self, f"{alliance}_score") or 0], dtype=np.float32)
        return np.array(
            [
                getattr(self, f"{alliance}_no_foul") or 0,
                getattr(self, f"{alliance}_auto") or 0,
                getattr(self, f"{alliance}_teleop") or 0,
                getattr(self, f"{alliance}_endgame") or 0,
                int(getattr(self, f"{alliance}_rp_1") or False),
                int(getattr(self, f"{alliance}_rp_2") or False),
                int(getattr(self, f"{alliance}_rp_3") or False),
                getattr(self, f"{alliance}_tiebreaker") or 0,
                *[getattr(self, f"{alliance}_comp_{i}") or 0 for i in range(0, 10)],
            ],
            dtype=np.float32,
        )

    def get_breakdowns(self: "Match") -> Tuple[Any, Any]:
        return self.get_breakdown("red"), self.get_breakdown("blue")

    def to_dict(self: "Match") -> Dict[str, Any]:
        clean: Dict[str, Any] = {
            "key": self.key,
            "year": self.year,
            "event": self.event,
            "week": self.week,
            "elim": self.elim,
            "comp_level": self.comp_level,
            "set_number": self.set_number,
            "match_number": self.match_number,
            "match_name": get_match_name(self.key),
            "time": self.time,
            "predicted_time": self.predicted_time,
            "status": self.status,
            "video": self.video,
            "alliances": {
                "red": {
                    "team_keys": self.get_red(),
                    "surrogate_team_keys": self.get_red_surrogates(),
                    "dq_team_keys": self.get_red_dqs(),
                },
                "blue": {
                    "team_keys": self.get_blue(),
                    "surrogate_team_keys": self.get_blue_surrogates(),
                    "dq_team_keys": self.get_blue_dqs(),
                },
            },
            "pred": {
                "winner": self.epa_winner,
                "red_win_prob": self.epa_win_prob,
                "red_score": self.epa_red_score_pred,
                "blue_score": self.epa_blue_score_pred,
            },
            "result": {
                "winner": self.winner,
                "red_score": self.red_score,
                "blue_score": self.blue_score,
                "red_no_foul": self.red_no_foul,
                "blue_no_foul": self.blue_no_foul,
            },
        }

        if self.year >= 2016:
            rps = ["rp_1", "rp_2"]
            if self.year >= 2025:
                rps += ["rp_3"]

            for rp in rps:
                rp_name = key_to_name[self.year][rp]
                clean["pred"][f"red_{rp_name}"] = getattr(self, f"epa_red_{rp}_pred")
                clean["pred"][f"blue_{rp_name}"] = getattr(self, f"epa_blue_{rp}_pred")
            for rp in rps:
                clean["pred"][f"red_{rp}"] = getattr(self, f"epa_red_{rp}_pred")
                clean["pred"][f"blue_{rp}"] = getattr(self, f"epa_blue_{rp}_pred")
            pairs = list(key_to_name[self.year].items())
            pairs += [(rp, rp) for rp in rps]
            for key, name in pairs:
                clean["result"][f"red_{name}"] = getattr(self, f"red_{key}")
                clean["result"][f"blue_{name}"] = getattr(self, f"blue_{key}")

        clean["pre_epas"] = self.pre_epas or {}
        clean["epas"] = self.epas or {}

        return clean


@dataclass(slots=True, eq=False)
class _Team:
    team: int = None
    name: str = None
    country: Optional[str] = None
    state: Optional[str] = None
    rookie_year: Optional[int] = None
    district: Optional[str] = None
    active: bool = False
    last_active_year: Optional[int] = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    count: int = 0
    winrate: float = 0
    norm_epa: Optional[float] = None
    norm_epa_recent: Optional[float] = None
    norm_epa_mean: Optional[float] = None
    norm_epa_max: Optional[float] = None


class Team(_Team, Model):
    def pk(self: "Team") -> str:
        return str(self.team)

    def __hash__(self: "Team") -> int:
        return hash(self.pk())

    def __str__(self: "Team") -> str:
        # Only refresh DB if these change (during 1 min partial update)
        return "_".join([str(self.team), str(self.count)])

    def to_dict(self: "Team") -> Dict[str, Any]:
        return {
            "team": self.team,
            "name": self.name,
            "country": self.country,
            "state": self.state,
            "district": self.district,
            "rookie_year": self.rookie_year,
            "active": self.active,
            "last_active_year": self.last_active_year,
            "record": {
                "wins": self.wins,
                "losses": self.losses,
                "ties": self.ties,
                "count": self.count,
                "winrate": self.winrate,
            },
            "norm_epa": {
                "current": self.norm_epa,
                "recent": self.norm_epa_recent,
                "mean": self.norm_epa_mean,
                "max": self.norm_epa_max,
            },
        }


@dataclass(slots=True, eq=False)
class _ETag:
    year: int = None
    path: str = None
    etag: Optional[str] = None


class ETag(_ETag, Model):
    def pk(self: "ETag") -> str:
        return self.path

    def __hash__(self: "ETag") -> int:
        return hash(self.pk())


def match_dict_to_objs(data: MatchDict, year: int, week: int) -> Match:
    elim = data["comp_level"] != CompLevel.QUAL

    red_breakdown = data["red_score_breakdown"]
    blue_breakdown = data["blue_score_breakdown"]

    match = Match(
        key=data["key"],
        year=year,
        event=data["event"],
        week=week,
        elim=elim,
        comp_level=data["comp_level"],
        set_number=data["set_number"],
        match_number=data["match_number"],
        time=data["time"],
        predicted_time=data["predicted_time"],
        status=data["status"],
        video=data["video"],
        red_1=data["red_1"],
        red_2=data["red_2"],
        red_3=data["red_3"],
        red_dq=data["red_dq"],
        red_surrogate=data["red_surrogate"],
        blue_1=data["blue_1"],
        blue_2=data["blue_2"],
        blue_3=data["blue_3"],
        blue_dq=data["blue_dq"],
        blue_surrogate=data["blue_surrogate"],
        winner=data["winner"],
        red_score=data["red_score"],
        red_no_foul=red_breakdown["no_foul_points"],
        red_foul=red_breakdown["foul_points"],
        red_auto=red_breakdown["auto_points"],
        red_teleop=red_breakdown["teleop_points"],
        red_endgame=red_breakdown["endgame_points"],
        red_rp_1=red_breakdown["rp_1"],
        red_rp_2=red_breakdown["rp_2"],
        red_rp_3=red_breakdown["rp_3"],
        red_tiebreaker=red_breakdown["tiebreaker"],
        red_comp_0=red_breakdown["comp_0"],
        red_comp_1=red_breakdown["comp_1"],
        red_comp_2=red_breakdown["comp_2"],
        red_comp_3=red_breakdown["comp_3"],
        red_comp_4=red_breakdown["comp_4"],
        red_comp_5=red_breakdown["comp_5"],
        red_comp_6=red_breakdown["comp_6"],
        red_comp_7=red_breakdown["comp_7"],
        red_comp_8=red_breakdown["comp_8"],
        red_comp_9=red_breakdown["comp_9"],
        blue_score=data["blue_score"],
        blue_no_foul=blue_breakdown["no_foul_points"],
        blue_foul=blue_breakdown["foul_points"],
        blue_auto=blue_breakdown["auto_points"],
        blue_teleop=blue_breakdown["teleop_points"],
        blue_endgame=blue_breakdown["endgame_points"],
        blue_rp_1=blue_breakdown["rp_1"],
        blue_rp_2=blue_breakdown["rp_2"],
        blue_rp_3=blue_breakdown["rp_3"],
        blue_tiebreaker=blue_breakdown["tiebreaker"],
        blue_comp_0=blue_breakdown["comp_0"],
        blue_comp_1=blue_breakdown["comp_1"],
        blue_comp_2=blue_breakdown["comp_2"],
        blue_comp_3=blue_breakdown["comp_3"],
        blue_comp_4=blue_breakdown["comp_4"],
        blue_comp_5=blue_breakdown["comp_5"],
        blue_comp_6=blue_breakdown["comp_6"],
        blue_comp_7=blue_breakdown["comp_7"],
        blue_comp_8=blue_breakdown["comp_8"],
        blue_comp_9=blue_breakdown["comp_9"],
    )

    return match


__all__ = [
    "Model",
    "ETag",
    "Event",
    "Match",
    "TeamEvent",
    "TeamYear",
    "Team",
    "Year",
    "match_dict_to_objs",
]

# sb loader: interface, deviations, surprises

Upstream: Statbotics backend (MIT) vendored read-only at `vendor/backend/src`, package `src`.
This stage turns one raw TBA season dump into the upstream `objs_type` tuple.  Rule followed:
port upstream behaviour exactly; where upstream looks wrong, keep it and list it below.

## How it works (read this first)

The loader does **not re-type** upstream logic.  `sb/shim.py` registers stand-in modules in
`sys.modules` so the vendored files run unmodified:

| upstream import | replaced by |
|---|---|
| `src.db.models` | `sb.models` (dataclasses, same field names/order/defaults, upstream method bodies copied verbatim, `match_dict_to_objs` copied verbatim) |
| `src.tba.main` (`get_tba`, needs `requests`+network) | dump-backed `get_tba(url, etag, cache)` serving the same TBA v3 URLs from the raw dump |
| `src.data.utils` | `objs_type`, `create_objs`, `Timer` (verbatim) with no DB/GCS imports |
| `src.db.functions` | no-ops |

Then `load_year` runs, unmodified: `src.data.tba.load_teams` and `process_year` (which calls
`src.tba.read_tba.get_events / get_event_matches / get_event_rankings / get_event_alliances`,
`src.tba.breakdown.clean_breakdown* / post_clean_breakdown`, `src.tba.clean_data`),
then `src.data.avg.process_year`, then `src.data.wins.process_year`.  That is the order of
upstream `src/data/main.py::process_year` up to (not including) the EPA step.

### Calling it

```python
import sys; sys.path.insert(0, "/home/claude/sbrebuild")
from sb.loader import load_year, load_year_full, year_stats, index_team_years

objs = load_year(2023, ".../tba_2023.json.gz")        # objs_type
year, team_years, events, team_events, matches, etags = objs
res  = load_year_full(2023, path)   # LoadResult: .objs .teams .deviations .clean_breakdown_errors .source_calls
```

* `objs[0]` `Year` with score_mean/score_sd/foul/no_foul/auto/teleop/endgame/rp_1..3/tiebreaker/comp_0..9 means filled.
* `objs[1]` `{"<team>_<year>": TeamYear}`: name/country/state/district/rookie_year, wins/losses/ties/count/winrate, next_event_*. EPA fields are defaults (the EPA stage fills them).
* `objs[2]` `{event_key: Event}`: key, name, time, country, state, district, start_date, end_date, type (EventType), week, video, status, num_teams, current_match, qual_matches.
* `objs[3]` `{"<team>_<event>": TeamEvent}`: one per (team, event) with a match; rank (from rankings), records, qual_count, rps, first_event.
* `objs[4]` `{match_key: Match}`: full upstream Match (scores, cleaned breakdown fields red_/blue_ no_foul, foul, auto, teleop, endgame, rp_1..3, tiebreaker, comp_0..9, teams, dq/surrogate strings, winner, `time`, week, elim, status).  `pre_epas/epas/epa_*` are None until the EPA stage.
* `objs[5]` `{}` always.  **Upstream's objs_type has 6 elements (etags last); kept so `(year_obj, *objs[1:])` and `objs[0..4]` indexing work unchanged.**  Your brief listed 5.
* `load_year` kwargs: `tz`, `today`, `with_wins`, `raw` (see below).
* `year_stats(objs)` recomputes the Year means (calls upstream avg.py).  Already done inside load_year.
* `index_team_years(objs) -> {team: TeamYear}` is the per-year dict upstream calls `all_team_years[year]`.

### Running upstream EPA code on it

```python
import sb.loader               # installs the shim (any `import sb.shim; sb.shim.install()` also works)
import src.data.epa.main as E  # or src.models.epa.main.EPA etc.
objs = E.process_year(objs, all_team_years)   # all_team_years: {year: {team: TeamYear}} for the 4 prior seasons
```
Smoke test (rookie priors only, `all_team_years={}`): ran end to end on 2023 in ~16 s, world max auto EPA 21.39
(archive 21.49 with real priors).  No imports of sqlalchemy/requests/attrs/FastAPI/GCS anywhere.

## Verified (2023, vs archive; `python3 -I sb/checks/check_loader.py`)

* Year stats: score_mean 74.59, score_sd 29.37, foul 4.36, no_foul 70.35, auto 19.52, teleop 32.70, endgame 18.13.  All seven equal the archive to 0.00.  rp_1/rp_2/tiebreaker means also equal.
* Events: 185 vs archive 185 non-offseason (61 regional, 94 district, 21 district_cmp, 8 champs_div, 1 einstein); key sets identical; week/time/type/district/state/country/start_date/name 0 mismatches (archive district `chs` mapped through DISTRICT_MAPPING).
* Matches: 16,342 vs archive 16,342 non-offseason; **0 keys missing, 0 extra** (archive total 20,256 includes 3,914 offseason).
* Random 50 matches (seed 1218): 0 mismatches on score/no_foul/auto/teleop/endgame (both alliances), team lists, elim, week, time, winner, comp_level, dq, surrogate, rp_1/2, foul, tiebreaker.
* All 16,342 matches: mismatches only: scores 26 (`-1` vs archive None, unplayed matches, below), team-list order 7 red / 6 blue (all `2023cmptx` einsteins, order differs in TBA now vs March 2024; same teams), winner 3 (2023ilpe_sf8m1, 2023mose_sf3m1, 2023txfor_sf6m1: 125-125 style ties where TBA's current `winning_alliance` differs from the archive).  `time` 0 mismatches on all matches.
* Derived TeamYear.district vs archive: 3284 of 3290 agree; 6 disagree = placeholder teams 9980-9985 (archive None, derived `pch`).
* Determinism: identical SHA-256 of all objects across repeated runs, after loading another season in between, and in fresh processes with different PYTHONHASHSEED and machine TZ.

## Deviations from upstream

1. **TZ for `Event.time`.**  Upstream `get_timestamp_from_str` uses `time.mktime`, i.e. the machine's local zone.  The archive shows local midnight US Eastern (+4h/+5h from UTC by DST), so `load_year` defaults to `tz="America/New_York"` (set via TZ env + tzset only during the call, then restored).  `tz=None` = machine zone.  Matters only for `Event.time`, `TeamEvent.time` and the fallback match time (below).
2. **`datetime.now()` in data/wins.py** (drives `TeamYear.next_event_*`) is pinned to the dump's `fetched` date (override with `today="YYYY-MM-DD"`), so output is a function of the file.  No effect on finished seasons.
3. **Shared `empty_breakdown` reset per load.**  Upstream (see surprises #3) leaks state through a module-level dict; `load_year` resets it to its import-time value before and after each load, so a season loads identically regardless of what was loaded earlier in the process.  Within a season the aliasing is reproduced as upstream has it.
4. **Teams.**  Upstream pulls `teams/0..49` from TBA.  Dumps carry `"teams"`: a list of team objects (key, nickname, city, state_prov, country, rookie_year).  They are served as `teams/{n // 500}` pages through the unmodified `get_teams`/`load_teams`; missing fields default to None.  Names/country are as of the dump fetch (2026-11-11), not as of the season: only `TeamYear.name`, `TeamEvent.team_name`, `country`, `state` are affected, and none feed the EPA.  Teams that played but are not in the list get upstream's own placeholder `Team(name=str(team))`.  If `"teams"` is absent all teams are placeholders.
5. **District team lists** (`/districts/{year}`, `/district/{key}/teams`).  Not in the 2022-2025 dumps.  When `"districts": {abbr: [teams]}` is present (2026), it is served as those two URLs and the unmodified upstream code runs (including `mar`->`fma`); team entries may be `"frc1218"` or `1218`.  When absent, `TeamYear.district` is derived after `process_year`: most common `Event.district` among the team's DISTRICT-type events, else DISTRICT_CMP events, ties alphabetical, otherwise None.  Differs from TBA for teams registered in a district that never played a district event (they get None) and for out-of-district visitors (rare).  2023 check: 99.8% agreement, remainder = placeholder teams.  Logged in `LoadResult.deviations`.
6. **Event team lists** (`/event/{key}/teams/simple`).  Upstream only reads this for events with zero matches in the current year (status Upcoming).  Served from per-event `"teams"` in `event_data` when present (2026); otherwise those events get no team_events.  Past seasons are unaffected.
7. **Offseason.**  The pinned upstream drops event types 99/100 (and any event with `week` None) in `get_events`, has no `offseason` attribute on Event/Match, and never produces `EventType.OFFSEASON`.  The dumps contain only official events anyway, so there is nothing to flag.  (The old archive had an `offseason` column and kept those events: its 307 vs our 185 events, 20,256 vs 16,342 matches.)
8. **Return shape.**  6-tuple, see above.
9. `sb.models.Model.to_dict` uses `dataclasses.asdict` instead of `attr.asdict`.  All concrete models override `to_dict`, so it is unused in practice.  `Model.from_dict` is unchanged and relies on `cls.__slots__` (provided by dataclass slots).
10. **wins.py ported (i.e. run) on purpose.**  The EPA path needs it: `data/epa/calc.py` re-records any TeamEvent with `qual_count == 0`, and Event.num_teams/current_match/qual_matches and TeamEvent.count/qual_* come only from there.  `with_wins=False` skips it.

## Things upstream does that look wrong or surprising (all reproduced)

1. **`elim_alliance` and `is_captain` are never populated.**  `get_event_alliances` keys its dicts by `team[3:]` (a `str`), while `process_year` looks them up with `int` team numbers.  2023: rank is set for 7208/7274 team_events, elim_alliance and is_captain for 0.
2. **Matches are ordered by TBA's response order, not time.**  `objs[4]` is in dump order (finals can come first); the EPA stage does `sorted(..., key=lambda m: m.time)`, which is stable, so ties in `time` fall back to insertion order = event order of `/events/{year}` then match order inside the dump.  `time` is TBA's scheduled time and ties are very common (about 12k of 16k matches in 2023 share a time with some other match), but they are overwhelmingly different events, i.e. disjoint teams, so order is irrelevant.  The order-sensitive cases (tie group containing a shared team) number 4 (2022), 1 (2023: 2023ncash_sf12m1/m2), 1 (2024), 1 (2025: the 2025 sf3 group plus cur_sf3m1/m2).  Keep the dump's match order if you re-serialise.
3. **`clean_breakdown` returns the module-level `empty_breakdown` dict itself** for matches with no breakdown / score 0 / score None.  `post_clean_breakdown` (2018: comp_6..9; 2025: tiebreaker) then writes into it, so every empty alliance of an event aliases one dict and takes the last written values.  In a long-lived upstream process the values also persist into later seasons.  Visible effect in 2025: empty alliances get `tiebreaker=0` instead of None.  In 2018 (not in the dumps) empties would get non-zero comp_6..9.  Reset per load, see deviation 3.
4. **Unplayed matches keep TBA's score `-1`** (`red_score`/`blue_score` = -1, status Upcoming, winner None).  The March-2024 archive stores None.  26 in 2022, 26 in 2023 (all `2023nhgrs_qm53..`), 2 in 2024, 21 in 2025.  Year means filter on status COMPLETED, so unaffected.
5. **A completed match with one alliance on 0** gets no breakdown for that alliance (`score == 0` short-circuits `clean_breakdown`): its no_foul/auto/teleop are None, not 0.  Completed matches with a missing alliance breakdown: 83 (2022), 38 (2023), 20 (2024), 17 (2025).  The EPA `get_breakdown` turns None into 0.
6. **Event status for past seasons is COMPLETED whenever the event has at least one match** (INVALID with none), regardless of whether all matches were played.  Current-year logic (CURR_YEAR=2026 in vendored constants.py) uses Upcoming/Ongoing/Completed.  An event passing the filter but with no matches stays in `objs[2]` with status Invalid (2023: `2023tuis3`); it has no team_events and `num_teams=0`.
7. **Week arithmetic.**  TBA's `week` is 0-based; upstream adds 1 for regional/district/district_cmp ("bug in TBA API"), forces champs divisions and einstein to 8 and does not add 1 to them, and drops events whose week is None.  Week 1 therefore means TBA week 0.  Year means are computed on week 1 only (quals and elims).
8. **`src.models.epa.init.get_constants` is `lru_cache`d on a `Year`.**  `Year.__hash__`/`__eq__` are by `pk` (year number), so a second `Year` object for the same season, with different means, returns the first one's constants.  Fine in a one-shot-per-season process; clear the cache (`get_constants.cache_clear()`) if the engine recomputes a season in-process.  Models reproduce this exactly.
9. **2025 processor-algae hack lives in avg.py** (subtracts 3 x comp_6_mean from no_foul, teleop, comp_7 means).  It runs as upstream has it inside `year_stats`; the matches themselves keep 6-point processor values until `models/epa/breakdown.py` revalues them.
10. `clean_breakdown` prints `ERROR <key> <alliance> <n>` when no_foul != auto+teleop+endgame, and folds the difference into teleop.  Stdout is captured into `LoadResult.clean_breakdown_errors`; 2022-2025 produce 0 lines.
11. The match-time fallback (`get_match_time`, used when TBA `time` is null) is untested by real data (no null `time` in 2022-2025).  Exercised with a synthetic edit: qm -> event_time + number, finals -> event_time + 500 + number.  It compares the raw string `"qm"` with the str-Enum `CompLevel.QUAL`, which works because of the `str` mixin.
12. The auth key committed in `vendor/.../tba/constants.py` is never used (no network path exists in this port).

## Not covered / hand-offs

* 2026 dump: code paths for `districts`, per-event `teams` and Upcoming/Ongoing statuses were exercised with a synthetic 2026-shaped dump (2025 data relabelled; `check_loader.py --only synthetic`), not with real 2026 data.  `clean_breakdown_2026` has not seen real data.  If the real `districts` dict uses another key format than `abbr -> [frcN | N]`, edit `DumpSource.get` in sb/shim.py only.
* `all_team_years` seeding from the archive (norm_epa priors for 2022/2023) is the engine builder's job; `index_team_years` gives the per-year dict shape.
* `get_constants` cache, see surprise 8; numpy scalars end up in TeamEvent/TeamYear EPA fields (`np.float64`) because upstream stores rounded numpy values.

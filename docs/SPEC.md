# PitWall v1 technical spec (WP0)

Status: spec for build branch `build/v1`. Every work package (WP) builds against this file and the
contract files next to it:

- `docs/schema/manifest.schema.json` (manifest, models.json, history)
- `docs/schema/season.schema.json` (season.json, asof.json, traj.json)
- `docs/schema/event.schema.json` (per-event files)
- `docs/perrobot_fields.json` (per-robot TBA fields 2022-2026, machine-readable)
- `docs/BRIEF.md` (decisions, tabs, design rules) and `docs/design/*` (tokens, CSS, templates)

Words: MUST = required for acceptance. SHOULD = do it unless you write down why not. Where this
spec and BRIEF.md disagree, BRIEF.md wins on product decisions and this spec wins on interfaces;
report the conflict.

Ground rules for every WP:

1. `pipeline/vendor/` is read-only. Upstream code runs unmodified through the `sb` shim.
2. Nothing commits the TBA key, prints it, or ships it to the site.
3. Outputs are deterministic: same inputs and code give byte-identical files.
4. Raw TBA data is untrusted: parse with `json` only, never `eval`, never use it to build file
   paths without validating the key against `^[0-9]{4}[a-z0-9]+$` (events) or `^frc[0-9]+$` (teams).
5. You own only the files listed for your WP in section 7. Need a change elsewhere: ask the owner.

---

## 1. Architecture and repo layout

```
TBA API ──(GitHub Action, TBA_KEY secret)──► raw cache (data branch)
         ──► pipeline (Python: EPA + Fast EPA + backtest + features) ──► data/ shards (gzipped JSON)
         ──► Pages artifact (site/ + data/) ──► browser (static ES modules, service worker, offline)
```

The site never runs Python and never talks to TBA. All numbers come from shards.

### 1.1 Branches

| Branch | Holds | Notes |
|---|---|---|
| `main` (and `build/v1` until merged) | code, docs, site source | Pages deploys from the Action, not from a branch |
| `data` | raw TBA cache, prior-chain CSVs, last published `data/` tree, `state.json` | Orphan branch, exactly one commit, force-pushed by the Action each run. Not history, a cache. |

### 1.2 Repo layout (target; owner WP in brackets)

```
.github/workflows/update.yml        [WP1] scheduled + manual update, deploy
.github/workflows/ci.yml            [WP1] tests on push/PR (no secrets)
docs/                               [WP0] this spec, schemas, perrobot_fields.json, design/ (given)
pipeline/
  config.json                       [WP1] seasons, current season, defaults
  requirements.txt                  [WP1] numpy, scipy, jsonschema (pinned)
  build.py                          [WP1] plan + run orchestration (calls WP2/3b/3c functions)
  run.py                            [WP1] existing local CLI, keep working
  fetch/tba_fetch.py, assemble.py   [WP1] ETag fetcher, cache -> loader dump
  priors/archive_team_years_2016_2023.csv   (given)
  models/fast_epa.json              [WP3b] frozen Fast EPA config + tuning record
  tune/tune_fast_epa.py             [WP3b]
  sb/ loader.py shim.py models.py   [WP2]  (existing; change only if needed, e.g. 2026 districts)
  sb/engine.py                      [WP3b] (existing)
  sb/export.py                      [WP2]  (existing CSV export, keep)
  sb/priors.py                      [WP2]  (existing; add archive-file path support)
  sb/jsonout.py shards.py validate_shards.py perrobot.py season_meta.py   [WP2]
  sb/model_runner.py fast_epa.py backtest.py                              [WP3b]
  sb/features.py                    [WP3c]
  sb/model_info.py                  [WP3]
  tests/                            each WP owns tests/test_<its module>.py
  vendor/                           read-only
site/
  index.html                        [WP4a]
  manifest.webmanifest, sw.js       [WP6]
  css/tokens.css (generated)        [WP4a] via site/tools/build_tokens.py
  css/pitwall.css                   [WP4a] components (from docs/design/pitwall-light.css, no :root)
  css/views/<view>.css              owner of the view
  assets/fonts/*.woff2, OFL.txt     [WP4a]
  brand/                            (given SVGs) + PNG icons [WP6]
  vendor/fflate/                    [WP4a] gunzip fallback (MIT)
  js/app.js, js/core/*, js/components/*, js/charts/*   [WP4a] except files listed for WP5/WP6
  js/core/watchlist.js, js/core/csv.js, js/components/asof.js   [WP5]
  js/model/predict.js               [WP3]
  js/views/teams.js team.js         [WP4a]
  js/views/events.js event.js match.js   [WP4b]
  js/views/compare.js hypothetical.js breakdown.js models.js   [WP5]
  js/views/offline.js, js/core/offline.js   [WP6]
  tools/build_tokens.py             [WP4a]
  tools/build_precache.py           [WP6]
  tests/*.test.mjs                  each WP its own files
THIRD_PARTY.md                      [WP1] add fflate (MIT), fonts (OFL) entries supplied by WP4a
```

Published Pages artifact (`_site/`, built in the Action, never committed to `main`):

```
_site/index.html, js/, css/, assets/, brand/, vendor/, sw.js, sw-precache.json, manifest.webmanifest
_site/data/manifest.json
_site/data/models.json
_site/data/history.json.gz
_site/data/{season}/season.json.gz
_site/data/{season}/asof.json.gz
_site/data/{season}/traj.json.gz
_site/data/{season}/events/{event_key}.json.gz
```

---

## 2. Action contract (WP1)

### 2.1 Triggers and modes

```yaml
on:
  workflow_dispatch:
    inputs:
      mode:            {type: choice, options: [auto, live, daily, full], default: auto}
      seasons:         {type: string, default: ""}     # e.g. "2025 2026"; empty = by mode
      force_recompute: {type: boolean, default: false} # rebuild listed (or all) seasons
      deploy:          {type: boolean, default: true}
  schedule:
    - cron: "7,37 * * 2-4 0,4,5,6"   # every 30 min, Thu-Sun (UTC), Feb-Apr
    - cron: "7,37 0-6 * 2-4 1"       # Mon 00:00-06:59 UTC = Sunday evening in US time zones
    - cron: "23 9 * * *"             # daily
```

Minutes 7/37/23 avoid the top-of-hour queue. Mode resolution (`auto`): the two 30-minute crons
-> `live`; the daily cron -> `daily`, except on Mondays (UTC) -> `full`; manual dispatch with
`auto` -> `daily`.

| Mode | Fetches | Typical calls |
|---|---|---|
| live | current season: `/events/{y}`; per-event endpoints only for events in the active window (start_date - 1 day <= today UTC <= end_date + 2 days) or whose last built status is not Completed although end_date has passed | 1 to ~150 |
| daily | current season: everything (events, all event endpoints, districts, team pages) | ~700 |
| full | every configured season: everything | ~3,000 |

A run with no changed responses MUST finish in under 1 minute of job time after checkout.

### 2.2 Permissions, concurrency, secrets

```yaml
permissions: {contents: write, pages: write, id-token: write}
concurrency: {group: pitwall-update, cancel-in-progress: false}   # queue, never cancel mid-run
```

- `TBA_KEY` is exposed only to the fetch step: `env: {TBA_KEY: ${{ secrets.TBA_KEY }}}`. The
  fetcher sends it as header `X-TBA-Auth-Key`, never in a URL, never logged, never written to the
  cache (cache stores bodies and ETags only). A test greps the cache and logs for the key.
- `ci.yml` (push/PR) gets no secrets and runs fixtures only.
- Jobs: `update` (timeout 45 min) then `deploy` (needs `update`, `if: needs.update.outputs.deploy == 'true'`,
  `environment: github-pages`, uses `actions/deploy-pages@v4`).

### 2.3 Steps of the `update` job

1. `actions/checkout@v4` (code). `actions/checkout@v4` with `ref: data, path: cache, fetch-depth: 1`;
   if the branch does not exist, start an empty `cache/`.
2. `actions/setup-python@v5` (python 3.13, pip cache), `pip install -r pipeline/requirements.txt`.
3. `actions/configure-pages@v5` (id `pages`) to learn `base_url`. `curl -fsS {base_url}/data/manifest.json`
   into `live_manifest.json` (missing is fine on first deploy).
4. Fetch: `python pipeline/fetch/tba_fetch.py --cache cache/raw --config pipeline/config.json --mode $MODE [--seasons ...] --report fetch_report.json`.
5. Plan: `python pipeline/build.py plan --cache cache --config pipeline/config.json --fetch-report fetch_report.json --live-manifest live_manifest.json [--force] [--seasons ...] --out plan.json`.
   If `plan.rebuild` is empty and `plan.deploy` is false: write job summary "no changes" and stop (success).
6. Run: `python pipeline/build.py run --plan plan.json --cache cache --work work --out-data staged_data`.
   Builds each season in `plan.rebuild` in ascending order; copies untouched seasons' files from
   `cache/published/`; writes `models.json`, `history.json.gz`, `manifest.json`.
7. Validate: `python pipeline/sb/validate_shards.py staged_data docs/schema` (JSON Schema +
   column-length + VK/BK checks + size budget). A failing season is replaced by its last good
   shards (2.6) before continuing.
8. Stage site: `python pipeline/tools/stage_site.py --site site --data staged_data --out _site`
   (copies, then runs `site/tools/build_precache.py _site` to write `sw-precache.json`).
9. `actions/upload-pages-artifact@v3` with `path: _site`.
10. Update data branch: replace `cache/published/` with `staged_data/`, write `cache/state.json`,
    then in `cache/`: `git checkout --orphan next && git add -A && git commit -m "data <data_version>" && git push -f origin HEAD:data`.
11. Outputs: `deploy=true` if `plan.deploy` and input `deploy` is true.

### 2.4 Raw cache on the `data` branch

```
cache/raw/etags.json                                  {url: {etag, status, fetched_at, sha256}}
cache/raw/{y}/events.json.gz                          /events/{y}  (full objects)
cache/raw/{y}/event/{key}/matches.json.gz             /event/{key}/matches
cache/raw/{y}/event/{key}/rankings.json.gz            /event/{key}/rankings
cache/raw/{y}/event/{key}/alliances.json.gz           /event/{key}/alliances
cache/raw/{y}/event/{key}/teams.json.gz               /event/{key}/teams/keys    (current season only)
cache/raw/{y}/districts.json.gz                       /districts/{y}            (current season only)
cache/raw/{y}/district/{dkey}/teams.json.gz           /district/{dkey}/teams/keys (current season only)
cache/raw/{y}/teams/{page}.json.gz                    /teams/{y}/{page}, page 0.. until empty
cache/raw/{y}/fingerprint.json                        {inputs_hash, endpoint_count, last_change_at}
cache/out/{y}/team_years.csv, run_meta.json           EPA team_years (prior chain), from sb.export
cache/published/                                      last good data/ tree, exactly as deployed
cache/state.json                                      {data_version, code_hash, seasons: {y: {inputs_hash, code_hash, norm_hash, built_at, ok}}}
```

Rules:

- Bodies are stored verbatim (the exact bytes TBA returned, before any parsing), gzip level 9,
  `mtime=0`, so an unchanged body produces an identical blob. **Order matters**: upstream breaks
  match-time ties by `/events/{y}` order then by match order inside `/event/{key}/matches`
  (sb/NOTES.md surprise 2), so nothing may re-sort these lists.
- Requests use `If-None-Match: <etag>`. 304 -> keep cached body. 200 -> store body + new ETag.
  404 on an event endpoint -> store `null` body (the loader treats it as missing). Other errors:
  retry 3 times with 2/4/8 s backoff, then keep the cached body and record the URL under
  `fetch_report.errors`.
- Politeness: at most 4 requests in flight, at least 50 ms between request starts.
- `inputs_hash` (per season) = SHA-256 of the sorted lines `"{relative_path} {sha256(body)}"` over
  every cached endpoint of that season, plus the loader options (`districts`, `event teams`
  inclusion flags).
- The current-season-only endpoints (event teams, districts) are fetched only for
  `config.current_season`. Past seasons are built without them, which keeps 2022-2025 identical to
  the validated runs (NOTES.md deviations 5 and 6). See open question Q4.

`pipeline/fetch/assemble.py season_dump(cache, year) -> dict` builds the loader's raw-dump shape
(sb/loader.py docstring): `{"year", "fetched", "events", "event_data": {key: {"matches", "rankings",
"alliances", ["teams"]}}, "teams", ["districts"]}`. `fetched` = the season's `last_change_at`
(ISO UTC). `districts` = `{abbr: [team keys]}` with `abbr` = district key minus the 4-digit year.
`assemble.py --split tba_YYYY.json.gz --cache DIR` does the reverse (seed a cache from an old
dump; used by tests and for bootstrapping).

### 2.5 What recomputes

`build.py plan` marks season `y` for rebuild when any of:

1. `inputs_hash(y)` differs from `state.seasons[y].inputs_hash`;
2. `code_hash` differs: SHA-256 over the sorted contents of `pipeline/**/*.py`,
   `pipeline/models/*.json`, `pipeline/config.json`, `docs/perrobot_fields.json`,
   `docs/schema/*.json` (the vendored tree included);
3. an earlier season was rebuilt and its `norm_hash` changed (SHA-256 of sorted `team,norm_epa`
   pairs in `out/{y}/team_years.csv`): all later seasons rebuild;
4. `--force` (with `--seasons`, only those; without, all).

The current season is always rebuilt when (1) holds; a live run therefore costs one season
(about 40-60 s: load, EPA, Fast EPA, features, backtest, export). Even with no rebuild,
`plan.deploy = true` when `cache/published/manifest.json` `data_version` differs from the live
site's manifest (a previous deploy failed) or `plan.rebuild` is non-empty.

### 2.6 Priors

- Season 2022 priors come from `pipeline/priors/archive_team_years_2016_2023.csv`: upstream reads
  `year - 1 .. year - 4` minus 2021 and uses the first two found, so 2022 uses **2020 then 2019**
  `norm_epa`. Rows with `offseason == 't'` are excluded (`include_offseason=False`, the validated
  default). Non-numeric team ids (e.g. `100B`) are skipped (existing `sb.priors` behaviour).
- 2023 and later use our own computed seasons wherever available: 2023 <- {2022 computed, 2020
  archive}, 2024 <- {2023, 2022}, 2025 <- {2024, 2023}, 2026 <- {2025, 2024}. A season's prior
  CSV is `cache/out/{y}/team_years.csv` written by `sb.export.export_year` on the EPA run.
- Fast EPA uses the same `all_team_years` (EPA's `norm_epa`) unless its config says
  `"seed": "pieces"` (4.3). It never chains off its own `norm_epa` in v1.
- Note: the validated 2023 replay used archive 2022 priors. Chaining from computed 2022 changes
  2023 slightly; WP2 reports the 2023 `epa_start` MAE between the two chains (Q2).

### 2.7 Failure behaviour (keep last good data)

| Failure | Behaviour |
|---|---|
| TBA unreachable / some endpoints error | Cached bodies kept; seasons whose inputs did not change are not rebuilt. If more than 20% of requested endpoints failed, skip compute and deploy, fail the job (red X; GitHub emails the repo owner). |
| A season's compute or validation fails | Its previous shards are copied from `cache/published/`; its manifest entry gets `stale: true`, `stale_reason`; other seasons still publish; job finishes the deploy and then exits non-zero. `state.seasons[y].ok = false` so the next run retries. |
| Deploy fails | Data branch already holds the new shards; next run sees live `data_version` differs and redeploys. |
| First run, nothing published, compute fails | No deploy; job fails. |
| Concurrency | Runs queue (`cancel-in-progress: false`). |

`stale` is shown in the site header ("Data for 2026 is from Mar 14, 10:07; the last update failed").

### 2.8 CI (`ci.yml`)

On push and pull_request: `pip install -r pipeline/requirements.txt`, `python -m pytest pipeline/tests -q`,
`node --test site/tests/`, schema validation of the fixture shards in `pipeline/tests/fixtures/`,
the leak audit on a 2023 fixture (4.5.4). Fixtures are cut from the existing 2023 dump (one
district event and one champs division, plus their teams) by WP1's `assemble.py --split`; WP1
commits them (< 5 MB).

---

## 3. Data shards (WP2 writes, everyone reads)

### 3.1 Conventions

- **Files.** `data/manifest.json` and `data/models.json` are plain JSON (small, fetched first).
  Everything else is `*.json.gz`: UTF-8 JSON, `gzip` level 9, header `mtime=0`. The browser
  decompresses (5.4); servers may or may not add `Content-Encoding`, so the client checks the
  gzip magic bytes `1f 8b` before decompressing.
- **Serialization.** `json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`,
  keys in the order given here, numpy values converted to Python, NaN/inf -> `null`, `-0.0` -> `0.0`.
  One writer: `pipeline/sb/jsonout.py` (`dump_json(obj, path)`, `dump_gz(obj, path) -> (bytes, hash12)`).
- **Hash.** `hash12` = first 12 hex chars of SHA-256 of the bytes on disk. Fetch URL =
  `data/{path}?v={hash12}`. Same content -> same hash -> caches stay valid.
- **Columnar tables** (the main size saving: measured 44-48% smaller than row objects after gzip on 2025 team_years and team_matches, and
  cheap column scans in JS):

  ```json
  {"n": 3690,
   "cols":   {"team": [1, 2, ...], "name": ["...", ...]},
   "models": {"epa":  {"epa": [12.3, ...], ...},
              "fast": {"epa": [13.1, ...], ...}}}
  ```

  Every array has exactly `n` entries; row `i` is the i-th entry of every array. `models` holds
  model-dependent columns under the model id (`epa`, `fast`). **Column names inside `models.*`
  are upstream dataclass field names even under `fast`** (e.g. `models.fast.epa_acc` is Fast EPA's
  accuracy). This keeps one mapping for both models.
- **Units and rounding** (round with `round(float(x), nd)` at write time):

  | Kind | Unit | Decimals |
  |---|---|---|
  | ratings: `epa`, `auto_epa`, `teleop_epa`, `endgame_epa`, `tiebreaker_epa`, `comp_i_epa`, `epa_start`, `epa_max`, `epa_mean`, `pre_*`, `attr_*`, `resid`, `cons_sd`, `opp_effect*`, `change_mean_resid` | points per match (or count per match for count comps, see `keys.units`) | 2 |
  | `rp_*_epa` | RP score (pre-sigmoid, team contribution) | 4 |
  | `norm_epa`, `unitless_epa` | unitless (mean 1500, sd 250) | 0 (integer) |
  | win prob, RP probability preds, percentiles, winrate, backtest metrics, `change_z` | 0-1 or unitless | 4 |
  | predicted scores, `red_pred`/`blue_pred` point entries | points | 2 |
  | match actuals | as TBA/upstream (integers; `rp_*` booleans) | as is |
  | times | Unix seconds (int) | - |
  | dates | `YYYY-MM-DD` | - |
  | timestamps | ISO 8601 UTC `2026-11-16T12:07:00Z` | - |

- **Missing** = `null`. Unplayed scores (`-1` upstream, NOTES surprise 4) are exported as `null`.
  Enums are their upstream string values (`"qm"`, `"Completed"`, `"district_cmp"`, `"red"`).

### 3.2 Season-dependent keys

`SLOT_INDEX` (position in the upstream 18-entry rating vector, `Year.get_mean_components` order):

```
epa:0 auto_epa:1 teleop_epa:2 endgame_epa:3 rp_1_epa:4 rp_2_epa:5 rp_3_epa:6 tiebreaker_epa:7 comp_i_epa:8+i
```

- **VK** (vector keys) for season `y` = `["epa","auto_epa","teleop_epa","endgame_epa","rp_1_epa","rp_2_epa"]`
  + `["rp_3_epa"]` if `rp_3` is named in `key_to_name[y]` (2025, 2026) + `["tiebreaker_epa"]`
  + `comp_i_epa` for every `comp_i` named in `key_to_name[y]` (2022: comp_0-5; 2023-2025: 0-9; 2026: 0-6).
- **BK** (breakdown keys) = `["score","no_foul","foul","auto","teleop","endgame","rp_1","rp_2"]` +
  `["rp_3"]` (2025+) + `["tiebreaker"]` + named `comp_i`.
- `keys.names` = `key_to_name[y]` (from `src.breakdown`); labels in the UI come from these names
  (`auto_coral_points` -> "Auto coral points").
- `keys.units[vk]`: `rp_score` for rp keys; `points` for `epa`, `auto_epa`, `teleop_epa`,
  `endgame_epa`, `tiebreaker_epa` and any comp whose season name ends in `_points`; `count` for
  other comps (e.g. `coral_l1`, `links`, `auto_pieces`, `processor_algae`, `amplified_notes`).
  Exceptions in `sb/season_meta.py` (WP2): all 2026 comps (`auto_fuel`, `auto_tower`,
  `transition_fuel`, `first_shift_fuel`, `second_shift_fuel`, `endgame_fuel`, `endgame_tower`)
  are `points`, because clean_breakdown_2026 reads `*Points` fields.
- `keys.groups[vk]`: `epa`->total; `auto_epa`/`teleop_epa`/`endgame_epa` -> their phase; rp ->
  `rp`; tiebreaker -> `tiebreaker`; comps by name prefix `auto_`/`teleop_`/`endgame_` (2026:
  `transition_`, `first_shift_`, `second_shift_` -> teleop), else `match` (spans phases, e.g.
  `coral_l4`, `speaker_points`).
- `keys.auto_comps` = `sb.export.auto_comps(y)`.

### 3.3 `data/manifest.json` (schema: manifest.schema.json root)

```json
{"schema": 1, "kind": "manifest",
 "data_version": "20261116T1207Z-811a6fc",
 "generated_at": "2026-11-16T12:07:41Z",
 "code_version": "811a6fc",
 "default_season": 2026, "default_model": "epa", "default_region": "fma",
 "default_watchlist": [1218, 1219],
 "models": [{"id": "epa", "label": "EPA", "default": true},
            {"id": "fast", "label": "Fast EPA", "default": false}],
 "seasons": [
  {"season": 2026, "game": "Rebuilt", "status": "live",
   "updated_at": "2026-11-16T12:07:30Z", "last_tba_change_at": "2026-11-16T12:05:02Z",
   "stale": false, "stale_reason": null,
   "counts": {"teams": 3700, "events": 210, "matches": 9100, "completed_matches": 8800},
   "files": {"season": {"path": "2026/season.json.gz", "hash": "a1b2c3d4e5f6", "bytes": 512000},
             "asof":   {"path": "2026/asof.json.gz",   "hash": "...", "bytes": 340000},
             "traj":   {"path": "2026/traj.json.gz",   "hash": "...", "bytes": 600000}},
   "events_count": 205, "events_bytes": 10900000,
   "inputs_hash": "<64 hex>", "code_hash": "<64 hex>"}],
 "files": {"models":  {"path": "models.json", "hash": "...", "bytes": 9000},
           "history": {"path": "history.json.gz", "hash": "...", "bytes": 210000}},
 "credits": {"data": "The Blue Alliance", "model": "Statbotics EPA (MIT)", "notice": "Not affiliated with FIRST."}}
```

Seasons newest first. `game`: 2022 Rapid React, 2023 Charged Up, 2024 Crescendo, 2025 Reefscape,
2026 Rebuilt (names only; no logos or game art anywhere). `status`: `upcoming` (no completed
match), `live` (current season with any event not Completed or today <= last end_date),
`complete`. Per-event hashes are NOT in the manifest; they are in `season.json`
(`events.cols.file_hash`), keeping the manifest small (it is fetched network-first on every load).

### 3.4 `data/{season}/season.json.gz` (schema: season.schema.json root)

Top level, in this order: `schema, kind:"season", season, game, status, generated_at, data_version,
keys, year, predict, events, teams, robot, backtest`. Write order per season: event files, then
asof and traj, then season.json (it embeds the event file hashes), then the manifest last.

- `keys`: 3.2.
- `year.stats`: upstream `Year` model-independent fields: `score_mean, score_sd, foul_mean,
  no_foul_mean, auto_mean, teleop_mean, endgame_mean, rp_1_mean, rp_2_mean, rp_3_mean,
  tiebreaker_mean, comp_0_mean..comp_9_mean` (only named comps).
- `year.models.{m}.percentiles`: for each VK key `k`: `{p99, p90, p75, p25}` from Year
  `{k}_99p` ... (upstream names). `year.models.{m}.metrics`: upstream Year metric fields verbatim
  (`count, epa_conf, epa_acc, epa_mse, champs_count, epa_champs_*, epa_score_rmse,
  epa_score_error, epa_champs_score_*, rp_count, epa_rp_i_error, epa_rp_i_acc, champs_rp_count, ...`).
- `predict`: `{num_teams: 3, score_sd, foul_rate, models: {epa: {k}, fast: {k}}}`; `foul_rate` =
  `Year.get_foul_rate()`; `k` = model's win-probability constant (`EPA.k_func(y)` = -0.625; Fast
  from config).
- `events` (table, rows sorted by `start_date`, then `key`):
  `cols`: `key, name, week, type, district, state, country, start_date, end_date, status,
  num_teams, qual_matches, current_match, video, match_count, completed_count, file_hash,
  file_bytes` (`file_hash`/`file_bytes` null when no event file was written: no matches and no
  team list).
  `models.{m}`: `epa_max, epa_top_8, epa_top_24, epa_mean, epa_sd, count, epa_conf, epa_acc,
  epa_mse, epa_score_rmse, epa_score_error, rp_count, epa_rp_1_error, epa_rp_1_acc,
  epa_rp_2_error, epa_rp_2_acc, epa_rp_3_error, epa_rp_3_acc` (upstream Event fields).
- `teams` (table, rows sorted by team number, all `TeamYear`s of the season, including teams
  with `count == 0` in a live season):
  `cols`: `team, name, country, state, district, rookie_year, wins, losses, ties, count, winrate,
  next_event_key, next_event_week, events` (`events` = array of event keys the team has a
  TeamEvent at, by event start date).
  `models.{m}`: `epa_start, epa_pre_champs, epa_max,` every VK key (end-of-current-data vector),
  `unitless_epa, norm_epa, total_epa_rank, total_epa_percentile, total_team_count,
  country_epa_rank, country_epa_percentile, country_team_count, state_epa_rank,
  state_epa_percentile, state_team_count, district_epa_rank, district_epa_percentile,
  district_team_count` (upstream TeamYear fields), then features from WP3c (4.6):
  `cons_sd, cons_n, change_flag, change_event, change_z, opp_effect, opp_effect_se, opp_n`.
- `robot`: `fields` = `[{key, codes, active, match_rate}]` for the season's per-robot fields
  (from `perrobot_fields.json`); `counts[key]` = per team row, counts per code over the team's
  counted completed matches (quals and playoffs). Teams tab shows rates from these.
- `backtest.{m}`: 4.5.3 summary for this season.

### 3.5 `data/{season}/events/{event_key}.json.gz` (schema: event.schema.json)

Written for every event with at least one match or a team list. Top level: `schema, kind:"event",
season, event, meta, team_events, matches, team_matches, alliances, robot`. Event files carry no
`generated_at` or `data_version`: an event whose content is unchanged is byte-identical (same hash)
across rebuilds, so caches and the offline download stay valid. Those stamps live only in the
manifest and `season.json`.

#### 3.5.1 `meta`
Event row fields: `key, name, week, type, district, state, country, start_date, end_date,
status, num_teams, qual_matches, current_match, video`.

#### 3.5.2 `team_events` (table, sorted by team)
`cols`: `team, team_name, first_event, wins, losses, ties, count, winrate, qual_wins,
qual_losses, qual_ties, qual_count, qual_winrate, rps, rps_per_match, rank, num_teams`.
`models.{m}`: `epa_start, epa_pre_elim, epa_mean, epa_max,` every VK key (end-of-event vector,
upstream TeamEvent), `unitless_epa, norm_epa,` then `pre_<vk>` for every VK key (the full rating
vector entering the event = the team's pre-match vector at its first match here, or its current
vector if it has not played here yet), then change detector fields `change_k, change_mean_resid,
change_z, change_flag` (4.6).

Upstream never fills `elim_alliance`/`is_captain` (NOTES surprise 1); they are not exported.
Alliances come from raw TBA (3.5.6).

#### 3.5.3 `matches` (table, rows in processing order = `seq` ascending)
`cols`: `key, comp_level, set_number, match_number, elim, time, predicted_time, status, video,
seq, red, blue, red_dq, blue_dq, red_surrogate, blue_surrogate, winner,` then `red_<bk>` and
`blue_<bk>` for every BK key interleaved as `red_score, blue_score, red_no_foul, blue_no_foul, ...`.
- `seq` = index in the season's processing order (`sb.export.processing_order`), global across
  events, so cross-event charts sort by it.
- `red`/`blue` = arrays of team numbers (3, or 4 on Einstein-style alliances); `*_dq`,
  `*_surrogate` = arrays of team numbers (upstream comma strings split).
- `winner`: `"red" | "blue" | "tie" | null`.

`models.{m}`: `epa_winner, epa_win_prob, epa_red_score_pred, epa_blue_score_pred,
epa_red_rp_1_pred, epa_red_rp_2_pred, epa_red_rp_3_pred, epa_blue_rp_1_pred, epa_blue_rp_2_pred,
epa_blue_rp_3_pred` (upstream Match fields; recorded before the match updates any rating, also for
unplayed matches), then `red_pred, blue_pred`: arrays aligned with `keys.vector` holding the
model's alliance prediction for each slot exactly as `predict_match` built it (sum of the three
pre vectors after `post_process_breakdown`; rp entries after the sigmoid, i.e. probabilities;
point entries before the foul-rate scaling). Recorded by the model runner (4.1).

#### 3.5.4 `team_matches` (table)
One row per (match, team) in match-row order, red slots then blue slots, same rows as
`team_matches.csv` for the event. `cols`: `m` (row index into `matches`), `team`, `alliance`
(0 red, 1 blue), `slot` (1-4).
`models.{m}`: every VK key = post-match vector (null when the team was not updated: unplayed
match, or slot 4 beyond `num_teams`), then `attr_epa, attr_auto_epa, attr_teleop_epa,
attr_endgame_epa` = the model's attribution (target value) for that team in that match (null
when not attributed).

**Pre-match vectors are derived, not stored** (halves the file): for row `r` (team `t`) and model
`M`, `pre(r)` = `M` post vector of the nearest earlier row of `t` in this file whose post is
non-null; if none, the `team_events` row of `t`: `models.M.pre_<vk>`. This is exact because a
team's rating changes only through its own matches and Fast EPA's passback runs only between
events (4.3). WP2's test MUST reconstruct `pre_*` of `team_matches.csv` exactly for every row,
2023-2025.

Derived per-row quantities the site uses: change = post - pre; residual = `attr_epa - pre.epa`;
effective step = (post.epa - pre.epa) / (attr_epa - pre.epa) when the denominator is non-zero.

#### 3.5.5 `match_auto` is a view, not a table
Every `match_auto.csv` column is reconstructible: identifiers and teams from `matches`;
`auto_points` = `red_auto`/`blue_auto`; auto comps = `{alliance}_{comp}` for `keys.auto_comps`;
`auto_not_in_comps` = auto - sum(auto comps); `pred_auto` = sum of the three pre `auto_epa`
(rounded 2). WP2's test MUST reproduce `match_auto.csv` exactly from the event files.

#### 3.5.6 `alliances`
From raw `/event/{key}/alliances` (not upstream): `[{name, picks:[int], declines:[int],
backup: {in:int, out:int}|null, status: {level, status, record}|null}]`, or `null` if absent.

#### 3.5.7 `robot`
Per match row, six entries: red slots 1-3 then blue slots 1-3 (`RobotN` = `team_keys[N-1]`).
- `fields`: `[{key, codes, active}]`.
- `values[key]`: per match `[6 codes]` (index into `codes`) or `null` if no breakdown.
- `points[slot]`: per match `[6 numbers]`: robot-attributed points for that breakdown slot
  (`comp_i`, `auto`, `endgame`), computed with `perrobot_fields.json` rules (4.4).
- `scored`: per match `[6 numbers]`: total robot-attributable points per robot, each point counted
  once (2022 comp_5 and endgame are the same points; 2025 comp_9 and endgame likewise).
- `ok`: per match `[red, blue]`: true if every active part reproduced its `check_field` exactly.

The Match tab marks `scored` as "robot-attributed" and the remainder (`no_foul - sum(scored)`) as
"alliance points split by the model".

### 3.6 `data/{season}/asof.json.gz` (schema: season.schema.json#/$defs/asofFile)

Rows aligned with `season.json` teams (same order and n; `team` repeated for safety).
`points` = 9 entries `w0..w8`: `w0` = preseason (`epa_start` vector), `wN` = after every match
with `week <= N` (upstream week numbering: 1-7 in-season, 8 = Champs). Labels: "Preseason",
"After week 1" .. "After week 7", "End of season". "Before Champs" = `w7`.
`models.{m}.{epa,auto_epa,teleop_epa,endgame_epa}` = per team `[9 values]`, carried forward
when the team did not play that week. Ranks as of a week are computed in the browser.

### 3.7 `data/{season}/traj.json.gz` (schema: season.schema.json#/$defs/trajFile)

Per team (aligned with teams rows): `seq[]` and `event[]` (index into season events rows) for
each counted match with a non-null post vector, in processing order; `models.{m}.epa[]` =
post total per entry (2 decimals); `models.{m}.resid[]` = `attr_epa - pre.epa` per entry, **1 decimal**
(null if the match did not update ratings). **`seq[]` is delta-coded**: per team, the first entry is the
absolute global match seq and every later entry is the difference from the previous one
(seq[i] = seq[i-1] + delta[i]); seqs are strictly increasing, so deltas are >= 1. Used by Team (rating over time vs leaders), Compare, sparklines.

### 3.8 `data/history.json.gz` and `data/models.json` (manifest.schema.json $defs)

- `history`: one row per (team, season) over all published seasons: `cols` `team, season, name,
  district, state, country, wins, losses, ties, count`; `models.{m}`: `epa, auto_epa, teleop_epa,
  endgame_epa, norm_epa, total_epa_rank, total_team_count, district_epa_rank,
  district_team_count`. Rebuilt whenever any season is rebuilt. Team page "Season over season".
- `models.json`: per model `id, label, short_description, how_it_works[], constants, settings,
  source, config_hash, [tuning]` (4.7), plus `scoreboard[]` = `{season, model, in_tuning_set,
  overall}` for every published season and model.

### 3.9 Size estimates (measured)

Prototype: real 2023-2025 CSV outputs written in the 3.4-3.7 layout, gzip 9, with Fast EPA
simulated as EPA x (1 + 5% noise) at 2 decimals (worst case for compression). Not yet included:
`red_pred/blue_pred`, `robot.points/scored`, features; budget +15% for them.

| Season | season.json | asof | traj | event files (n, median, max) | Season total |
|---|---|---|---|---|---|
| 2023 | 0.43 MB | 0.31 MB | 0.55 MB | 9.64 MB (184, 52 KB, 91 KB) | 10.9 MB |
| 2024 | 0.42 MB | 0.31 MB | ~0.57 MB | 9.09 MB (190, 48 KB, 82 KB) | 10.4 MB |
| 2025 | 0.48 MB | 0.35 MB | 0.62 MB | 10.98 MB (203, 54 KB, 93 KB) | 12.4 MB |
| 2022 (est., 14.6k matches) | ~0.40 | ~0.30 | ~0.50 | ~8.6 MB | ~9.8 MB |
| 2026 (est.) | ~0.50 | ~0.35 | ~0.62 | ~11 MB | ~12.5 MB |

All five seasons: about 56 MB gzipped (65 MB with the +15%), 1,000 files; well inside the Pages
1 GB site limit. Uncompressed JSON is about 3.2x (an event file is 150-180 KB in memory).
"Download season for offline" = 11-14 MB per season. Validator budgets (fail the season if
exceeded): season.json <= 800 KB, asof <= 600 KB, traj <= 1 MB, any event file <= 200 KB.

---

## 4. Pipeline model interface

### 4.1 Model runner (WP3b): one loader pass, two models

`pipeline/sb/model_runner.py`:

```python
@dataclass
class ModelRun:
    model_id: str                      # "epa" | "fast"
    year: int
    objs: tuple                        # upstream objs_type (deep copy); every EPA field holds THIS model's values
    alliance_pred: Dict[str, Tuple[np.ndarray, np.ndarray]]   # match_key -> (red, blue) breakdown after post_process_breakdown (18-slot order)
    attrib: Dict[str, Dict[int, np.ndarray]]                  # match_key -> team -> attribution vector (18-slot order)
    updated: Set[Tuple[str, int]]      # (match_key, team) pairs where update_team ran
    config_hash: str                   # "" for EPA; sha256 of the canonical Fast EPA config

def load_base(year: int, raw: dict | str) -> tuple            # sb.loader.load_year once per season
def run_model(year: int, base_objs: tuple, all_team_years, model_id: str,
              config: dict | None = None, robot_facts=None) -> ModelRun
```

Mechanism (no vendored edits): deep-copy `base_objs`; clear `get_constants` cache (engine.py
does the same); temporarily set `src.data.epa.calc.EPA` to a zero-argument factory returning the
model instance; call `src.data.epa.main.process_year(objs, all_team_years)` (calc -> agg ->
metrics, exactly upstream); restore `calc.EPA` in `finally`; clear the cache again. `agg` and
`metrics` then fill ranks, `norm_epa`, Year/Event metrics for whichever model ran.

Recording: both models inherit `RecordingMixin`, which wraps `predict_match` (stores
`(red_pred.breakdown, blue_pred.breakdown)` copies), `attribute_match` (stores the returned
attributions' vectors) and `update_team` (adds `(match.key, team)`), always calling `super()`
first and never touching state. `RecordingEPA(RecordingMixin, EPA)` is the EPA model.

### 4.2 EPA

The vendored `src.models.epa.main.EPA`, untouched. Acceptance (WP3b): the `RecordingEPA` run
exported with `sb.export.export_year` is byte-identical to `engine.run_year` + `export_year` for
2023, 2024, 2025.

### 4.3 Fast EPA (WP3b)

`pipeline/sb/fast_epa.py`: `class FastEPA(RecordingMixin, EPA)` with `__init__(self, config, robot_facts)`.
It reuses `EPA.predict_match` unchanged (so the browser predictor serves both models; only `k`
may differ) and overrides `start_season` (seed), `attribute_match` (robot attribution, 4.4),
`update_team` (dampening, elim weight) and adds passback. Every behaviour is a config switch.

`pipeline/models/fast_epa.json`:

```json
{"id": "fast", "version": 1,
 "settings": {
  "percent": {"first_n": 3, "first": 0.6, "start": 0.4, "end": 0.2, "ramp_start": 3, "ramp_len": 6},
  "elim_weight": 0.3333,
  "mean_reversion": 0.4,
  "year_one_weight": 0.7,
  "seed": "norm_epa",
  "robot_attribution": true,
  "robot_weight": 1.0,
  "residual_split": "equal",
  "passback": {"enabled": false, "percent_scale": 0.5, "scope": "quals"},
  "win_k": -0.625
 },
 "tuning": {"tuned_on": [], "tuned_at": null, "objective": "mean over seasons of win-prob Brier, all completed matches", "search": {}, "baseline": {}, "chosen": {}, "top": []}}
```

(the numbers above are starting defaults; WP3b replaces them with tuned values.)

- **percent(x)**, x = team's qual matches counted so far this season:
  `first` if `x < first_n`; else `clip(start - (start - end) * (x - ramp_start) / ramp_len, end, start)`.
  The EPA-equivalent config is `first_n 0, start 1/3, end 0.2, ramp_start 6, ramp_len 6`
  (upstream `2/3 * clip(0.5 - 0.2/6*(x-6), 0.3, 0.5)`).
- **elim_weight**, **mean_reversion**, **year_one_weight**: as upstream constants, tunable.
- **seed**: `norm_epa` (upstream `get_init_epa`) or `pieces`: the prior z-score comes from the
  team's previous-season z-score of its piece-count rating (sum of that season's piece comps:
  2022 comp_1-4, 2023 comp_3-5, 2024 comp_1+comp_2, 2025 comp_2-5; z over teams with count > 0)
  blended 0.7/0.3 over the last two computed seasons, then `mean_reversion` as upstream. Falls back
  to `norm_epa` for teams without a computed prior season (always for 2022).
- **robot_attribution**, **robot_weight**, **residual_split**: 4.4.
- **passback**: when a team is about to play its first match at a new event (or at season end),
  replay its completed qual matches at its previous event in order: recompute that match's
  alliance prediction from the current ratings of all six teams, recompute this team's
  attribution, apply `update_team` with `percent * percent_scale`. Runs only between events, so
  it never sees a match later than the one being predicted. Partners' ratings are read, not
  changed. Deterministic order: by team number within a match time.
- **win_k**: win probability constant (exported to `season.predict.models.fast.k`).

Tuning (`pipeline/tune/tune_fast_epa.py`, run by WP3b in a session, not in the Action):
seasons 2022-2025 only (the script MUST refuse 2026; a test asserts it), coordinate search over
`percent.*`, `elim_weight`, `mean_reversion`, `robot_weight`, `residual_split`, `passback.*`,
`seed`, `win_k` (start from the EPA-equivalent point; at most 300 configs). Objective: mean of
per-season win-prob Brier (4.5.2) over all completed matches; tie-break score MAE. The chosen
config, the EPA baseline and the top 20 configs are written into `fast_epa.json.tuning`
with `tuned_on: [2022, 2023, 2024, 2025]`. The Action never tunes; it runs the frozen config.
2026 metrics are reported as the out-of-sample check (`in_tuning_set: false`).

Acceptance (WP3b): FastEPA with the EPA-equivalent config, `robot_attribution false`, passback
off, seed `norm_epa`, `win_k -0.625` reproduces the EPA run byte-identically (CSV export) for 2023.
Each model run <= 60 s per season on a GitHub runner.

### 4.4 Per-robot attribution (WP2 facts, WP3b model)

`pipeline/sb/perrobot.py` (WP2) loads `docs/perrobot_fields.json` and exposes:

```python
def robot_facts(year: int, raw: dict) -> RobotFacts
# RobotFacts.by_match: Dict[match_key, Tuple[Optional[AllianceFacts], Optional[AllianceFacts]]]  (red, blue)
# AllianceFacts.values: Dict[field_key, List[Optional[str]]]   # 3 robots
# AllianceFacts.points: np.ndarray shape (3, 18)                # robot points per SLOT_INDEX slot; NaN = not robot-attributable
# AllianceFacts.ok: bool                                        # every active part matched its check_field
# RobotFacts.active_parts: List[dict]                           # parts whose season match rate >= 0.99
# RobotFacts.match_rate: Dict[str, float]                       # per part id "{field}:{slot}"
```

Which components get robot-level credit (only active parts whose `slot` is a `comp_i` or a
standard slot that is itself a rating slot; summary of perrobot_fields.json):

| Season | Robot-credited slots | Contained in | Residual (alliance-level, split) |
|---|---|---|---|
| 2022 | comp_0 taxi (2/robot), comp_5 = endgame (hangar 4/6/10/15) | auto, endgame, total | cargo (comp_1-4), teleop |
| 2023 | comp_0 (mobility 3/robot + auto dock 8/12 split among docked), comp_9 (park 2, dock 6/10) | auto, endgame, total | grid pieces, links (comp_1-8) |
| 2024 | comp_0 leave 2; comp_5 park 1; comp_6 onstage 3; comp_7 harmony (shared chain); comp_8 trap 5 (location credit, unverified attribution); comp_9 spotlight 1 | auto, endgame, total | notes (comp_1-4) |
| 2025 | comp_9 = endgame (barge 2/6/12). Leave (3/robot) is a fact and robot points but has no slot: residual | endgame, total | coral, algae, leave |
| 2026 | comp_1 auto tower, comp_6 endgame tower (all unverified; enabled only if the build check passes) | auto, endgame, total | fuel (comp_0, comp_2-5) |

Attribution in `FastEPA.attribute_match` for an alliance with teams i = 1..3, current means
`mu_i` (18-slot), alliance actual `Y` and prediction `P` (from `predict_match`), `E = Y - P`:

1. If robot attribution is off, facts are missing, or `ok` is false: EPA rule, `err_i = E / 3`.
2. Robot slot `c` (active part): `err_i[c] = y_i[c] - mu_i[c]` (the robot's own error).
3. Aggregate slot `K` in {total(0), auto(1), teleop(2), endgame(3)} with robot slots
   `R_K = {c : an active part has slot c and K in also_in}`:
   `r_i = sum_{c in R_K} (y_i[c] - mu_i[c])`;
   residual `Q = E[K] - sum_j r_j`; `err_i[K] = r_i + s_i * Q`.
4. Every other slot: `err_i[s] = E[s] / 3`.
5. `robot_weight w`: final `err_i = w * err_i(robot) + (1 - w) * E / 3`.
6. `attrib_i = post_process_attrib(year_obj, mu_i, err_i, match.elim)` (keeps the 2025 processor
   adjustment and the elim RP freeze), wrapped as upstream `Attribution`.

Residual split `s_i`: `equal` -> 1/3; `share` -> `max(0.5, mu_i[K] - sum_{c in R_K} mu_i[c]) /
sum_j (same)` (each team's predicted non-robot share). By construction `sum_i err_i[K] = E[K]`
whenever facts sum to the alliance totals, so alliance totals are conserved.

The browser never re-does attribution; it shows `robot.points`, `robot.scored` and the residual.

### 4.5 Backtest harness (WP3b)

#### 4.5.1 What counts as pre-match
A prediction for match `m` is the value `predict_match` recorded for `m` while the season is
replayed in processing order, before `m` updates anything. Both models are online, so the
recorded `epa_*` match fields are true pre-match predictions. Forbidden: any replay or passback
that uses `m` or a later match before `m` is predicted; hindsight ratings; tuning on 2026.

Known look-ahead, kept for Statbotics parity and reported in every summary as
`year_constants: "season_week1"`: Year means and `score_sd` (rating initialisation, win-prob
scale, foul rate) come from the season's week-1 matches, including matches being predicted in
week 1. Effect is small; quantifying it is open question Q3.

#### 4.5.2 Metrics (per model, over a set of matches)
Included: `status == Completed`, both scores >= 0, prediction present. Outcome `o` = 1 red win,
0 blue win, 0.5 tie (upstream `outcome_dict`). `p` = win prob clipped to `[EPS, 1-EPS]`
(`src.constants.EPS`).

| Metric | Definition |
|---|---|
| `n`, `ties` | matches; of which ties |
| `acc` | `mean((p > 0.5) == (o > 0.5))` (upstream `get_acc`, so EPA numbers match Statbotics) |
| `acc_no_ties` | same over non-tie matches |
| `brier` | `mean((p - o)^2)` (upstream `epa_mse`) |
| `log_loss` | `-mean(o ln p + (1 - o) ln(1 - p))` |
| `conf` | `mean(max(p, 1 - p))` |
| `score_n`, `score_mae`, `score_rmse`, `score_bias` | per alliance, predicted score (with foul scaling) vs final score: `mean |pred - actual|`, RMSE, `mean(pred - actual)` |
| `rp.rp_k` | quals only, per alliance: `n`, `brier`, `acc` of the RP probability vs the RP boolean |

#### 4.5.3 Slices and output
`backtest.score_season(run: ModelRun, leak_audit: dict) -> dict` returns the
`season.schema.json#/$defs/backtestSummary` object:
- `overall`;
- `slices.week` "1".."8"; `slices.event_type` regional/district/district_cmp/champs_div/einstein;
  `slices.comp_level` qual/elim;
- `slices.season_matches`: `first3` if the minimum, over the up-to-six counted teams, of matches
  each had completed and been updated on this season before `m` is < 3, else `later`;
- `slices.event_matches`: same count restricted to matches at `m`'s event;
- `calibration`: 10 bins `[0, 0.1)`, ..., `[0.9, 1.0]` of `p`: `n`, `mean_pred`, `frac_red_win`
  (ties count 0.5).
All floats rounded to 4 decimals.

#### 4.5.4 Leak audit (truncation invariance)
`backtest.leak_audit(year, base_objs, priors, model_id, config, robot_facts, cuts, seed=1218)`:
pick `cuts` cut times `T` from the season's distinct match times (seeded). For each, make a copy
of the loaded season where every match with `time >= T` is set to status Upcoming with scores and
breakdowns cleared, hold the Year object fixed to the full-season one, run the model, and compare
the recorded `epa_win_prob` and score predictions for every match with `time <= T` (the first
unplayed time included) to the full run. Pass = all differences 0 (EPA) / <= 1e-9 (Fast EPA).
CI runs 10 cuts on the 2023 fixture for both models; `full` mode runs 3 cuts per season; other
runs copy the last result into `leak_audit`.

### 4.6 Model features (WP3c)

`pipeline/sb/features.py`: `compute(run: ModelRun, events: dict, config: dict) -> SeasonFeatures`
with `team_year[team]`, `team_event[(team, event)]` dicts holding exactly the export fields below.
Residual per updated team-match: `resid = attrib[0] - pre[0]` (total).

- **Consistency**: `cons_sd` = sample SD (ddof 1) of `resid` over the team's updated qual
  matches this season; `cons_n` = count; `cons_sd = null` when `cons_n < 4`. Sparklines use
  `traj.resid`.
- **Change detector** (per team-event): first `k = min(3, quals played here)` updated quals at the
  event, need `k >= 2`. `change_mean_resid` = mean of `attr_epa` over those matches minus the
  team's `pre_epa` entering the event (fixed baseline, not the moving pre). `sigma` = pooled SD of
  all qual `resid` in the season for that model. `change_z = change_mean_resid / (sigma / sqrt(k))`.
  `change_flag` = +1 if `change_z > 2`, -1 if `< -2`, 0 otherwise, null if `k < 2`. Team-year
  fields: `change_flag`, `change_event`, `change_z` from the team's latest event with non-null flag.
  Threshold 2.0 lives in `features.CONFIG` and is shown in "How this rating works".
- **Opponent effect (experimental)**: over the team's updated quals, `o_j` = opposing alliance
  actual `no_foul` minus that alliance's predicted `no_foul` (`alliance_pred` slot 0), not divided
  by 3 (it is an effect on a whole alliance). `opp_effect` = mean `o_j`, `opp_effect_se` =
  SD (ddof 1) / sqrt(n), `opp_n` = n; all null when `n < 12`. Negative = opponents scored less than predicted. The site labels it experimental
  and shows it only when `|opp_effect| > 2 * opp_effect_se`.

### 4.7 Model info (WP3)

`pipeline/sb/model_info.py: describe(model_id, year_obj=None) -> dict` returns the `models.json`
entry. EPA constants are read from the vendored modules at runtime (`constants.NORM_MEAN`,
`NORM_SD`, `INIT_PENALTY`, `YEAR_ONE_WEIGHT`, `MEAN_REVERSION`, `ELIM_WEIGHT`, `EPA.k_func(2026)`,
`EPA.percent_func` sampled at x = 0, 6, 9, 12), never retyped. `how_it_works` is 3-6 short
paragraphs in the BRIEF voice (plain, sentence case, numbers with units). Fast EPA reads
`pipeline/models/fast_epa.json`.

---

## 5. Site

### 5.1 Technology decision

**Plain ES modules, no build step, no framework, charts as hand-written SVG.**

- Pages serves `site/` as is; the Action only copies files and writes `sw-precache.json`. Nothing
  to break in a build, and every file a student opens in the browser is the file in the repo.
- The design system is defined in CSS classes, including the SVG chart classes (`pw-line--you`,
  `pw-tick`, `pw-axis-title`, ...). SVG charts use them directly, theme through CSS variables with
  no redraw, print cleanly, and take `role="img"` + `aria-label`. Our largest chart is ~4 lines x
  ~80 points, so canvas speed (uPlot) buys nothing; uPlot is not used in v1. Revisit only if a
  later chart needs thousands of points.
- Svelte would need a compile step in the Action and a second toolchain (Node build) for little
  gain at this size.
- No runtime CDN: fonts (OFL) are self-hosted in `site/assets/fonts/` (latin subset woff2:
  Barlow 400/500/600/400i, Barlow Condensed 500/600/700, JetBrains Mono 400/500/600, Source Serif
  4 400/400i), and the only third-party code is `site/vendor/fflate/` (gunzip fallback, MIT).
- Browsers: last 2 versions of Chrome/ChromeOS, Safari/iOS (16.4+), Firefox, Edge.
- All URLs are relative (`./data/...`, `./js/...`): the site lives at `/<repo>/` on Pages.

Rendering pattern: `js/core/html.js` exports a tagged template `html\`...\`` that escapes every
interpolated value unless it is a `SafeHTML` (from `html` or `raw()`), and `mount(el, safe)`.
Views re-render their root on state change and use event delegation (`data-act="..."`
attributes), no per-row listeners. Tables over 100 rows render the first 100 with a
"Show all N teams" button.

### 5.2 Core module APIs (WP4a unless noted; other WPs code against these signatures)

```js
// js/core/data.js
getManifest({force}?) -> Promise<Manifest>            // network-first via SW; memoized per page load
getSeason(season) -> Promise<Season>                 // decoded season.json + helpers below
getAsof(season), getTraj(season) -> Promise<object>
getEvent(season, eventKey) -> Promise<EventData>
getModels(), getHistory() -> Promise<object>
fileUrl(fileRef) -> string                           // "./data/" + path + "?v=" + hash
// decoded objects keep the JSON shape; tables get helpers:
tbl(t) -> {n, col(name, model?), get(i, name, model?), rows(model) /* memoized row objects merging cols + models[model] */,
           index(name) /* Map value -> row index */}
derivePre(eventData, model) -> Array<object|null>    // 3.5.4 rule, per team_matches row
isStale(response) -> boolean                         // SW header X-PitWall-Stale

// js/core/router.js
parse(hash) -> {view, path: string[], query: Record<string,string>}
link(view, path?, query?) -> string                  // "#/team/1218?season=2025"
navigate(view, path, query, {replace}?)
start(routes)                                        // routes: {teams: () => import('../views/teams.js'), ...}

// js/core/state.js
get() -> {season, model, theme, watchlist}           // resolved: URL > localStorage prefs > manifest defaults
setQuery(partial, {replace}?)                        // merges into current hash query
subscribe(fn) -> unsubscribe

// js/core/prefs.js   (localStorage, every access in try/catch, falls back to memory)
read(key, fallback), write(key, value)

// js/core/fmt.js
rating(x) "74.6" | rp(x) "0.53" | pct(p) "92%" | delta(x) "+3.3"/"−3.3" (U+2212) | rank(r, n) "6th of 139"
matchLabel(comp_level, set, number) "Q32" | "QF2-1" | "SF2-1" | "F1"
dateTime(unix|iso) "Nov 11, 2026, 23:34" (local) | record(w, l, t) "35-22-1"
compLabel(name) "Auto coral points" | unit(key, keys) "points" | "count"

// js/core/regions.js
REGIONS (district abbr -> display name), regionOptions(season), matchRegion(teamRow, region)

// js/core/watchlist.js  [WP5]
get() -> number[] (default manifest.default_watchlist), has(team), toggle(team), subscribe(fn)

// js/core/csv.js  [WP5]
toCSV(columns: {key, label}[], rows: object[]) -> string (RFC 4180, "\n")
download(filename, text)   // filename: pitwall-{view}-{season}[-{region}][-{asof}].csv

// js/components/asof.js  [WP5]
asofControl({scope: 'season'|'event', value, options}) -> SafeHTML  // pw-select, plus range input on event scope
asofOptions(season|eventData) -> {id, label}[]

// js/model/predict.js  [WP3]  (pure, no DOM)
predictAlliances(season, redVecs, blueVecs, {model, elim}) ->
  {red: {score, breakdown /* by VK */, rp: [p1, p2, p3]}, blue: {...}, winProbRed}
```

### 5.3 Routes and state

Hash routing; the query carries everything needed to reproduce a view (shareable links).

| Route | View | Query (defaults in brackets) |
|---|---|---|
| `#/` | redirect to `#/teams` | |
| `#/teams` | Teams | `season` [manifest default], `model` [epa], `region` [fma], `asof` [end], `cols` [ratings: ratings/components/rp/robot], `q`, `sort` [-epa] |
| `#/team/{num}` | Team | `season`, `model` |
| `#/events` | Events | `season`, `region` [all], `week`, `type` |
| `#/event/{key}` | Event | `model`, `tab` [teams: teams/matches/rankings/alliances/accuracy], `asof` [end; or start, or a match label like Q32] |
| `#/match/{key}` | Match | `model` (both models are always shown; this picks the lead one) |
| `#/compare` | Compare | `season`, `model`, `teams` (2-4, comma list) [watchlist] |
| `#/hypothetical` | Hypothetical | `season`, `model`, `red`, `blue` (3 teams each), `ratings` [end, or `event:{key}` = pre-event vectors from that event file] |
| `#/breakdown` | Breakdown | `season`, `model`, `team` [first watchlist team], `region` [fma] |
| `#/models` | Models | `season` |
| `#/offline` | Offline | |

Region syntax: district abbreviation (`fma`), `state:PA`, `country:USA`, or `all`. Region options
list districts present in the season, then states and countries with >= 10 teams.
Season `asof`: `end`, `w0`..`w8`, `prechamps` (= `w7`). Event `asof`: `start`, `end`, or a match label.

State split:
- **URL**: season, model, region, asof, sort, filters, team lists, tab.
- **localStorage** (keys versioned): `pw.v1.theme` (`light`|`dark`; absent = system),
  `pw.v1.watchlist` (int array, default `[1218, 1219]`), `pw.v1.prefs` (`{season, model, region}`
  last used, fills missing URL params), `pw.v1.offline` (`{season: data_version}` downloaded).
  Every read/write in try/catch (private mode, quota); failures fall back to memory.
- Missing URL params are filled from prefs then manifest defaults and written back with
  `replace` so the address bar always shows the full state.

### 5.4 Data loading

`fetch(fileUrl(ref))` -> `arrayBuffer` -> if bytes start `1f 8b`, decompress with
`new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).json()`,
falling back to `fflate.gunzipSync` when `DecompressionStream` is missing; else parse as JSON.
Memoize per URL for the page lifetime. Show a skeleton-free loading line ("Loading 2025 teams")
in a polite live region; errors render an inline message with a Retry button, never a blank page.

### 5.5 Components (mapped to design-system classes)

| Component (file) | Classes | Notes |
|---|---|---|
| App shell, header, nav, footer (`components/shell.js`) | `pw-shell-top`, `pw-wordmark`, `pw-nav` (`aria-current="page"`), `pw-small`, `pw-eyebrow` | Logo mark (light/dark SVG) + wordmark per BRIEF lockup; "data updated {time}" from manifest on every page; stale notice; footer credits exactly as BRIEF |
| Season select, Find box | `pw-select`, `pw-input` | Find: team number/name, event name/key, current season |
| Model and theme toggles (`components/seg.js`) | `pw-seg`, `button[aria-pressed]` | group `aria-label` |
| Button | `pw-btn` | |
| Lede | serif 18/28 (`.pw-lede`, new) | the decision sentence, first thing in every view |
| Stat tile (`components/stat.js`) | `pw-stat`, `__value`, `__line`, `__context`, `pw-delta--up/--down` | value + unit + comparison ("6th of 139 in FMA · FMA median 26.0") |
| Data table (`components/table.js`) | `pw-table`, `td.n`, `td.team`, `tr.you`, `pw-bar` | sortable `<button>` headers with `aria-sort`; numeric right-aligned mono; first column sticky on phones; `<caption>` or `aria-labelledby` |
| Team chip (`components/team.js`) | `pw-team`, `--you`, `--red-soft`, `--blue-soft` | links to `#/team/{n}`; watchlist teams get `--you` |
| Watchlist star | `pw-btn` icon-less text toggle "Watch"/"Watching" with `aria-pressed` | no decorative icon |
| Figure (`components/figure.js`) | `pw-figure`, `__title`, `__sub`, `pw-caption` | title states the finding; caption defines encodings, n, source, update time |
| Line chart (`charts/line.js`) | `pw-grid`, `pw-axis`, `pw-tick`, `pw-axis-title`, `pw-line--you/--context/--ref`, `pw-label--you/--context`, `pw-note` | `lineChart({series:[{id,label,role,points:[{x,y}]}], x:{title}, y:{title}, refLines, annotations, ariaLabel})` -> SVG string, viewBox 780x340 as in the template, 3-6 "nice" ticks, direct end labels with vertical collision nudge, no legend |
| Sparkline (`charts/spark.js`) | new `pw-spark` (`--context` bars, hairline zero) | residual bars, last 12 matches |
| Small multiples (`charts/multiples.js`) | as line chart, shared axes | > 3 series |
| Bars (`charts/bars.js`) | `pw-bar` | start at zero |
| Match card (`components/match.js`) | `pw-match*`, `pw-wp__labels`, `pw-wp__track` | red top, blue bottom; prediction line names the model ("EPA prediction 212–146 · called correctly") |
| Win-prob chip | `div-red-3..div-blue-3` tokens via new `pw-wp-chip--r3..b3` | always prints the value |
| As-of control [WP5] | `pw-select`; event scope adds new `pw-range` (styled range input) | |
| Change flag | text "Rebuilt? ▲" / "▼" in `pw-delta--up/--down` | never red/blue |
| Explain-this-number (`components/explain.js`) | `pw-figure` popover/panel, `pw-table` | last 10 updates: match, predicted (pre), actual (attr), change, step |

New classes go in `site/css/pitwall.css` (WP4a) using only token variables; view-only classes go
in the view's CSS file.

### 5.6 Views (lede first, then evidence)

- **Teams** [WP4a]: lede ("341 led FMA at 90.6 points per match. 1218 finished 6th of 139."),
  filters (region, as of, column set, text filter, CSV), table: rank, star, team, name, EPA with
  bar, auto, teleop, endgame, norm EPA, record; components set = VK comps with season labels;
  RP set = rp EPAs and RP rates; robot set = per-robot rates from `season.robot`; consistency
  (`cons_sd` + sparkline), change flag. As-of uses `asof.json` (ranks recomputed client-side).
- **Team** [WP4a]: template `template-team-page`: eyebrow, h1, lede, 4 stat tiles, rating-over-time
  vs the region's top two (traj), event table (start/end/change/rank), recent matches (event file),
  season over season (history), explain-this-number on each tile.
- **Events** [WP4b]: list by week, filters region/week/type, columns top-8, top-24, mean (model),
  teams, status, model accuracy once complete.
- **Event** [WP4b]: tabs teams (pre-event and current rating, change flags), matches (cards or
  compact list, both models' win prob), rankings, alliances, accuracy (event metrics both models);
  as-of slider replays the event using derived pre vectors.
- **Match** [WP4b]: both models side by side: per-slot predicted (`red_pred`) vs actual, win prob,
  RP odds; per-robot facts and robot-attributed points marked vs alliance residual; each team's
  pre/post and change.
- **Compare** [WP5]: 2-4 teams: stat rows, trajectory small multiples, component bars, robot rates.
- **Hypothetical** [WP5]: pick 3 v 3, `predict.js` with season constants; predicted score per
  alliance and slot, win prob (diverging scale, printed), RP odds; ratings `end` or `event:{key}`.
- **Breakdown** [WP5]: one team's VK table with season names, value, rank in region, region
  median, world p90/p75/p25 (year percentiles).
- **Models** [WP5]: scoreboard (models.json) by season with deltas vs EPA, slices from
  `season.backtest`, calibration chart, settings and "How this rating works" per model, tuning
  record, the week-1 look-ahead note.
- **Offline** [WP6]: per season size, download/remove, storage used.

Voice: BRIEF rules. Uncertain numbers say so: any rating with `count < 6` or `cons_n < 6` gets
"n = 4 matches, treat as a guess". Predictions are labelled as predictions and name the model.

### 5.7 Theme (light and dark from tokens.json)

`site/tools/build_tokens.py docs/design/tokens.json site/css/tokens.css` (WP4a) generates:

```css
:root { color-scheme: light; --paper: #f8f7f4; ... /* every color.light */
        --font-display: ...; --space-1: 4px; ... --radius-*; --stroke-*; --shadow-float: <light>;
        --type-stat: 600 40px/44px var(--font-display); ... /* every type token */ }
:root[data-theme="dark"] { color-scheme: dark; --paper: #0d0f12; ... /* every color.dark */ --shadow-float: <dark>; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { /* same dark block */ } }
```

Every color token in tokens.json appears in both blocks (test). `pitwall.css` = the component
rules of `docs/design/pitwall-light.css` with its `:root` block removed and no literal colours.
`index.html` has an inline 3-line script in `<head>` that sets `data-theme` from
`pw.v1.theme` before first paint. Theme toggle writes the pref and the attribute; `<meta
name="theme-color">` updates to `--paper`. Logo: light/dark mark SVGs swapped by the same selectors.

### 5.8 Accessibility (WCAG 2.1 AA)

- Text contrast >= 4.5:1 (>= 3:1 for >= 18.66px bold / 24px), non-text >= 3:1, both themes;
  WP6 adds `site/tests/contrast.test.mjs` computing every foreground/background token pair used.
- Keyboard: everything reachable; visible focus = 2px `--focus-ring` outline, 2px offset; skip link
  to `<main>`; no keyboard traps in popovers (Esc closes, focus returns).
- Route change: update `document.title` ("Team 1218 · 2025 · PitWall"), move focus to the h1.
- Tables: `<th scope>`, sortable headers are buttons with `aria-sort`; charts `role="img"` with an
  `aria-label` stating the finding, plus a "Show data" toggle rendering the same numbers as a table
  (tooltips never hold the only copy).
- Colour is never the only carrier: alliances also labelled Red/Blue; deltas carry ▲▼ or +/−.
- `prefers-reduced-motion`: no transitions. `lang="en"`. Live region for loading/errors.

### 5.9 Phone layout

Breakpoints: phone <= 640px, tablet <= 1024px.
- Header: wordmark row; controls (season, model, theme, find) collapse into a "Controls"
  disclosure button; nav is one horizontally scrollable row.
- Touch targets >= 44x44 px under `(pointer: coarse)` (buttons, seg buttons, nav links, chips,
  table header buttons).
- Page padding `--space-4`; stat tiles 2 per row; figures full width; charts scale by viewBox
  with tick labels >= 11px.
- Tables scroll inside their box (`overflow-x: auto`), first column sticky; never page-level
  horizontal scroll at 360px width.
- Match cards one column. Popovers become bottom sheets.

---

## 6. PWA (WP6)

- `site/manifest.webmanifest`: `name` "PitWall", `short_name` "PitWall", `start_url` "./#/teams",
  `scope` "./", `display` "standalone", `background_color` and `theme_color` "#f8f7f4",
  `icons`: `brand/icon-192.png`, `brand/icon-512.png`, `brand/icon-maskable-512.png`
  (`purpose: "maskable"`, mark inside the 80% safe zone on paper), `brand/pitwall-app-icon.svg`.
  Plus `apple-touch-icon` 180 and the existing favicon SVG. PNGs are rendered once from
  `site/brand/pitwall-app-icon.svg` (e.g. with cairosvg) and committed.
- `site/sw.js` (scope `./`), registered by `app.js` after load:
  - **App shell, cache-first.** `sw-precache.json` (`{version, files: [{url, hash}]}`, written by
    `site/tools/build_precache.py` at stage time) lists index.html, CSS, JS modules, fonts,
    icons, vendor. Cache name `pw-shell-{version}`; install precaches all; activate deletes other
    `pw-shell-*`. Navigations are answered with cached `index.html`.
  - **Manifest, network-first** with a 4 s timeout, fallback to cache (`pw-data`).
  - **Shards, cache-first by exact URL** (`?v=hash` makes them immutable); on miss fetch and store;
    after storing a new version, delete other cached versions of the same path. Offline and not
    cached at that version: serve any cached version of the same path (`ignoreSearch`) with
    header `X-PitWall-Stale: 1`; the app shows "Showing data from {date}; you are offline".
    This is stale-while-revalidate keyed by manifest hashes: a new manifest points at new hashes,
    which are fetched when online and used offline only once cached.
  - Update flow: a waiting worker shows "A new version of PitWall is ready. Reload" (`pw-small`
    + `pw-btn`); the button posts `SKIP_WAITING` then reloads.
- **Download season for offline** (`js/core/offline.js`, view `#/offline`): reads the manifest
  and `season.json`, fetches `season`, `asof`, `traj` and every event file with a `file_hash`
  (4 concurrent) through the SW, shows progress (files and MB of `events_bytes`), calls
  `navigator.storage.persist()`, records `pw.v1.offline[season] = data_version`. "Remove" deletes
  that season's cache entries. Viewing anything also caches it ("offline after viewing").

---

## 7. Work packages

Order: WP0 -> (WP1, WP2, WP3, WP3b in parallel) -> WP3c -> (WP4a, WP4b, WP5) -> WP6 -> WP7 (QA).
Ownership is exclusive while a WP is active. After a WP is accepted, WP6 (dark/a11y) and WP7 may
edit its files only for fixes in their own scope, and say so in their report.

Shared test data: `/home/claude/sbrebuild/out/{2023,2024,2025}/` (real outputs) and the raw
dumps `tba_{2022..2025}.json.gz`; CI fixtures from WP1.

### WP1 Action, fetcher, orchestration
- Owns: `.github/workflows/update.yml`, `.github/workflows/ci.yml`, `pipeline/config.json`,
  `pipeline/requirements.txt`, `pipeline/build.py`, `pipeline/run.py`, `pipeline/fetch/*`,
  `pipeline/tools/stage_site.py`, `pipeline/tests/test_fetch.py`, `test_assemble.py`,
  `test_build_plan.py`, `pipeline/tests/fixtures/`, `THIRD_PARTY.md`.
- Inputs: TBA API, `data` branch, functions from WP2/WP3b/WP3c by the signatures in this spec.
- Outputs: data branch layout (2.4), Pages deployment, `plan.json`.
- `config.json`: `{"seasons": [2022,2023,2024,2025,2026], "current_season": 2026,
  "archive_priors": "pipeline/priors/archive_team_years_2016_2023.csv", "default_region": "fma",
  "default_watchlist": [1218,1219], "default_model": "epa"}`.
- Acceptance:
  1. Against a local fake TBA server serving the split 2023 dump with ETags: first run fetches
     everything; second run sends only conditional requests, gets 304s, `plan.rebuild` is empty,
     finishes < 60 s, no deploy unless the live manifest differs.
  2. `assemble.py --split` then `season_dump` round-trips 2022-2025 so that `run.py` outputs are
     byte-identical to runs on the original dumps.
  3. The key string never appears in cache files, logs, or `_site/` (grep test with a fake key).
  4. Failure injection: an exception in season 2025's build -> 2025 served from `published/` with
     `stale: true`, others updated, deploy happens, job exits non-zero.
  5. Data branch has exactly one commit after two runs; unchanged bodies keep their blob ids.
  6. Recompute rules 2.5 unit-tested (input change, code change, norm propagation, force).

### WP2 Exporters, per-robot map, priors
- Owns: `pipeline/sb/jsonout.py`, `shards.py`, `validate_shards.py`, `perrobot.py`,
  `season_meta.py`, `export.py`, `priors.py`, `loader.py`, `shim.py`, `models.py`,
  `pipeline/tests/test_shards.py`, `test_perrobot.py`, `test_priors.py`.
- Inputs: `ModelRun`s (until WP3b lands, build the `fast` run as a copy of the EPA run), features
  (until WP3c lands, nulls), backtest summaries (until WP3b lands, computed from the run with
  `backtest`'s documented formulas or nulls), raw dump, `perrobot_fields.json`, schemas.
- Outputs: every file in 3.3-3.8. Entry points:
  `write_season(year, runs, features, backtests, robot, raw, out_dir, meta) -> SeasonFiles` and
  `write_global(out_dir, seasons_meta, models_info, history_rows, manifest_meta)`.
  `priors.build_priors` accepts the archive CSV path (file or directory).
- Acceptance:
  1. All shards for 2023-2025 validate (schemas + column lengths + VK/BK) and meet 3.9 budgets.
  2. Exact round trips: team_years/team_events/matches values equal the CSVs; derived pre vectors
     equal `team_matches.csv` `pre_*` for every row; `match_auto.csv` reproduced exactly.
  3. Two runs give byte-identical shards (different `PYTHONHASHSEED`, TZ).
  4. `perrobot.py` reproduces the evidence counts in `perrobot_fields.json` for 2022-2025 and
     reports 2026 match rates (or "field not found") once a 2026 dump exists.
  5. 2022 built with archive priors 2020/2019, offseason excluded; 2023 `epa_start` MAE between
     computed-2022 and archive-2022 chains reported in the WP report.

### WP3 Predictions and hypothetical
- Owns: `site/js/model/predict.js`, `site/tests/predict.test.mjs`,
  `pipeline/tests/make_predict_fixtures.py`, `pipeline/tests/fixtures/predict_*.json`,
  `pipeline/sb/model_info.py`, `pipeline/tests/test_model_info.py`.
- `predict.js` ports `EPA.predict_match` for 2022-2026: sum of pre vectors (18-slot via
  `SLOT_INDEX`), `post_process_breakdown` (rp `unit_sigmoid`; 2023 grid overflow; 2025 processor
  algae), `get_score_from_breakdown` (2023 special case; else no_foul), `win_prob = 1/(1+10^(k *
  (red - blue)/score_sd))` on unscaled scores, then scores x `(1 + foul_rate)`.
- Acceptance: on fixtures of 2,000 matches per season 2023-2025 (all playoff matches included),
  using exported rounded pre vectors: |win prob - exported| <= 0.002, |score - exported| <= 0.05,
  |rp prob - exported| <= 0.002, |breakdown - red_pred/blue_pred| <= 0.02; for both models once
  Fast EPA exists. `model_info.describe` output validates against `modelsFile`.

### WP3b Model runner, Fast EPA, backtest, tuning
- Owns: `pipeline/sb/model_runner.py`, `fast_epa.py`, `backtest.py`, `engine.py`,
  `pipeline/models/fast_epa.json`, `pipeline/tune/tune_fast_epa.py`,
  `pipeline/tests/test_runner.py`, `test_fast_epa.py`, `test_backtest.py`.
- Inputs: loader, vendored EPA, `perrobot.robot_facts` (WP2; stub with `None` facts until it lands).
- Outputs: `ModelRun`, backtest summaries, leak audit results, tuned config.
- Acceptance: 4.2 and 4.3 byte-identity tests; leak audit passes (EPA exact, Fast <= 1e-9);
  tune script refuses 2026; tuned config, baseline and top-20 written; per-season runtime <= 60 s;
  backtest summary validates against `backtestSummary`; WP report states plainly whether Fast EPA
  beat EPA on 2022-2025 and on 2026 (when 2026 exists), by how much, and in which slices.

### WP3c Consistency, change detector, opponent effect
- Owns: `pipeline/sb/features.py`, `pipeline/tests/test_features.py`.
- Inputs: `ModelRun` (attrib, alliance_pred, updated, objs). Outputs: `SeasonFeatures` (4.6).
- Acceptance: synthetic-season unit tests for each formula (known residuals -> known sd, z, flag);
  null rules (n < 4, k < 2, n < 12); on 2025 EPA: share of team-events flagged reported (expect
  roughly 5% at |z| > 2 if residuals were pure noise; report the actual rate).

### WP4a Site shell, core, Teams, Team
- Owns: `site/index.html`, `site/js/app.js`, `site/js/core/{data,router,state,prefs,fmt,html,regions}.js`,
  `site/js/components/{shell,seg,stat,table,team,figure,match,explain}.js`,
  `site/js/charts/{line,spark,multiples,bars}.js`, `site/js/views/{teams,team}.js`,
  `site/css/tokens.css`, `site/css/pitwall.css`, `site/css/views/{teams,team}.css`,
  `site/tools/build_tokens.py`, `site/assets/fonts/*`, `site/vendor/fflate/*`,
  `site/tests/{core,charts,tokens}.test.mjs`.
- Inputs: shards (use WP2 output or a fixture written by hand to the schemas).
- Acceptance: Teams and Team match the templates' layout at 1280px and work at 360px; URL
  round-trip (copying the URL reproduces the view); every token in both themes (test); no network
  request outside the site origin (checked in a headless browser); Find works; CSV of the
  visible Teams table equals the table.

### WP4b Events, Event, Match
- Owns: `site/js/views/{events,event,match}.js`, `site/css/views/{events,event,match}.css`,
  `site/tests/views_event.test.mjs`.
- Acceptance: Event as-of slider at `Q1`/`start` shows pre-event ratings and at `end` shows
  end-of-event ratings equal to `team_events`; match page robot points sum check (`scored` +
  residual = `no_foul`); both models shown; works at 360px.

### WP5 Compare, Hypothetical, Breakdown, Models, watchlist, as-of, CSV
- Owns: `site/js/views/{compare,hypothetical,breakdown,models}.js`,
  `site/css/views/{compare,hypothetical,breakdown,models}.css`, `site/js/core/watchlist.js`,
  `site/js/core/csv.js`, `site/js/components/asof.js`, `site/tests/wp5.test.mjs`.
- Deliver `watchlist.js`, `csv.js`, `asof.js` first (day one); WP4a/WP4b import them.
- Acceptance: Hypothetical with a real match's six teams and `ratings=event:{key}` reproduces that
  match's exported win prob within 0.002; watchlist persists and defaults to 1218, 1219; CSV
  RFC 4180 (quotes, commas, newlines in names); Models tab shows both models' 2022-2026 rows and
  marks 2026 as out-of-sample.

### WP6 PWA, offline download, dark pass, accessibility pass
- Owns: `site/manifest.webmanifest`, `site/sw.js`, `site/tools/build_precache.py`,
  `site/js/core/offline.js`, `site/js/views/offline.js`, `site/css/views/offline.css`,
  `site/brand/*.png`, `site/tests/{contrast,sw}.test.mjs`.
- Acceptance: installable (Lighthouse PWA checks pass); after visiting Teams and one Event online,
  both load in airplane mode; a downloaded season's every view loads offline; a new deploy
  replaces cached shards on next online visit and old versions are evicted; contrast test passes
  in both themes; axe-core (dev-only, Playwright) reports no serious/critical issues on all views
  in both themes at 1280px and 360px.

### WP7 Independent QA (fresh context; listed for completeness)
Numbers vs statbotics.io (30 teams, 10 events), backtest leakage audit review, offline test,
phone/Chromebook check, Lighthouse.

---

## 8. Open questions and risks

| # | Item | Proposal |
|---|---|---|
| Q1 | **TBA API terms** for republishing derived ratings and per-match data on a public site, and attribution wording. Statbotics does it, but confirm on thebluealliance.com/apidocs before the repo goes public. | Footer already credits TBA; publish only derived data plus match results, never raw dumps. |
| Q2 | Chaining 2023 from our computed 2022 (BRIEF) instead of the archive's 2022 changes the validated 2023 replay slightly. | Accept; WP2 reports the MAE. |
| Q3 | Week-1 Year-constant look-ahead (4.5.1). | Keep for Statbotics parity; WP3b measures the effect on 2023 by recomputing constants from other events' week-1 matches for 3 events and reports it on the Models tab. |
| Q4 | Districts and event team lists: fetched and used for the current season only, so 2022-2025 stay identical to the validated runs (derived districts, 99.8% agreement). Using TBA district lists for past seasons would be more correct but changes validated numbers. | Current season only in v1. |
| Q5 | 2026 per-robot field names, values and points are inferred (perrobot_fields.json). | Build-time check gates them; WP2 fixes the JSON once a 2026 dump exists. |
| Q6 | Chart library: plain SVG instead of uPlot (5.1). | Confirm. |
| R1 | Pages limits: 1 GB site, 100 GB/month soft bandwidth, 10 builds/hour soft limit. We use ~65 MB and at most 2 deploys/hour. | Fine. |
| R2 | Actions minutes: unlimited on standard runners for public repos. A private repo would pay for ~2 runs/hour x 3 days x 13 weeks + daily runs (~1,500 runs/season, mostly < 2 min). | Keep the repo public. |
| R3 | GitHub disables scheduled workflows in public repos after 60 days without repository activity; force-pushes to `data` may not count. | Owner gets an email; re-enable in the Actions tab. Optional keepalive: `full` mode commits `state.json` to `main` monthly (not in v1). |
| R4 | Cron delays: scheduled runs can lag 5-30 minutes at peak. | Acceptable; manual dispatch for urgent updates. |
| R5 | Live-season Year constants move as week 1 completes, so early-season ratings shift between runs. | Expected upstream behaviour; mention on Models tab. |
| R6 | Event 4-team alliances: per-robot fields exist for three robots; which three is not stated. | Slot 4 gets null; Fast EPA falls back to the EPA rule if the counted three differ from the first three team keys. |
| R7 | Safari < 16.4 lacks `DecompressionStream`. | fflate fallback. |
| R8 | Data branch force-push loses history of raw responses. | Intended: it is a cache; published shards are reproducible from code + current TBA. |

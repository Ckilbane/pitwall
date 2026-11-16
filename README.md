# PitWall

FRC ratings and strategy for FRC 1218 and friends: Statbotics-style EPA ratings, recomputed from
The Blue Alliance match data, plus a faster-adapting "Fast EPA", published as a static,
installable site on GitHub Pages.

- `pipeline/`: Python. Fetches TBA data (ETag cache), runs EPA and Fast EPA, the leak-free
  backtest and team features, and writes gzipped JSON shards (`docs/schema/`).
- `site/`: static ES-module site (no build step, no framework), light and dark themes,
  works offline (service worker; "download season for offline").
- `.github/workflows/update.yml`: scheduled and manual data updates, deploys to Pages.
- `docs/`: brief, spec, design tokens.

## First-run setup
1. Settings > Pages > Source: **GitHub Actions**.
2. Settings > Secrets and variables > Actions: repository secret **`TBA_KEY`** (a TBA read key).
3. Merge to `main`. Scheduled runs and Pages deploys only work from the default branch.
4. Don't protect the `data` branch; the workflow creates and force-pushes it.
5. Actions > "Update data" > Run workflow. The first run always builds every season
   (about 25-35 minutes).

## Local development
- Tests: `python3 -m pytest -q pipeline/tests` and `node --test "site/tests/**/*.test.mjs"`.
- Real data from raw dumps: `python3 -I pipeline/tools/local_build.py --raw-dir RAW --out OUT --years 2022 2023 2024 2025`.

## Credits
- Match data: [The Blue Alliance](https://www.thebluealliance.com)
- EPA model: [Statbotics](https://github.com/avgupta456/statbotics) (MIT license); see THIRD_PARTY.md
- Not affiliated with FIRST.

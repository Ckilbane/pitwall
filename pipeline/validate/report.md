# Validation report (2026-11-15)

Upstream: avgupta456/statbotics @ a2cea55. Seasons computed: 2023 (replay), 2024, 2025. 2026 pending raw data.

## 2023 replay vs March 2024 archive (3284 teams; MAE / p95 / max)
- epa 0.0105 / 0.04 / 1.33; auto_epa 0.0030 / 0.01 / 0.37 (82% exact); teleop 0.0050 / 0.02 / 0.62; endgame 0.0028 / 0.01 / 0.41
- norm_epa 0.19 / 1 / 9; epa_start 0.017 / 0 / 5.01
- World max auto 21.49 (team 6328), same as archive.
- Error is largest at match 1 (0.0060) and fades (0.0009 after 40 matches): prior difference, not update rule.

## Named causes of the residual
1. Archive priors included offseason-only team_years (71 teams). Including them makes epa_start exact; default stays "exclude" because the archive's own 2024 epa_start excludes them.
2. Einstein 4-team alliances: upstream keeps the first 3; TBA team order changed since March 2024 (8 teams).
3. Placeholder teams 9980-9985 are in our norm fit but not the archive export.

## Chain
- 2024: 3477 teams, max auto 19.83 (1771), top-10 mean 18.16.
- 2025: 3690 teams, max auto 32.55 (1690), top-10 mean 27.88. 2056 pre-champs 31.42; 1690 after division finals 31.37.
- 2024 epa_start vs archive snapshot: 99.7% within rounding after correcting for the archive's partial-season year means.

## Identities
- auto+teleop+endgame = epa within 0.02 (2024, 2025). 2023 differs (mean 0.21) due to upstream grid-overflow correction.
- Auto comps: 2024 leave+notes = auto exactly. 2025 has auto_coral_points only (no leave slot). 2023 auto_pieces is a count. 2026 auto_fuel + auto_tower = auto.

## Determinism
27 of 27 output files byte-identical across runs, including different hash seed and timezone.

## 2026
compare_2026.py ready. Reference exports cover 141 teams, rounded to 0.1, snapshot timing unclear (pre or during champs).

## Win probability vs the March 2024 archive
Ratings and predicted scores match the archive; win probabilities do not exactly (MAE about 0.05). The archive used an older upstream win-probability formula. PitWall follows the vendored upstream code (commit a2cea55), k = -0.625 for EPA. Predicted winners and event accuracy are unaffected.

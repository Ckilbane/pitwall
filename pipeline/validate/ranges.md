# EPA ranges, tiers and head-to-head odds

How PitWall turns one EPA number into a range, and how well that range holds up. Scripts:
`pipeline/tune/ranges/collect.py` (dumps every team's qual residuals during a normal build) and
`pipeline/tune/ranges/fit.py` (fits and scores). Data: TBA dumps on the data branch, Nov 2026.

## What is exported (season.json teams, per model)
- `match_sd`: SD of a team's qual residuals (attributed points minus pre-match EPA), shrunk toward the
  season's pooled SD with 8 pseudo-matches.
- `epa_se`: `c * match_sd / sqrt(min(n + 5, 6))`, with c = 1.80 (EPA) or 1.71 (Fast EPA).
- asof.json also carries `epa_se` per week, using only matches played by then.

The site shows **EPA ± 1.96 · epa_se** as the likely range.

## Calibration target
For every team and qual match i with 6 later quals: is the mean attributed points over the next 6
quals inside `pre_i ± 1.96 · sqrt(c² se_i² + match_sd_i² / 6)`? That asks whether the range holds
the team's real level over the next few matches, drift included. Fit on 2022-2024; 2025 and 2026
held out. n0 and nmax were chosen so coverage is flat across early and late season.

| Season | EPA coverage | Fast EPA coverage | EPA median ± (points) |
|---|---|---|---|
| 2022 (fit) | 94.1% | 95.1% | 6.7 |
| 2023 (fit) | 95.0% | 95.3% | 8.7 |
| 2024 (fit) | 95.8% | 94.6% | 6.1 |
| 2025 (held out) | 94.5% | 94.2% | 10.7 |
| 2026 (held out) | 94.4% | 94.5% | 29.5 |

Coverage by matches played (0-2, 3-7, 8-14, 15+) stays between 92% and 97% in every season.
The best fit caps the effective sample at 6 matches: EPA keeps adapting, so a team's range does not
keep shrinking all season. Ranges are wide; that is the honest answer.

## Tiers
Teams sorted by EPA; a new tier starts when its first team is at least 90% likely to be below the
tier leader's true level, `Phi((leader - team) / sqrt(se_l² + se_t²)) >= 0.9`.

## Head to head
"A adds more points than B in one match" = `Phi((epa_a - epa_b) / sqrt(match_sd_a² + match_sd_b²))`.
Checked on every pair of opposing teams in 2024-2026 quals (about 130,000 pairs a season): actual
rates sit within about 3 points of the prediction in every 10% bin for EPA (2025 Brier 0.115).
Fast EPA is slightly cautious in 2026 (a predicted 65% came true 77% of the time). Adding `epa_se`
on top of `match_sd` double-counts and makes the odds too timid (2025 Brier 0.118), so it isn't used.

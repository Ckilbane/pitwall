# PitWall build brief

PitWall is a Statbotics-style FRC ratings site for FRC 1218 (SCH Robotics, FIRST Mid-Atlantic) and its students. Public GitHub Pages site in `Ckilbane/pitwall`.

## Decisions
- Seasons 2022-2026, FRC only. Default view: 2026, FMA district, EPA model, teams 1218 and 1219 starred.
- **Compute in GitHub Actions, display in the browser.** The Python pipeline in `pipeline/` (vendored Statbotics backend, MIT, commit a2cea55, validated: 2023 replay auto EPA MAE 0.003 vs the Statbotics archive) runs in an Action and publishes small gzipped JSON shards. The site is static HTML/JS; it never runs Python.
- **One shared TBA read key** in repo secret `TBA_KEY`, used only by the Action. Never in the site, browser, logs or commits.
- Triggers: `workflow_dispatch` plus cron (every 30 min Thu-Sun during build season Feb-Apr; daily otherwise). A run with no changed TBA events exits fast.
- Raw TBA cache on a `data` branch (or Actions cache), not in the deployed site. Fetch only events whose ETag changed.
- Models in v1: **EPA** (exact Statbotics reproduction, default) and **Fast EPA** (faster dampening, optional per-event passback, optional piece-count seed, per-robot attribution where TBA has per-robot fields; keeps auto/teleop/endgame split).
- **Honest backtest:** every model scored only on true pre-match ratings (state built only from earlier matches; no replay may use the match being predicted). Accuracy, score MAE, Brier, by week, event type, first-3-matches vs later. Fast EPA tuned on 2022-2025, checked on 2026, never tuned on 2026.
- Installable PWA, offline after viewing; "download season for offline".
- Per-device state in localStorage (watchlist, theme, later pick lists), wrapped in try/catch.
- No scouting import. CSV export from tables.
- Credits in footer: data from The Blue Alliance; EPA model from Statbotics (MIT). "Not affiliated with FIRST." No FIRST logos or game art.

## v1 tabs
Teams, Team, Events, Event, Match, Compare, Hypothetical, Breakdown, Models. Plus: "as of" week/match slider, explain-this-number (a rating's last 10 updates: predicted, actual, change), watchlist, season-over-season view, "data updated" timestamp on every page, light/dark toggle.
Model features: rebuild/change detector (first 2-3 matches at an event differ from prior by > ~2 SD of match noise), consistency (SD of match residuals, sparkline), opponent effect (experimental), "How this rating works" panel with each model's constants.
Later: v1.5 Pick List, OPR/Cridge (in-browser), Vibe reimplementation, auto decomposition. v2 event simulation, IDM tab.

## Design system (docs/design/)
- `tokens.json`: every colour in light and dark, fonts, type, space, radius, stroke. `pitwall-light.css`: light-theme CSS variables + `pw-` components. Templates: `template-team-page.dc.html`, `template-teams-table.dc.html` (design-canvas HTML; read for layout and markup, ignore the `x-dc`/`helmet`/`support.js` wrapper).
- Principles: answer the question first (each view leads with the sentence that drives a decision; chart titles state the finding). Data-ink over chrome: no 3D, gradients, card shadows, decorative icons; hairline `rule` borders. One thing in colour: your team is `highlight` (amber), everyone else `context` grey; red/blue mean alliances only, never status or decoration; red on left/top, blue right/bottom. Label data directly; legends only when unavoidable; >3 series -> small multiples. Dense, not cramped.
- Voice: plain analyst briefing; FRC vocabulary (quals, playoffs, auto/teleop/endgame, EPA); match keys `Q32`, `SF2-1`, `F1`; bare team numbers. Sentence case. No emoji, exclamation marks, hype. Numbers get units and comparisons ("42.7 EPA · 3rd of 38"). Uncertain numbers say so ("n = 4 matches, treat as a guess").
- Type: Barlow Condensed (numbers, titles), Barlow (UI), JetBrains Mono (anything compared down a column; right-align numeric columns), Source Serif 4 italic (annotations, ledes). Google Fonts.
- Charts are figures: finding-as-title, subtitle with scope, axis titles "Quantity (unit)" on every quantitative axis, 3-6 round ticks, direct labels, caption defining encodings with n, source, last update. Show spread with centres; always print n; bars start at zero; small multiples share axes; define EPA/OPR on first use; mark predictions as predictions and name the model. No pie/donut, dual axes, rainbow, 3D. Tooltips never hold the only copy of anything.
- Change uses `delta-up`/`delta-down` (green/orange) with ▲▼ or +/−. Win probability uses the diverging `div-*` scale with printed values.
- Square data marks; `radius-md` controls/cards; focus ring 2px `focus-ring` with 2px offset. Touch targets ≥ 44px on mobile. Works at phone width (menus stack, tables scroll in a box).

## Logo
Option 11 "stripe climb": wide amber stripe below, narrow ink stripe above, flat then climbing at 45°. Files in `site/brand/` (mark light/dark, favicon with prefers-color-scheme, app-icon tile). Lockup: mark height = wordmark font size, gap 1/4 mark; wordmark "PitWall" Barlow Condensed 600 ink. Clear space 1/4 mark. Mark ≥ 24px; favicon version below. Never alliance colours.

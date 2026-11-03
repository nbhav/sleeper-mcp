# Provider Contract Research

Issue: #42 (epic #35, integration target #32)
Scope: research and contract only. No provider data is connected to scoring,
recommendations, CLI, or MCP output by this work.
Research date: 2026-11-03. Every licensing, cost, rate-limit, coverage, and
history claim below links to its source. Anything we could not confirm from a
primary source is marked **unverified**.

Related docs: [Player Context Source Audit](player-context-source-audit.md)
(what Sleeper already gives us), [Context Provider Calibration](context-provider-calibration.md)
(the existing registry and backtest harness).

## Summary And Recommendation

1. **Keep nflverse, loaded through `nflreadpy`, as the free provider of record**
   for schedule and game identity, raw box-score stats, snap counts, play-by-play
   (EPA, air yards, red zone, pace inputs), NGS tracking aggregates, schedule-level
   lines, and roof/temp/wind. It is MIT (package) and CC-BY 4.0 (most data), has
   history back to 1999 for play-by-play, and refreshes nightly in season
   ([nflreadpy][nflreadpy], [data schedule][nflverse-schedule]). The parity spike
   in #42 already matched 4,481 of 4,488 Sleeper observations.
2. **Routes and route participation are the one input nflverse cannot supply.**
   Its participation `route` field covers only the primary receiver's route, and
   from 2023 on participation data ships only after the postseason
   ([participation dictionary][nflreadr-dict-participation], [load_participation][nflreadr-participation]).
   TPRR and YPRR stay `not_evaluable_missing_routes` until a licensed source is chosen.
3. **Run a paid trial before choosing a commercial source.** SportsDataIO is the
   cheapest supported API ($99–149/mo, [getting started][sdio-start]) and has snap
   denominators back to 2012. But its current data dictionary has no route fields.
   Route participation appears only in a 2019 marketing PDF, so SportsDataIO routes
   are **unverified**. FTN Data and PFF publish all-routes charting. FTN's price is
   **unverified**. PFF Pro API access is $199.99/yr with no stated license terms
   ([PFF premium][pff-premium]).
4. **Use The Odds API for timestamped spreads, totals, and implied totals** once
   live lines matter. nflverse schedule lines have no timestamp and no
   opening/closing label.
5. **The follow-up issue** (last section) persists provider observations under their
   own source, validates them with the contract added here, and scores the paid
   candidates on one shared four-week trial payload.

## Providers Compared

| Provider | Type | Access | Cost | Auth / quota | License for our use |
|---|---|---|---|---|---|
| **nflverse** (nflreadpy loaders over `nflverse-data` GitHub releases) | Open data | Parquet/CSV release files. `nflreadpy==0.1.5` is already a dependency (`python/pyproject.toml`) | Free | No key. Configurable cache (`NFLREADPY_CACHE`, `_DIR`, `_DURATION`) ([config][nflreadpy-config]). GitHub release download limits **unverified** | Package MIT. Most data CC-BY 4.0. FTN-sourced data CC-BY-SA 4.0 ([nflreadpy][nflreadpy], [LICENSE][nflverse-license], [load_ftn_charting][nflreadr-ftn]) |
| **SportsDataIO** (formerly FantasyData) | Commercial REST | `https://api.sportsdata.io/v3/nfl/{scores\|stats\|odds}/json/...` ([API docs][sdio-api]) | Fantasy $99/mo or $599/yr. Odds $99/mo or $599/yr. Fantasy + Odds $149/mo or $899/yr. Free tier with last season's data ([getting started][sdio-start]) | `key` query parameter or `Ocp-Apim-Subscription-Key` header ([API docs][sdio-api]). 100–1,000 calls/day on Discovery Lab tiers. The trial returns scrambled data ([getting started][sdio-start]). The API docs page says the free trial covers only the UEFA Champions League, which **conflicts** with the getting-started page | Discovery Lab data is "not licensed for commercial redistribution". Trial data cannot be used for analysis or display. Storage, caching, and MCP-exposure rights for paid tiers are **unverified** |
| **Sportradar NFL** | Enterprise REST | Schedules, game statistics, play-by-play, weekly depth charts, injuries, and seasonal stats ([NFL overview][sr-overview]) | Not published (**unverified**, sales-led) | Trial: 30 days, 1,000 requests per rolling 30 days, 1 QPS, with production-quality data ([account docs][sr-account]) | **Unverified** |
| **FTN Data** | Commercial charting API | REST JSON "Advanced Charted NFL Data and Player Participation" (search-result snippet; [ftnfantasy.com/data][ftn-data] returned 403) | A $5,000 entry package with charting back to 2019 appears in a search snippet only (**unverified**). DVOA is an add-on (**unverified**) | **Unverified** | **Unverified**. The subset nflverse republishes is CC-BY-SA 4.0 ([load_ftn_charting][nflreadr-ftn]) |
| **PFF** | Commercial grades/charting | Pro tier includes "API and programmatic data access" via API or CLI ([PFF premium][pff-premium]) | $199.99/yr ([PFF premium][pff-premium]) | **Unverified** (see developer.pff.com) | Not stated on the page (**unverified**) |
| **NFL Savant** | Public site / CSV | The site exposes no documented API. The about page returned no content ([about][savant-about]) | **Unverified** | **Unverified** | **Unverified**. Research-only, per the owner's comment on #42 |
| **The Odds API** (lines only) | Commercial REST | `https://api.the-odds-api.com`, sport `americanfootball_nfl`, markets `h2h`/`spreads`/`totals` ([v4 guide][odds-guide]) | Free plan 500 credits/mo. $30/mo for 20K, $59 for 100K, $119 for 5M, $249 for 15M ([pricing][odds-home]) | `apiKey` query parameter. Cost = markets × regions. Historical calls cost 10 per region per market. `x-requests-remaining` header ([v4 guide][odds-guide]) | Storage and redistribution terms are not on the pricing page (**unverified**) |
| **NWS api.weather.gov** (weather only) | Government REST | `/points` then `forecastHourly`, ISO-8601 times ([NWS API][nws]) | Free | Requires a `User-Agent`. Rate limit "not public" ([NWS API][nws]) | "Open data, free to use for any purpose" ([NWS API][nws]) |
| **Open-Meteo** (weather fallback) | REST | No key mentioned ([terms][open-meteo]) | Free for non-commercial use only | Under 10,000 calls/day, 5,000/hr, 600/min, 300,000/month. No uptime guarantee ([terms][open-meteo]) | CC-BY 4.0 ([terms][open-meteo]) |

Viable as a production dependency today: **nflverse** (free core) and **The Odds
API** (lines). SportsDataIO, Sportradar, FTN, and PFF stay candidates until a
trial payload confirms routes, IDs, and license terms. NFL Savant is not viable
because it has no documented API or terms.

## Capability Matrix

Notation:
- **Y**: documented field.
- **D**: derivable locally from documented fields. A derived value needs a
  `calculation_version`.
- **P**: proxy only. It must not be relabeled as the real metric.
- **N**: not available.
- **U**: unverified.

Units: `ratio` is 0–1 and `percent` is 0–100.

### nflverse (via nflreadpy)

| Input | Avail | Loader. Field(s). Numerator / denominator. Definition | History | Freshness / corrections |
|---|---|---|---|---|
| Routes, route participation | **N / P** | `load_participation`: `route` is "the route the primary receiver on a play took". `offense_players` is a list of gsis_ids on the field ([dictionary][nflreadr-dict-participation]). On-field presence on dropbacks is only a pass-snap proxy, not routes run | 2016+ ([load_participation][nflreadr-participation]) | 2023+ comes from FTN and is released only after the postseason. It does not update in season ([data schedule][nflverse-schedule]) |
| Snaps + team snap denominators | **Y** | `load_snap_counts`: `offense_snaps`, `offense_pct`, `defense_snaps`, `defense_pct`, `st_snaps`, `st_pct`. Keyed by `game_id`, `pfr_player_id`, `team`, `opponent` ([dictionary][nflreadr-dict-snaps]). The team denominator is implied by snaps / pct. Whether `*_pct` is 0–1 or 0–100 is **unverified**, so check it against data before use | 2012+ ([load_snap_counts][nflreadr-snaps]) | Source is PFR. Updates at 0/6/12/18 UTC in season ([data schedule][nflverse-schedule]) |
| Target share | **Y** | `load_player_stats`: `targets`, `target_share` ("share of team receiving targets in this game") ([player stats dictionary][nflreadr-dict-ps-json]). Team targets come from `load_team_stats` or a pbp sum | 1999+ (pbp-derived) | Nightly after game days. A Wednesday-to-Thursday re-run picks up stat corrections ([data schedule][nflverse-schedule]) |
| Carry share | **Y / D** | `carries` counts "official rush attempts (incl. scrambles and kneel downs)" ([dictionary][nflreadr-dict-ps-json]). A designed-rush share must be derived from pbp by excluding `qb_scramble` and kneels ([pbp dictionary][nflreadr-dict-pbp]) | 1999+ | As above |
| Red-zone opportunity | **D** | pbp `yardline_100 <= 20` with `receiver_player_id` or `rusher_player_id`. Denominator: team plays inside the 20 ([pbp dictionary][nflreadr-dict-pbp]) | 1999+ | As above |
| Air-yard share | **Y** | `receiving_air_yards` (includes incompletions), `air_yards_share`, `wopr` = 1.5 × target_share + 0.7 × air_yards_share ([playerstats dictionary][nflreadr-dict-ps-csv]). NGS `percent_share_of_intended_air_yards` is a **percent**, 0–100 ([NGS dictionary][nflreadr-dict-ngs]) | pbp 1999+. NGS 2016+ ([load_nextgen_stats][nflreadr-ngs]) | NGS updates nightly. NGS applies a minimum-attempt threshold, so an absent row is not zero ([load_nextgen_stats][nflreadr-ngs]) |
| Explosive rates | **D** | pbp `rushing_yards` / `receiving_yards` / `air_yards` at a local threshold (proposed: rush ≥ 10, pass ≥ 20), over team plays of that type. Threshold and version belong in `definition` and `calculation_version` | 1999+ | Nightly |
| Neutral pace | **D** | pbp `wp`, `qtr`, `half_seconds_remaining`, `game_seconds_remaining`, `pass_oe`/`xpass` ([pbp dictionary][nflreadr-dict-pbp]). Proposed definition: offensive plays with 0.20 ≤ wp ≤ 0.80, qtr ≤ 4, and more than 120 s left in the half. Unit: seconds per play | 1999+ | Nightly |
| Pressure | **P / Y (lagged)** | pbp `qb_hit`, sacks (outcomes only). Participation `was_pressure`, `number_of_pass_rushers`, `time_to_throw` ([dictionary][nflreadr-dict-participation]) arrive only after the postseason for 2023+. FTN charting has `n_pass_rushers` and `n_blitzers` but no pressure field ([FTN dictionary][nflreadr-dict-ftn]). `load_pfr_advstats` has `times_pressured` and `pressure_pct` ([PFR dictionary][nflreadr-dict-pfr]). Weekly availability of the pressure columns is **unverified**, since "some data is only available at the season level" ([load_pfr_advstats][nflreadr-pfr]) | Participation 2016+. PFR 2018+. FTN 2022+ | FTN charting is done "within 48 hours" of each game ([load_ftn_charting][nflreadr-ftn]) and updates four times daily ([data schedule][nflverse-schedule]) |
| Schedule / game identity | **Y** | `load_schedules`: `game_id` = season_week_away_home, `gameday`, `gametime` (24-hour, **US Eastern**), `home_team`, `away_team`, `location` (Home/Neutral), plus alternate game ids `old_game_id`, `gsis`, `pfr`, `pff`, `espn` ([schedules dictionary][nflreadr-dict-schedules]) | All seasons ([load_schedules][nflreadr-schedules]) | Every 5 minutes in season ([data schedule][nflverse-schedule]) |
| Weather / venue | **Y (observed)** | Schedules: `roof`, `surface`, `temp`, `wind` (outdoors/open only), `stadium_id`, `stadium`. pbp `weather` is a string that "doesn't change during the game" ([pbp dictionary][nflreadr-dict-pbp]). Source is PFR. These are observed values, not forecasts | All seasons | Pre-game forecasts need NWS or Open-Meteo |
| Spread / total / implied totals | **Y / D** | `spread_line` (positive = home favored), `total_line`, moneylines, spread odds ([schedules dictionary][nflreadr-dict-schedules]). Implied totals: home = (total_line + spread_line) / 2, away = (total_line − spread_line) / 2. ffopportunity also ships an `implied_total` ([ffopps dictionary][nflreadr-dict-ffopps]). **No line timestamp, and opening vs. closing is not stated** ([load_schedules][nflreadr-schedules]) | All seasons | Value changes with no as-of. Revision status is `unknown` |
| EPA | **Y (model)** | pbp `epa`, `cpoe`, `xyac_*`, `xpass`, `pass_oe`. player_stats `passing_epa`, `rushing_epa`, `receiving_epa` ([dictionaries][nflreadr-dict-pbp]) | 1999+ | The nflfastR model version must be recorded |
| DVOA | **N** | FTN product. Add-on availability is **unverified** | – | – |
| xFP | **Y (model)** | `load_ff_opportunity` with `model_version` `latest` or `v1.0.0`, `stat_type` `weekly`/`pbp_pass`/`pbp_rush` ([load_ff_opportunity][nflreadr-ffopps]). xgboost trained on nflverse data from 2006–2020 ([ffopportunity][ffopportunity]) | **Unverified** start season | GitHub Actions. Schedule **unverified** |
| WAR | **N** | No nflverse dataset. Any WAR source is **unverified** | – | – |

### SportsDataIO

The field names below come from its NFL data dictionary ([data dictionary][sdio-dict]).

| Input | Avail | Field(s) / notes |
|---|---|---|
| Routes, route participation, yards per route | **U** | Listed as "Route Participation", "Yards / Pass Route", and "Fantasy Points / Pass Route" in a 2019 PlayerProfiler partnership PDF ([advanced metrics PDF][sdio-pdf]). The current data dictionary has no field containing "route" ([data dictionary][sdio-dict]). Needs a trial payload |
| Snaps + team denominators | **Y** | `OffensiveSnapsPlayed`, `OffensiveTeamSnaps`, `DefensiveSnapsPlayed`, `DefensiveTeamSnaps`, `SpecialTeamsSnapsPlayed`, `SpecialTeamsTeamSnaps`, "from 2012", "available the morning after the game" ([data dictionary][sdio-dict]) |
| Targets / carries / red zone | **Y** | Player game stats, plus `PlayerGameRedZoneStats` and the inside-five and inside-ten variants (from 2015) ([API docs][sdio-api], [data dictionary][sdio-dict]) |
| Air yards, explosive plays, pace, pressure | **U** | Play-by-play endpoints exist ([API docs][sdio-api]). Field-level air-yard and pressure coverage is unverified. The 2019 PDF lists "Under Pressure Completion %" and "Air Yards" ([PDF][sdio-pdf]) |
| Schedule / game identity | **Y** | `GameKey` (season, season type, week, home team), `GlobalGameID`, `ScoreID` (superseded by `GameID`), `Status` (with a postponed/suspended guide), `Canceled`, and `DateTime` in US Eastern. "All dates & times are in US Eastern Time" ([data dictionary][sdio-dict]) |
| Weather / venue | **Y (forecast)** | `ForecastTempLow`/`High`, `ForecastWindSpeed` (from 2015), `StadiumID`, `PlayingSurface`, `GeoLat`/`GeoLong`. The `Schedules` endpoint includes stadium, betting, and weather ([API docs][sdio-api], [data dictionary][sdio-dict]) |
| Spread / total / implied totals | **Y** | `PointSpread`, `OverUnder`, moneylines, pre-game odds, and line-movement endpoints. `Updated` on betting markets is in US Eastern ([API docs][sdio-api], [data dictionary][sdio-dict]) |
| EPA / DVOA / xFP / WAR | **U / N** | The 2019 PDF lists "Expected Touchdowns" and "Production Premium" ([PDF][sdio-pdf]). No EPA, DVOA, or WAR fields were found |
| Player identity | **Y** | `PlayerID` plus a `SportRadarPlayerID` cross-reference. No `GsisID` field was found ([data dictionary][sdio-dict]) |

### Sportradar, FTN, PFF, The Odds API, weather

- **Sportradar.** It offers schedules, game statistics, play-by-play, weekly depth
  charts, injuries, and seasonal stats ([overview][sr-overview]). The NFL
  statistics summary lists per-play "Players Rushed", "Men in Box", and "QB at Snap"
  ([stats summary][sr-stats], from a search-result summary, not a direct fetch).
  Whether it has snaps, routes, TPRR, YPRR, or xFP is **unverified**.
- **FTN.** Its charting includes the participation used to filter specific routes
  (search snippet, **unverified**). nflverse republishes only FTN's play-level
  charting subset (2022+, [FTN dictionary][nflreadr-dict-ftn]), and that subset
  has no routes.
- **PFF.** Its play data fields include "players running routes" and "all routes
  run", plus hurries, hits, sacks, and time to pressure (2018 field list,
  [PFF play data fields][pff-fields], from a search-result summary). Current API field names are **unverified**.
- **The Odds API.** It covers spreads and totals per bookmaker with ISO-8601 UTC
  timestamps. Historical snapshots go back to 2020-06-06 at 10-minute intervals,
  and 5-minute intervals from September 2022 ([v4 guide][odds-guide]). A
  `team_totals` market is not documented in the v4 guide, so we derive implied
  team totals from spread and total.
- **Weather.** NWS offers hourly forecasts for the next 7 days ([NWS][nws]).
  Open-Meteo is the non-commercial fallback ([terms][open-meteo]). Both need
  stadium coordinates and the roof type from schedules. Dome and closed-roof games
  should record weather as "not applicable", not zero.

## Raw Fields, Denominators, Units, Timestamps

Every provider value enters as a `ProviderMetricObservation`
(`python/src/sleeper_tooling/context_providers.py`). Required parts:

| Concern | Contract field(s) |
|---|---|
| Game identity | `GameIdentity`: `season`, `season_type` (PRE/REG/POST), `week`, `game_id` (nflverse `season_week_away_home`), `kickoff_utc` (timezone-aware; naive kickoffs are rejected), `home_team`, `away_team`, `status` (scheduled/in_progress/final/postponed/canceled), `original_kickoff_utc`, `neutral_site`, `venue_id`, `roof`, and `provider_game_ids` (gsis, pfr, SportsDataIO `GlobalGameID`, Odds API event id) |
| Team context | `team`, `opponent`, and `home_away`, each checked against the game |
| Player identity | `PlayerIdentity`: `provider`, `provider_player_id`, `gsis_id`, `sleeper_player_id`, `id_path` |
| Metric | `provider`, `dataset`, `metric_name` (provider-native name), optional `context_input`, `value`, `units` (count/ratio/percent/rate/yards/points/seconds/seconds_per_play/mph/fahrenheit/expected_points/index/category), and `scope` (player_game/team_game/game) |
| Ratio parts | `numerator`, `denominator`, `numerator_field`, `denominator_field`, `definition`. These are required for ratio, percent, rate, and seconds_per_play units |
| Versioning | `calculation_version` for locally derived values. `model_family` (epa/dvoa/xfp/war) plus `ProviderModelMetadata` (`model_name`, `model_version`, `sample_size`, `sample_scope`, `provenance_url`, `calculated_by`) for model output |
| Provenance | `source_record_id` and/or `source_url`, `source_updated_at`, `fetched_at`, and the original `RawProviderPayload` |
| Freshness | `freshness_status(obs, now=..., max_age_seconds=...)`. `now` is injected, so the result is deterministic |
| Revision | `revision_status`: preliminary/final/corrected/backfilled/unknown |
| Licensing | `ProviderLicense`: `name`, `attribution`, `url`, `storage_allowed`, `redistribution_allowed`, `mcp_exposure` (raw/derived_only/none/unverified) |

Validation (`validate_observation` / `validate_observations`) returns explicit
reason codes and never a zero:

| Code | Trigger |
|---|---|
| `provider_source_mismatch` | Observation provider or raw-payload provider differs from the fetching provider |
| `provider_week_mismatch` | Game season, season_type, or week differs from the request |
| `provider_game_mismatch` | Team not in the game, or opponent/home_away disagree with the game |
| `provider_game_not_played` | Player or team stats attached to a postponed or canceled game |
| `provider_identity_unresolved` | Player row with no Sleeper `player_id` |
| `provider_provenance_missing` | No source record id/URL, or no `fetched_at` |
| `provider_denominator_missing` / `provider_numerator_missing` / `provider_definition_missing` | Ratio without its parts or definition |
| `provider_invalid_percentage` | Ratio outside 0–1, percent outside 0–100, negative, or non-numeric |
| `provider_ratio_mismatch` | Value disagrees with numerator / denominator |
| `provider_model_metadata_missing` | EPA/DVOA/xFP/WAR from a provider without model name, version, sample, and provenance |
| `provider_data_stale` | `now - fetched_at > max_age_seconds` |
| `provider_duplicate_record` | Two or more rows share a key. All of them are rejected as ambiguous |

`lookup_observation` keeps three outcomes distinct:
- `present`: includes a real `0`.
- `null_value`: the key exists but has no value.
- `absent`: there is no row.

The last two map to `not_evaluable_missing_<input>`, including
`not_evaluable_missing_routes`, `_pressure`, `_weather`, and `_implied_totals`.

`ContextProviderRegistry` accepts `observations` from providers, validates them
against the request (with injected `now` and `max_age_seconds`), and returns
accepted observations, `observation_issues`, and missing-input rows carrying
those codes. It does not copy observation values into `EnrichedPlayerContext`,
so nothing reaches scoring.

The sample payload and every accept/reject case live in
`python/tests/fixtures/provider_contract_sample.json`. All values are synthetic.
The fixture covers routes, TPRR, YPRR, target share, carry share, red-zone share,
pressure, neutral pace, wind, implied totals, EPA, xFP, a real zero, a null
value, a traded player, and a postponed game.

## Identity Mapping

- **Sleeper players.** Sleeper player rows carry `gsis_id`, `sportradar_id`,
  `fantasy_data_id`, `pff_id`, `pfr_id`, `espn_id`, `yahoo_id`, and others. These
  are stored in `player_external_ids(player_id, provider, external_id, source)`,
  with the provider labels `gsis`, `sportradar`, `fantasydata`, `pff`, and
  `pro-football-reference` (`python/src/sleeper_tooling/db.py`,
  `PLAYER_EXTERNAL_ID_FIELDS`).
- **nflverse.** `load_ff_playerids` maps `gsis_id`, `sleeper_id`, `pfr_id`,
  `sportradar_id`, `pff_id`, and others, with `mfl_id` as primary key
  ([ff_playerids dictionary][nflreadr-dict-ffids]). The paths by dataset:
  - player_stats, NGS, and participation use gsis → sleeper (already used in
    `nflverse.py`).
  - **snap_counts use `pfr_player_id`**, so the path is pfr → sleeper, either
    through ff_playerids or `player_external_ids(provider='pro-football-reference')`.
  - FTN charting is play-level and joins through `nflverse_game_id` and
    `nflverse_play_id`.
- **SportsDataIO.** `PlayerID` is its own id. The likely join paths are
  `player_external_ids(provider='fantasydata')` or `SportRadarPlayerID` →
  `sportradar`. Both are **unverified** until a trial payload is compared.
- **Sportradar.** UUIDs, joined via `sportradar_id`. **Unverified**.
- **Resolution rule.** Accept only one-to-one mappings. If a provider id maps to
  zero or several Sleeper ids, emit `provider_identity_unresolved` and never guess
  by name.
- **Traded players.** Observation keys include `game_id` and `team`, so a player
  who changes teams has distinct rows each week (fixture rows `wr2_week3_lv` and
  `wr2_week4_kc`). **Known gap in merged code:**
  `nflverse.sync_nflverse_stats` uses `recent_team` before `team`. The current
  player_stats dictionary defines a per-game `team` and `opponent_team`
  ([dictionary][nflreadr-dict-ps-json]), and `recent_team` is a legacy
  "most recent team" field ([playerstats dictionary][nflreadr-dict-ps-csv]). The
  follow-up should prefer `team`.
- **Multi-team games.** A player has at most one row per game, so the key
  `(provider, dataset, metric, season, season_type, week, game_id, team, player, scope)`
  is unique. Duplicates are rejected, not merged.
- **Postponed games.** Store `status` and `original_kickoff_utc`. Player and team
  observations on postponed or canceled games are rejected. Whether nflverse
  re-keys `game_id` or `week` for a rescheduled game is **unverified**.
  SportsDataIO documents postponed and suspended handling ([data dictionary][sdio-dict]).
- **player_stats has no `game_id`** in its current dictionary
  ([dictionary][nflreadr-dict-ps-json]). Join on `(season, season_type, week, team)`
  to schedules to get `game_id`. Each team plays once a week, so that join is
  unique.

## Operational Comparison

| Concern | nflverse | SportsDataIO | Sportradar | The Odds API |
|---|---|---|---|---|
| Freshness | pbp/player_stats nightly. snap_counts 4×/day. NGS nightly. schedules every 5 min. participation post-season only ([schedule][nflverse-schedule]) | Recommended poll intervals per endpoint, e.g. player game stats 5 min, schedules 3 min ([API docs][sdio-api]) | **Unverified** | Live, plus historical snapshots ([guide][odds-guide]) |
| Corrections | A Wed–Thu re-run picks up stat corrections ([schedule][nflverse-schedule]). Record `revision_status=preliminary` until then | Docs say to update cached records and handle corrections after Final ([getting started][sdio-start]) | **Unverified** | Lines move. Store `source_updated_at` |
| Timezone | `gametime` and pbp `start_time` in Eastern. pbp `time_of_day` in UTC ([dictionaries][nflreadr-dict-schedules]). nflreadr's data timezone constant is `America/Toronto` ([source][nflreadr-tz]) | "All dates & times are in US Eastern Time" ([dictionary][sdio-dict]) | **Unverified** | ISO-8601 with `Z` ([guide][odds-guide]) |
| Outage / SLA | No SLA. Data is "scraped via GitHub Actions" ([nflverse-data][nflverse-data]) | **Unverified** | **Unverified** | **Unverified** |
| Local Docker | Already works (`nflreadpy` in the image) | HTTPS + key via env | HTTPS + key via env | HTTPS + key via env |
| Worker / D1 | Parquet parsing in a Worker is impractical. Ingest in Python, then sync normalized rows to D1 | JSON is Worker-friendly, but keys must stay in Worker secrets and quotas are shared | Same | Same |

Rules for all providers:
- Normalize kickoffs to UTC at ingest.
- Never derive `week` from a date.
- Keep the provider's raw timestamp in `RawProviderPayload`.

## Licensing, Storage, MCP Exposure, Attribution

- **nflverse CC-BY 4.0.** Storage and redistribution are allowed with attribution,
  a link to the license, and a note when data was modified ([LICENSE][nflverse-license]).
  Proposed attribution string: "Data from nflverse (CC-BY 4.0), modified."
- **FTN-sourced data (charting and 2023+ participation) is CC-BY-SA 4.0.**
  Credit "FTN Data via nflverse". Derived datasets we redistribute must be
  share-alike ([load_ftn_charting][nflreadr-ftn], [load_participation][nflreadr-participation]).
  Keep these rows separately licensed and do not mix them into CC-BY tables.
- **Upstream PFR and NGS rights.** nflverse republishes PFR-sourced snap counts,
  weather, and advstats, plus NGS data. Sports-Reference prohibits automated
  scraping without written permission ([SR data use][sr-data-use], from a
  search-result summary). Whether
  nflverse's CC-BY grant fully covers the upstream PFR/NGS content is
  **unverified**. Use the nflverse releases rather than scraping PFR or NGS, and
  expose derived summaries through MCP rather than bulk raw tables.
- **SportsDataIO, Sportradar, FTN, PFF, The Odds API.** Assume
  `storage_allowed=None` and `mcp_exposure="unverified"` until the contract says
  otherwise. SportsDataIO's free tiers forbid commercial redistribution, and its
  trial data cannot be used for analysis or display ([getting started][sdio-start]).
- **MCP.** Expose only derived, attributed summaries with `source`, `fetched_at`,
  and license name. Never expose raw paid payloads.

## Follow-up Issue (body to file)

**Title:** Persist validated provider observations and score paid route-data candidates

**Body:**

> Follow-up to #42 (contract research in `docs/provider-contract-research.md`).
> Still no scoring or recommendation changes.
>
> ### Persistence
> - Add `provider_metric_observations` (SQLite and D1). Primary key:
>   `(provider, dataset, metric_name, season, season_type, week, game_id, team,
>   provider_player_id, scope)`. Columns: `sleeper_player_id`, `gsis_id`,
>   `opponent`, `home_away`, `kickoff_utc`, `value`, `units`, `numerator`,
>   `denominator`, `numerator_field`, `denominator_field`, `definition`,
>   `calculation_version`, `model_json`, `source_record_id`, `source_url`,
>   `source_updated_at`, `fetched_at`, `revision_status`, `license_name`,
>   `raw_json`.
> - Add `provider_games` (game identity, status, original kickoff, provider game
>   ids) populated from `nflreadpy.load_schedules`.
> - Only rows accepted by `validate_observations` are written. Rejected rows are
>   counted by reason code in `provider_sync_metadata`.
>
> ### nflverse adapter (free tier)
> - schedules → `provider_games`, wind/temp/roof observations, and derived
>   implied totals (`revision_status=unknown`, no line timestamp).
> - snap_counts with a pfr → sleeper join. Verify whether `offense_pct` is 0–1 or
>   0–100.
> - player_stats target/air-yard share with team denominators. Prefer per-game
>   `team` over `recent_team`, and fix that in `nflverse.sync_nflverse_stats`.
> - pbp-derived red-zone share, explosive rates, and neutral pace, each with a
>   `calculation_version`.
> - ff_opportunity xFP and pbp EPA, with model metadata.
> - Mark rows `preliminary` until the Wed–Thu correction run, then `final` or
>   `corrected`.
>
> ### Paid candidate gate
> - Request trials from SportsDataIO (route participation), FTN Data, and PFF Pro.
>   Pull the same four-week 2026 fixture from each.
> - Score each provider on:
>   - all-routes denominators with a documented route definition;
>   - weekly history depth;
>   - a one-to-one ID crosswalk to Sleeper (`fantasydata`/`sportradar`/`pff`
>     ids);
>   - correction timestamps;
>   - written terms for local SQLite, D1, and MCP-derived output;
>   - cost.
> - If none pass, keep routes as `not_evaluable_missing_routes`.
>
> ### Lines and weather
> - The Odds API adapter for timestamped opening/current spread and total (UTC).
>   Derive implied team totals with source and timestamp.
> - NWS forecast adapter for outdoor/open venues. Domes record "not applicable".
>
> ### Acceptance
> - Mocked adapter tests using the #42 fixture shape.
> - Sync status shows provider, dataset, coverage, row counts, reason-code counts,
>   and freshness.
> - Docker and Worker/D1 parity for the new tables.
> - No scoring changes.

[nflreadpy]: https://nflreadpy.nflverse.com/
[nflreadpy-config]: https://nflreadpy.nflverse.com/api/configuration/
[nflverse-schedule]: https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html
[nflverse-license]: https://github.com/nflverse/nflverse-data/blob/master/LICENSE.md
[nflverse-data]: https://github.com/nflverse/nflverse-data
[nflreadr-participation]: https://nflreadr.nflverse.com/reference/load_participation.html
[nflreadr-ftn]: https://nflreadr.nflverse.com/reference/load_ftn_charting.html
[nflreadr-ngs]: https://nflreadr.nflverse.com/reference/load_nextgen_stats.html
[nflreadr-snaps]: https://nflreadr.nflverse.com/reference/load_snap_counts.html
[nflreadr-schedules]: https://nflreadr.nflverse.com/reference/load_schedules.html
[nflreadr-ffopps]: https://nflreadr.nflverse.com/reference/load_ff_opportunity.html
[nflreadr-pfr]: https://nflreadr.nflverse.com/reference/load_pfr_advstats.html
[nflreadr-dict-participation]: https://nflreadr.nflverse.com/articles/dictionary_participation.html
[nflreadr-dict-snaps]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_snap_counts.csv
[nflreadr-dict-schedules]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_schedules.csv
[nflreadr-dict-pbp]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_pbp.csv
[nflreadr-dict-ftn]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_ftn_charting.csv
[nflreadr-dict-ngs]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_nextgenstats.csv
[nflreadr-dict-ffopps]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_ffopps.csv
[nflreadr-dict-ffids]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_ff_playerids.csv
[nflreadr-dict-pfr]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_pfr_passing.csv
[nflreadr-dict-ps-json]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_player_stats.json
[nflreadr-dict-ps-csv]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_playerstats.csv
[nflreadr-tz]: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/nflverse_data_timezone.R
[ffopportunity]: https://ffopportunity.ffverse.com/
[sdio-start]: https://sportsdata.io/developers/getting-started
[sdio-api]: https://sportsdata.io/developers/api-documentation/nfl
[sdio-dict]: https://sportsdata.io/developers/data-dictionary/nfl
[sdio-pdf]: https://sportsdata.io/files/SDio_NFL_Advanced_Metrics.pdf
[sr-overview]: https://developer.sportradar.com/football/reference/nfl-overview
[sr-account]: https://developer.sportradar.com/getting-started/docs/your-account
[sr-stats]: https://developer.sportradar.com/football/v5/reference/nfl-statistics-summary
[ftn-data]: https://ftnfantasy.com/data
[pff-premium]: https://www.pff.com/premium
[pff-fields]: https://media.pff.com/2018/08/PFF-Play-Data-Fields.pdf
[savant-about]: https://www.nflsavant.com/about.php
[odds-guide]: https://the-odds-api.com/liveapi/guides/v4/
[odds-home]: https://the-odds-api.com/
[nws]: https://www.weather.gov/documentation/services-web-api
[open-meteo]: https://open-meteo.com/en/terms
[sr-data-use]: https://www.sports-reference.com/data_use.html

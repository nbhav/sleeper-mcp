# Player Context Source Audit

Issue: #36
Scope: audit only; no scoring, context-engine, CLI, or MCP behavior changes.

## Verification Snapshot

This audit is based on code and tests in this checkout plus a Dockerized live
Sleeper key inventory run on 2026-10-03.

Containerized inventory input:

```text
state.season=2026
state.week=4
weeks=[1,2,3,4]
positions=[QB,RB,WR,TE,K,DEF]
sources=[stats,projections]
```

Sleeper can add or remove stat keys over time. The normalized model is dynamic:
every numeric `stats` entry in a Sleeper weekly stats/projections row is synced
into `player_week_stat_values` without requiring a schema migration.

## Current Read And Storage Paths

| Area | Current behavior |
|---|---|
| Weekly stats/projections fetch | `SleeperClient.get_stats()` and `get_projections()` read Sleeper data endpoints. |
| Normalized sync | `SleeperSyncService._sync_league_week()` fetches league matchups, transactions, stats, and projections for each synced week. |
| Row-level weekly table | `player_week_rows` stores `source`, `season`, `week`, `player_id`, `player_name`, `team`, `position`, `player_json`, `raw_json`, `sleeper_points`, and `fantasy_points`. |
| Tall stat table | `player_week_stat_values` stores one numeric value per `source`, `season`, `week`, `player_id`, and `stat_key`. This is the main field inventory for context signals. |
| Scoring contribution table | `player_week_scoring_values` stores league-scoring point contributions per scoring key when scoring settings are available. |
| Trend reads | `query_numeric_stat_rows()`, `player_stat_trends()`, `multi_stat_usage_trends()`, `position_stat_leaders()`, `projection_actual_deltas()`, and `week_over_week_movers()` read the tall stat table. |
| Decision reads | `NormalizedDecisionReader` reads league/users/rosters/matchups/projections/recent actual rows, but currently exposes weekly player rows mainly as points/name/team/position. Context engines that need stat-specific usage should use the trend reads or repository stat rows. |

Important distinction:

- `player_week_rows.raw_json` preserves the full Sleeper row, including
  nonnumeric fields.
- `player_week_stat_values` includes only numeric keys from `raw_json.stats`.
- `player_week_rows` has typed `team` and `position`, but no typed `opponent`,
  `game_id`, `date`, home/away, weather, stadium, betting total, or spread
  columns.

## NFL Schedule And Opponent Data

Sleeper weekly stats/projections rows observed on 2026-10-03 include top-level
raw fields:

```text
category, company, date, game_id, last_modified, opponent, player, player_id,
season, season_type, sport, stats, status, team, updated_at, week, week_shard
```

Therefore:

- NFL `opponent`, `game_id`, and `date` exist in raw Sleeper weekly
  stats/projection rows and are preserved in `player_week_rows.raw_json`.
- They are not normalized into typed columns, not exposed by the current trend
  row output, and not joined to a durable NFL schedule table.
- The local `team_schedule_context` table exists but is reserved/local-manual
  schema; the current sync path does not populate it.
- `matchups` and `matchup_players` are fantasy-league matchup tables. They
  identify the user's fantasy opponent roster, not the player's NFL opponent
  or matchup quality.
- Home/away was not observed as a typed or raw top-level field in the sampled
  weekly rows.

V1 can use raw `opponent` only as a partial label after reading
`player_week_rows.raw_json`; it cannot compute opponent strength, pace, pressure
allowed, coverage quality, weather, or implied total.

## Current Metadata And Depth Limitations

The `players` table stores current Sleeper player metadata:

```text
player_id, full_name, first_name, last_name, team, position,
fantasy_positions_json, status, injury_status, depth_chart_order,
depth_chart_position, raw_json, updated_at
```

Usable current metadata:

- `team`, `position`, and `fantasy_positions`
- `status`, `injury_status`, and raw injury fields when present
- `depth_chart_order` and `depth_chart_position`
- external IDs from player-map fields such as `espn_id`, `rotowire_id`,
  `sportradar_id`, `gsis_id`, and others
- `bye_week`/`bye` only when present in the raw player payload or live player
  map path; it is not a typed normalized column

Limitations:

- Player metadata is current-state metadata, not a per-week historical depth
  chart snapshot.
- There is no per-week role/depth snapshot table.
- There are no route, route share, snap share percentage, target share, carry
  share, red-zone share, personnel grouping, offensive line, defensive pressure,
  weather, stadium, betting line, or implied-total sources.
- Actual stats include some raw snap counts (`off_snp`, `st_snp`, `def_snp`,
  `tm_off_snp`, `tm_st_snp`, `tm_def_snp`) for some positions, but projections
  generally do not. `off_snp` is an absolute player snap count; where both
  `off_snp` and `tm_off_snp` are valid, `off_snp / tm_off_snp` is only a
  snap-share proxy. It is not a route share or an authoritative position-
  specific snap share, and no share is currently computed by the repository.

## Window And Field Semantics

The audit uses these meanings so a reason is not accidentally marked available
because a similarly named field exists in a different time window:

- **Actual window:** completed-game `stats` rows for a specific season/week.
  These are historical outcomes and cannot establish a pre-game role or
  matchup expectation.
- **Projection window:** future-week `projections` rows for the same
  season/week. These are forecasts, not observed usage, and generally do not
  contain actual-only snap, air-yard, defensive-event, or team-denominator
  fields. A projected reason is unavailable when its required input is
  actual-only.
- **Actual/projection delta:** compare the two sources only for the same
  player, season, and week. It is a forecast-error signal, not a historical
  usage trend unless the actual row is from a completed week and the
  projection window is known.
- **Absolute versus share:** `off_snp`, `st_snp`, `def_snp`, `rec_air_yd`, and
  `pass_air_yd` are absolute observations when present. Snap share and
  air-yard share require valid denominators and must not be named as if the
  absolute field were already a share.

## Sleeper-Computable Signals By Position

The keys below are original Sleeper `stats` keys as stored in
`player_week_stat_values.stat_key`.

### QB

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Passing volume | `pass_att`, `pass_cmp`, `pass_inc`, `pass_fd`, `pass_rz_att` | `pass_att`, `pass_cmp`, `pass_inc`, `pass_fd` | partial |
| Passing production | `pass_yd`, `pass_td`, `pass_td_40p`, `pass_td_50p`, `pass_td_lng`, `pass_ypa`, `pass_ypc`, `cmp_pct`, `pass_rtg` | `pass_yd`, `pass_td`, `pass_cmp_40p`, `cmp_pct` | computed |
| Rushing floor | `rush_att`, `rush_yd`, `rush_td`, `rush_fd`, `rush_rz_att`, `rush_2pt` | `rush_att`, `rush_yd`, `rush_td`, `rush_fd`, `rush_2pt` | computed |
| Negative plays | `pass_int`, `pass_sack`, `pass_sack_yds`, `fum`, `fum_lost`, `penalty`, `penalty_yd` | `pass_int`, `pass_sack`, `fum`, `fum_lost` | computed |
| TD dependency | `pass_td`, `rush_td`, `anytime_tds`, `first_td`, long-TD keys | `pass_td`, `rush_td` | partial |
| Availability/context | `gms_active`, `gp`, `gs`, weekly raw `opponent`, current `status`/`injury_status` | `gp`, weekly raw `opponent`, current status fields | partial |

### RB

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Rushing workload | `rush_att`, `rush_fd`, `rush_rz_att` | `rush_att`, `rush_fd` | partial |
| Receiving role | `rec_tgt`, `rec`, `rec_fd`, `rec_rz_tgt` | `rec_tgt`, `rec`, `rec_fd` | partial |
| Production | `rush_yd`, `rush_td`, `rec_yd`, `rec_td`, `rush_rec_yd`, `pts_*` | same core production keys | computed |
| Efficiency/explosive | `rush_ypa`, `rush_yac`, `rush_btkl`, `rush_lng`, `rush_40p`, reception yard buckets | `rush_40p`, reception yard buckets | partial |
| Ball security/negative | `fum`, `fum_lost`, `rec_drop`, `rush_tkl_loss`, `rush_tkl_loss_yd` | `fum`, `fum_lost` | partial |
| TD dependency | `rush_td`, `rec_td`, `anytime_tds`, `first_td`, long-TD keys | `rush_td`, `rec_td` | partial |

### WR

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Opportunity | `rec_tgt`, `rec`, `rec_fd`, `rec_rz_tgt` | `rec_tgt`, `rec`, `rec_fd` | partial |
| Production | `rec_yd`, `rec_td`, `pts_*`, reception yard buckets | same core production keys | computed |
| Secondary rushing/passing | `rush_att`, `rush_yd`, `rush_td`, passing keys for gadget plays | `rush_att`, `rush_yd`, `rush_td` | partial |
| Reliability/volatility | `rec_drop`, `fum`, `fum_lost`, `rec_ypr`, `rec_ypt`, `rec_air_yd`, `rec_yar` | `fum`, `fum_lost` | partial |
| TD dependency | `rec_td`, `rush_td`, `st_td`, `anytime_tds`, `first_td`, long-TD keys | `rec_td`, `rush_td` | partial |
| Missing route role | no observed route keys | no observed route keys | not_evaluable_missing_source |

### TE

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Opportunity | `rec_tgt`, `rec`, `rec_fd`, `rec_rz_tgt` | `rec_tgt`, `rec`, `rec_fd` | partial |
| Production | `rec_yd`, `rec_td`, `pts_*`, reception yard buckets | same core production keys | computed |
| Secondary rushing | `rush_att`, `rush_yd`, `rush_td`, `rush_rz_att` | `rush_att`, `rush_yd`, `rush_td` | partial |
| Reliability/volatility | `rec_drop`, `fum`, `fum_lost`, `rec_ypr`, `rec_ypt`, `rec_air_yd`, `rec_yar` | `fum`, `fum_lost` | partial |
| TD dependency | `rec_td`, `rush_td`, `anytime_tds`, `first_td`, long-TD keys | `rec_td`, `rush_td` | partial |
| Missing route/snap share | raw actual snap counts may exist (`off_snp`, `def_snp`, `st_snp`), but no route or share keys | no observed route/share keys | not_evaluable_missing_source |

### K

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Field-goal volume | `fga`, `fgm` | `fga`, `fgm` | computed |
| PAT volume | `xpa`, `xpm` | `xpa`, `xpm` | computed |
| Distance buckets | `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50_59`, `fgm_50p`, `fgm_60p`, `fgm_lng`, `fgm_yds`, `fgm_yds_over_30` | `fgm_0_19`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50p`, `fgm_yds` | computed |
| Misses/penalties | `fgmiss`, `fgmiss_20_29`, `fgmiss_30_39`, `fgmiss_40_49`, `fgmiss_50_59`, `fgmiss_50p`, `fgmiss_60p`, `xpmiss`, blocked kick fields | `fgmiss_30_39`, `fgmiss_40_49`, `fgmiss_50p`, `xpmiss` | computed |
| Game environment | weekly raw `opponent`, current team/status only | weekly raw `opponent`, current team/status only | partial |
| Missing enriched context | weather, dome/outdoor, wind, implied team total | same missing | not_evaluable_missing_source |

### DEF

| Signal | Actual stats keys | Projection keys | Tier |
|---|---|---|---|
| Pass-rush disruption | `sack`, `sack_yd`, `qb_hit`, `tkl_loss` | `sack`, `tkl_loss` | partial |
| Turnovers | `int`, `ff`, `fum_rec`, `def_st_ff`, `def_st_fum_rec` | `int`, `ff`, `fum_rec` | computed |
| Defensive/special TDs | `def_td`, `def_st_td`, `td`, return and block-related TD fields | `def_td`, `def_fum_td`, `def_kr_td`, `def_pr_td`, `pass_int_td`, `st_td` | computed |
| Points allowed | `pts_allow`, `pts_allow_1_6`, `pts_allow_7_13`, `pts_allow_14_20`, `pts_allow_21_27`, `pts_allow_28_34`, `pts_allow_35p` | `pts_allow`, `pts_allow_14_20`, `pts_allow_21_27`, `pts_allow_28_34` | computed |
| Yards allowed | `yds_allow`, yardage buckets from `yds_allow_100_199` through `yds_allow_550p` | `yds_allow`, buckets from `yds_allow_200_299` through `yds_allow_400_449` | computed |
| Matchup strength | weekly raw `opponent` label only | weekly raw `opponent` label only | partial |
| Missing enriched context | opponent offensive line pressure allowed, QB turnover pressure, betting/weather | same missing | not_evaluable_missing_source |

## Observed Numeric Stat-Key Inventory

These are sampled keys from Sleeper 2026 weeks 1-4. The normalized store will
also ingest future numeric keys automatically.

| Position | Actual stats keys observed | Projection keys observed |
|---|---|---|
| QB | `anytime_tds`, `bonus_fd_qb`, `bonus_pass_cmp_25`, `bonus_pass_yd_300`, `bonus_pass_yd_400`, `bonus_rush_td_qb`, `cmp_pct`, `first_td`, `fum`, `fum_lost`, `gms_active`, `gp`, `gs`, `idp_tkl`, `idp_tkl_solo`, `off_snp`, `pass_2pt`, `pass_air_yd`, `pass_att`, `pass_cmp`, `pass_cmp_40p`, `pass_fd`, `pass_inc`, `pass_int`, `pass_int_td`, `pass_lng`, `pass_rtg`, `pass_rush_yd`, `pass_rz_att`, `pass_sack`, `pass_sack_yds`, `pass_td`, `pass_td_40p`, `pass_td_50p`, `pass_td_lng`, `pass_yd`, `pass_ypa`, `pass_ypc`, `penalty`, `penalty_yd`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pts_half_ppr`, `pts_idp`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_air_yd`, `rec_lng`, `rec_tgt`, `rec_yar`, `rec_yd`, `rec_ypr`, `rec_ypt`, `rush_2pt`, `rush_att`, `rush_btkl`, `rush_fd`, `rush_lng`, `rush_rec_yd`, `rush_rz_att`, `rush_td`, `rush_td_lng`, `rush_tkl_loss`, `rush_tkl_loss_yd`, `rush_yac`, `rush_yd`, `rush_ypa`, `tm_def_snp`, `tm_off_snp`, `tm_st_snp` | `adp_dd_ppr`, `bonus_rush_td_qb`, `cmp_pct`, `def_fum_td`, `fum`, `fum_lost`, `gp`, `pass_2pt`, `pass_att`, `pass_cmp`, `pass_cmp_40p`, `pass_fd`, `pass_inc`, `pass_int`, `pass_int_td`, `pass_sack`, `pass_td`, `pass_yd`, `pos_adp_dd_ppr`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `rush_2pt`, `rush_40p`, `rush_att`, `rush_fd`, `rush_td`, `rush_yd` |
| RB | `anytime_tds`, `bonus_fd_rb`, `bonus_rec_rb`, `bonus_rush_att_20`, `bonus_rush_rec_yd_100`, `bonus_rush_rec_yd_200`, `bonus_rush_yd_100`, `first_td`, `fum`, `fum_lost`, `gms_active`, `gp`, `gs`, `idp_tkl`, `idp_tkl_ast`, `idp_tkl_solo`, `kr`, `kr_lng`, `kr_yd`, `kr_ypa`, `off_snp`, `pass_rush_yd`, `penalty`, `penalty_yd`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pts_half_ppr`, `pts_idp`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_air_yd`, `rec_drop`, `rec_fd`, `rec_lng`, `rec_rz_tgt`, `rec_td`, `rec_td_lng`, `rec_tgt`, `rec_yar`, `rec_yd`, `rec_ypr`, `rec_ypt`, `rush_2pt`, `rush_40p`, `rush_att`, `rush_btkl`, `rush_fd`, `rush_lng`, `rush_rec_yd`, `rush_rz_att`, `rush_td`, `rush_td_40p`, `rush_td_50p`, `rush_td_lng`, `rush_tkl_loss`, `rush_tkl_loss_yd`, `rush_yac`, `rush_yd`, `rush_ypa`, `st_snp`, `st_tkl_solo`, `tm_def_snp`, `tm_off_snp`, `tm_st_snp` | `adp_dd_ppr`, `bonus_rec_rb`, `def_fum_td`, `def_kr_td`, `def_kr_yd`, `fum`, `fum_lost`, `gp`, `pos_adp_dd_ppr`, `pr`, `pr_yd`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_2pt`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_fd`, `rec_td`, `rec_tgt`, `rec_yd`, `rush_2pt`, `rush_40p`, `rush_att`, `rush_fd`, `rush_td`, `rush_yd` |
| WR | `anytime_tds`, `bonus_fd_wr`, `bonus_rec_wr`, `bonus_rec_yd_100`, `bonus_rush_rec_yd_100`, `cmp_pct`, `def_snp`, `first_td`, `fum`, `fum_lost`, `gms_active`, `gp`, `gs`, `idp_blk_kick`, `idp_int`, `idp_pass_def`, `idp_tkl`, `idp_tkl_ast`, `idp_tkl_solo`, `kr`, `kr_lng`, `kr_yd`, `kr_ypa`, `off_snp`, `pass_air_yd`, `pass_att`, `pass_cmp`, `pass_fd`, `pass_inc`, `pass_lng`, `pass_rtg`, `pass_rush_yd`, `pass_yd`, `pass_ypa`, `pass_ypc`, `penalty`, `penalty_yd`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pr`, `pr_lng`, `pr_yd`, `pr_ypa`, `pts_half_ppr`, `pts_idp`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_2pt`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_air_yd`, `rec_drop`, `rec_fd`, `rec_lng`, `rec_rz_tgt`, `rec_td`, `rec_td_40p`, `rec_td_50p`, `rec_td_lng`, `rec_tgt`, `rec_yar`, `rec_yd`, `rec_ypr`, `rec_ypt`, `rush_att`, `rush_btkl`, `rush_fd`, `rush_lng`, `rush_rec_yd`, `rush_rz_att`, `rush_td`, `rush_td_lng`, `rush_tkl_loss`, `rush_tkl_loss_yd`, `rush_yac`, `rush_yd`, `rush_ypa`, `st_fum_rec`, `st_snp`, `st_td`, `st_tkl_solo`, `tm_def_snp`, `tm_off_snp`, `tm_st_snp` | `adp_dd_ppr`, `bonus_rec_wr`, `def_fum_td`, `def_kr_td`, `def_kr_yd`, `fum`, `fum_lost`, `gp`, `pos_adp_dd_ppr`, `pr`, `pr_td`, `pr_yd`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_2pt`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_fd`, `rec_td`, `rec_tgt`, `rec_yd`, `rush_2pt`, `rush_40p`, `rush_att`, `rush_fd`, `rush_td`, `rush_yd` |
| TE | `anytime_tds`, `bonus_fd_te`, `bonus_rec_te`, `bonus_rec_yd_100`, `bonus_rush_rec_yd_100`, `def_snp`, `first_td`, `fum`, `fum_lost`, `gms_active`, `gp`, `gs`, `idp_ff`, `idp_tkl`, `idp_tkl_ast`, `idp_tkl_solo`, `off_snp`, `pass_rush_yd`, `penalty`, `penalty_yd`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pts_half_ppr`, `pts_idp`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_air_yd`, `rec_drop`, `rec_fd`, `rec_lng`, `rec_rz_tgt`, `rec_td`, `rec_td_40p`, `rec_td_lng`, `rec_tgt`, `rec_yar`, `rec_yd`, `rec_ypr`, `rec_ypt`, `rush_att`, `rush_fd`, `rush_lng`, `rush_rec_yd`, `rush_rz_att`, `rush_td`, `rush_td_lng`, `rush_yac`, `rush_yd`, `rush_ypa`, `st_ff`, `st_snp`, `st_tkl_solo`, `tm_def_snp`, `tm_off_snp`, `tm_st_snp` | `adp_dd_ppr`, `bonus_rec_te`, `def_fum_td`, `fum`, `fum_lost`, `gp`, `pos_adp_dd_ppr`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `rec`, `rec_0_4`, `rec_10_19`, `rec_20_29`, `rec_2pt`, `rec_30_39`, `rec_40p`, `rec_5_9`, `rec_fd`, `rec_td`, `rec_tgt`, `rec_yd`, `rush_2pt`, `rush_40p`, `rush_att`, `rush_fd`, `rush_td`, `rush_yd` |
| K | `fg_blkd`, `fga`, `fgm`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50_59`, `fgm_50p`, `fgm_60p`, `fgm_lng`, `fgm_pct`, `fgm_yds`, `fgm_yds_over_30`, `fgmiss`, `fgmiss_20_29`, `fgmiss_30_39`, `fgmiss_40_49`, `fgmiss_50_59`, `fgmiss_50p`, `fgmiss_60p`, `gms_active`, `gp`, `kick_pts`, `off_snp`, `penalty`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `st_snp`, `st_tkl_solo`, `tm_def_snp`, `tm_off_snp`, `tm_st_snp`, `xp_blkd`, `xpa`, `xpm`, `xpmiss` | `adp_dd_ppr`, `fga`, `fgm`, `fgm_0_19`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50p`, `fgm_yds`, `fgmiss_30_39`, `fgmiss_40_49`, `fgmiss_50p`, `gp`, `pos_adp_dd_ppr`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `xpa`, `xpm`, `xpmiss` |
| DEF | `blk_kick`, `def_3_and_out`, `def_4_and_stop`, `def_forced_punts`, `def_kr`, `def_kr_lng`, `def_kr_yd`, `def_kr_ypa`, `def_pass_def`, `def_pr`, `def_pr_lng`, `def_pr_yd`, `def_pr_ypa`, `def_st_ff`, `def_st_fum_rec`, `def_st_td`, `def_st_tkl_solo`, `def_td`, `fan_pts_allow`, `fan_pts_allow_def`, `fan_pts_allow_k`, `fan_pts_allow_qb`, `fan_pts_allow_rb`, `fan_pts_allow_te`, `fan_pts_allow_wr`, `ff`, `ff_misc`, `fg_blkd`, `fum_rec`, `fum_ret_yd`, `gp`, `int`, `int_ret_yd`, `penalty`, `penalty_yd`, `pos_rank_half_ppr`, `pos_rank_ppr`, `pos_rank_std`, `pts_allow`, `pts_allow_14_20`, `pts_allow_1_6`, `pts_allow_21_27`, `pts_allow_28_34`, `pts_allow_35p`, `pts_allow_7_13`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `qb_hit`, `sack`, `sack_yd`, `safe`, `td`, `tkl`, `tkl_ast`, `tkl_ast_misc`, `tkl_loss`, `tkl_solo`, `tkl_solo_misc`, `yds_allow`, `yds_allow_100_199`, `yds_allow_200_299`, `yds_allow_300_349`, `yds_allow_350_399`, `yds_allow_400_449`, `yds_allow_450_499`, `yds_allow_500_549`, `yds_allow_550p` | `adp_dd_ppr`, `blk_kick`, `def_fum_td`, `def_kr_td`, `def_kr_yd`, `def_pr_td`, `def_pr_yd`, `def_td`, `ff`, `fum_rec`, `gp`, `int`, `pass_int_td`, `pos_adp_dd_ppr`, `pr_td`, `pr_yd`, `pts_allow`, `pts_allow_14_20`, `pts_allow_21_27`, `pts_allow_28_34`, `pts_half_ppr`, `pts_ppr`, `pts_std`, `sack`, `safe`, `st_td`, `tkl_loss`, `yds_allow`, `yds_allow_200_299`, `yds_allow_300_349`, `yds_allow_350_399`, `yds_allow_400_449` |

## Missing Enriched-Provider Signals

These should remain explicit missing-input outputs until a provider, schedule
loader, or local override source populates them:

- Routes, routes run, route participation, and route share.
- Snap share and position-specific snap share. Actual absolute snap counts exist
  for some rows. `off_snp / tm_off_snp` can be retained as an explicitly named
  actual snap-share proxy when both values are present, but it is not a
  position-specific share and projections generally lack the denominator.
- Target share, carry share, rush share, red-zone share, and goal-line share.
- Air-yards share and a team air-yards denominator. Actual `rec_air_yd` is
  absolute receiver air yards; actual `pass_air_yd` is absolute passer air
  yards. Neither is air-yard share.
- True pressure rate, pressures allowed, pass-block quality, pass-rush win rate,
  and opponent offensive-line context.
- Weather, wind, stadium/roof/surface, temperature, precipitation.
- Betting context: implied team total, game total, spread, moneyline.
- NFL schedule table with home/away, kickoff time, byes, and opponent joins.
- Historical depth chart snapshots and historical injury/availability snapshots.

## Computability Tier Definitions

| Tier | Meaning |
|---|---|
| `computed` | Sleeper-normalized data has enough fields to compute the reason deterministically for the stated position and source window. The row must name whether that window is actual, projection, or a same-week delta. |
| `partial` | A useful proxy can be computed from available fields, but an important desired input is missing, denominator quality is not guaranteed, or the signal is only available in one source window (usually actuals, not projections). |
| `not_evaluable_missing_source` | The reason depends on a source the repo does not currently sync or type. Emit an explicit missing-input reason instead of inferring. |
| `current_metadata_only` | The reason can only use the current `players` metadata snapshot (`status`, injury fields, depth chart, team/position, bye when present). It must not be used for a field preserved in a week-specific `player_week_rows.raw_json` row. |

## Planned Reason-Code Computability Matrix

The position column makes the planned position-specific coverage explicit. A
row marked `actual-only` must not be presented as projection evidence.

| Reason code | Position | Tier | Window/source | Notes |
|---|---|---|---|---|
| `role_change_recent` | all | partial | actual weekly trends | Raw volume change only; no historical depth, route, or share denominator. |
| `role_loss_recent` | all | partial | actual weekly trends | Declining raw volume cannot prove demotion, committee change, snap-share loss, or route loss. |
| `projection_lagging_role_change` | all | partial | same-week actual/projection delta | Uses shared attempts, targets, receptions, and points; no route/snap/share context. |
| `season_average_stale` | all | computed | actual weekly rows; projection version projection-only | Never mix forecast rows into completed-season averages. |
| `recent_spike_against_stable_usage` | all | partial | actual weekly rows | Points plus raw usage; stable snap/route/share is unavailable. |
| `insufficient_post_change_sample` | all | computed | actual week counts | A projection change is not an actual post-change sample. |
| `low_touch_big_points` | RB/WR/TE | computed | actual weekly rows | `rush_att + rec` is a touch proxy, not opportunity share. |
| `low_target_big_points` | RB/WR/TE | computed | actual weekly rows | `rec_tgt` is available where present; target share is missing. |
| `low_route_big_points` | WR/TE | not_evaluable_missing_source | missing source | No route or route-share keys are synced/observed. |
| `td_only_low_usage` | QB/RB/WR/TE | partial | actual weekly rows; projection variant projection-only | Position-specific TD keys plus raw usage; low raw count does not establish low opportunity/share. |
| `qb_low_rush_floor` | QB | computed | actual or projection window | `rush_att`, `rush_yd`, and `rush_td` are direct fields; this is not rushing share. |
| `rb_workload_change` | RB | partial | actual weekly trends | `rush_att`, `rec_tgt`, and `rec` are workload proxies; carry/target share is missing. |
| `rb_receiving_role_change` | RB | partial | actual weekly trends | Receiving volume only; no route or target share. |
| `rb_efficiency_spike` | RB | partial | actual weekly rows | `rush_ypa`, `rush_yac`, and explosive buckets exist, but sample/opportunity context is limited. |
| `wr_target_role_change` | WR | partial | actual weekly trends | `rec_tgt`/`rec` trend; no target, route, snap, or air-yard share. |
| `wr_air_yard_opportunity` | WR | partial | actual-only | `rec_air_yd` is absolute receiving air yards; air-yard share and team denominator are missing. |
| `wr_td_only_low_usage` | WR | partial | actual weekly rows | TDs against raw targets/touches; no route or opportunity-share conclusion. |
| `te_target_role_change` | TE | partial | actual weekly trends | `rec_tgt`/`rec` trend; no route or target share. |
| `te_air_yard_opportunity` | TE | partial | actual-only | `rec_air_yd` is absolute receiving air yards; no air-yard share or projection field. |
| `te_td_only_low_usage` | TE | partial | actual weekly rows | TDs against raw targets/touches; no route or snap-share conclusion. |
| `k_volume_change` | K | computed | actual or projection window | `fga`, `fgm`, `xpa`, and `xpm` are direct fields. |
| `long_kick_spike` | K | computed | actual or projection window | Distance buckets and `fgm_lng`/`fgm_50p` are available; projections are coarser. |
| `def_td_spike` | DEF | computed | actual or projection window | Defensive/special-teams TD keys are directly visible. |
| `def_turnover_spike_without_pressure` | DEF | partial | actual weekly rows | Turnovers plus sack/qb-hit/tackle-loss proxies; true pressure rate and matchup inputs are missing. |
| `def_pass_rush_disruption` | DEF | partial | actual-only; projection version projection-only | Actual events exist; projections lack `qb_hit` and do not establish pressure rate. |
| `matchup_opponent_label_present` | all | computed | actual or projection weekly raw row | `raw_json.opponent` is week-specific raw input, not current metadata or a strength grade. |
| `matchup_strength_missing_source` | all | not_evaluable_missing_source | missing source | No defensive strength, positional allowance, pace, pass funnel, or run funnel source. |
| `matchup_weather_missing_source` | all | not_evaluable_missing_source | missing source | No weather/stadium source. |
| `matchup_implied_total_missing_source` | all | not_evaluable_missing_source | missing source | No game total, spread, moneyline, or team implied total. |
| `matchup_pressure_missing_source` | QB/WR/TE/DEF | not_evaluable_missing_source | missing source | Actual `qb_hit`/`sack` are outcomes, not pre-game matchup inputs. |
| `depth_chart_current_only` | all | current_metadata_only | current `players` snapshot | Historical role/depth changes cannot be proven. |
| `injury_current_only` | all | current_metadata_only | current `players` snapshot | Historical injury snapshots are not stored per week. |

### Exact Position Rule Codes From The Plan

The following planned codes are deliberately listed even when the current
source audit can only classify them as partial or missing. This prevents a
future implementation from silently collapsing a position-specific rule into a
generic points trend:

| Position | Planned codes | Current audit result |
|---|---|---|
| QB | `qb_volume_rise`, `qb_rushing_role_rise`, `pass_td_spike_low_volume`, `rushing_td_spike`, `garbage_time_unconfirmed` | volume/rush/TD proxies are partial or computed in actual/projection windows; garbage-time context is `not_evaluable_missing_source` |
| RB | `rb_touch_share_rise`, `rb_receiving_role_rise`, `injury_replacement`, `long_run_spike_unconfirmed` | raw touch/receiving trends are partial; injury replacement is `current_metadata_only` unless weekly availability/depth evidence exists; long-run spike is partial |
| WR | `wr_target_role_rise`, `wr_depth_chart_breakout`, `low_target_big_points`, `long_td_spike`, `multi_td_low_volume` | target/TD/raw-point proxies are partial; depth-chart portion is `current_metadata_only` |
| TE | `te_target_role_rise`, `te_red_zone_role_rise`, `te_td_only_week`, `low_route_big_points`, `not_evaluable_missing_routes` | target/red-zone/TD proxies are partial; route rule is explicitly missing without route data |
| K | `k_team_opportunity_rise`, `long_kick_spike`, `low_attempt_kicker_spike` | kick-volume and distance fields are computed in each stated window; environment remains missing |
| DEF | `def_pressure_rise`, `def_points_allowed_improving`, `def_td_spike`, `turnover_spike_without_pressure`, `score_dependent_def_week` | historical event proxies are partial or computed; true pressure/matchup context is missing |
| Cross-position one-off/context | `td_spike_low_usage`, `injury_replacement`, `low_route_big_points`, `low_touch_big_points`, `turnover_td_spike`, `projection_disagreement`, `depth_chart_disagreement`, `season_average_stale`, `change_point_unconfirmed`, `not_evaluable_missing_nfl_schedule` | use the more specific position rows above; missing inputs must remain explicit reason codes |

## V1 Implementation Guidance

- Use `player_week_stat_values` for all stat-key computations and trends.
- Use `player_week_rows.raw_json` only when a signal is preserved by Sleeper but
  not typed yet, such as raw `opponent`, `game_id`, or `date`.
- Label weekly raw fields as weekly inputs, not current metadata. In particular,
  raw `opponent`, `game_id`, and `date` can support presence/identity checks but
  cannot support matchup quality, home/away, or schedule joins without a
  schedule provider.
- Emit missing-input reason codes for routes, route share, snap share, target
  share, carry share, red-zone share, pressure, weather, implied totals, and
  matchup strength. The plan's `not_evaluable_missing_routes` and
  `not_evaluable_missing_nfl_schedule` names should be preserved in emitted
  output rather than replaced by a generic missing-source label.
- Keep `off_snp`, `st_snp`, and `def_snp` as absolute snap counts. If a future
  implementation divides `off_snp` by `tm_off_snp`, name the result an
  `actual_snap_share_proxy` and keep it separate from position-specific snap
  share.
- Keep `rec_air_yd` and `pass_air_yd` as absolute air-yard fields. Do not call
  either one air-yard share without a validated team denominator.
- Treat depth chart, injury, bye, and status as `current_metadata_only` unless
  a future issue adds per-week snapshots.
- Prefer position-specific proxies:
  - QB: passing volume, rushing floor, interceptions/sacks/fumbles.
  - RB: carries plus targets/receptions, TD and fumble dependency.
  - WR/TE: targets/receptions plus TD dependency; no route claims.
  - K: attempts, makes, misses, distance buckets; no weather/total claims.
  - DEF: sacks, turnovers, TDs, points/yardage allowed; no pressure-matchup
    claims.

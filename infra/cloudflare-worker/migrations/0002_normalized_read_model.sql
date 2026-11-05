-- Normalized D1 read model populated by the scheduled() handler (issue #19).
-- Mirrors the Python SQLite store concepts (python/src/sleeper_tooling/db.py:
-- player_week_rows, players, league_settings, league_users, rosters,
-- sync_runs, provider_sync_metadata) with Worker-specific choices sized for the
-- D1 Free plan (100k rows written/day, index writes count):
--
-- * player_week_rows keeps one row per Sleeper stats/projections row per
--   (source, season, week, position) slice, matching the Worker's per-position
--   `position[]` requests. Python's per-stat player_week_stat_values table is
--   not ported (it would multiply daily row writes by ~30).
-- * Players are a slim snapshot stored as a few JSON chunks, not 12k rows.
-- * sync_datasets is the freshness ledger tools consult before trusting a
--   normalized read; anything missing or stale falls back to Sleeper.
--
-- Additive only (see 0001 for the expand/contract rule).

CREATE TABLE IF NOT EXISTS sync_datasets (
  dataset TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  season INTEGER,
  week INTEGER,
  position TEXT,
  fetched_at INTEGER NOT NULL,
  row_count INTEGER NOT NULL DEFAULT 0,
  payload_hash TEXT,
  complete INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (dataset, scope_key)
);

CREATE TABLE IF NOT EXISTS player_week_rows (
  source TEXT NOT NULL,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  position TEXT NOT NULL,
  player_id TEXT NOT NULL,
  row_index INTEGER NOT NULL,
  player_name TEXT,
  team TEXT,
  sleeper_points REAL,
  raw_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (source, season, week, position, player_id)
);

-- Per-player lookups serve usage context, which reads completed-week stats
-- only. A partial index keeps projection writes (the largest writer) at one
-- row written per row.
CREATE INDEX IF NOT EXISTS idx_player_week_rows_stats_player
  ON player_week_rows (player_id, season, week)
  WHERE source = 'stats';

CREATE TABLE IF NOT EXISTS player_snapshot_chunks (
  snapshot_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  player_count INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS league_settings (
  league_id TEXT PRIMARY KEY,
  name TEXT,
  season INTEGER,
  status TEXT,
  scoring_settings_json TEXT NOT NULL DEFAULT '{}',
  roster_positions_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS league_users (
  league_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  row_index INTEGER NOT NULL,
  display_name TEXT,
  raw_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS rosters (
  league_id TEXT NOT NULL,
  roster_id TEXT NOT NULL,
  row_index INTEGER NOT NULL,
  owner_id TEXT,
  raw_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (
  sync_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,
  league_id TEXT,
  season INTEGER,
  week INTEGER,
  status TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  row_counts_json TEXT NOT NULL DEFAULT '{}',
  error_text TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_started_at
  ON sync_runs (started_at);

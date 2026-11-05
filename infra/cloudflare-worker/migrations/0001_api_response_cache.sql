-- Raw Sleeper response cache (fetch/provenance layer).
--
-- Production already has this table: earlier Worker versions created it at
-- request time. Keep `IF NOT EXISTS` so the first `migrations apply` is a
-- no-op for the table and only adds the eviction index.
--
-- Migration rule: expand/contract only. A migration may add tables, columns
-- with defaults, or indexes. It must not drop, rename, or retype anything the
-- previous Worker version still reads, so `make worker-rollback` stays safe
-- after `make worker-d1-migrate`. Contract (drop) in a later migration, after
-- no deployed version reads the old shape.

CREATE TABLE IF NOT EXISTS api_response_cache (
  cache_key TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  response_json TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_response_cache_expires_at
  ON api_response_cache (expires_at);

// Normalized D1 read model (issue #19). The scheduled handler (sync.ts) fills
// the tables created by migrations/0002_normalized_read_model.sql; tool calls
// read them here when the sync_datasets ledger says a dataset is fresh and fall
// back to Sleeper (through the raw response cache) otherwise.
//
// Every normalized read returns the same JSON shape the Sleeper endpoint
// returns, so tool output contracts do not depend on which path served them.
// Any D1 error (unmigrated database, a rollback to an older schema, a test
// fake without `.all()`) counts as "not fresh" and falls back.
//
// D1 queries count toward the Workers subrequest limit (50 per invocation on
// the Free plan, https://developers.cloudflare.com/workers/platform/limits/),
// so reads are bulk: one ledger read per tool call, one query per group of
// slices, and one batched query per usage-context pass.

import { type JsonMap, objectValue } from "./shared.ts";

export type StoreEnv = { SLEEPER_CACHE_DB?: D1Database };

export type LedgerRow = {
  dataset: string;
  scope_key: string;
  season: number | null;
  week: number | null;
  position: string | null;
  fetched_at: number;
  row_count: number;
  payload_hash: string | null;
  complete: number;
  metadata_json: string;
};

export type Ledger = Map<string, LedgerRow>;

// Read-side freshness. The cron refreshes well inside these windows
// (see SYNC_REFRESH_AFTER_SECONDS in sync.ts); the slack covers a missed tick.
export const READ_MAX_AGE_SECONDS: Record<string, number> = {
  players: 8 * 3600,
  league_settings: 36 * 3600,
  league_users: 36 * 3600,
  rosters: 3600,
  projections: 8 * 3600,
  stats: 3 * 3600
};
// A week's slice synced after the week ended only changes for stat corrections.
export const COMPLETE_SLICE_MAX_AGE_SECONDS = 14 * 86400;

const APP_ORIGIN_PATH = "https://api.sleeper.app/v1";
const DATA_ORIGIN = "https://api.sleeper.com";
const MAX_IDS_PER_STATEMENT = 90;

class RequestScope {
  ledger?: Promise<Ledger | undefined>;
  players?: Promise<Record<string, JsonMap>>;
  slices = new Map<string, JsonMap[]>();
  normalizedDatasets = new Map<string, number>();
  fallbackReads = 0;
}

const scopes = new WeakMap<object, RequestScope>();

// One scope per tool call (decision_smoke_report sub-calls share it), so the
// ledger, players map, and prefetched slices are read once per invocation.
export function withRequestScope<T extends object>(env: T): T {
  const scoped = { ...env };
  scopes.set(scoped, new RequestScope());
  return scoped;
}

function scopeFor(env: object): RequestScope | undefined {
  return scopes.get(env);
}

export function noteSleeperRead(env: object): void {
  const scope = scopeFor(env);
  if (scope) scope.fallbackReads += 1;
}

function noteNormalized(scope: RequestScope, dataset: string, fetchedAt: number): void {
  const previous = scope.normalizedDatasets.get(dataset);
  scope.normalizedDatasets.set(dataset, previous === undefined ? fetchedAt : Math.min(previous, fetchedAt));
}

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

export function ledgerKey(dataset: string, scopeKey: string): string {
  return `${dataset}|${scopeKey}`;
}

export function sliceScopeKey(season: number, week: number, position: string): string {
  return `${season}:${week}:${position}`;
}

export function isLedgerRowFresh(row: LedgerRow | undefined, now = nowSeconds()): boolean {
  if (!row) return false;
  const maxAge = row.complete ? COMPLETE_SLICE_MAX_AGE_SECONDS : READ_MAX_AGE_SECONDS[row.dataset];
  return maxAge !== undefined && now - Number(row.fetched_at) <= maxAge;
}

export async function readLedger(db: D1Database): Promise<Ledger> {
  const result = await db.prepare(
    "SELECT dataset, scope_key, season, week, position, fetched_at, row_count, payload_hash, complete, metadata_json FROM sync_datasets"
  ).all<LedgerRow>();
  const ledger: Ledger = new Map();
  for (const row of result.results || []) {
    ledger.set(ledgerKey(row.dataset, row.scope_key), row);
  }
  return ledger;
}

async function scopedLedger(env: StoreEnv): Promise<[RequestScope, D1Database, Ledger] | undefined> {
  const scope = scopeFor(env);
  const db = env.SLEEPER_CACHE_DB;
  if (!scope || !db) return undefined;
  if (!scope.ledger) {
    scope.ledger = readLedger(db).catch(() => undefined);
  }
  const ledger = await scope.ledger;
  return ledger ? [scope, db, ledger] : undefined;
}

function freshEntry(ledger: Ledger, dataset: string, scopeKey: string): LedgerRow | undefined {
  const row = ledger.get(ledgerKey(dataset, scopeKey));
  return isLedgerRowFresh(row) ? row : undefined;
}

function sliceKey(source: string, season: number, week: number, position: string): string {
  return `${source}:${sliceScopeKey(season, week, position)}`;
}

// Load every fresh (week, position) slice in one query and keep it on the
// request scope; per-slice reads (normalizedJson) are then served from memory.
export async function prefetchSlices(env: StoreEnv, source: string, season: number, weeks: number[], positions: string[]): Promise<void> {
  const context = await scopedLedger(env);
  if (!context) return;
  const [scope, db, ledger] = context;
  const needed: Array<[number, string, LedgerRow]> = [];
  for (const week of new Set(weeks)) {
    for (const position of new Set(positions)) {
      const entry = freshEntry(ledger, source, sliceScopeKey(season, week, position));
      if (entry && !scope.slices.has(sliceKey(source, season, week, position))) needed.push([week, position, entry]);
    }
  }
  if (!needed.length) return;
  const neededWeeks = [...new Set(needed.map(([week]) => week))];
  const neededPositions = [...new Set(needed.map(([, position]) => position))];
  try {
    const result = await db.prepare(
      `SELECT week, position, raw_json FROM player_week_rows WHERE source = ? AND season = ? AND week IN (${placeholders(neededWeeks.length)}) AND position IN (${placeholders(neededPositions.length)}) ORDER BY week, position, row_index`
    ).bind(source, season, ...neededWeeks, ...neededPositions).all<{ week: number; position: string; raw_json: string }>();
    const grouped = new Map<string, JsonMap[]>();
    for (const [week, position] of needed) grouped.set(sliceKey(source, season, week, position), []);
    for (const row of result.results || []) {
      grouped.get(sliceKey(source, season, Number(row.week), String(row.position)))?.push(JSON.parse(row.raw_json));
    }
    for (const [key, rows] of grouped) scope.slices.set(key, rows);
    for (const [, , entry] of needed) noteNormalized(scope, source, Number(entry.fetched_at));
  } catch {
    // Fall back to Sleeper for these slices.
  }
}

// Serve a Sleeper URL from the normalized tables, or return undefined so the
// caller falls back to the raw response cache and Sleeper.
export async function normalizedJson(url: URL, env: StoreEnv): Promise<unknown | undefined> {
  const raw = url.toString();
  if (raw.startsWith(`${APP_ORIGIN_PATH}/`)) {
    const path = raw.slice(APP_ORIGIN_PATH.length);
    if (path === "/players/nfl") return readPlayers(env);
    const league = path.match(/^\/league\/(\d+)(\/users|\/rosters)?$/);
    if (league) {
      const dataset = league[2] === "/users" ? "league_users" : league[2] === "/rosters" ? "rosters" : "league_settings";
      return readLeague(env, league[1], dataset);
    }
    return undefined;
  }
  if (url.origin === DATA_ORIGIN) {
    const match = url.pathname.match(/^\/(stats|projections)\/nfl\/(\d{4})\/(\d{1,2})$/);
    if (!match) return undefined;
    const params = [...url.searchParams.entries()];
    const keys = params.map(([key]) => key).sort().join(",");
    if (url.searchParams.get("season_type") !== "regular") return undefined;
    if (url.searchParams.has("order_by") && url.searchParams.get("order_by") !== "pts_ppr") return undefined;
    if (keys !== "position[],season_type" && keys !== "order_by,position[],season_type") return undefined;
    const position = String(url.searchParams.get("position[]"));
    const [source, season, week] = [match[1], Number(match[2]), Number(match[3])];
    const key = sliceKey(source, season, week, position);
    const scope = scopeFor(env);
    if (!scope?.slices.has(key)) await prefetchSlices(env, source, season, [week], [position]);
    return scopeFor(env)?.slices.get(key);
  }
  return undefined;
}

async function readPlayers(env: StoreEnv): Promise<unknown | undefined> {
  const context = await scopedLedger(env);
  if (!context) return undefined;
  const [scope, db, ledger] = context;
  const entry = freshEntry(ledger, "players", "nfl");
  if (!entry) return undefined;
  const snapshotId = String(objectValue(safeJson(entry.metadata_json)).snapshot_id || "");
  if (!snapshotId) return undefined;
  try {
    const result = await db.prepare(
      "SELECT payload_json FROM player_snapshot_chunks WHERE snapshot_id = ? ORDER BY chunk_index"
    ).bind(snapshotId).all<{ payload_json: string }>();
    const chunks = result.results || [];
    if (!chunks.length) return undefined;
    const players: Record<string, JsonMap> = {};
    for (const chunk of chunks) Object.assign(players, JSON.parse(chunk.payload_json));
    noteNormalized(scope, "players", Number(entry.fetched_at));
    return players;
  } catch {
    return undefined;
  }
}

async function readLeague(env: StoreEnv, leagueId: string, dataset: string): Promise<unknown | undefined> {
  const context = await scopedLedger(env);
  if (!context) return undefined;
  const [scope, db, ledger] = context;
  const entry = freshEntry(ledger, dataset, leagueId);
  if (!entry) return undefined;
  try {
    let payload: unknown;
    if (dataset === "league_settings") {
      const row = await db.prepare("SELECT raw_json FROM league_settings WHERE league_id = ?").bind(leagueId).first<{ raw_json: string }>();
      if (!row) return undefined;
      payload = JSON.parse(row.raw_json);
    } else {
      const table = dataset === "league_users" ? "league_users" : "rosters";
      const result = await db.prepare(`SELECT raw_json FROM ${table} WHERE league_id = ? ORDER BY row_index`).bind(leagueId).all<{ raw_json: string }>();
      payload = (result.results || []).map((row) => JSON.parse(row.raw_json));
    }
    noteNormalized(scope, dataset, Number(entry.fetched_at));
    return payload;
  } catch {
    return undefined;
  }
}

// Players are read at most once per tool call.
export function memoizedPlayers(env: object, load: () => Promise<Record<string, JsonMap>>): Promise<Record<string, JsonMap>> {
  const scope = scopeFor(env);
  if (!scope) return load();
  if (!scope.players) scope.players = load();
  return scope.players;
}

export type PlayerStatRows = { fresh: Set<string>; rows: Map<string, JsonMap> };

// Usage context needs only the evaluated players' own rows, so it returns them
// by player_id instead of shipping whole (week, position) slices.
// load() calls made in the same tick are coalesced into one batched D1 query.
// Keys are `${week}:${position}`; `fresh` lists the slices the ledger trusts
// (a fresh slice without a row for the player means the player had no row).
export function createStatRowsLoader(env: StoreEnv, season: number, throughWeek: number): (playerId: string) => Promise<PlayerStatRows | undefined> {
  let pending: Map<string, Array<(value: PlayerStatRows | undefined) => void>> | undefined;
  const flush = async (batch: Map<string, Array<(value: PlayerStatRows | undefined) => void>>) => {
    const resolveAll = (value: (playerId: string) => PlayerStatRows | undefined) => {
      for (const [playerId, resolvers] of batch) for (const resolve of resolvers) resolve(value(playerId));
    };
    const context = throughWeek >= 1 ? await scopedLedger(env) : undefined;
    if (!context) return resolveAll(() => undefined);
    const [scope, db, ledger] = context;
    const fresh = new Set<string>();
    let oldest: number | undefined;
    for (const entry of ledger.values()) {
      if (entry.dataset !== "stats" || Number(entry.season) !== season || Number(entry.week) > throughWeek || !isLedgerRowFresh(entry)) continue;
      fresh.add(`${entry.week}:${entry.position}`);
      oldest = oldest === undefined ? Number(entry.fetched_at) : Math.min(oldest, Number(entry.fetched_at));
    }
    if (!fresh.size) return resolveAll(() => undefined);
    const ids = [...batch.keys()];
    const statements: D1PreparedStatement[] = [];
    for (let index = 0; index < ids.length; index += MAX_IDS_PER_STATEMENT) {
      const chunk = ids.slice(index, index + MAX_IDS_PER_STATEMENT);
      statements.push(db.prepare(
        `SELECT week, position, player_id, raw_json FROM player_week_rows WHERE source = 'stats' AND season = ? AND week >= 1 AND week <= ? AND player_id IN (${placeholders(chunk.length)})`
      ).bind(season, throughWeek, ...chunk));
    }
    try {
      const results = await db.batch<{ week: number; position: string; player_id: string; raw_json: string }>(statements);
      const byPlayer = new Map<string, Map<string, JsonMap>>();
      for (const result of results) {
        for (const row of result.results || []) {
          const rows = byPlayer.get(String(row.player_id)) || new Map<string, JsonMap>();
          rows.set(`${row.week}:${row.position}`, JSON.parse(row.raw_json));
          byPlayer.set(String(row.player_id), rows);
        }
      }
      if (oldest !== undefined) noteNormalized(scope, "stats", oldest);
      resolveAll((playerId) => ({ fresh, rows: byPlayer.get(playerId) || new Map() }));
    } catch {
      resolveAll(() => undefined);
    }
  };
  return (playerId: string) => new Promise((resolve) => {
    if (!pending) {
      const batch = new Map<string, Array<(value: PlayerStatRows | undefined) => void>>();
      pending = batch;
      queueMicrotask(() => {
        pending = undefined;
        void flush(batch);
      });
    }
    const resolvers = pending.get(playerId) || [];
    resolvers.push(resolve);
    pending.set(playerId, resolvers);
  });
}

// Freshness metadata attached to decision tool results as `data_freshness`.
export function freshnessSummary(env: object): JsonMap | undefined {
  const scope = scopeFor(env);
  if (!scope) return undefined;
  const datasets = [...scope.normalizedDatasets.keys()].sort();
  const oldest = datasets.length ? Math.min(...scope.normalizedDatasets.values()) : undefined;
  const readModel = datasets.length
    ? (scope.fallbackReads ? "d1_normalized_with_sleeper_fallback" : "d1_normalized")
    : "sleeper_fallback";
  return {
    read_model: readModel,
    normalized_datasets: datasets,
    oldest_normalized_sync_at: oldest === undefined ? null : new Date(oldest * 1000).toISOString(),
    sleeper_fallback_reads: scope.fallbackReads,
    note: "normalized datasets come from the scheduled D1 sync; other reads use the raw response cache or live Sleeper"
  };
}

export function placeholders(count: number): string {
  return Array.from({ length: count }, () => "?").join(", ");
}

function safeJson(value: string | null | undefined): unknown {
  try {
    return value ? JSON.parse(value) : {};
  } catch {
    return {};
  }
}

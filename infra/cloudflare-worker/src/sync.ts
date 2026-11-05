// Scheduled D1 sync (issue #19). Runs only from the Worker's scheduled()
// handler (Cron Triggers in wrangler.toml); fetch() never routes here.
//
// Budget. Workers allow 50 subrequests per invocation on the Free plan, and
// fetch(), D1 queries, and Cache API calls all count
// (https://developers.cloudflare.com/workers/platform/limits/). A db.batch()
// counts as one D1 query (https://developers.cloudflare.com/d1/platform/limits/).
// Each tick therefore syncs a bounded slice of work:
//
//   1 ledger read + (0 or 1) /state/nfl fetch
//   + per task: its Sleeper fetches + 1 D1 batch write
//   + 1 final D1 batch (sync_runs row, ledger, cache eviction)
//
// With the default of 6 fetches per tick that is at most 2 + 6 + 6 + 1 = 15
// subrequests; SYNC_MAX_FETCHES_PER_TICK is clamped to 20 (at most 43), and a
// hard guard stops before SUBREQUEST_BUDGET. Free-plan cron CPU is 10 ms; the
// players refresh parses a ~15 MB payload and runs on its own cron.
//
// D1 Free also caps rows written at 100k/day (index rows count). Slices whose
// payload hash is unchanged only touch their ledger row, and row upserts skip
// rows whose JSON did not change.

import { type JsonMap, arrayValue, objectValue } from "./shared.ts";
import { type Ledger, type LedgerRow, ledgerKey, nowSeconds, readLedger, sliceScopeKey } from "./d1-store.ts";
import { APP_BASE_URL, DATA_BASE_URL } from "./response-cache.ts";

export type SyncEnv = {
  SLEEPER_CACHE_DB?: D1Database;
  SLEEPER_DEFAULT_LEAGUE_ID?: string;
  SYNC_MAX_FETCHES_PER_TICK?: string;
};

export const SYNC_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];
export const MAIN_CRON = "*/15 * * * *";
export const PLAYERS_CRON = "23 */6 * * *";
export const SUBREQUEST_BUDGET = 45;
export const DEFAULT_MAX_FETCHES_PER_TICK = 6;
export const MAX_FETCHES_PER_TICK_CAP = 20;
const REGULAR_SEASON_WEEKS = 18;
const MAX_JSON_PARAM_BYTES = 1_500_000;
const CACHE_ROW_CAP = 2000;
const EVICT_BATCH = 500;
const SYNC_RUN_RETENTION_SECONDS = 14 * 86400;

// Write-side refresh cadence (read-side tolerance is READ_MAX_AGE_SECONDS).
export const SYNC_REFRESH_AFTER_SECONDS = {
  nfl_state: 3600,
  players: 5 * 3600,
  league_settings: 24 * 3600,
  rosters: 30 * 60,
  projections: 4 * 3600,
  stats: 2 * 3600,
  latest_completed_stats: 24 * 3600
};

// Player fields the Worker reads (grep index.ts / usage-context.ts). The slim
// snapshot keeps every player but only these keys; add a key here before a
// tool starts reading it.
export const PLAYER_SNAPSHOT_FIELDS = [
  "player_id", "full_name", "first_name", "last_name", "name", "team", "position", "fantasy_positions",
  "status", "injury_status", "active", "depth_chart_order", "depth_chart_position", "bye_week", "bye",
  "rostered_percent", "rostered_pct", "percent_rostered", "percent_owned", "owned_percent",
  "market_type", "acquisition_market", "acquisition_type", "waiver", "is_waiver"
];

export type SyncTask =
  | { kind: "slice"; source: "stats" | "projections"; season: number; week: number; position: string; complete: boolean }
  | { kind: "league_meta"; leagueId: string }
  | { kind: "rosters"; leagueId: string }
  | { kind: "players" };

type NflState = { season: number; seasonType: string; week: number };

type TaskResult = { task: string; status: "ok" | "unchanged" | "error"; rows?: number; error?: string };

export type SyncReport = {
  status: "ok" | "partial" | "error" | "skipped";
  cron: string;
  reason?: string;
  season?: number;
  week?: number;
  season_type?: string;
  max_fetches: number;
  fetches: number;
  d1_queries: number;
  subrequests: number;
  tasks: TaskResult[];
};

type Context = { db: D1Database; now: number; fetches: number; d1Queries: number };

export function maxFetchesPerTick(env: SyncEnv): number {
  const parsed = Number(env.SYNC_MAX_FETCHES_PER_TICK);
  if (!Number.isInteger(parsed) || parsed < 1) return DEFAULT_MAX_FETCHES_PER_TICK;
  return Math.min(parsed, MAX_FETCHES_PER_TICK_CAP);
}

export function taskCost(task: SyncTask): number {
  return task.kind === "league_meta" ? 2 : 1;
}

export function taskLabel(task: SyncTask): string {
  if (task.kind === "slice") return `${task.source}:${sliceScopeKey(task.season, task.week, task.position)}`;
  if (task.kind === "players") return "players";
  return `${task.kind}:${task.leagueId}`;
}

function age(row: LedgerRow | undefined, now: number): number {
  return row ? now - Number(row.fetched_at) : Number.POSITIVE_INFINITY;
}

// Regular-season weeks only: every tool reads season_type=regular.
export function weekWindow(state: NflState): { currentWeek: number | null; completedThrough: number } | undefined {
  if (state.seasonType === "regular") {
    const currentWeek = Math.max(1, Math.min(REGULAR_SEASON_WEEKS, Math.trunc(state.week) || 1));
    return { currentWeek, completedThrough: currentWeek - 1 };
  }
  if (state.seasonType === "post") return { currentWeek: null, completedThrough: REGULAR_SEASON_WEEKS };
  return undefined;
}

// Priority-ordered due work for one tick, trimmed to the fetch budget.
export function planSyncTasks(input: {
  ledger: Ledger;
  state: NflState | undefined;
  now: number;
  leagueId: string | undefined;
  maxFetches: number;
  cron: string;
}): SyncTask[] {
  const { ledger, now, leagueId } = input;
  const due: SyncTask[] = [];
  const entry = (dataset: string, scopeKey: string) => ledger.get(ledgerKey(dataset, scopeKey));
  if (input.cron === PLAYERS_CRON) {
    if (age(entry("players", "nfl"), now) > SYNC_REFRESH_AFTER_SECONDS.players) due.push({ kind: "players" });
    return due;
  }
  if (leagueId) {
    if (age(entry("rosters", leagueId), now) > SYNC_REFRESH_AFTER_SECONDS.rosters) due.push({ kind: "rosters", leagueId });
    if (Math.max(age(entry("league_settings", leagueId), now), age(entry("league_users", leagueId), now)) > SYNC_REFRESH_AFTER_SECONDS.league_settings) {
      due.push({ kind: "league_meta", leagueId });
    }
  }
  const window = input.state ? weekWindow(input.state) : undefined;
  if (input.state && window) {
    const season = input.state.season;
    const slice = (source: "stats" | "projections", week: number, complete: boolean): SyncTask[] =>
      SYNC_POSITIONS.map((position) => ({ kind: "slice", source, season, week, position, complete }));
    const sliceRow = (source: string, week: number, position: string) => entry(source, sliceScopeKey(season, week, position));
    if (window.currentWeek !== null) {
      const week = window.currentWeek;
      due.push(...slice("projections", week, false).filter((task) => task.kind === "slice" && age(sliceRow("projections", week, task.position), now) > SYNC_REFRESH_AFTER_SECONDS.projections));
      due.push(...slice("stats", week, false).filter((task) => task.kind === "slice" && age(sliceRow("stats", week, task.position), now) > SYNC_REFRESH_AFTER_SECONDS.stats));
    }
    for (let week = window.completedThrough; week >= 1; week -= 1) {
      due.push(...slice("stats", week, true).filter((task) => {
        if (task.kind !== "slice") return false;
        const row = sliceRow("stats", week, task.position);
        if (!row || !row.complete) return true;
        return week === window.completedThrough && age(row, now) > SYNC_REFRESH_AFTER_SECONDS.latest_completed_stats;
      }));
    }
    for (let week = window.completedThrough; week >= 1; week -= 1) {
      due.push(...slice("projections", week, true).filter((task) => {
        const row = task.kind === "slice" ? sliceRow("projections", week, task.position) : undefined;
        return !row || !row.complete;
      }));
    }
  }
  const planned: SyncTask[] = [];
  let fetches = 0;
  for (const task of due) {
    if (fetches + taskCost(task) > input.maxFetches) continue;
    planned.push(task);
    fetches += taskCost(task);
  }
  return planned;
}

export async function runScheduledSync(env: SyncEnv, options: { cron?: string; now?: number } = {}): Promise<SyncReport> {
  const cron = options.cron || MAIN_CRON;
  const maxFetches = maxFetchesPerTick(env);
  const db = env.SLEEPER_CACHE_DB;
  const report: SyncReport = { status: "skipped", cron, max_fetches: maxFetches, fetches: 0, d1_queries: 0, subrequests: 0, tasks: [] };
  if (!db) {
    report.reason = "no SLEEPER_CACHE_DB binding";
    return report;
  }
  const context: Context = { db, now: options.now ?? nowSeconds(), fetches: 0, d1Queries: 0 };
  const finish = (status: SyncReport["status"]): SyncReport => {
    report.status = status;
    report.fetches = context.fetches;
    report.d1_queries = context.d1Queries;
    report.subrequests = context.fetches + context.d1Queries;
    return report;
  };

  let ledger: Ledger;
  try {
    context.d1Queries += 1;
    ledger = await readLedger(db);
  } catch (error) {
    report.reason = `normalized tables unavailable (run make worker-d1-migrate): ${errorText(error)}`;
    return finish("error");
  }

  const leagueId = String(env.SLEEPER_DEFAULT_LEAGUE_ID || "").trim() || undefined;
  const finalStatements: D1PreparedStatement[] = [];
  let state: NflState | undefined;
  if (cron !== PLAYERS_CRON) {
    try {
      state = await nflState(context, ledger, finalStatements);
      report.season = state.season;
      report.week = state.week;
      report.season_type = state.seasonType;
    } catch (error) {
      report.tasks.push({ task: "nfl_state", status: "error", error: errorText(error) });
    }
  }

  const tasks = planSyncTasks({ ledger, state, now: context.now, leagueId, maxFetches, cron });
  for (const task of tasks) {
    // Guard: this task's fetches + its write batch + the final batch.
    if (context.fetches + context.d1Queries + taskCost(task) + 2 > SUBREQUEST_BUDGET) break;
    try {
      const outcome = await runTask(task, context, ledger);
      report.tasks.push({ task: taskLabel(task), ...outcome });
    } catch (error) {
      report.tasks.push({ task: taskLabel(task), status: "error", error: errorText(error) });
    }
  }

  const errors = report.tasks.filter((task) => task.status === "error");
  const status: SyncReport["status"] = errors.length === 0 ? "ok" : errors.length === report.tasks.length ? "error" : "partial";
  finalStatements.push(...maintenanceStatements(db, context.now, cron));
  finalStatements.push(db.prepare(
    "INSERT INTO sync_runs (target, league_id, season, week, status, started_at, finished_at, row_counts_json, error_text, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(
    "worker_scheduled_sync",
    leagueId ?? null,
    state?.season ?? null,
    state?.week ?? null,
    status,
    context.now,
    nowSeconds(),
    JSON.stringify(Object.fromEntries(report.tasks.filter((task) => task.rows !== undefined).map((task) => [task.task, task.rows]))),
    errors.length ? errors.map((task) => `${task.task}: ${task.error}`).join("; ").slice(0, 2000) : null,
    JSON.stringify({ cron, tasks: report.tasks.map((task) => `${task.task}:${task.status}`), fetches: context.fetches })
  ));
  try {
    context.d1Queries += 1;
    await db.batch(finalStatements);
  } catch (error) {
    report.tasks.push({ task: "finalize", status: "error", error: errorText(error) });
    return finish("error");
  }
  return finish(report.tasks.length === 0 ? "ok" : status);
}

async function nflState(context: Context, ledger: Ledger, finalStatements: D1PreparedStatement[]): Promise<NflState> {
  const row = ledger.get(ledgerKey("nfl_state", "nfl"));
  let payload: JsonMap;
  if (row && age(row, context.now) <= SYNC_REFRESH_AFTER_SECONDS.nfl_state) {
    payload = objectValue(JSON.parse(row.metadata_json || "{}"));
  } else {
    payload = objectValue(await fetchJson(context, `${APP_BASE_URL}/state/nfl`));
    finalStatements.push(ledgerUpsert(context.db, {
      dataset: "nfl_state", scopeKey: "nfl", season: Number(payload.season) || null, week: Number(payload.week) || null,
      position: null, fetchedAt: context.now, rowCount: 1, hash: null, complete: false, metadata: payload
    }));
  }
  const season = Number(payload.season || payload.league_season);
  if (!Number.isInteger(season) || season < 2000) throw new Error("Sleeper state has no season");
  return { season, seasonType: String(payload.season_type || ""), week: Number(payload.week) || 0 };
}

async function runTask(task: SyncTask, context: Context, ledger: Ledger): Promise<Omit<TaskResult, "task">> {
  if (task.kind === "slice") return runSliceTask(task, context, ledger);
  if (task.kind === "players") return runPlayersTask(context);
  if (task.kind === "rosters") return runRostersTask(task.leagueId, context);
  return runLeagueMetaTask(task.leagueId, context);
}

async function runSliceTask(task: Extract<SyncTask, { kind: "slice" }>, context: Context, ledger: Ledger): Promise<Omit<TaskResult, "task">> {
  const { db, now } = context;
  const url = `${DATA_BASE_URL}/${task.source}/nfl/${task.season}/${task.week}?season_type=regular&position%5B%5D=${task.position}&order_by=pts_ppr`;
  const text = await fetchText(context, url);
  const hash = await sha256(text);
  const scopeKey = sliceScopeKey(task.season, task.week, task.position);
  const previous = ledger.get(ledgerKey(task.source, scopeKey));
  const rows = previous?.payload_hash === hash ? undefined : arrayValue(JSON.parse(text));
  const statements: D1PreparedStatement[] = [];
  if (rows) {
    let offset = 0;
    for (const chunk of jsonChunks(rows)) {
      statements.push(db.prepare(
        `INSERT INTO player_week_rows (source, season, week, position, player_id, row_index, player_name, team, sleeper_points, raw_json, updated_at)
         SELECT ?1, ?2, ?3, ?4, CAST(json_extract(value, '$.player_id') AS TEXT), CAST(key AS INTEGER) + ?5,
           COALESCE(json_extract(value, '$.player.full_name'), TRIM(COALESCE(json_extract(value, '$.player.first_name'), '') || ' ' || COALESCE(json_extract(value, '$.player.last_name'), ''))),
           json_extract(value, '$.player.team'), json_extract(value, '$.stats.pts_ppr'), value, ?6
         FROM json_each(?7) WHERE json_extract(value, '$.player_id') IS NOT NULL
         ON CONFLICT (source, season, week, position, player_id) DO UPDATE SET
           row_index = excluded.row_index, player_name = excluded.player_name, team = excluded.team,
           sleeper_points = excluded.sleeper_points, raw_json = excluded.raw_json, updated_at = excluded.updated_at
         WHERE player_week_rows.raw_json IS NOT excluded.raw_json OR player_week_rows.row_index IS NOT excluded.row_index`
      ).bind(task.source, task.season, task.week, task.position, offset, now, chunk.json));
      offset += chunk.count;
    }
    const ids = JSON.stringify(rows.map((row) => row.player_id).filter((id) => id !== undefined && id !== null).map(String));
    statements.push(db.prepare(
      "DELETE FROM player_week_rows WHERE source = ? AND season = ? AND week = ? AND position = ? AND player_id NOT IN (SELECT value FROM json_each(?))"
    ).bind(task.source, task.season, task.week, task.position, ids));
  }
  statements.push(ledgerUpsert(db, {
    dataset: task.source, scopeKey, season: task.season, week: task.week, position: task.position,
    fetchedAt: now, rowCount: rows ? rows.length : Number(previous?.row_count || 0), hash, complete: task.complete, metadata: {}
  }));
  context.d1Queries += 1;
  await db.batch(statements);
  return rows ? { status: "ok", rows: rows.length } : { status: "unchanged" };
}

async function runPlayersTask(context: Context): Promise<Omit<TaskResult, "task">> {
  const { db, now } = context;
  const players = objectValue(JSON.parse(await fetchText(context, `${APP_BASE_URL}/players/nfl`)));
  const slim: Record<string, JsonMap> = {};
  for (const [playerId, value] of Object.entries(players)) {
    const player = objectValue(value);
    const kept: JsonMap = {};
    for (const field of PLAYER_SNAPSHOT_FIELDS) {
      if (player[field] !== undefined) kept[field] = player[field];
    }
    slim[playerId] = kept;
  }
  const snapshotId = `players-${now}`;
  const statements: D1PreparedStatement[] = [];
  const entries = Object.entries(slim);
  let chunkIndex = 0;
  let current: string[] = [];
  let currentBytes = 2;
  const flushChunk = () => {
    if (!current.length) return;
    statements.push(db.prepare(
      "INSERT OR REPLACE INTO player_snapshot_chunks (snapshot_id, chunk_index, player_count, payload_json, created_at) VALUES (?, ?, ?, ?, ?)"
    ).bind(snapshotId, chunkIndex, current.length, `{${current.join(",")}}`, now));
    chunkIndex += 1;
    current = [];
    currentBytes = 2;
  };
  for (const [playerId, player] of entries) {
    const piece = `${JSON.stringify(playerId)}:${JSON.stringify(player)}`;
    if (currentBytes + piece.length + 1 > MAX_JSON_PARAM_BYTES) flushChunk();
    current.push(piece);
    currentBytes += piece.length + 1;
  }
  flushChunk();
  statements.push(ledgerUpsert(db, {
    dataset: "players", scopeKey: "nfl", season: null, week: null, position: null, fetchedAt: now,
    rowCount: entries.length, hash: null, complete: false, metadata: { snapshot_id: snapshotId, chunks: chunkIndex }
  }));
  statements.push(db.prepare("DELETE FROM player_snapshot_chunks WHERE snapshot_id <> ?").bind(snapshotId));
  context.d1Queries += 1;
  await db.batch(statements);
  return { status: "ok", rows: entries.length };
}

async function runLeagueMetaTask(leagueId: string, context: Context): Promise<Omit<TaskResult, "task">> {
  const { db, now } = context;
  const league = objectValue(await fetchJson(context, `${APP_BASE_URL}/league/${leagueId}`));
  const users = arrayValue(await fetchJson(context, `${APP_BASE_URL}/league/${leagueId}/users`));
  const statements = [
    db.prepare(
      `INSERT INTO league_settings (league_id, name, season, status, scoring_settings_json, roster_positions_json, raw_json, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (league_id) DO UPDATE SET name = excluded.name, season = excluded.season, status = excluded.status,
         scoring_settings_json = excluded.scoring_settings_json, roster_positions_json = excluded.roster_positions_json,
         raw_json = excluded.raw_json, updated_at = excluded.updated_at`
    ).bind(
      leagueId,
      league.name === undefined ? null : String(league.name),
      Number(league.season) || null,
      league.status === undefined ? null : String(league.status),
      JSON.stringify(objectValue(league.scoring_settings)),
      JSON.stringify(Array.isArray(league.roster_positions) ? league.roster_positions : []),
      JSON.stringify(league),
      now
    ),
    db.prepare("DELETE FROM league_users WHERE league_id = ?").bind(leagueId),
    db.prepare(
      `INSERT OR REPLACE INTO league_users (league_id, user_id, row_index, display_name, raw_json, updated_at)
       SELECT ?1, CAST(json_extract(value, '$.user_id') AS TEXT), CAST(key AS INTEGER), json_extract(value, '$.display_name'), value, ?2
       FROM json_each(?3) WHERE json_extract(value, '$.user_id') IS NOT NULL`
    ).bind(leagueId, now, JSON.stringify(users)),
    ledgerUpsert(db, { dataset: "league_settings", scopeKey: leagueId, season: Number(league.season) || null, week: null, position: null, fetchedAt: now, rowCount: 1, hash: null, complete: false, metadata: {} }),
    ledgerUpsert(db, { dataset: "league_users", scopeKey: leagueId, season: Number(league.season) || null, week: null, position: null, fetchedAt: now, rowCount: users.length, hash: null, complete: false, metadata: {} })
  ];
  context.d1Queries += 1;
  await db.batch(statements);
  return { status: "ok", rows: users.length + 1 };
}

async function runRostersTask(leagueId: string, context: Context): Promise<Omit<TaskResult, "task">> {
  const { db, now } = context;
  const rosters = arrayValue(await fetchJson(context, `${APP_BASE_URL}/league/${leagueId}/rosters`));
  context.d1Queries += 1;
  await db.batch([
    db.prepare("DELETE FROM rosters WHERE league_id = ?").bind(leagueId),
    db.prepare(
      `INSERT OR REPLACE INTO rosters (league_id, roster_id, row_index, owner_id, raw_json, updated_at)
       SELECT ?1, CAST(json_extract(value, '$.roster_id') AS TEXT), CAST(key AS INTEGER), CAST(json_extract(value, '$.owner_id') AS TEXT), value, ?2
       FROM json_each(?3) WHERE json_extract(value, '$.roster_id') IS NOT NULL`
    ).bind(leagueId, now, JSON.stringify(rosters)),
    ledgerUpsert(db, { dataset: "rosters", scopeKey: leagueId, season: null, week: null, position: null, fetchedAt: now, rowCount: rosters.length, hash: null, complete: false, metadata: {} })
  ]);
  return { status: "ok", rows: rosters.length };
}

// Response-cache eviction (issue A) plus sync_runs retention. The row cap
// scans the whole cache table, so it runs only on the 6-hourly players cron.
function maintenanceStatements(db: D1Database, now: number, cron: string): D1PreparedStatement[] {
  const statements = [
    db.prepare(
      `DELETE FROM api_response_cache WHERE cache_key IN (SELECT cache_key FROM api_response_cache WHERE expires_at <= ? LIMIT ${EVICT_BATCH})`
    ).bind(now),
    db.prepare("DELETE FROM sync_runs WHERE started_at < ?").bind(now - SYNC_RUN_RETENTION_SECONDS)
  ];
  if (cron === PLAYERS_CRON) {
    statements.push(db.prepare(
      `DELETE FROM api_response_cache WHERE cache_key IN (SELECT cache_key FROM api_response_cache ORDER BY created_at DESC LIMIT -1 OFFSET ${CACHE_ROW_CAP})`
    ));
  }
  return statements;
}

function ledgerUpsert(db: D1Database, row: {
  dataset: string; scopeKey: string; season: number | null; week: number | null; position: string | null;
  fetchedAt: number; rowCount: number; hash: string | null; complete: boolean; metadata: JsonMap;
}): D1PreparedStatement {
  return db.prepare(
    `INSERT INTO sync_datasets (dataset, scope_key, season, week, position, fetched_at, row_count, payload_hash, complete, metadata_json)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (dataset, scope_key) DO UPDATE SET season = excluded.season, week = excluded.week, position = excluded.position,
       fetched_at = excluded.fetched_at, row_count = excluded.row_count, payload_hash = excluded.payload_hash,
       complete = excluded.complete, metadata_json = excluded.metadata_json`
  ).bind(row.dataset, row.scopeKey, row.season, row.week, row.position, row.fetchedAt, row.rowCount, row.hash, row.complete ? 1 : 0, JSON.stringify(row.metadata));
}

function* jsonChunks(rows: JsonMap[]): Generator<{ json: string; count: number }> {
  let pieces: string[] = [];
  let bytes = 2;
  for (const row of rows) {
    const piece = JSON.stringify(row);
    if (pieces.length && bytes + piece.length + 1 > MAX_JSON_PARAM_BYTES) {
      yield { json: `[${pieces.join(",")}]`, count: pieces.length };
      pieces = [];
      bytes = 2;
    }
    pieces.push(piece);
    bytes += piece.length + 1;
  }
  if (pieces.length) yield { json: `[${pieces.join(",")}]`, count: pieces.length };
}

async function fetchText(context: Context, url: string): Promise<string> {
  context.fetches += 1;
  const response = await fetch(url, { headers: { "Accept": "application/json", "User-Agent": "sleeper-mcp-worker/0.1 (scheduled sync)" } });
  if (!response.ok) throw new Error(`Sleeper API request failed: ${response.status} ${url}`);
  return response.text();
}

async function fetchJson(context: Context, url: string): Promise<unknown> {
  return JSON.parse(await fetchText(context, url));
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

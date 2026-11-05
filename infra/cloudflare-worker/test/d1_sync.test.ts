import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import worker from "../src/index.ts";
import { ensureCacheTable, isCacheableUrl } from "../src/response-cache.ts";
import { MAIN_CRON, PLAYERS_CRON, SUBREQUEST_BUDGET, planSyncTasks, runScheduledSync } from "../src/sync.ts";
import { createD1 } from "./d1-shim.ts";

const POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];
const LEAGUE_ID = "1234567890";

// Same shape as budgetFetch in index.test.ts, plus /state/nfl, so the sync
// and the tools read one fixture. Week 10 is the in-progress week.
function fixtureFetch(urls: string[], options: { statsBump?: number } = {}): typeof fetch {
  const players: Record<string, Record<string, unknown>> = {};
  for (const position of POSITIONS) {
    players[`r-${position}`] = { player_id: `r-${position}`, full_name: `Roster ${position}`, team: "DEN", position, fantasy_positions: [position], status: "Active", years_exp: 3, news_updated: 1 };
    players[`f-${position}`] = { player_id: `f-${position}`, full_name: `Free ${position}`, team: "KC", position, fantasy_positions: [position], status: "Active", years_exp: 1 };
  }
  const roster = POSITIONS.map((p) => `r-${p}`);
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    let payload: unknown = [];
    const path = url.split("?")[0];
    if (path.endsWith("/state/nfl")) payload = { season: "2026", season_type: "regular", week: 10, display_week: 10 };
    else if (path.endsWith("/players/nfl")) payload = players;
    else if (path.endsWith(`/league/${LEAGUE_ID}/rosters`)) payload = [{ roster_id: 1, owner_id: "u1", players: roster }];
    else if (path.endsWith(`/league/${LEAGUE_ID}/users`)) payload = [{ user_id: "u1", display_name: "Me", metadata: { team_name: "Team Me" } }];
    else if (/\/league\/\d+\/matchups\//.test(path)) payload = [{ roster_id: 1, matchup_id: 1, starters: roster, players: roster }];
    else if (path.endsWith(`/league/${LEAGUE_ID}`)) payload = { league_id: LEAGUE_ID, name: "Fixture League", season: "2026", status: "in_season", roster_positions: POSITIONS, scoring_settings: { rush_att: 0.1, rec: 1 } };
    else {
      const match = url.match(/\/(stats|projections)\/nfl\/2026\/(\d+)\?.*position%5B%5D=([A-Z]+)/);
      if (match) {
        const bump = match[1] === "stats" ? options.statsBump || 0 : 0;
        payload = ["r", "f"].map((prefix) => ({
          player_id: `${prefix}-${match[3]}`,
          player: { team: prefix === "r" ? "DEN" : "KC", position: match[3], first_name: prefix, last_name: match[3] },
          stats: { rush_att: 10 + Number(match[2]) + bump, rec_tgt: 5, rec: 3, pass_att: 30, fga: 2, xpa: 3, sack: 2, pts_ppr: prefix === "f" ? 12 : 8 }
        }));
      }
    }
    return new Response(JSON.stringify(payload), { headers: { "content-type": "application/json" } });
  }) as typeof fetch;
}

async function withFetch<T>(fake: typeof fetch, run: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch;
  globalThis.fetch = fake;
  try {
    return await run();
  } finally {
    globalThis.fetch = original;
  }
}

async function callTool(name: string, args: Record<string, unknown>, env: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await worker.fetch(new Request("https://worker.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } })
  }), env as never);
  const body = (await response.json()) as { result?: { content: [{ text: string }] }; error?: unknown };
  assert.ok(body.result, JSON.stringify(body.error));
  return JSON.parse(body.result.content[0].text) as Record<string, unknown>;
}

// Run main-cron ticks until nothing is due, plus one players-cron tick.
async function hydrate(env: Record<string, unknown>, urls: string[] = []) {
  const reports = [];
  await withFetch(fixtureFetch(urls), async () => {
    reports.push(await runScheduledSync(env, { cron: PLAYERS_CRON }));
    for (let tick = 0; tick < 40; tick += 1) {
      const report = await runScheduledSync(env, { cron: MAIN_CRON });
      reports.push(report);
      if (!report.tasks.some((task) => task.task !== "nfl_state")) break;
    }
  });
  return reports;
}

function count(sqlite: DatabaseSync, sql: string): number {
  return Number((sqlite.prepare(sql).get() as Record<string, unknown>).n);
}

test("migrations apply cleanly, are idempotent, and adopt a runtime-created api_response_cache", () => {
  const sqlite = new DatabaseSync(":memory:");
  // Older Workers created this table at request time; 0001 must not fail on it.
  sqlite.exec("CREATE TABLE IF NOT EXISTS api_response_cache (cache_key TEXT PRIMARY KEY, url TEXT NOT NULL, response_json TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)");
  for (let pass = 0; pass < 2; pass += 1) {
    for (const file of ["0001_api_response_cache.sql", "0002_normalized_read_model.sql"]) {
      sqlite.exec(readFileSync(new URL(`../migrations/${file}`, import.meta.url), "utf8"));
    }
  }
  const tables = (sqlite.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all() as Array<{ name: string }>).map((row) => row.name);
  for (const table of ["api_response_cache", "league_settings", "league_users", "player_snapshot_chunks", "player_week_rows", "rosters", "sync_datasets", "sync_runs"]) {
    assert.ok(tables.includes(table), table);
  }
  // Expand/contract: no migration drops or renames anything.
  for (const file of ["0001_api_response_cache.sql", "0002_normalized_read_model.sql"]) {
    const sql = readFileSync(new URL(`../migrations/${file}`, import.meta.url), "utf8").toUpperCase();
    assert.ok(!/\bDROP\b|\bRENAME\b/.test(sql.replace(/--.*$/gm, "")), file);
  }
});

test("scheduled handler is reachable only as a cron, never over HTTP", async () => {
  for (const path of ["/__scheduled", "/cdn-cgi/handler/scheduled", "/scheduled"]) {
    for (const method of ["GET", "POST"]) {
      const response = await worker.fetch(new Request(`https://worker.test${path}?cron=*`, { method }), {} as never);
      assert.equal(response.status, 404, `${method} ${path}`);
    }
  }
  assert.equal(typeof worker.scheduled, "function");
});

test("mocked scheduled handler hydrates the default league within the per-tick subrequest budget", async () => {
  const shim = createD1();
  const env = { SLEEPER_CACHE_DB: shim.db, SLEEPER_DEFAULT_LEAGUE_ID: LEAGUE_ID };
  const urls: string[] = [];
  const logs: string[] = [];
  const originalLog = console.log;
  console.log = (line: string) => { logs.push(line); };
  try {
    await withFetch(fixtureFetch(urls), async () => {
      await worker.scheduled({ cron: MAIN_CRON, scheduledTime: Date.now(), noRetry() {} } as never, env as never);
    });
  } finally {
    console.log = originalLog;
  }
  const report = JSON.parse(logs[0]);
  assert.equal(report.event, "scheduled_sync");
  assert.equal(report.status, "ok");
  assert.equal(report.season, 2026);
  assert.equal(report.week, 10);
  // Default budget: 6 fetches -> 1 ledger + 1 state + 6 fetches + <= 6 batches + 1 final.
  assert.ok(report.fetches <= 7, JSON.stringify(report));
  assert.ok(report.subrequests <= 15, JSON.stringify(report));
  assert.equal(report.subrequests, urls.length + shim.queryCount());
  // Rosters first, then league settings/users, then current-week projections.
  assert.deepEqual(report.tasks.map((task: { task: string }) => task.task).slice(0, 3), [`rosters:${LEAGUE_ID}`, `league_meta:${LEAGUE_ID}`, "projections:2026:10:QB"]);
  assert.ok(urls.some((url) => url.endsWith("/state/nfl")));
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM rosters"), 1);
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM league_users"), 1);
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM sync_runs"), 1);
  assert.ok(count(shim.sqlite, "SELECT COUNT(*) AS n FROM player_week_rows WHERE source = 'projections' AND week = 10") > 0);
});

test("raising SYNC_MAX_FETCHES_PER_TICK is clamped under the Free-plan 50-subrequest limit", async () => {
  const shim = createD1();
  const env = { SLEEPER_CACHE_DB: shim.db, SLEEPER_DEFAULT_LEAGUE_ID: LEAGUE_ID, SYNC_MAX_FETCHES_PER_TICK: "500" };
  const reports = await hydrate(env);
  for (const report of reports) {
    assert.ok(report.subrequests <= SUBREQUEST_BUDGET, JSON.stringify(report));
    assert.ok(report.subrequests < 50);
    assert.ok(report.fetches <= 21);
  }
  const main = reports.filter((report) => report.cron === MAIN_CRON);
  // 3 league fetches + 2 sources x 6 positions x 10 weeks = 123 fetches at 20 per tick.
  assert.ok(main.length <= 8, `ticks: ${main.length}`);
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM sync_datasets WHERE dataset IN ('stats', 'projections')"), 120);
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM sync_datasets WHERE dataset = 'stats' AND complete = 1"), 54);
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM sync_datasets WHERE dataset = 'players'"), 1);
  // Players are a slim chunked snapshot, not one row per player.
  const chunk = JSON.parse(String((shim.sqlite.prepare("SELECT payload_json FROM player_snapshot_chunks").get() as Record<string, unknown>).payload_json));
  assert.equal(chunk["r-QB"].full_name, "Roster QB");
  assert.equal(chunk["r-QB"].years_exp, undefined);
});

test("unchanged slices only touch the ledger and stale rows are pruned", async () => {
  const shim = createD1();
  const env = { SLEEPER_CACHE_DB: shim.db, SLEEPER_DEFAULT_LEAGUE_ID: LEAGUE_ID, SYNC_MAX_FETCHES_PER_TICK: "20" };
  await hydrate(env);
  const later = Math.floor(Date.now() / 1000) + 5 * 3600;
  // Current-week slices are due again after their refresh window.
  const report = await withFetch(fixtureFetch([]), () => runScheduledSync(env, { cron: MAIN_CRON, now: later }));
  const sliceTasks = report.tasks.filter((task) => task.task.startsWith("projections:2026:10") || task.task.startsWith("stats:2026:10"));
  assert.ok(sliceTasks.length > 0);
  assert.ok(sliceTasks.every((task) => task.status === "unchanged"), JSON.stringify(sliceTasks));
  // A player who drops out of a slice is deleted from it.
  shim.sqlite.prepare("INSERT INTO player_week_rows (source, season, week, position, player_id, row_index, raw_json, updated_at) VALUES ('stats', 2026, 9, 'QB', 'gone', 99, '{}', 1)").run();
  shim.sqlite.prepare("UPDATE sync_datasets SET payload_hash = 'stale', fetched_at = 1 WHERE dataset = 'stats' AND scope_key = '2026:9:QB'").run();
  await withFetch(fixtureFetch([], { statsBump: 1 }), () => runScheduledSync(env, { cron: MAIN_CRON, now: later }));
  assert.equal(count(shim.sqlite, "SELECT COUNT(*) AS n FROM player_week_rows WHERE player_id = 'gone'"), 0);
});

test("scheduled sync evicts expired response-cache rows", async () => {
  const shim = createD1();
  const now = Math.floor(Date.now() / 1000);
  shim.sqlite.prepare("INSERT INTO api_response_cache VALUES ('old', 'old', '[]', ?, ?)").run(now - 10, now - 100);
  shim.sqlite.prepare("INSERT INTO api_response_cache VALUES ('live', 'live', '[]', ?, ?)").run(now + 600, now);
  await withFetch(fixtureFetch([]), () => runScheduledSync({ SLEEPER_CACHE_DB: shim.db }, { cron: MAIN_CRON, now }));
  const keys = (shim.sqlite.prepare("SELECT cache_key FROM api_response_cache ORDER BY cache_key").all() as Array<{ cache_key: string }>).map((row) => row.cache_key);
  assert.ok(!keys.includes("old"));
  assert.ok(keys.includes("live"));
});

test("scheduled sync reports an unmigrated database instead of throwing", async () => {
  const shim = createD1({ migrate: false });
  const report = await withFetch(fixtureFetch([]), () => runScheduledSync({ SLEEPER_CACHE_DB: shim.db }, { cron: MAIN_CRON }));
  assert.equal(report.status, "error");
  assert.match(String(report.reason), /worker-d1-migrate/);
});

test("planner skips slices outside the regular season and orders backfill newest first", () => {
  const ledger = new Map();
  const now = 1_000_000;
  const offseason = planSyncTasks({ ledger, state: { season: 2026, seasonType: "off", week: 0 }, now, leagueId: undefined, maxFetches: 20, cron: MAIN_CRON });
  assert.deepEqual(offseason, []);
  const inSeason = planSyncTasks({ ledger, state: { season: 2026, seasonType: "regular", week: 3 }, now, leagueId: undefined, maxFetches: 20, cron: MAIN_CRON });
  const labels = inSeason.map((task) => task.kind === "slice" ? `${task.source}:${task.week}` : task.kind);
  assert.deepEqual(labels.slice(0, 6), Array(6).fill("projections:3"));
  assert.deepEqual(labels.slice(6, 12), Array(6).fill("stats:3"));
  assert.deepEqual(labels.slice(12, 18), Array(6).fill("stats:2"));
  assert.equal(inSeason.length, 20);
});

const WEEK10 = { league_id: LEAGUE_ID, roster_id: 1, season: 2026, week: 10 };

function withoutFreshness(value: Record<string, unknown>): Record<string, unknown> {
  const { data_freshness: _ignored, ...rest } = value;
  return rest;
}

test("normalized D1 reads match the raw Sleeper path and cut week-10 fetches", async () => {
  const shim = createD1();
  const env = { SLEEPER_CACHE_DB: shim.db, SLEEPER_DEFAULT_LEAGUE_ID: LEAGUE_ID, SYNC_MAX_FETCHES_PER_TICK: "20" };
  await hydrate(env);
  const counts: Record<string, Record<string, number | string>> = {};
  for (const [name, extra] of [["my_lineup", {}], ["waiver_wire_by_position", {}], ["decision_smoke_report", { format: "json" }]] as const) {
    const args = { ...WEEK10, ...extra };
    const rawUrls: string[] = [];
    const raw = await withFetch(fixtureFetch(rawUrls), () => callTool(name, args, {}));
    shim.resetCounts();
    const d1Urls: string[] = [];
    const normalized = await withFetch(fixtureFetch(d1Urls), () => callTool(name, args, env));
    const d1Queries = shim.queryCount();
    shim.resetCounts();
    const warmUrls: string[] = [];
    await withFetch(fixtureFetch(warmUrls), () => callTool(name, args, env));
    counts[name] = {
      before_fetches: rawUrls.length,
      after_fetches: d1Urls.length,
      after_d1_queries: d1Queries,
      after_subrequests: d1Urls.length + d1Queries,
      warm_fetches: warmUrls.length,
      warm_subrequests: warmUrls.length + shim.queryCount()
    };
    assert.deepEqual(withoutFreshness(normalized), withoutFreshness(raw), name);
    assert.deepEqual(d1Urls.filter((url) => /\/(stats|projections)\/nfl\/|\/players\/nfl$/.test(url.split("?")[0])), [], name);
    assert.ok(Number(counts[name].after_subrequests) < 50, JSON.stringify(counts[name]));
    if (name === "decision_smoke_report") continue;
    const freshness = normalized.data_freshness as Record<string, unknown>;
    assert.equal(freshness.read_model, "d1_normalized_with_sleeper_fallback");
    assert.ok((freshness.normalized_datasets as string[]).includes("stats"), JSON.stringify(freshness));
    assert.ok((freshness.normalized_datasets as string[]).includes("projections"));
    assert.ok((freshness.normalized_datasets as string[]).includes("players"));
    assert.equal((raw.data_freshness as Record<string, unknown>).read_model, "sleeper_fallback");
  }
  console.log("worker read budget (week 10, D1 hydrated):", JSON.stringify(counts));
  assert.ok(Number(counts.my_lineup.before_fetches) >= 60);
  assert.ok(Number(counts.waiver_wire_by_position.before_fetches) >= 60);
});

test("stale normalized slices fall back to Sleeper", async () => {
  const shim = createD1();
  const env = { SLEEPER_CACHE_DB: shim.db, SLEEPER_DEFAULT_LEAGUE_ID: LEAGUE_ID, SYNC_MAX_FETCHES_PER_TICK: "20" };
  await hydrate(env);
  shim.sqlite.prepare("UPDATE sync_datasets SET fetched_at = 1").run();
  const urls: string[] = [];
  const result = await withFetch(fixtureFetch(urls), () => callTool("my_lineup", WEEK10, env));
  assert.equal((result.data_freshness as Record<string, unknown>).read_model, "sleeper_fallback");
  assert.ok(urls.some((url) => url.includes("/projections/nfl/2026/10")));
  assert.equal(urls.filter((url) => /\/stats\/nfl\/2026\/\d+\?/.test(url) && !url.includes("order_by")).length, 54);
});

test("response cache creates its table once per binding and only caches bounded Sleeper URLs", async () => {
  const statements: string[] = [];
  const db = { prepare(sql: string) { statements.push(sql); return { async run() { return { success: true }; } }; } } as unknown as D1Database;
  await ensureCacheTable(db);
  await ensureCacheTable(db);
  assert.equal(statements.length, 1);
  assert.ok(isCacheableUrl("https://api.sleeper.app/v1/league/123/rosters"));
  assert.ok(isCacheableUrl("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=100"));
  assert.ok(isCacheableUrl("https://api.sleeper.com/stats/nfl/2026/4?season_type=regular&position%5B%5D=WR&order_by=pts_ppr"));
  assert.ok(!isCacheableUrl("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=99999&limit=100"));
  assert.ok(!isCacheableUrl("https://api.sleeper.app/v1/league/not-a-number/rosters"));
  assert.ok(!isCacheableUrl("https://api.sleeper.com/stats/nfl/2026/4?season_type=regular&position%5B%5D=ZZZ"));
  assert.ok(!isCacheableUrl("https://api.sleeper.com/stats/nfl/2026/4?season_type=regular&extra=1"));
  assert.ok(!isCacheableUrl("https://example.com/league/1"));
});

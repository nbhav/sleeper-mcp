import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.ts";

function request(args: Record<string, unknown>): Request {
  return new Request("https://worker.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "player_matchup_context", arguments: args }
    })
  });
}

async function call(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await worker.fetch(request(args), {});
  const envelope = (await response.json()) as { result: { content: [{ text: string }] } };
  return JSON.parse(envelope.result.content[0].text) as Record<string, unknown>;
}

async function callWithEnv(args: Record<string, unknown>, env: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await worker.fetch(request(args), env);
  const envelope = (await response.json()) as { result: { content: [{ text: string }] } };
  return JSON.parse(envelope.result.content[0].text) as Record<string, unknown>;
}

async function envelope(requestValue: Request, env: Record<string, unknown> = {}) {
  const response = await worker.fetch(requestValue, env);
  const body = response.status === 202 ? {} : await response.json() as Record<string, unknown>;
  return { response, body };
}

function message(method: string, params?: Record<string, unknown>): Request {
  return new Request("https://worker.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 2, method, params })
  });
}

test("worker serves root metadata and rejects unsupported HTTP paths", async () => {
  const root = await envelope(new Request("https://worker.test/"));
  assert.equal(root.response.status, 200);
  assert.deepEqual(root.body, { name: "sleeper-mcp", endpoint: "/mcp" });

  const missing = await envelope(new Request("https://worker.test/other"));
  assert.equal(missing.response.status, 404);
  assert.deepEqual(missing.body, { error: "not found" });

  const getMcp = await envelope(new Request("https://worker.test/mcp"));
  assert.equal(getMcp.response.status, 405);
  assert.deepEqual(getMcp.body, { error: "method not allowed" });
});

test("worker handles MCP lifecycle, notifications, and unknown messages", async () => {
  const initialized = await envelope(message("initialize"));
  assert.equal(initialized.response.status, 200);
  assert.deepEqual((initialized.body.result as Record<string, unknown>).capabilities, { tools: {} });

  const notification = await envelope(message("notifications/initialized"));
  assert.equal(notification.response.status, 202);

  const unknown = await envelope(message("resources/list"));
  assert.equal((unknown.body.error as Record<string, unknown>).code, -32000);
  assert.equal((unknown.body.error as Record<string, unknown>).message, "Unknown method: resources/list");

  const unknownTool = await envelope(message("tools/call", {
    name: "not_a_tool",
    arguments: {}
  }));
  assert.equal((unknownTool.body.error as Record<string, unknown>).message, "Unknown tool: not_a_tool");
});

test("worker caches raw API responses in D1 between identical requests", async () => {
  const rows = new Map<string, string>();
  const statements: string[] = [];
  const db = {
    prepare(sql: string) {
      statements.push(sql);
      let bound: unknown[] = [];
      return {
        bind(...values: unknown[]) {
          bound = values;
          return this;
        },
        async first() {
          const key = String(bound[0] || "");
          const value = rows.get(key);
          return value === undefined ? undefined : { response_json: value };
        },
        async run() {
          if (sql.startsWith("INSERT")) rows.set(String(bound[0]), String(bound[2]));
          return { success: true };
        }
      };
    }
  };
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return new Response(JSON.stringify([
      { player_id: "p-1", player: { team: "DEN", position: "QB" }, stats: { opponent: "KC" } }
    ]), { headers: { "content-type": "application/json" } });
  };
  try {
    const first = await callWithEnv({ player_id: "p-1", season: 2026, week: 4, source: "stats" }, { SLEEPER_CACHE_DB: db });
    const second = await callWithEnv({ player_id: "p-1", season: 2026, week: 4, source: "stats" }, { SLEEPER_CACHE_DB: db });
    assert.equal(first.opponent, "KC");
    assert.equal(second.opponent, "KC");
    assert.equal(fetchCount, 1);
    assert.ok(statements.some((sql) => sql.startsWith("CREATE TABLE IF NOT EXISTS")));
    assert.ok(statements.some((sql) => sql.startsWith("INSERT OR REPLACE")));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("player matchup fallback preserves raw schedule context without fabricating a score", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify([
    {
      player_id: "p-1",
      player: { team: "DEN", position: "QB" },
      stats: { opponent: "KC", home: true, game_id: "game-1" }
    }
  ]), { headers: { "content-type": "application/json" } });
  try {
    const result = await call({ player_id: "p-1", season: 2026, week: 4, source: "stats" });
    assert.equal(result.status, "unavailable");
    assert.equal(result.team, "DEN");
    assert.equal(result.opponent, "KC");
    assert.equal(result.home_away, "home");
    assert.equal(result.availability, null);
    assert.equal(result.matchup_adjustment, 0);
    assert.deepEqual(result.source_availability, {
      normalized_player_stats: false,
      nfl_schedule: true,
      historical_opponent_stats: false,
      enriched_provider: false,
      weekly_availability: false
    });
    assert.deepEqual(result.missing_inputs, ["not_evaluable_missing_normalized_stats_row"]);
    assert.deepEqual(result.reason_codes, ["normalized_player_source_unavailable"]);
    assert.equal((result.evidence as Record<string, unknown>).source, "stats");
    assert.equal(
      ((result.evidence as Record<string, unknown>).raw_schedule_context as Record<string, unknown>).raw_row_found,
      true
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("player matchup fallback has the complete unavailable contract when raw data is absent", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify([]), {
    headers: { "content-type": "application/json" }
  });
  try {
    const result = await call({ player_id: "missing", season: 2026, week: 4, source: "projections" });
    assert.equal(result.status, "unavailable");
    assert.equal(result.position, null);
    assert.equal(result.team, null);
    assert.equal(result.opponent, null);
    assert.equal(result.home_away, null);
    assert.equal(result.availability, null);
    assert.equal(result.matchup_adjustment, 0);
    assert.deepEqual(result.missing_inputs, ["not_evaluable_missing_normalized_projections_row"]);
    assert.deepEqual(result.reason_codes, ["normalized_player_source_unavailable"]);
    assert.deepEqual(result.evidence, {
      source: "projections",
      raw_schedule_context: { raw_row_found: false, raw_source: "projections" },
      note: "Worker D1 currently caches raw API responses; normalized matchup rows and weekly availability are not yet materialized."
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

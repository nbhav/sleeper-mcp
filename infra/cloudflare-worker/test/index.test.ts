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

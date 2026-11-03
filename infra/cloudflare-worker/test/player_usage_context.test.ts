import assert from "node:assert/strict";
import test from "node:test";
import { PLAYER_USAGE_CONTEXT_SCHEMA_VERSION, evaluateWorkerUsageContext, normalizeUsageStats } from "../src/index.ts";

test("usage context preserves actual-first windows and reports unsupported sources", () => {
  const result = evaluateWorkerUsageContext(
    "RB",
    [
      { week: 1, weighted_touches: 10, points: 8, active: true, bye: false },
      { week: 2, weighted_touches: 10, points: 8, active: true, bye: false },
      { week: 4, weighted_touches: 16, points: 14, active: true, bye: false },
      { week: 5, weighted_touches: 17, points: 15, active: true, bye: false }
    ],
    [{ week: 5, weighted_touches: 12, points: 12, active: true, bye: false }],
    { depth_chart_order: 2, depth_chart_role: 2 }
  );

  assert.equal(result.role_label, "emerging_rotation");
  assert.equal(result.source_flags.actual_stats, true);
  assert.equal(result.source_flags.projections, true);
  assert.equal(result.source_flags.nfl_schedule, false);
  assert.ok(result.reason_codes.includes("role_change_recent"));
  assert.ok(result.reason_codes.includes("not_evaluable_missing_nfl_schedule"));
  assert.deepEqual(result.scores, { ...result.scores });
});

test("projection-only context does not invent actual rows", () => {
  const result = evaluateWorkerUsageContext(
    "WR",
    [],
    [{ week: 1, targets: 7, points: 12, active: true, bye: false }],
    {}
  );

  assert.equal(result.source_flags.actual_stats, false);
  assert.equal(result.source_flags.projections, true);
  assert.ok(result.reason_codes.includes("not_evaluable_missing_actual_stats"));
  assert.ok(result.missing_inputs.includes("actual_stats"));
  assert.equal(result.scores.opportunity_score, 35);
});

test("raw receiving touchdowns and targets produce the canonical spike reasons", () => {
  const result = evaluateWorkerUsageContext(
    "WR",
    [{ week: 5, targets: 3, rec_tds: 2, points: 24, active: true, bye: false }],
    [],
    {}
  );

  assert.ok(result.reason_codes.includes("low_target_big_points"));
  assert.ok(result.reason_codes.includes("td_only_low_usage"));
  assert.ok(result.reasons.every((reason: Record<string, unknown>) =>
    ["code", "component", "polarity", "computability_tier", "evidence", "severity", "missing_inputs", "description"].every((key) => key in reason)
  ));
});

test("usage context contract is v2", () => {
  assert.equal(PLAYER_USAGE_CONTEXT_SCHEMA_VERSION, "player_usage_context.v2");
});

test("RB weighted_touches uses rec_tgt while touches uses rec as evidence", () => {
  const normalized = normalizeUsageStats("RB", { rush_att: 12, rec: 2, rec_tgt: 6 });
  assert.equal(normalized.weighted_touches, 18);
  assert.equal(normalized.touches, 14);
  const lowTouch = evaluateWorkerUsageContext("RB", [{ week: 5, points: 22, weighted_touches: 12, touches: 30, rush_tds: 2, active: true, bye: false }], [], {});
  assert.ok(lowTouch.reason_codes.includes("low_touch_big_points"));
  const missing = evaluateWorkerUsageContext("RB", [{ week: 5, points: 22, touches: 5, rush_tds: 2, active: true, bye: false }], [], {});
  assert.ok(missing.reason_codes.includes("not_evaluable_missing_weighted_touches"));
  assert.ok(!missing.reason_codes.includes("low_touch_big_points"));
});

test("sack keys are position-aware", () => {
  const qb = normalizeUsageStats("QB", { pass_att: 30, pass_sack: 2, sack: 9 });
  assert.equal(qb.dropbacks, 32);
  assert.equal(qb.sacks_taken, 2);
  assert.equal(qb.sacks, undefined);
  const defense = normalizeUsageStats("DEF", { sack: 3, pass_sack: 8 });
  assert.equal(defense.sacks, 3);
  assert.equal(defense.dropbacks, undefined);
  assert.equal(normalizeUsageStats("DEF", { pass_sack: 4 }).sacks, undefined);
});

test("normalizer reads exact keys and sums only distinct canonical stats", () => {
  const defense = normalizeUsageStats("DEF", { int: 2, fum_rec: 1, def_int: 5, interceptions: 5, td: 3 });
  assert.equal(defense.takeaways, 3);
  assert.equal(defense.def_tds, undefined);
  const kicker = normalizeUsageStats("K", { fg_att: 4, extra_point_attempts: 3, fgm_50p: 1 });
  assert.equal(kicker.total_kick_attempts, undefined);
  assert.equal(kicker.made_50_plus, 1);
  assert.equal(normalizeUsageStats("K", { fga: 3, xpa: 4 }).total_kick_attempts, 7);
  const receiver = normalizeUsageStats("WR", { targets: 9, rec_tgt: 4 });
  assert.equal(receiver.targets, 4);
});

test("WR missing inputs flag air-yard share, not absolute air yards", () => {
  const result = evaluateWorkerUsageContext("WR", [{ week: 1, targets: 5, points: 6, active: true, bye: false }], [], {});
  assert.ok(result.missing_inputs.includes("air_yard_share"));
  assert.ok(!result.missing_inputs.includes("air_yards"));
});

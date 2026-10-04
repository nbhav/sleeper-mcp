import assert from "node:assert/strict";
import test from "node:test";
import { evaluateWorkerUsageContext } from "../src/index.ts";

test("usage context preserves actual-first windows and reports unsupported sources", () => {
  const result = evaluateWorkerUsageContext(
    "RB",
    [
      { week: 1, touches: 10, points: 8, active: true, bye: false },
      { week: 2, touches: 10, points: 8, active: true, bye: false },
      { week: 4, touches: 16, points: 14, active: true, bye: false },
      { week: 5, touches: 17, points: 15, active: true, bye: false }
    ],
    [{ week: 5, touches: 12, points: 12, active: true, bye: false }],
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

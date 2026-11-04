import assert from "node:assert/strict";
import test from "node:test";
import { PLAYER_USAGE_CONTEXT_SCHEMA_VERSION, buildUsageRow, detectWorkerChangePoint, evaluateWorkerUsageContext, isTdScoringKey, normalizeUsageStats, workerProjectionComparison, workerTdDependency } from "../src/usage-context.ts";

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

test("usage context contract is v3", () => {
  assert.equal(PLAYER_USAGE_CONTEXT_SCHEMA_VERSION, "player_usage_context.v3");
});

function rbRows(values: number[], extra: Record<string, unknown> = {}): Record<string, unknown>[] {
  return values.map((value, index) => ({ week: index + 1, weighted_touches: value, points: 10, active: true, bye: false, ...extra }));
}

test("change point: flat series and noisy single-week spike are not changes", () => {
  assert.equal(detectWorkerChangePoint("RB", rbRows([10, 11, 10, 11, 10, 11])).status, "no_change");
  assert.equal(detectWorkerChangePoint("RB", rbRows([10, 10, 10, 16, 10])).status, "no_change");
  assert.equal(detectWorkerChangePoint("RB", rbRows([10, 10, 10, 16, 16, 10])).status, "no_change");
  assert.equal(detectWorkerChangePoint("RB", rbRows([10, 18])).status, "insufficient_sample");
});

test("change point at week 4 evaluated at week 7 uses the post-change window", () => {
  const rows = rbRows([10, 10, 10, 14, 14, 14, 14]);
  const changePoint = detectWorkerChangePoint("RB", rows);
  assert.equal(changePoint.status, "change_detected");
  assert.equal(changePoint.change_week, 4);
  assert.deepEqual(changePoint.post_change_weeks, [4, 5, 6, 7]);
  const result = evaluateWorkerUsageContext("RB", rows, [], {});
  assert.equal(result.trend_window.name, "post_change_window");
  assert.deepEqual(result.trend_window.weeks, [4, 5, 6, 7]);
  assert.deepEqual(result.trend_window.baseline_weeks, [1, 2, 3]);
  assert.equal(result.post_change_window.detected, true);
  assert.ok(result.reason_codes.includes("role_change_recent"));
  // Worker has no availability history: explicit not-evaluable, no guess.
  assert.ok(result.reason_codes.includes("not_evaluable_missing_teammate_availability"));
  assert.ok(!result.reason_codes.includes("injury_replacement"));
  assert.equal(result.modifiers.context, 4);
});

test("TD share uses the league scoring breakdown, including non-standard TD values", () => {
  const settings6 = { pass_td: 6, rush_td: 6, pass_yd: 0.04, pass_int_td: -1 };
  const settings4 = { ...settings6, pass_td: 4 };
  const raw = { player_id: "qb", stats: { pass_att: 30, pass_td: 2, rush_td: 1, pass_yd: 250, pass_int_td: 1, pts_ppr: 99 } };
  const six = buildUsageRow(raw, "qb", "QB", "stats", 1, {}, settings6);
  const four = buildUsageRow(raw, "qb", "QB", "stats", 1, {}, settings4);
  assert.equal(six.points, 27);
  assert.equal(six.points_source, "league_scoring");
  const sixTd = workerTdDependency("QB", [six], "last_2_weeks");
  assert.equal(sixTd.method, "league_scoring_contributions");
  assert.deepEqual(sixTd.td_scoring_keys_used, ["pass_td", "rush_td"]);
  assert.equal(sixTd.value, Math.round((18 / 27) * 10000) / 10000);
  assert.equal(workerTdDependency("QB", [four], "last_2_weeks").value, Math.round((14 / 23) * 10000) / 10000);
  const result = evaluateWorkerUsageContext("QB", [six], [], {});
  assert.equal(result.td_dependency_method, "league_scoring_contributions");
  assert.equal(result.scores.td_dependency_score, Math.round((18 / 27) * 10000) / 100);
  assert.equal(isTdScoringKey("pass_int_td"), false);
  assert.equal(isTdScoringKey("bonus_rec_td_40p"), true);
});

test("TD dependency without league scoring is labeled heuristic", () => {
  const result = evaluateWorkerUsageContext("WR", [{ week: 1, targets: 3, rec_tds: 2, points: 24, active: true, bye: false }], [], {});
  assert.equal(result.td_dependency_method, "heuristic");
  assert.equal(result.td_dependency.reason, "league_scoring_settings_unavailable");
  assert.equal(result.scores.td_dependency_score, Math.round((2 * 20 / 24) * 100) / 100);
});

test("long kick share reads real keys and league points", () => {
  const bucketLeague = buildUsageRow({ player_id: "k", stats: { fga: 1, fgm: 1, fgm_50_59: 1, fgm_50p: 1, fgm_lng: 52, xpa: 2, xpm: 2 } }, "k", "K", "stats", 1, {}, { fgm_50_59: 5, xpm: 1 });
  assert.equal(bucketLeague.long_kick_point_share, Math.round((5 / 7) * 10000) / 10000);
  assert.equal(bucketLeague.made_50_plus, 1);
  assert.ok(evaluateWorkerUsageContext("K", [bucketLeague], [], {}).reason_codes.includes("long_kick_spike"));
  const yardLeague = buildUsageRow({ player_id: "k", stats: { fga: 1, fgm: 1, fgm_50p: 1, fgm_yds: 52, xpa: 2, xpm: 2 } }, "k", "K", "stats", 1, {}, { fgm_yds: 0.1, xpm: 1 });
  assert.equal(yardLeague.long_kick_point_share, undefined);
  assert.equal(yardLeague.long_kick_point_share_reason, "not_evaluable_long_field_goal_points_not_separable");
  assert.ok(!evaluateWorkerUsageContext("K", [yardLeague], [], {}).reason_codes.includes("long_kick_spike"));
  // Legacy fgm_50_plus is never read, and no share is derived from counts.
  const legacy = normalizeUsageStats("K", { fgm_50_plus: 3, fgm: 3 });
  assert.equal(legacy.long_kick_point_share, undefined);
  assert.equal(legacy.made_50_plus, undefined);
});

test("projection presence flags separate missing rows and keys from projected zero", () => {
  const actual = [1, 2, 3].map((week) => buildUsageRow({ player_id: "wr", stats: { rec_tgt: 6, pts_ppr: 10 } }, "wr", "WR", "stats", week, {}));
  const projections = [
    buildUsageRow({ player_id: "wr", stats: { rec: 3 } }, "wr", "WR", "projections", 2, {}),
    buildUsageRow({ player_id: "wr", stats: { rec_tgt: 0, pts_ppr: 0 } }, "wr", "WR", "projections", 3, {})
  ];
  const [missingRow, missingKey, projectedZero] = workerProjectionComparison("WR", actual, projections);
  assert.equal(missingRow.classification, "same_week_delta");
  assert.equal(missingRow.actual_source, "stats");
  assert.equal(missingRow.projection_source, "projections");
  assert.equal(missingRow.projection_row_present, false);
  assert.deepEqual(missingRow.projection_key_present, { points: false, rec_tgt: false });
  assert.equal(missingRow.projection_delta, null);
  assert.equal(missingKey.projection_row_present, true);
  assert.deepEqual(missingKey.projection_key_present, { points: false, rec_tgt: false });
  assert.equal(missingKey.projected_points, null);
  assert.deepEqual(projectedZero.projection_key_present, { points: true, rec_tgt: true });
  assert.equal(projectedZero.projected_points, 0);
  assert.deepEqual(projectedZero.projected_values, { rec_tgt: 0 });
  assert.equal(projectedZero.projection_delta, -10);
});

test("RB and TE missing inputs use red_zone_share", () => {
  for (const position of ["RB", "TE"]) {
    const result = evaluateWorkerUsageContext(position, [{ week: 1, targets: 5, weighted_touches: 10, points: 6, active: true, bye: false }], [], {});
    assert.ok(result.missing_inputs.includes("red_zone_share"));
    assert.ok(!result.missing_inputs.includes("red_zone_use"));
    assert.ok(result.reason_codes.includes("not_evaluable_missing_red_zone_share"));
  }
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

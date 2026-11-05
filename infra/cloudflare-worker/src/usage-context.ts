// Worker player usage context (v3). Lives outside index.ts so the Worker entry
// module exports only `default` (wrangler treats every named export of the main
// module as an entrypoint and rejects non-handler values).
import { type JsonMap, calculateFantasyPoints, numberValue, objectValue, round } from "./shared.ts";

// Worker emits the v3 contract: exact keys, weighted_touches, position-aware
// sacks, last_1_week, change-point post_change_window (mirrors Python), same-week
// projection presence flags, and a labeled td_dependency_method. Python-only:
// the derived_metrics block and injury_replacement (no availability history).
export const PLAYER_USAGE_CONTEXT_SCHEMA_VERSION = "player_usage_context.v3";

const USAGE_MISSING_CONTEXT: Record<string, string[]> = {
  QB: ["pressure", "implied_total"],
  RB: ["touch_share", "red_zone_share", "nfl_schedule", "implied_total"],
  WR: ["routes", "target_share", "air_yard_share", "nfl_schedule"],
  TE: ["routes", "target_share", "red_zone_share", "nfl_schedule"],
  K: ["nfl_schedule", "weather", "implied_total"],
  DEF: ["nfl_schedule", "pressure", "implied_total"]
};

// Mirrors Python POSITION_USAGE_THRESHOLDS for the opportunity metric; used
// only by change-point detection.
const CHANGE_POINT_THRESHOLDS: Record<string, { pct?: number; perGame?: number }> = {
  QB: { pct: 0.15 },
  RB: { pct: 0.3, perGame: 5 },
  WR: { pct: 0.3, perGame: 2.5 },
  TE: { pct: 0.3, perGame: 2 },
  K: { pct: 0.25 },
  DEF: { pct: 0.3 }
};

const CHANGE_POINT_RULE = "earliest week i with >=1 earlier and >=2 later active non-bye weeks where every week from i through the latest clears the position role threshold against the mean of the weeks before i, in one direction (mirrors Python detect_change_point)";

export function opportunityKeyFor(position: string): string {
  return position === "QB" ? "dropbacks" : position === "RB" ? "weighted_touches" : ["WR", "TE"].includes(position) ? "targets" : position === "K" ? "total_kick_attempts" : "sacks";
}

function thresholdDirection(threshold: { pct?: number; perGame?: number }, value: number, base: number): string {
  const pct = base === 0 ? (value === 0 ? undefined : Infinity) : (value - base) / Math.abs(base);
  const delta = value - base;
  if ((threshold.pct !== undefined && pct !== undefined && pct > threshold.pct) || (threshold.perGame !== undefined && delta >= threshold.perGame)) return "increased";
  if ((threshold.pct !== undefined && pct !== undefined && pct <= -threshold.pct) || (threshold.perGame !== undefined && delta <= -threshold.perGame)) return "decreased";
  return "stable";
}

export function detectWorkerChangePoint(position: string, rows: JsonMap[], key = opportunityKeyFor(position)): JsonMap {
  const base: JsonMap = { rule: CHANGE_POINT_RULE, metric: key, change_week: null, direction: null, post_change_weeks: [], pre_change_weeks: [] };
  const threshold = CHANGE_POINT_THRESHOLDS[position];
  const eligible = eligibleRows(rows);
  const series = eligible.filter((row) => numberValue(row[key]) !== undefined);
  if (!threshold) return { ...base, status: "not_applicable" };
  if (eligible.length && !series.length) return { ...base, status: `not_evaluable_missing_${key}` };
  if (series.length < 3) return { ...base, status: "insufficient_sample" };
  for (let index = 1; index <= series.length - 2; index += 1) {
    const pre = series.slice(0, index);
    const post = series.slice(index);
    const preAverage = pre.reduce((sum, row) => sum + Number(row[key]), 0) / pre.length;
    const directions = new Set(post.map((row) => thresholdDirection(threshold, Number(row[key]), preAverage)));
    if (directions.size === 1 && !directions.has("stable")) {
      return { ...base, status: "change_detected", change_week: Number(post[0].week), direction: [...directions][0], post_change_weeks: post.map((row) => Number(row.week)), pre_change_weeks: pre.map((row) => Number(row.week)) };
    }
  }
  return { ...base, status: "no_change" };
}

export function evaluateWorkerUsageContext(position: string, actualRows: JsonMap[], projectionRows: JsonMap[], metadata: JsonMap): JsonMap {
  const primaryRows = actualRows.length ? actualRows : projectionRows;
  const opportunityKey = opportunityKeyFor(position);
  const changePoint = detectWorkerChangePoint(position, actualRows, opportunityKey);
  // Spike reasons keep reading the latest two weeks; the role signal and scores
  // use post_change_window when a change is detected.
  const spikeRows = eligibleRows(primaryRows).slice(-2);
  let recent = spikeRows;
  let baseline = eligibleRows(actualRows).slice(0, -2);
  let trendName = "last_2_weeks";
  if (changePoint.status === "change_detected") {
    const postWeeks = new Set(changePoint.post_change_weeks as number[]);
    const preWeeks = new Set(changePoint.pre_change_weeks as number[]);
    recent = eligibleRows(actualRows).filter((row) => postWeeks.has(Number(row.week)));
    baseline = eligibleRows(actualRows).filter((row) => preWeeks.has(Number(row.week)));
    trendName = "post_change_window";
  }
  const recentOpportunity = averageUsage(recent, opportunityKey);
  const baselineOpportunity = averageUsage(baseline, opportunityKey);
  const reasonCodes: string[] = [];
  const reasons: JsonMap[] = [];
  const modifiers: Record<string, number> = {};
  if (recentOpportunity !== undefined && baselineOpportunity !== undefined && baselineOpportunity !== 0) {
    const delta = (recentOpportunity - baselineOpportunity) / Math.abs(baselineOpportunity);
    const threshold = position === "RB" ? 0.3 : position === "WR" || position === "TE" ? 0.3 : position === "QB" ? 0.15 : position === "K" || position === "DEF" ? 0.25 : 0.3;
    if (delta >= threshold) {
      reasonCodes.push("role_change_recent");
      reasons.push(reason("role_change_recent", "opportunity", "positive", "partial", ["recent opportunity", "season baseline"], "medium", "recent opportunity increased beyond the position threshold"));
      modifiers.context = 4;
    } else if (delta <= -threshold) {
      reasonCodes.push("role_loss_recent");
      reasons.push(reason("role_loss_recent", "opportunity", "negative", "partial", ["recent opportunity", "season baseline"], "medium", "recent opportunity declined beyond the position threshold"));
      modifiers.context = -4;
    }
  } else if (primaryRows.length && primaryRows.every((row) => numberValue(row[opportunityKey]) === undefined)) {
    addMissingReason(reasonCodes, reasons, `not_evaluable_missing_${opportunityKey}`, "opportunity", `${opportunityKey} is missing; this signal is not evaluable`, [opportunityKey]);
  }
  if (reasonCodes.includes("role_change_recent") && ["RB", "WR", "TE"].includes(position)) {
    // injury_replacement needs week-specific teammate availability, which the
    // Worker does not store; say so instead of guessing.
    addMissingReason(reasonCodes, reasons, "not_evaluable_missing_teammate_availability", "one_off_risk", "the role increase cannot be checked for an injury replacement because week-specific teammate availability is missing (the Worker has no availability history)", ["teammate_availability"]);
  }
  if (metadata.depth_chart_role) {
    reasonCodes.push("current_metadata_only_depth_chart");
    reasons.push(reason("current_metadata_only_depth_chart", "depth_chart_confidence", "neutral", "current_metadata_only", ["current depth metadata"], "medium", "depth chart metadata is current-only and not historical", ["historical depth chart"]));
    modifiers.context = Math.min(6, (modifiers.context || 0) + 2);
  }
  const recentSpike = spikeRows.flatMap((row) => spikeReasons(position, row));
  for (const code of recentSpike) {
    if (!reasonCodes.includes(code)) reasonCodes.push(code);
    const spikeReason = spikeReasonDefinition(code);
    if (spikeReason) reasons.push(spikeReason);
    else addMissingReason(reasonCodes, reasons, code, "opportunity", `${code.replace("not_evaluable_missing_", "")} is missing; this signal is not evaluable`, [code.replace("not_evaluable_missing_", "")]);
    modifiers.context = Math.max(-6, (modifiers.context || 0) - (code === "long_kick_spike" ? 4.5 : 3));
  }
  if (!actualRows.length) addMissingReason(reasonCodes, reasons, "not_evaluable_missing_actual_stats", "context_confidence", "No actual Sleeper stats were available; projections are shown separately", ["actual stats"], "high");
  const missingInputs = new Set(USAGE_MISSING_CONTEXT[position] || []);
  if (!actualRows.length) missingInputs.add("actual_stats");
  for (const input of missingInputs) {
    const code = input === "pressure" ? "not_evaluable_missing_matchup_pressure" : input === "implied_total" ? "not_evaluable_missing_matchup_implied_total" : input === "weather" ? "not_evaluable_missing_matchup_weather" : `not_evaluable_missing_${input}`;
    const component = ["pressure", "implied_total", "weather", "nfl_schedule"].includes(input) ? "matchup_adjustment" : "context_confidence";
    if (!reasonCodes.includes(code)) addMissingReason(reasonCodes, reasons, code, component, `${input} is unavailable in Sleeper-only mode`, [input]);
  }
  const tdDependency = workerTdDependency(position, recent, trendName);
  const scores = workerScores(primaryRows, recent, baseline, position, modifiers, missingInputs.size, actualRows.length, tdDependency);
  const roleLabel = reasonCodes.includes("role_loss_recent") ? "role_declining" : reasonCodes.includes("role_change_recent") ? "emerging_rotation" : Number(metadata.depth_chart_order) === 1 ? "established_role" : "uncertain_role";
  const postRows = changePoint.status === "change_detected" ? recent : [];
  return {
    reason_codes: reasonCodes,
    reasons: uniqueReasons(reasons),
    missing_inputs: [...missingInputs].sort(),
    modifiers,
    scores: { ...scores, matchup_adjustment: 0 },
    role_label: roleLabel,
    trend_window: { name: trendName, weeks: recent.map((row) => Number(row.week)), baseline_weeks: baseline.map((row) => Number(row.week)), change_point: changePoint },
    post_change_window: { detected: changePoint.status === "change_detected", status: changePoint.status, change_week: changePoint.change_week, direction: changePoint.direction, pre_change_weeks: changePoint.pre_change_weeks, actual: postRows, sample_size: postRows.length, minimum_samples: 2 },
    td_dependency: tdDependency,
    td_dependency_method: tdDependency.method,
    source_flags: { actual_stats: actualRows.length > 0, projections: projectionRows.length > 0, player_metadata: Object.keys(metadata).length > 0, role_snapshots: false, availability_snapshots: false, nfl_schedule: false, matchup_implied_total: false, league_scoring: actualRows.some((row) => row.scoring_breakdown !== undefined) }
  };
}

// TD-scoring keys: "*_td" or "*_td_*" contribution keys, except pass_int_td (a
// thrown pick-six penalty). Mirrors Python td_scoring_key_family.
const TD_SCORING_KEY_EXCLUSIONS = new Set(["pass_int_td"]);
const LONG_FIELD_GOAL_SCORING_KEYS = ["fgm_50_59", "fgm_60p", "fgm_50p"];
const FIELD_GOAL_YARDAGE_SCORING_KEYS = ["fgm_yds", "fgm_yds_over_30"];

export function isTdScoringKey(key: string): boolean {
  return !TD_SCORING_KEY_EXCLUSIONS.has(key) && (key.endsWith("_td") || key.includes("_td_"));
}

// Returns { method: "league_scoring_contributions" | "heuristic", ... }. The
// league method needs every trend row to carry a scoring_breakdown and
// league_points (only when league scoring_settings were available).
export function workerTdDependency(position: string, rows: JsonMap[], window: string): JsonMap {
  const leagueRows = rows.length > 0 && rows.every((row) => row.scoring_breakdown !== undefined && numberValue(row.league_points) !== undefined);
  if (leagueRows) {
    let tdPoints = 0;
    const keys = new Set<string>();
    for (const row of rows) {
      for (const [key, value] of Object.entries(objectValue(row.scoring_breakdown))) {
        const points = numberValue(value) || 0;
        if (points !== 0 && isTdScoringKey(key)) {
          tdPoints += points;
          keys.add(key);
        }
      }
    }
    const denominator = rows.reduce((sum, row) => sum + (numberValue(row.league_points) || 0), 0);
    const value = denominator > 0 ? round(tdPoints / denominator, 4) : null;
    return { method: "league_scoring_contributions", source: "worker calculateFantasyPoints(league scoring_settings)", classification: "actual", window, weeks: rows.map((row) => Number(row.week)), td_scoring_keys_used: [...keys].sort(), td_points: round(tdPoints, 4), fantasy_points: round(denominator, 4), value, reason: value === null ? "denominator_non_positive" : null };
  }
  const points = averageUsage(rows, "points") || 0;
  const td = rows.reduce((sum, row) => sum + (numberValue(row.pass_tds) || 0) + (numberValue(row.rush_tds) || 0) + (numberValue(row.rec_tds) || 0) + (numberValue(row.def_tds) || 0), 0) / Math.max(rows.length, 1);
  return { method: "heuristic", source: "touchdown counts", classification: "actual", formula: "min(100, td_count_per_game * 20 / max(points_per_game, 1))", window, weeks: rows.map((row) => Number(row.week)), value: null, heuristic_score: round(Math.min(100, td * 20 / Math.max(points, 1)), 2), reason: "league_scoring_settings_unavailable" };
}

// long_kick_point_share = long-FG league points / league points for one row.
// Undefined without a breakdown, or when the league scores FGs by yardage
// (fgm_yds) so long kicks cannot be separated.
function longKickPointShare(stats: JsonMap, breakdown: JsonMap, points: number): [number | undefined, string | undefined] {
  const longPoints = LONG_FIELD_GOAL_SCORING_KEYS.reduce((sum, key) => sum + (numberValue(breakdown[key]) || 0), 0);
  const yardagePoints = FIELD_GOAL_YARDAGE_SCORING_KEYS.reduce((sum, key) => sum + Math.abs(numberValue(breakdown[key]) || 0), 0);
  const longMakes = numberValue(stats.fgm_50p) ?? ((numberValue(stats.fgm_50_59) || 0) + (numberValue(stats.fgm_60p) || 0));
  if (longMakes > 0 && longPoints === 0 && yardagePoints > 0) return [undefined, "not_evaluable_long_field_goal_points_not_separable"];
  if (points <= 0) return [undefined, "denominator_non_positive"];
  return [round(longPoints / points, 4), undefined];
}

const PROJECTION_KEYS: Record<string, string[]> = { QB: ["pass_att", "pass_sack"], RB: ["rush_att", "rec_tgt"], WR: ["rec_tgt"], TE: ["rec_tgt"], K: ["fga", "xpa"], DEF: ["sack"] };

// Same-week actual vs projection pairing; a missing row or key is never a projected zero.
export function workerProjectionComparison(position: string, actualRows: JsonMap[], projectionRows: JsonMap[]): JsonMap[] {
  const keys = PROJECTION_KEYS[position] || [];
  return actualRows.map((actual) => {
    const projection = projectionRows.find((row) => Number(row.week) === Number(actual.week));
    const stats = objectValue(projection?.raw_stats);
    const pointsPresent = projection !== undefined && projection.points_source !== undefined && numberValue(projection.points) !== undefined;
    const keyPresent: JsonMap = { points: pointsPresent };
    for (const key of keys) keyPresent[key] = numberValue(stats[key]) !== undefined;
    const projectedPoints = pointsPresent ? numberValue(projection?.points) : undefined;
    const actualPoints = numberValue(actual.points);
    return {
      week: Number(actual.week),
      classification: "same_week_delta",
      actual_source: "stats",
      projection_source: "projections",
      projection_row_present: projection !== undefined,
      projection_key_present: keyPresent,
      actual_points: actualPoints ?? null,
      projected_points: projectedPoints ?? null,
      projected_values: Object.fromEntries(keys.map((key) => [key, numberValue(stats[key]) ?? null])),
      projection_delta: projectedPoints !== undefined && actualPoints !== undefined ? round(projectedPoints - actualPoints, 4) : null
    };
  });
}

function reason(code: string, component: string, polarity: string, tier: string, evidence: string[], severity: string, description: string, missingInputs: string[] = []): JsonMap { return { code, component, polarity, computability_tier: tier, evidence, severity, description, missing_inputs: missingInputs }; }
function addMissingReason(codes: string[], reasons: JsonMap[], code: string, component: string, description: string, missingInputs: string[], severity = "medium"): void { if (!codes.includes(code)) codes.push(code); reasons.push(reason(code, component, "neutral", "not_evaluable_missing_source", [], severity, description, missingInputs)); }
function uniqueReasons(reasons: JsonMap[]): JsonMap[] { return reasons.filter((item, index, all) => all.findIndex((candidate) => candidate.code === item.code) === index); }
export function metadataContext(player: JsonMap): JsonMap { return Object.fromEntries(Object.entries({ depth_chart_order: player.depth_chart_order, depth_chart_position: player.depth_chart_position, status: player.status, injury_status: player.injury_status, depth_chart_role: player.depth_chart_position || player.depth_chart_order }).filter(([, value]) => value !== undefined && value !== null)); }
function eligibleRows(rows: JsonMap[]): JsonMap[] { return rows.filter((row) => row.active !== false && row.bye !== true).sort((a, b) => Number(a.week) - Number(b.week)); }
export function usageWindows(actualRows: JsonMap[], projectionRows: JsonMap[]): JsonMap { const output: JsonMap = {}; for (const [name, count] of [["last_1_week", 1], ["last_2_weeks", 2], ["last_3_weeks", 3], ["last_4_weeks", 4], ["season_to_date", undefined]] as const) { const actual = eligibleRows(actualRows); const projection = eligibleRows(projectionRows); output[name] = { actual: count === undefined ? actual : actual.slice(-count), projection: count === undefined ? projection : projection.slice(-count), sample_size: count === undefined ? actual.length : actual.slice(-count).length }; } return output; }
function workerScores(primary: JsonMap[], recent: JsonMap[], baseline: JsonMap[], position: string, modifiers: Record<string, number>, missingCount: number, actualCount: number, tdDependency: JsonMap): JsonMap { const key = opportunityKeyFor(position); const opportunity = averageUsage(recent, key) || 0; const points = averageUsage(recent, "points") || 0; const baselineOpportunity = averageUsage(baseline, key); const tdValue = numberValue(tdDependency.value); const tdScore = tdDependency.method === "league_scoring_contributions" ? (tdValue === undefined ? null : round(Math.max(0, Math.min(100, tdValue * 100)), 2)) : numberValue(tdDependency.heuristic_score) ?? 0; return { opportunity_score: round(Math.min(100, opportunity * 5), 2), role_stability_score: recent.length ? 60 : 0, production_quality_score: round(Math.min(100, Math.max(0, points * 4)), 2), td_dependency_score: tdScore, depth_chart_confidence: 60, small_sample_risk: Math.max(0, 100 - recent.length * 35), one_off_risk: modifiers.context && modifiers.context < 0 ? round(-modifiers.context * 12.5, 2) : 0, trend_change_score: round(Math.max(0, Math.min(100, 50 + (baselineOpportunity ? ((opportunity - baselineOpportunity) / Math.abs(baselineOpportunity)) * 12.5 : 0))), 2), season_context_score: 50, context_confidence: round(Math.max(0, 100 - missingCount * 7 - (actualCount < 2 ? 20 : 0)), 2) }; }
function spikeReasons(position: string, row: JsonMap): string[] { const points = numberValue(row.points) || 0; const tds = (numberValue(row.pass_tds) || 0) + (numberValue(row.rush_tds) || 0) + (numberValue(row.rec_tds) || 0) + (numberValue(row.def_tds) || 0); const codes: string[] = []; const opportunityKey = position === "QB" ? "rush_attempts" : position === "RB" ? "weighted_touches" : ["WR", "TE"].includes(position) ? "targets" : undefined; if (position === "RB" && points >= 20 && numberValue(row.weighted_touches) !== undefined && Number(row.weighted_touches) <= 12) codes.push("low_touch_big_points"); if (["WR", "TE"].includes(position) && points >= 15 && numberValue(row.targets) !== undefined && Number(row.targets) <= (position === "TE" ? 4 : 5)) codes.push("low_target_big_points"); if (["QB", "RB", "WR", "TE"].includes(position) && tds >= (position === "TE" ? 1 : position === "WR" || position === "RB" ? 2 : 3)) { if (opportunityKey && numberValue(row[opportunityKey]) === undefined) codes.push(`not_evaluable_missing_${opportunityKey}`); else if (opportunityKey) codes.push("td_only_low_usage"); } if (position === "K" && (Number(row.made_50_plus) >= 2 || Number(row.long_kick_point_share) >= 0.4)) codes.push("long_kick_spike"); if (position === "DEF" && Number(row.def_tds) >= 1) codes.push("def_td_spike"); return codes; }
function spikeReasonDefinition(code: string): JsonMap | undefined { const definitions: Record<string, JsonMap> = { low_touch_big_points: reason(code, "one_off_risk", "negative", "computed", ["fantasy points", "weighted touches (rush_att + rec_tgt)"], "high", "skill-player points spiked on a low weighted-touch week"), low_target_big_points: reason(code, "one_off_risk", "negative", "computed", ["fantasy points", "targets"], "high", "receiving points spiked on a low-target week"), td_only_low_usage: reason(code, "td_dependency", "negative", "computed", ["weighted touches or targets", "touchdown stats"], "high", "touchdown points dominate a low-usage performance"), long_kick_spike: reason(code, "one_off_risk", "negative", "computed", ["long field goals", "kicking points"], "medium", "kicker value was inflated by long field goals"), def_td_spike: reason(code, "one_off_risk", "negative", "computed", ["defensive touchdown stats"], "high", "defensive value was inflated by a touchdown") }; return definitions[code]; }

export function buildUsageRow(row: JsonMap, playerId: string, position: string, source: string, week: number, player: JsonMap, scoringSettings?: JsonMap): JsonMap {
  const stats = objectValue(row.stats);
  const sleeperPoints = numberValue(stats.pts_ppr) ?? numberValue(stats.pts_half_ppr) ?? numberValue(stats.pts_std);
  const usageRow: JsonMap = { week, player_id: playerId, team: String(player.team || ""), position, source, raw: row, raw_stats: stats, points: sleeperPoints, points_source: sleeperPoints === undefined ? undefined : "sleeper_points", sleeper_points: sleeperPoints, active: true, bye: false, ...normalizeUsageStats(position, stats) };
  if (scoringSettings) {
    // League points and per-stat breakdown, the same computation as Python
    // scoring.calculate_fantasy_points at sync time.
    const [points, breakdown] = calculateFantasyPoints(stats, scoringSettings);
    usageRow.points = points;
    usageRow.points_source = "league_scoring";
    usageRow.league_points = points;
    usageRow.scoring_breakdown = breakdown;
    if (position === "K" && source === "stats") {
      const [share, reasonCode] = longKickPointShare(stats, breakdown, points);
      usageRow.long_kick_point_share = share;
      if (reasonCode) usageRow.long_kick_point_share_reason = reasonCode;
    }
  }
  return usageRow;
}

export function appendUsageRow(target: JsonMap[], rows: JsonMap[], playerId: string, position: string, source: string, week: number, player: JsonMap, scoringSettings?: JsonMap): void {
  const row = rows.find((candidate) => String(candidate.player_id || "") === playerId);
  if (!row) return;
  target.push(buildUsageRow(row, playerId, position, source, week, player, scoringSettings));
}

function sumPresent(values: Array<number | undefined>): number | undefined {
  return values.every((value) => value === undefined) ? undefined : values.reduce<number>((sum, value) => sum + (value || 0), 0);
}

// Mirrors the Python STAT_KEYS contract: each field reads one exact Sleeper key,
// sacks are position-aware (QB pass_sack for dropbacks, DEF sack), and the only
// sums are of distinct canonical stats (DEF takeaways = int + fum_rec).
export function normalizeUsageStats(position: string, stats: JsonMap): JsonMap {
  const normalized: JsonMap = {};
  normalized.pass_attempts = numberValue(stats.pass_att);
  normalized.rush_attempts = numberValue(stats.rush_att);
  normalized.targets = numberValue(stats.rec_tgt);
  normalized.receptions = numberValue(stats.rec);
  normalized.pass_tds = numberValue(stats.pass_td);
  normalized.rush_tds = numberValue(stats.rush_td);
  normalized.rec_tds = numberValue(stats.rec_td);
  normalized.field_goal_attempts = numberValue(stats.fga);
  normalized.made_50_plus = numberValue(stats.fgm_50p);
  // long_kick_point_share is not derived from counts here: it needs league
  // scoring points (see buildUsageRow / longKickPointShare). The legacy
  // fgm_50_plus key never appears in Sleeper data and is not read.
  if (position === "QB") normalized.sacks_taken = numberValue(stats.pass_sack);
  if (position === "DEF") {
    normalized.sacks = numberValue(stats.sack);
    normalized.def_tds = numberValue(stats.def_td);
    normalized.takeaways = sumPresent([numberValue(stats.int), numberValue(stats.fum_rec)]);
  }
  normalized.total_kick_attempts = sumPresent([numberValue(stats.fga), numberValue(stats.xpa)]);
  normalized.dropbacks = sumPresent([numberValue(stats.pass_att), numberValue(normalized.sacks_taken)]);
  // weighted_touches (rush_att + rec_tgt) is the RB opportunity metric; touches (rush_att + rec) is evidence only.
  normalized.weighted_touches = sumPresent([numberValue(stats.rush_att), numberValue(stats.rec_tgt)]);
  normalized.touches = sumPresent([numberValue(stats.rush_att), numberValue(stats.rec)]);
  return Object.fromEntries(Object.entries(normalized).filter(([, value]) => value !== undefined));
}

export function averageUsage(rows: JsonMap[], key: string): number | undefined {
  const values = rows.map((row) => numberValue(row[key])).filter((value): value is number => value !== undefined);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : undefined;
}

// --- Usage context integration (issue #32) -----------------------------------
// Mirrors python/src/sleeper_tooling/usage_context_integration.py: a compact
// summary block, per-player bounded components decomposed by reason code, and a
// capped add-minus-drop move delta. Worker divergences: there is no
// score_waiver_move matrix, so the delta is added to priority_score; move values
// are projection-only, so the TD-dependency component is never applied; and the
// heuristic TD method (no league scoring) is not evaluable against thresholds.

export const USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION = "usage_context_summary.v1";
export const USAGE_CONTEXT_ADJUSTMENT_VERSION = "usage_context_adjustment.v1";
export const USAGE_SUPPORTED_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];
const MOVE_SCORE_ACTUAL_PPG_WEIGHT = 2.6;
// Mirrors Python TD_DEPENDENCY_FORMULAS dependency_threshold (as 0-100) and
// TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT / TD_DEPENDENCY_WEIGHT_PROVENANCE.
const TD_THRESHOLD_PCT: Record<string, number> = { QB: 45, RB: 35, WR: 40, TE: 45, K: 40, DEF: 50 };
export const TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT: Record<string, number> = { QB: 0.05, RB: 0.15, WR: 0.15, TE: 0.15, K: 0.1, DEF: 0.1 };
const TD_WEIGHT_PROVENANCE: Record<string, string> = { QB: "calibrated_preliminary_prior", RB: "calibrated_preliminary_prior", WR: "calibrated_preliminary_prior", TE: "calibrated_preliminary_prior", K: "uncalibrated_prior", DEF: "uncalibrated_prior" };
// Mirrors Python _REASON_MODIFIERS amounts for the scored codes.
const USAGE_REASON_MODIFIERS: Record<string, number> = { role_change_recent: 4, role_loss_recent: -4, injury_replacement: -2, projection_lagging_role_change: 4, low_touch_big_points: -3, low_target_big_points: -3, td_only_low_usage: -3, def_td_spike: -3, turnover_spike_without_pressure: -3, long_kick_spike: -4.5 };
const COMPONENT_CAPS: Record<string, [number, number]> = { opportunity_role: [-4, 4], trend_change: [-4, 4], one_off_risk: [-6, 0], role_uncertainty: [-2, 0], td_dependency: [-3, 0], matchup: [-2, 2] };
const CONTEXT_CAP: [number, number] = [-6, 6];
const OPPORTUNITY_ROLE_CODES = ["role_change_recent", "role_loss_recent", "injury_replacement"];
const TREND_CHANGE_CODES = ["projection_lagging_role_change"];
const ONE_OFF_CODES = ["low_touch_big_points", "low_target_big_points", "td_only_low_usage", "def_td_spike", "turnover_spike_without_pressure", "long_kick_spike"];
const SEVERE_ONE_OFF_CODES = ["low_touch_big_points", "low_target_big_points"];
const POSITIVE_SIGNAL_CODES = ["role_change_recent", "projection_lagging_role_change"];
const RISK_SIGNAL_CODES = ["role_loss_recent", "injury_replacement", "season_average_stale"];
const EXCLUDED_SCORING_CODES: Record<string, string> = {
  season_average_stale: "points-based (recent vs season fantasy points); recent scoring is already priced by player_values",
  current_metadata_only_depth_chart: "current-only metadata present for nearly every player; not week-specific evidence"
};
// Usage-context missing inputs -> context provider inputs. null = intentionally
// unmapped because the provider metric is a different quantity.
export const USAGE_INPUT_PROVIDER_INPUTS: Record<string, string | null> = {
  red_zone_share: "red_zone_use",
  routes: "routes",
  target_share: "target_share",
  pressure: "pressure",
  implied_total: "implied_totals",
  weather: "weather",
  touch_share: null,
  air_yard_share: null,
  nfl_schedule: null,
  actual_stats: null
};
const SUMMARY_SCORE_KEYS = ["opportunity_score", "role_stability_score", "trend_change_score", "small_sample_risk", "one_off_risk", "td_dependency_score", "context_confidence"];

function clampValue(value: number, bounds: [number, number]): number {
  return round(Math.max(bounds[0], Math.min(bounds[1], value)), 2);
}

export function tdExcessPct(position: string, score: number | null | undefined): number | null {
  if (score === null || score === undefined) return null;
  return round(Math.max(0, score - (TD_THRESHOLD_PCT[position] ?? 0)), 2);
}

export function workerUsageContextSummary(evaluated: JsonMap, identity: { playerId: string; position: string; contextWeek: number; decisionWeek: number; actualFirst: boolean }): JsonMap {
  const scores = objectValue(evaluated.scores);
  const codes = (evaluated.reason_codes as string[] | undefined) || [];
  const trend = objectValue(evaluated.trend_window);
  const changePoint = objectValue(trend.change_point);
  const td = objectValue(evaluated.td_dependency);
  const method = String(evaluated.td_dependency_method || td.method || "");
  const tdScore = scores.td_dependency_score === null || scores.td_dependency_score === undefined ? null : numberValue(scores.td_dependency_score) ?? null;
  let tdStatus = "evaluated";
  let tdForExcess: number | null = tdScore;
  if (method !== "league_scoring_contributions") {
    tdStatus = "not_evaluable_missing_league_scoring_contributions";
    tdForExcess = null;
  } else if (tdScore === null) {
    tdStatus = String(td.reason || "not_evaluable_missing_scoring_contributions");
  }
  const missing = (evaluated.missing_inputs as string[] | undefined) || [];
  return {
    schema_version: USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION,
    status: identity.actualFirst ? "evaluated" : "not_evaluable_missing_actual_stats",
    player_id: identity.playerId,
    position: identity.position,
    decision_week: identity.decisionWeek,
    evaluated_through_week: identity.contextWeek,
    role_label: evaluated.role_label ?? null,
    context_confidence: scores.context_confidence ?? null,
    trend_window: { name: trend.name ?? null, weeks: trend.weeks || [], baseline_weeks: trend.baseline_weeks || [], change_status: changePoint.status ?? null, change_week: changePoint.change_week ?? null, change_direction: changePoint.direction ?? null },
    positive_signals: codes.filter((code) => POSITIVE_SIGNAL_CODES.includes(code)),
    risk_signals: codes.filter((code) => RISK_SIGNAL_CODES.includes(code)),
    one_off_flags: codes.filter((code) => ONE_OFF_CODES.includes(code)),
    reason_codes: codes.filter((code) => !code.startsWith("not_evaluable_")),
    not_evaluable: codes.filter((code) => code.startsWith("not_evaluable_")),
    missing_inputs: missing,
    provider_inputs_for_missing: Object.fromEntries(missing.filter((name) => USAGE_INPUT_PROVIDER_INPUTS[name]).map((name) => [name, USAGE_INPUT_PROVIDER_INPUTS[name]])),
    injury_replacement: null,
    td_dependency: { status: tdStatus, method, window: td.window ?? null, score: tdScore, threshold_pct: TD_THRESHOLD_PCT[identity.position] ?? null, excess_pct: tdExcessPct(identity.position, tdForExcess) },
    score_components: Object.fromEntries(SUMMARY_SCORE_KEYS.map((key) => [key, scores[key] ?? null]))
  };
}

export function unavailableUsageContext(status: string, playerId: string, position: string | null, decisionWeek: number | null, detail?: string): JsonMap {
  return {
    schema_version: USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION,
    status,
    player_id: playerId,
    position: position || null,
    decision_week: decisionWeek,
    evaluated_through_week: decisionWeek ? decisionWeek - 1 : null,
    role_label: null,
    context_confidence: null,
    trend_window: null,
    positive_signals: [],
    risk_signals: [],
    one_off_flags: [],
    reason_codes: [],
    not_evaluable: status.startsWith("not_evaluable_") ? [status] : [],
    missing_inputs: [],
    provider_inputs_for_missing: {},
    injury_replacement: null,
    td_dependency: null,
    score_components: null,
    ...(detail ? { detail } : {})
  };
}

export function notEvaluatedOutsideBudget(playerId: unknown, position: unknown, decisionWeek: number | null): JsonMap {
  return unavailableUsageContext("not_evaluated_outside_context_budget", String(playerId || ""), position ? String(position).toUpperCase() : null, decisionWeek);
}

export function workerPlayerUsageAdjustment(summary: JsonMap | undefined, actualsInValue: boolean): JsonMap {
  if (!summary || summary.status !== "evaluated") {
    return { status: String(summary?.status || "not_evaluable_missing_usage_context"), total: null, components: {} };
  }
  const codes = (summary.reason_codes as string[] | undefined) || [];
  const position = String(summary.position || "").toUpperCase();
  const td = objectValue(summary.td_dependency);
  const tdEvaluable = td.status === "evaluated" && numberValue(td.excess_pct) !== undefined;
  const components: Record<string, JsonMap> = {};
  const fromCodes = (name: string, allowed: string[], skip: string[] = []): void => {
    const used = codes.filter((code) => allowed.includes(code) && !skip.includes(code));
    const raw = used.reduce((sum, code) => sum + (USAGE_REASON_MODIFIERS[code] || 0), 0);
    components[name] = { status: "evaluated", value: clampValue(raw, COMPONENT_CAPS[name]), raw: round(raw, 2), codes: used };
  };
  fromCodes("opportunity_role", OPPORTUNITY_ROLE_CODES);
  fromCodes("trend_change", TREND_CHANGE_CODES);
  fromCodes("one_off_risk", ONE_OFF_CODES, tdEvaluable ? ["td_only_low_usage"] : []);
  const uncertaintyCodes: string[] = [];
  let uncertainty = 0;
  if (summary.role_label === "uncertain_role") { uncertainty -= 1; uncertaintyCodes.push("uncertain_role"); }
  const trendWeeks = (objectValue(summary.trend_window).weeks as unknown[] | undefined) || [];
  if (trendWeeks.length < 2) { uncertainty -= 1; uncertaintyCodes.push("small_trend_sample"); }
  components.role_uncertainty = { status: "evaluated", value: clampValue(uncertainty, COMPONENT_CAPS.role_uncertainty), raw: uncertainty, codes: uncertaintyCodes };
  if (!tdEvaluable) {
    components.td_dependency = { status: String(td.status || "not_evaluable_missing_scoring_contributions"), value: null };
  } else if (!actualsInValue) {
    components.td_dependency = { status: "not_applicable_actual_points_not_in_move_value", value: null, excess_pct: td.excess_pct };
  } else {
    const weight = TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT[position] || 0;
    const raw = -(MOVE_SCORE_ACTUAL_PPG_WEIGHT * weight * Number(td.excess_pct));
    components.td_dependency = { status: "evaluated", value: clampValue(raw, COMPONENT_CAPS.td_dependency), raw: round(raw, 2), excess_pct: td.excess_pct, ppg_regression_per_excess_pct: weight, move_score_per_ppg: MOVE_SCORE_ACTUAL_PPG_WEIGHT, weight_provenance: TD_WEIGHT_PROVENANCE[position] ?? null };
  }
  const notEvaluable = [...((summary.not_evaluable as string[] | undefined) || []), ...codes];
  components.matchup = { status: notEvaluable.includes("not_evaluable_missing_nfl_schedule") ? "not_evaluable_missing_nfl_schedule" : "not_evaluable_missing_matchup_strength", value: null };
  const rawTotal = Object.values(components).reduce((sum, row) => sum + (numberValue(row.value) ?? 0), 0);
  return {
    status: "evaluated",
    total: clampValue(rawTotal, CONTEXT_CAP),
    raw_total: round(rawTotal, 2),
    cap: CONTEXT_CAP,
    components,
    excluded_codes: Object.fromEntries(codes.filter((code) => code in EXCLUDED_SCORING_CODES).map((code) => [code, EXCLUDED_SCORING_CODES[code]])),
    severe_one_off_flags: codes.filter((code) => SEVERE_ONE_OFF_CODES.includes(code))
  };
}

export function workerUsageMoveAdjustment(addSummary: JsonMap | undefined, dropSummary: JsonMap | undefined, hasDrop: boolean): JsonMap {
  if (!addSummary && !dropSummary) return { version: USAGE_CONTEXT_ADJUSTMENT_VERSION, status: "not_requested", applied_delta: 0, delta: null };
  // Worker move values are projection-only, so actual points never feed them.
  const add = workerPlayerUsageAdjustment(addSummary, false);
  const drop = hasDrop ? workerPlayerUsageAdjustment(dropSummary, false) : { status: "no_drop_candidate", total: null, components: {} };
  const base = { version: USAGE_CONTEXT_ADJUSTMENT_VERSION, add, drop, cap: CONTEXT_CAP };
  if (add.total === null || drop.total === null) {
    const side = add.total === null ? "add" : "drop";
    return { ...base, status: `not_applied_${side}_${side === "add" ? add.status : drop.status}`, applied_delta: 0, delta: null };
  }
  const rawDelta = Number(add.total) - Number(drop.total);
  const delta = clampValue(rawDelta, CONTEXT_CAP);
  return { ...base, status: "applied", raw_delta: round(rawDelta, 2), delta, applied_delta: delta };
}

export function startSitUsageEvidence(side: string, name: unknown, context: JsonMap): string {
  if (context.status !== "evaluated") return `${side} ${String(name || "")}: usage context ${String(context.status)}`;
  const parts = [`role ${String(context.role_label)}`];
  const signals = [...((context.positive_signals as string[]) || []), ...((context.risk_signals as string[]) || []), ...((context.one_off_flags as string[]) || [])];
  if (signals.length) parts.push(signals.join(", "));
  const td = objectValue(context.td_dependency);
  if (numberValue(td.excess_pct)) parts.push(`TD share ${String(td.score)} exceeds the ${String(td.threshold_pct)} threshold`);
  return `${side} ${String(name || "")}: usage context ${parts.join("; ")}`;
}

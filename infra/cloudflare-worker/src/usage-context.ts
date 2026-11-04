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

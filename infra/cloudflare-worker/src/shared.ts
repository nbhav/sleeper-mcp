// Small value helpers shared by the Worker entry module (index.ts) and the
// usage-context module. Keep index.ts free of named exports: in modules format
// wrangler treats every named export of the main module as an entrypoint.

export type JsonMap = Record<string, unknown>;

export function objectValue(value: unknown): JsonMap {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonMap : {};
}

export function arrayValue(value: unknown): JsonMap[] {
  return Array.isArray(value) ? value.map((item) => objectValue(item)) : [];
}

export function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  return undefined;
}

export function round(value: number, digits: number): number {
  const multiplier = 10 ** digits;
  return Math.round(value * multiplier) / multiplier;
}

export function calculateFantasyPoints(stats: JsonMap, scoringSettings: JsonMap): [number, JsonMap] {
  let total = 0;
  const breakdown: JsonMap = {};
  for (const [key, multiplierValue] of Object.entries(scoringSettings)) {
    const stat = numberValue(stats[key]);
    const multiplier = numberValue(multiplierValue);
    if (stat === undefined || multiplier === undefined) {
      continue;
    }
    const points = stat * multiplier;
    if (points === 0) {
      continue;
    }
    breakdown[key] = round(points, 4);
    total += points;
  }
  return [round(total, 2), breakdown];
}

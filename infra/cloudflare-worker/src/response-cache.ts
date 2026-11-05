// Raw Sleeper response cache (issue A: cache cost controls).
//
// * D1 `api_response_cache` holds payloads up to MAX_D1_RESPONSE_BYTES.
// * Larger payloads (the full `/players/nfl` map, ~15 MB) go to the Workers
//   Cache API when it is available, so player tools stop refetching it on
//   every call. Tools prefer the normalized player snapshot (d1-store.ts);
//   this is the fallback when the snapshot is missing or stale.
// * The table DDL runs once per D1 binding per isolate (migrations create it;
//   this only covers an unmigrated database).
// * Only known Sleeper URL shapes with bounded parameters are written, which
//   caps cache-key growth. Expired rows are evicted by the scheduled handler.

import { noteSleeperRead } from "./d1-store.ts";

export const MAX_D1_RESPONSE_BYTES = 900_000;
export const APP_BASE_URL = "https://api.sleeper.app/v1";
export const DATA_BASE_URL = "https://api.sleeper.com";

type CacheEnv = { SLEEPER_CACHE_DB?: D1Database };

const CACHE_TABLE_DDL = "CREATE TABLE IF NOT EXISTS api_response_cache (cache_key TEXT PRIMARY KEY, url TEXT NOT NULL, response_json TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)";
const ensuredBindings = new WeakSet<object>();
const LARGE_RESPONSE_URLS = new Set([`${APP_BASE_URL}/players/nfl`]);
const CACHEABLE_POSITIONS = new Set(["QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP"]);

export async function fetchSleeperJson(url: URL, env: CacheEnv): Promise<unknown> {
  const cacheKey = url.toString();
  noteSleeperRead(env);
  const cached = await cacheGet(cacheKey, env);
  if (cached !== undefined) {
    return cached;
  }
  const payload = await fetchUpstreamJson(url);
  await cacheSet(cacheKey, payload, ttlForUrl(cacheKey), env);
  return payload;
}

export async function fetchUpstreamJson(url: URL | string): Promise<unknown> {
  const response = await fetch(url, {
    headers: {
      "Accept": "application/json",
      "User-Agent": "sleeper-mcp-worker/0.1"
    }
  });
  if (!response.ok) {
    throw new Error(`Sleeper API request failed: ${response.status} ${url}`);
  }
  return response.json();
}

async function cacheGet(cacheKey: string, env: CacheEnv): Promise<unknown | undefined> {
  if (LARGE_RESPONSE_URLS.has(cacheKey)) {
    return edgeCacheGet(cacheKey);
  }
  if (!env.SLEEPER_CACHE_DB) {
    return undefined;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const row = await env.SLEEPER_CACHE_DB.prepare(
    "SELECT response_json FROM api_response_cache WHERE cache_key = ? AND expires_at > ?"
  ).bind(cacheKey, nowSeconds()).first<{ response_json: string }>();
  return row ? JSON.parse(row.response_json) : undefined;
}

async function cacheSet(cacheKey: string, payload: unknown, ttlSeconds: number, env: CacheEnv): Promise<void> {
  if (!isCacheableUrl(cacheKey)) {
    return;
  }
  const responseJson = JSON.stringify(payload);
  if (responseJson.length > MAX_D1_RESPONSE_BYTES) {
    await edgeCacheSet(cacheKey, responseJson, ttlSeconds);
    return;
  }
  if (!env.SLEEPER_CACHE_DB) {
    return;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const now = nowSeconds();
  await env.SLEEPER_CACHE_DB.prepare(
    "INSERT OR REPLACE INTO api_response_cache (cache_key, url, response_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?)"
  ).bind(cacheKey, cacheKey, responseJson, now + ttlSeconds, now).run();
}

export async function ensureCacheTable(db: D1Database): Promise<void> {
  if (ensuredBindings.has(db)) {
    return;
  }
  await db.prepare(CACHE_TABLE_DDL).run();
  ensuredBindings.add(db);
}

// The Workers Cache API is per data center and only active on a custom domain
// (this Worker's only route). It is absent under `node --test`.
function edgeCache(): Cache | undefined {
  const storage = (globalThis as { caches?: CacheStorage & { default?: Cache } }).caches;
  return storage?.default;
}

async function edgeCacheGet(cacheKey: string): Promise<unknown | undefined> {
  const cache = edgeCache();
  if (!cache) return undefined;
  try {
    const hit = await cache.match(new Request(cacheKey));
    return hit ? await hit.json() : undefined;
  } catch {
    return undefined;
  }
}

async function edgeCacheSet(cacheKey: string, responseJson: string, ttlSeconds: number): Promise<void> {
  const cache = edgeCache();
  if (!cache || !LARGE_RESPONSE_URLS.has(cacheKey)) return;
  try {
    await cache.put(new Request(cacheKey), new Response(responseJson, {
      headers: { "content-type": "application/json", "cache-control": `max-age=${ttlSeconds}` }
    }));
  } catch {
    // Best effort: a failed edge write only costs a refetch next time.
  }
}

// Allowlist of Sleeper URL shapes the Worker requests, with bounded parameter
// values. Anything else is still fetched but never written, so arbitrary tool
// arguments cannot grow the cache table without bound.
export function isCacheableUrl(rawUrl: string): boolean {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  const base = `${url.origin}`;
  const path = url.pathname;
  const params = [...url.searchParams.entries()];
  if (base === new URL(APP_BASE_URL).origin) {
    const appPath = path.replace(/^\/v1/, "");
    if (appPath === "/state/nfl" || appPath === "/players/nfl") return params.length === 0;
    if (/^\/players\/nfl\/trending\/(add|drop)$/.test(appPath)) {
      return params.length === 2
        && boundedInt(url.searchParams.get("lookback_hours"), 1, 168)
        && boundedInt(url.searchParams.get("limit"), 1, 500);
    }
    if (/^\/league\/\d{1,24}(\/users|\/rosters|\/(matchups|transactions)\/\d{1,2})?$/.test(appPath)) {
      return params.length === 0;
    }
    return false;
  }
  if (base === new URL(DATA_BASE_URL).origin) {
    if (!/^\/(stats|projections)\/nfl\/\d{4}\/\d{1,2}$/.test(path)) return false;
    const seen = new Set<string>();
    for (const [key, value] of params) {
      if (seen.has(key)) return false;
      seen.add(key);
      if (key === "season_type" && ["regular", "pre", "post"].includes(value)) continue;
      if (key === "position[]" && CACHEABLE_POSITIONS.has(value)) continue;
      if (key === "order_by" && value === "pts_ppr") continue;
      return false;
    }
    return true;
  }
  return false;
}

function boundedInt(value: string | null, min: number, max: number): boolean {
  if (value === null || !/^\d{1,4}$/.test(value)) return false;
  const parsed = Number(value);
  return parsed >= min && parsed <= max;
}

export function ttlForUrl(url: string): number {
  if (url.includes("/state/nfl")) return 300;
  if (url.includes("/trending/")) return 300;
  if (url.includes("/players/nfl")) return 21600;
  if (url.includes("/stats/") || url.includes("/projections/")) return 900;
  if (url.includes("/matchups/") || url.includes("/transactions/")) return 900;
  return 3600;
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

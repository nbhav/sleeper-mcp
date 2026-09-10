const PROTOCOL_VERSION = "2024-11-05";
const APP_BASE_URL = "https://api.sleeper.app/v1";
const DATA_BASE_URL = "https://api.sleeper.com";
const DEFAULT_POSITIONS = "QB,RB,WR,TE,K,DEF";
const DEFAULT_STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"];
const NON_STARTER_SLOTS = new Set(["BN", "BE", "IR", "TAXI"]);
const POSITION_SLOTS = new Set(["QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP"]);
const KNOWN_MARKET_TYPES = new Set(["free_agent", "waiver", "unknown"]);
const FLEX_SLOT_POSITIONS: Record<string, string[]> = {
  FLEX: ["RB", "WR", "TE"],
  "W/R/T": ["RB", "WR", "TE"],
  REC_FLEX: ["WR", "TE"],
  "WR/TE": ["WR", "TE"],
  "WR/RB": ["WR", "RB"],
  WRRB_FLEX: ["WR", "RB"],
  SUPER_FLEX: ["QB", "RB", "WR", "TE"],
  OP: ["QB", "RB", "WR", "TE"]
};
const MAX_D1_RESPONSE_BYTES = 900_000;

type JsonMap = Record<string, unknown>;
type Env = {
  SLEEPER_CACHE_DB?: D1Database;
  SLEEPER_DEFAULT_LEAGUE_ID?: string;
  SLEEPER_DEFAULT_ROSTER_ID?: string;
  SLEEPER_DEFAULT_OWNER_ID?: string;
};

const tools = [
  {
    name: "resolve_league_context",
    description: "Resolve league, owner, and roster IDs from a Sleeper league URL plus team/user name.",
    inputSchema: {
      type: "object",
      required: ["league_ref"],
      properties: {
        league_ref: { type: "string" },
        user_ref: { type: "string" },
        team_name: { type: "string", description: "Deprecated alias for user_ref." }
      }
    }
  },
  {
    name: "weekly_briefing",
    description: "League-aware weekly leaders plus waiver signal for the current or requested week.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        source: { type: "string", enum: ["stats", "projections"], default: "projections" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        leader_limit: { type: "integer", default: 5 },
        trend_limit: { type: "integer", default: 10 },
        lookback_hours: { type: "integer", default: 24 }
      }
    }
  },
  {
    name: "weekly_performance_backtest",
    description: "Back-test weekly leaders and deterministic week-over-week movers for a range of weeks.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        start_week: { type: "integer" },
        weeks: { type: "integer", default: 2 },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        source: { type: "string", enum: ["stats", "projections"], default: "stats" },
        limit: { type: "integer", default: 5 },
        movement_limit: { type: "integer", default: 5 }
      }
    }
  },
  {
    name: "waiver_watch",
    description: "Find trending unrostered players with projected value under league scoring.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        trend_type: { type: "string", enum: ["add", "drop"], default: "add" },
        lookback_hours: { type: "integer", default: 24 },
        trend_limit: { type: "integer", default: 100 },
        limit: { type: "integer", default: 25 }
      }
    }
  },
  {
    name: "my_lineup",
    description: "Return the current roster's starters and bench with slots, points so far, and league-scored projections.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS }
      }
    }
  },
  {
    name: "lineup_recommendations",
    description: "Recommend start/sit moves and compare roster players against available waiver/free-agent options.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        trend_limit: { type: "integer", default: 100 },
        lookback_hours: { type: "integer", default: 24 },
        min_delta: { type: "number", default: 1.0 },
        limit: { type: "integer", default: 10 }
      }
    }
  },
  {
    name: "waiver_wire_watch",
    description: "Return a compact actionable waiver shortlist with availability, projection, trends, injuries, and recent actuals.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        lookback_hours: { type: "integer", default: 24 },
        trend_limit: { type: "integer", default: 100 },
        limit: { type: "integer", default: 25 },
        recent_weeks: { type: "integer", default: 3 }
      }
    }
  },
  {
    name: "waiver_wire_by_position",
    description: "Return top waiver and free-agent options grouped by position with status, drop candidate, gain, and FAAB guidance.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        lookback_hours: { type: "integer", default: 24 },
        trend_limit: { type: "integer", default: 100 },
        per_position_limit: { type: "integer", default: 10 }
      }
    }
  },
  {
    name: "trade_opportunities",
    description: "Show every opposing team with needs, surplus, trade targets, multiple offer angles, and reasoning.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: "QB,RB,WR,TE" },
        targets_per_team: { type: "integer", default: 5 },
        offers_per_team: { type: "integer", default: 3 }
      }
    }
  },
  {
    name: "free_agent_watch",
    description: "Rank currently unrostered players by projection under league scoring.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        limit: { type: "integer", default: 25 }
      }
    }
  },
  {
    name: "injury_watch",
    description: "List injury-relevant players currently rostered in a league.",
    inputSchema: {
      type: "object",
      properties: { league_id: { type: "string" } }
    }
  },
  {
    name: "opponent_watch",
    description: "Summarize a roster's weekly opponent, projected starters, and injury flags.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" }
      }
    }
  },
  {
    name: "league_team_watch",
    description: "Show completed league transactions for a week, grouped into adds and drops.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        week: { type: "integer" }
      }
    }
  },
  {
    name: "player_card",
    description: "Return player metadata and chart-ready actual vs projected weekly points.",
    inputSchema: {
      type: "object",
      required: ["player_id"],
      properties: {
        player_id: { type: "string" },
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        weeks_back: { type: "integer", default: 6 }
      }
    }
  }
];

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return jsonResponse({ name: "sleeper-mcp", endpoint: "/mcp" });
    }
    if (url.pathname !== "/mcp") {
      return jsonResponse({ error: "not found" }, 404);
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "method not allowed" }, 405);
    }

    const message = (await request.json()) as JsonMap;
    const id = message.id;
    try {
      const result = await handleMcpMessage(message, env);
      if (result === null) {
        return new Response(null, { status: 202 });
      }
      return jsonResponse({ jsonrpc: "2.0", id, result });
    } catch (error) {
      return jsonResponse({
        jsonrpc: "2.0",
        id,
        error: {
          code: -32000,
          message: error instanceof Error ? error.message : String(error)
        }
      });
    }
  }
};

async function handleMcpMessage(message: JsonMap, env: Env): Promise<JsonMap | null> {
  const method = String(message.method || "");
  if (method === "notifications/initialized") {
    return null;
  }
  if (method === "initialize") {
    return {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: "sleeper-fantasy-tools", version: "0.1.0" }
    };
  }
  if (method === "tools/list") {
    return { tools };
  }
  if (method === "tools/call") {
    const params = objectValue(message.params);
    const name = String(params.name || "");
    const args = objectValue(params.arguments);
    const result = await callTool(name, args, env);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      isError: false
    };
  }
  throw new Error(`Unknown method: ${method}`);
}

async function callTool(name: string, args: JsonMap, env: Env): Promise<unknown> {
  switch (name) {
    case "resolve_league_context":
      return resolveLeagueContext(args, env);
    case "weekly_briefing":
      return weeklyBriefing(args, env);
    case "weekly_performance_backtest":
      return weeklyPerformanceBacktest(args, env);
    case "waiver_watch":
      return waiverWatch(args, env);
    case "my_lineup":
      return myLineup(args, env);
    case "lineup_recommendations":
      return lineupRecommendations(args, env);
    case "waiver_wire_watch":
      return waiverWireWatch(args, env);
    case "waiver_wire_by_position":
      return waiverWireByPosition(args, env);
    case "trade_opportunities":
      return tradeOpportunities(args, env);
    case "free_agent_watch":
      return freeAgentWatch(args, env);
    case "injury_watch":
      return injuryWatch(args, env);
    case "opponent_watch":
      return opponentWatch(args, env);
    case "league_team_watch":
      return leagueTeamWatch(args, env);
    case "player_card":
      return playerCard(args, env);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

async function resolveLeagueContext(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueRef = String(args.league_ref || "").trim();
  const userRef = String(args.user_ref || args.team_name || "").trim();
  if (!leagueRef) {
    throw new Error("league_ref is required");
  }
  if (!userRef) {
    throw new Error("user_ref is required");
  }

  const leagueId = extractLeagueId(leagueRef);
  const [users, rosters] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  const context = resolveLeagueContextFromRows(
    leagueId,
    userRef,
    arrayValue(users),
    arrayValue(rosters)
  );

  return {
    ...context,
    local_env_file: "/data/sleeper-mcp.env",
    env_text: renderContextEnv(context, leagueRef),
    cloudflare_vars: context.env,
    evidence: [
      "league_id was parsed from league_ref",
      "roster_id was matched from league users and rosters",
      "Cloudflare Workers cannot mutate runtime vars; set cloudflare_vars before deploy or through the Cloudflare dashboard"
    ]
  };
}

async function weeklyBriefing(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = optionalLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const source = stringArg(args, "source", "projections");
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, source, scoring, env);
  const trends = await getTrending("add", numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 10), env);
  const players = await getPlayers(env);
  return {
    season,
    week,
    league_id: leagueId,
    leader_source: source,
    scoring_source: leagueId || "sleeper_default_points",
    leaders: topPlayersByPosition(rows, numberArg(args, "leader_limit", 5)),
    waiver_signal: buildWaiverWatch(trends, players, rows, [], positions, "add")
  };
}

async function weeklyPerformanceBacktest(args: JsonMap, env: Env): Promise<JsonMap> {
  const weeks = numberArg(args, "weeks", 2);
  const limit = numberArg(args, "limit", 5);
  const movementLimit = numberArg(args, "movement_limit", 5);
  if (weeks < 1) throw new Error("weeks must be at least 1");
  if (limit < 1) throw new Error("limit must be at least 1");
  if (movementLimit < 1) throw new Error("movement_limit must be at least 1");

  const leagueId = optionalLeagueId(args, env);
  const [season, endWeek] = await resolveSeasonWeek(args, env);
  const startWeek = numberValue(args.start_week) ?? Math.max(1, endWeek - weeks + 1);
  const targetWeeks = Array.from({ length: weeks }, (_, index) => startWeek + index);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const source = stringArg(args, "source", "stats");
  validateStatSource(source);
  const scoring = await leagueScoringSettings(leagueId, env);
  const rankedByWeek: Record<string, JsonMap[]> = {};
  const weeklyLeaders: JsonMap[] = [];

  for (const targetWeek of targetWeeks) {
    const rows = await fetchRowsForPositions(season, targetWeek, positions, source, scoring, env);
    rankedByWeek[String(targetWeek)] = rankRowsByPosition(rows);
    weeklyLeaders.push({
      week: targetWeek,
      leaders: topPlayersByPosition(rows, limit)
    });
  }

  const comparisons: JsonMap[] = [];
  for (let index = 0; index < targetWeeks.length - 1; index += 1) {
    const previousWeek = targetWeeks[index];
    const currentWeek = targetWeeks[index + 1];
    comparisons.push(compareRankedWeeks({
      previousWeek,
      currentWeek,
      previousRows: rankedByWeek[String(previousWeek)] || [],
      currentRows: rankedByWeek[String(currentWeek)] || [],
      limit: movementLimit
    }));
  }

  return {
    season,
    start_week: startWeek,
    end_week: targetWeeks[targetWeeks.length - 1],
    weeks: targetWeeks,
    positions,
    source,
    league_id: leagueId,
    scoring_source: leagueId || "sleeper_default_points",
    weekly_leaders: weeklyLeaders,
    week_over_week: comparisons
  };
}

async function waiverWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const trendType = stringArg(args, "trend_type", "add");
  if (!["add", "drop"].includes(trendType)) {
    throw new Error("trend_type must be 'add' or 'drop'");
  }
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [trends, players, rosters] = await Promise.all([
    getTrending(trendType, numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 100), env),
    getPlayers(env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  return withContext(
    buildWaiverWatch(trends, players, rows, arrayValue(rosters), positions, trendType).slice(0, numberArg(args, "limit", 25)),
    { league_id: leagueId, season, week }
  );
}

async function myLineup(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  const scoring = objectValue(league.scoring_settings);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [users, rosters, matchups, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env)
  ]);
  return buildMyLineup({
    leagueId,
    rosterId,
    season,
    week,
    league,
    users: arrayValue(users),
    rosters: arrayValue(rosters),
    matchups: arrayValue(matchups),
    players,
    projectionRows
  });
}

async function lineupRecommendations(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const limit = numberArg(args, "limit", 10);
  const minDelta = numberArg(args, "min_delta", 1.0);
  if (limit < 1) throw new Error("limit must be at least 1");
  if (minDelta < 0) throw new Error("min_delta must be zero or greater");

  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  const scoring = objectValue(league.scoring_settings);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const lookbackHours = numberArg(args, "lookback_hours", 24);
  const trendLimit = numberArg(args, "trend_limit", 100);
  const [users, rosters, matchups, players, addTrends, dropTrends] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env),
    getTrending("add", lookbackHours, trendLimit, env),
    getTrending("drop", lookbackHours, trendLimit, env)
  ]);
  const rosterRows = arrayValue(rosters);
  const playerMap = recordValue(players);
  const lineup = buildMyLineup({
    leagueId,
    rosterId,
    season,
    week,
    league,
    users: arrayValue(users),
    rosters: rosterRows,
    matchups: arrayValue(matchups),
    players: playerMap,
    projectionRows
  });

  return buildLineupRecommendations({
    lineup,
    rosters: rosterRows,
    players: playerMap,
    projectionRows,
    addTrends,
    dropTrends,
    positions,
    minDelta,
    limit
  });
}

async function waiverWireWatch(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const limit = numberArg(args, "limit", 25);
  const recentWeeks = numberArg(args, "recent_weeks", 3);
  if (limit < 1) throw new Error("limit must be at least 1");
  if (recentWeeks < 0) throw new Error("recent_weeks must be at least 0");

  const scoring = await leagueScoringSettings(leagueId, env);
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [addTrends, dropTrends, players, rosters] = await Promise.all([
    getTrending("add", numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 100), env),
    getTrending("drop", numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 100), env),
    getPlayers(env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  const candidates = buildWaiverWatch(addTrends, players, projectionRows, arrayValue(rosters), positions, "add");
  const recentRows = await fetchRecentActuals(season, week, positions, scoring, recentWeeks, env);
  return {
    season,
    week,
    league_id: leagueId,
    positions,
    lookback_hours: numberArg(args, "lookback_hours", 24),
    scoring_source: leagueId,
    candidates: withContext(
      enrichWaiverCandidates(candidates, dropTrends, recentRows).slice(0, limit),
      { league_id: leagueId, season, week }
    ),
    evidence: [
      "candidates are unrostered in the league",
      "projected_points use league scoring",
      "recent_actual_points uses completed stats for prior weeks",
      "drop_trend_count is included to down-rank noisy add trends"
    ]
  };
}

async function waiverWireByPosition(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const perPositionLimit = numberArg(args, "per_position_limit", 10);
  if (perPositionLimit < 1) throw new Error("per_position_limit must be at least 1");

  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  const scoring = objectValue(league.scoring_settings);
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const lookbackHours = numberArg(args, "lookback_hours", 24);
  const trendLimit = numberArg(args, "trend_limit", 100);
  const [users, rosters, matchups, players, addTrends, dropTrends] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env),
    getTrending("add", lookbackHours, trendLimit, env),
    getTrending("drop", lookbackHours, trendLimit, env)
  ]);
  const rosterRows = arrayValue(rosters);
  const playerMap = recordValue(players);
  const projectionCandidates = buildFreeAgentWatch(projectionRows, rosterRows, playerMap, positions);
  const trendCandidates = buildWaiverWatch(addTrends, playerMap, projectionRows, rosterRows, positions, "add");
  const availableCandidates = mergeAvailableCandidates({
    projectionCandidates,
    trendCandidates,
    players: playerMap,
    addTrends,
    dropTrends
  });
  const lineup = buildMyLineup({
    leagueId,
    rosterId,
    season,
    week,
    league,
    users: arrayValue(users),
    rosters: rosterRows,
    matchups: arrayValue(matchups),
    players: playerMap,
    projectionRows
  });
  const rosterPlayers = arrayValue(lineup.lineup_table).filter((row) => row.player_id !== "0");
  return {
    season,
    week,
    league_id: leagueId,
    roster_id: rosterId,
    positions,
    per_position_limit: perPositionLimit,
    by_position: groupWaiverOptionsByPosition(availableCandidates, rosterPlayers, positions, perPositionLimit),
    evidence: [
      "options are grouped by position and exclude rostered players",
      "projected_gain_over_drop compares against an unprotected active roster drop candidate",
      "FAAB hints are included only when the acquisition market is known to be waiver"
    ]
  };
}

async function tradeOpportunities(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", "QB,RB,WR,TE"));
  const targetsPerTeam = numberArg(args, "targets_per_team", 5);
  const offersPerTeam = numberArg(args, "offers_per_team", 3);
  if (targetsPerTeam < 1) throw new Error("targets_per_team must be at least 1");
  if (offersPerTeam < 1) throw new Error("offers_per_team must be at least 1");

  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  const scoring = objectValue(league.scoring_settings);
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [users, rosters, matchups, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env)
  ]);
  const rosterRows = arrayValue(rosters);
  const playerMap = recordValue(players);
  const lineup = buildMyLineup({
    leagueId,
    rosterId,
    season,
    week,
    league,
    users: arrayValue(users),
    rosters: rosterRows,
    matchups: arrayValue(matchups),
    players: playerMap,
    projectionRows
  });
  return buildTradeOpportunities({
    leagueId,
    rosterId,
    season,
    week,
    lineup,
    users: arrayValue(users),
    rosters: rosterRows,
    players: playerMap,
    projectionRows,
    positions,
    targetsPerTeam,
    offersPerTeam
  });
}

async function freeAgentWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [players, rosters] = await Promise.all([
    getPlayers(env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  return withContext(
    buildFreeAgentWatch(rows, arrayValue(rosters), players, positions).slice(0, numberArg(args, "limit", 25)),
    { league_id: leagueId, season, week }
  );
}

async function injuryWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [users, rosters, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getPlayers(env)
  ]);
  return withContext(buildInjuryWatch(arrayValue(users), arrayValue(rosters), players), { league_id: leagueId });
}

async function opponentWatch(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const scoring = await leagueScoringSettings(leagueId, env);
  const positions = parsePositions(DEFAULT_POSITIONS);
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [users, rosters, matchups, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env)
  ]);
  return {
    league_id: leagueId,
    roster_id: rosterId,
    season,
    ...buildOpponentWatch(rosterId, week, arrayValue(users), arrayValue(rosters), arrayValue(matchups), players, projectionRows)
  };
}

async function leagueTeamWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [, week] = await resolveSeasonWeek(args, env);
  const [users, rosters, transactions, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/transactions/${week}`, env),
    getPlayers(env)
  ]);
  return withContext(buildLeagueTeamWatch(week, arrayValue(users), arrayValue(rosters), arrayValue(transactions), players), {
    league_id: leagueId,
    week
  });
}

async function playerCard(args: JsonMap, env: Env): Promise<JsonMap> {
  const playerId = String(args.player_id || "");
  if (!playerId) {
    throw new Error("player_id is required");
  }
  const leagueId = optionalLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const players = await getPlayers(env);
  const player = objectValue(players[playerId]);
  const position = String(player.position || "RB");
  const scoring = await leagueScoringSettings(leagueId, env);
  const weeksBack = numberArg(args, "weeks_back", 6);
  const weeklyPoints: JsonMap[] = [];
  const startWeek = Math.max(1, week - weeksBack + 1);

  for (let targetWeek = startWeek; targetWeek <= week; targetWeek += 1) {
    const [statsRows, projectionRows] = await Promise.all([
      fetchRowsForPositions(season, targetWeek, [position], "stats", scoring, env),
      fetchRowsForPositions(season, targetWeek, [position], "projections", scoring, env)
    ]);
    const statRow = findPlayerRow(statsRows, playerId);
    const projectionRow = findPlayerRow(projectionRows, playerId);
    weeklyPoints.push({
      week: targetWeek,
      actual_points: numberValue(statRow?.points) || 0,
      projected_points: numberValue(projectionRow?.points) || 0
    });
  }

  return {
    player_id: playerId,
    league_id: leagueId,
    name: playerName(player, playerId),
    team: String(player.team || ""),
    position,
    status: String(player.status || ""),
    injury_status: String(player.injury_status || ""),
    season,
    week,
    scoring_source: leagueId || "sleeper_default_points",
    chart_data: { weekly_points: weeklyPoints },
    evidence: [
      "actual_points and projected_points are calculated with league scoring when league_id is provided",
      "missing stat keys are treated as zero"
    ]
  };
}

async function resolveSeasonWeek(args: JsonMap, env: Env): Promise<[number, number]> {
  const season = numberValue(args.season);
  const week = numberValue(args.week);
  if (season !== undefined && week !== undefined) {
    return [season, week];
  }
  const resolvedSeason = season ?? currentSeasonYear();
  if (week !== undefined) {
    return [resolvedSeason, week];
  }
  const state = objectValue(await getApp("/state/nfl", env));
  return [resolvedSeason, Number(state.week)];
}

async function leagueScoringSettings(leagueId: string | undefined, env: Env): Promise<JsonMap | undefined> {
  if (!leagueId) {
    return undefined;
  }
  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  return objectValue(league.scoring_settings);
}

async function fetchRowsForPositions(
  season: number,
  week: number,
  positions: string[],
  source: string,
  scoringSettings: JsonMap | undefined,
  env: Env
): Promise<JsonMap[]> {
  validateStatSource(source);
  const rows: JsonMap[] = [];
  for (const position of positions) {
    const rawRows = arrayValue(await getData(`/${source}/nfl/${season}/${week}`, env, {
      "season_type": "regular",
      "position[]": position,
      "order_by": "pts_ppr"
    }));
    rows.push(...flattenRows(rawRows, scoringSettings).filter((row) => String(row.position || "").toUpperCase() === position));
  }
  return rows;
}

function flattenRows(rows: JsonMap[], scoringSettings: JsonMap | undefined): JsonMap[] {
  return rows.map((row) => {
    const player = objectValue(row.player);
    const stats = objectValue(row.stats);
    const sleeperPoints = stats.pts_ppr || stats.pts_half_ppr || stats.pts_std || row.pts_ppr || row.points || 0;
    if (!scoringSettings) {
      return {
        player_id: String(row.player_id || ""),
        name: String(player.full_name || playerName(player, String(row.player_id || ""))),
        team: String(player.team || ""),
        position: String(player.position || ""),
        points: sleeperPoints
      };
    }
    const [points, breakdown] = calculateFantasyPoints(stats, scoringSettings);
    return {
      player_id: String(row.player_id || ""),
      name: String(player.full_name || playerName(player, String(row.player_id || ""))),
      team: String(player.team || ""),
      position: String(player.position || ""),
      points,
      sleeper_points: sleeperPoints,
      scoring_rules_matched: Object.keys(breakdown).length,
      scoring_breakdown: breakdown
    };
  });
}

function calculateFantasyPoints(stats: JsonMap, scoringSettings: JsonMap): [number, JsonMap] {
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

function buildWaiverWatch(
  trends: JsonMap[],
  players: Record<string, JsonMap>,
  projectionRows: JsonMap[],
  rosters: JsonMap[],
  positions: string[],
  trendType: string
): JsonMap[] {
  const rostered = rosteredPlayerIds(rosters);
  const projections = Object.fromEntries(projectionRows.map((row) => [String(row.player_id || ""), row]));
  const allowed = new Set(positions.map((position) => position.toUpperCase()));
  return trends
    .flatMap((trend) => {
      const playerId = String(trend.player_id || "");
      if (!playerId || rostered.has(playerId)) {
        return [];
      }
      const player = objectValue(players[playerId]);
      const projection = objectValue(projections[playerId]);
      const position = String(player.position || projection.position || "");
      if (allowed.size > 0 && !allowed.has(position.toUpperCase())) {
        return [];
      }
      return [{
        player_id: playerId,
        name: playerName(player, playerId),
        ...playerContext(playerId, {
          players,
          projection,
          marketHint: inferMarketType(player, trend, "unknown")
        }),
        position,
        trend_type: trendType,
        trend_count: trend.count || 0,
        acquisition_action: "watch",
        projected_points: projection.points || 0,
        sleeper_projected_points: projection.sleeper_points || ""
      }];
    })
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points) || sortNumber(b.trend_count, a.trend_count));
}

function buildFreeAgentWatch(
  projectionRows: JsonMap[],
  rosters: JsonMap[],
  players: Record<string, JsonMap>,
  positions: string[]
): JsonMap[] {
  const rostered = rosteredPlayerIds(rosters);
  const allowed = new Set(positions.map((position) => position.toUpperCase()));
  return projectionRows
    .flatMap((projection) => {
      const playerId = String(projection.player_id || "");
      if (!playerId || rostered.has(playerId)) {
        return [];
      }
      const player = objectValue(players[playerId]);
      const position = String(player.position || projection.position || "");
      if (allowed.size > 0 && !allowed.has(position.toUpperCase())) {
        return [];
      }
      return [{
        player_id: playerId,
        name: playerName(player, playerId),
        ...playerContext(playerId, {
          players,
          projection,
          marketHint: inferMarketType(player, projection, "free_agent")
        }),
        position,
        acquisition_action: "add_now",
        projected_points: projection.points || 0,
        sleeper_projected_points: projection.sleeper_points || ""
      }];
    })
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points));
}

function buildMyLineup(input: {
  leagueId: string;
  rosterId: number;
  season: number;
  week: number;
  league: JsonMap;
  users: JsonMap[];
  rosters: JsonMap[];
  matchups: JsonMap[];
  players: Record<string, JsonMap>;
  projectionRows: JsonMap[];
}): JsonMap {
  const usersById = Object.fromEntries(input.users.map((user) => [String(user.user_id || ""), user]));
  const roster = input.rosters.find((row) => Number(row.roster_id) === input.rosterId) || {};
  const owner = objectValue(usersById[String(roster.owner_id || "")]);
  const matchup = input.matchups.find((row) => Number(row.roster_id) === input.rosterId) || {};
  const slots = starterSlots(input.league);
  const projectionsByPlayer = Object.fromEntries(input.projectionRows.map((row) => [String(row.player_id || ""), row]));
  const playerPoints = objectValue(matchup.players_points);
  const starterIds = listValue(matchup.starters).map((playerId) => String(playerId));
  const reserveIds = orderedPlayerIds(listValue(roster.reserve));
  const primaryPlayerIds = listValue(matchup.players).length
    ? listValue(matchup.players)
    : listValue(roster.players);
  const rosterPlayerIds = orderedPlayerIds([...primaryPlayerIds, ...listValue(roster.players), ...reserveIds]);
  const starterIdSet = new Set(starterIds);
  const reserveIdSet = new Set(reserveIds);
  const benchIds = rosterPlayerIds.filter((playerId) => !starterIdSet.has(playerId) && !reserveIdSet.has(playerId));
  const starters = starterIds.map((playerId, index) =>
    playerLineupSummary(playerId, {
      players: input.players,
      projectionsByPlayer,
      playerPoints,
      slot: slots[index] || `STARTER_${index + 1}`,
      lineupStatus: "starter"
    })
  );
  const bench = benchIds.map((playerId) =>
    playerLineupSummary(playerId, {
      players: input.players,
      projectionsByPlayer,
      playerPoints,
      slot: "BN",
      lineupStatus: "bench"
    })
  );
  const reserve = reserveIds
    .filter((playerId) => !starterIdSet.has(playerId))
    .map((playerId) =>
      playerLineupSummary(playerId, {
        players: input.players,
        projectionsByPlayer,
        playerPoints,
        slot: "IR",
        lineupStatus: "reserve"
      })
    );
  const lineupTable = [...starters, ...bench, ...reserve];
  const currentTotal = matchup.points || 0;
  const projectedStarterTotal = round(starters.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0), 2);
  const projectedTotal = round(lineupTable.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0), 2);

  return {
    league_id: input.leagueId,
    roster_id: input.rosterId,
    owner_id: roster.owner_id,
    team_name: ownerDisplayName(owner),
    season: input.season,
    week: input.week,
    lineup_found: Object.keys(matchup).length > 0,
    roster_slots: slots,
    starter_count: starters.length,
    bench_count: bench.length,
    active_bench_count: bench.length,
    reserve_count: reserve.length,
    current_total: currentTotal,
    points_so_far: currentTotal,
    projected_total: projectedTotal,
    projected_starter_total: projectedStarterTotal,
    projected_starter_points: projectedStarterTotal,
    bye_week_warnings: byePressureWarnings(lineupTable),
    lineup_table: lineupTable,
    starters,
    bench,
    reserve
  };
}

function buildLineupRecommendations(input: {
  lineup: JsonMap;
  rosters: JsonMap[];
  players: Record<string, JsonMap>;
  projectionRows: JsonMap[];
  addTrends: JsonMap[];
  dropTrends: JsonMap[];
  positions: string[];
  minDelta: number;
  limit: number;
}): JsonMap {
  const starters = arrayValue(input.lineup.starters).filter((row) => row.player_id !== "0");
  const bench = arrayValue(input.lineup.bench).filter((row) => row.player_id !== "0" && row.active_roster_spot !== false);
  const reserve = arrayValue(input.lineup.reserve).filter((row) => row.player_id !== "0");
  const rosterPlayers = [...starters, ...bench, ...reserve];
  const projectionCandidates = buildFreeAgentWatch(input.projectionRows, input.rosters, input.players, input.positions);
  const trendCandidates = buildWaiverWatch(input.addTrends, input.players, input.projectionRows, input.rosters, input.positions, "add");
  const availableCandidates = mergeAvailableCandidates({
    projectionCandidates,
    trendCandidates,
    players: input.players,
    addTrends: input.addTrends,
    dropTrends: input.dropTrends
  });
  const startSit = starters
    .flatMap((starter) => eligibleRosterRecommendations(starter, bench, input.minDelta))
    .sort((a, b) => sortNumber(b.projected_gain, a.projected_gain))
    .slice(0, input.limit);
  const waiverComparisons = availableCandidates
    .map((candidate) => compareAvailablePlayer(candidate, rosterPlayers))
    .filter((row) => (numberValue(row.projected_gain_over_drop) || 0) > 0)
    .sort((a, b) =>
      sortNumber(b.priority_score, a.priority_score)
      || sortNumber(b.projected_gain_over_drop, a.projected_gain_over_drop)
    )
    .slice(0, input.limit);
  const watchlist = availableCandidates
    .filter((candidate) => (numberValue(candidate.add_trend_count) || 0) > 0 || (numberValue(candidate.projected_points) || 0) > 0)
    .map((candidate) => watchlistRow(candidate))
    .sort((a, b) => sortNumber(b.priority_score, a.priority_score))
    .slice(0, input.limit);

  return {
    league_id: input.lineup.league_id,
    roster_id: input.lineup.roster_id,
    season: input.lineup.season,
    week: input.lineup.week,
    team_name: input.lineup.team_name,
    current_lineup: input.lineup,
    start_sit: startSit,
    waiver_comparisons: waiverComparisons,
    watchlist,
    evidence: [
      "starter and bench comparisons use projected_points under league scoring",
      "waiver comparisons exclude players already rostered in the league",
      "priority_score combines projection, projected roster gain, normalized add/drop momentum, and rostered percentage when present",
      "Sleeper player metadata does not always expose global rostered percentage"
    ]
  };
}

function buildInjuryWatch(users: JsonMap[], rosters: JsonMap[], players: Record<string, JsonMap>): JsonMap[] {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rows: JsonMap[] = [];
  for (const roster of rosters) {
    const owner = objectValue(usersById[String(roster.owner_id || "")]);
    for (const playerIdValue of listValue(roster.players)) {
      const playerId = String(playerIdValue);
      const player = objectValue(players[playerId]);
      if (!isInjuryRelevant(player)) {
        continue;
      }
      rows.push({
        roster_id: roster.roster_id,
        owner_id: roster.owner_id,
        team_name: ownerDisplayName(owner),
        player_id: playerId,
        name: playerName(player, playerId),
        team: String(player.team || ""),
        position: String(player.position || ""),
        status: String(player.status || ""),
        injury_status: String(player.injury_status || "")
      });
    }
  }
  return rows.sort((a, b) => `${a.team_name}${a.position}${a.name}`.localeCompare(`${b.team_name}${b.position}${b.name}`));
}

function buildOpponentWatch(
  rosterId: number,
  week: number,
  users: JsonMap[],
  rosters: JsonMap[],
  matchups: JsonMap[],
  players: Record<string, JsonMap>,
  projectionRows: JsonMap[]
): JsonMap {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rostersById = Object.fromEntries(rosters.map((roster) => [String(roster.roster_id || ""), roster]));
  const projectionsByPlayer = Object.fromEntries(projectionRows.map((row) => [String(row.player_id || ""), row]));
  const myMatchup = matchups.find((matchup) => Number(matchup.roster_id) === rosterId);
  if (!myMatchup) {
    return { roster_id: rosterId, week, opponent_found: false };
  }
  const matchupId = myMatchup.matchup_id;
  const opponentMatchup = matchups.find((matchup) => matchup.matchup_id === matchupId && Number(matchup.roster_id) !== rosterId);
  if (!opponentMatchup) {
    return { roster_id: rosterId, week, matchup_id: matchupId, opponent_found: false };
  }
  const opponentRosterId = Number(opponentMatchup.roster_id);
  const opponentRoster = objectValue(rostersById[String(opponentRosterId)]);
  const owner = objectValue(usersById[String(opponentRoster.owner_id || "")]);
  const starters = listValue(opponentMatchup.starters).map((playerId) =>
    playerProjectionSummary(String(playerId), players, projectionsByPlayer)
  );
  return {
    roster_id: rosterId,
    week,
    matchup_id: matchupId,
    opponent_found: true,
    opponent_roster_id: opponentRosterId,
    opponent_team_name: ownerDisplayName(owner),
    opponent_points_so_far: opponentMatchup.points || 0,
    opponent_projected_starter_points: round(starters.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0), 2),
    opponent_starters: starters,
    opponent_injuries: starters.filter((row) => row.injury_status || String(row.status || "").toLowerCase() !== "active")
  };
}

function buildLeagueTeamWatch(
  week: number,
  users: JsonMap[],
  rosters: JsonMap[],
  transactions: JsonMap[],
  players: Record<string, JsonMap>
): JsonMap[] {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rosterOwners = Object.fromEntries(rosters.map((roster) => [String(roster.roster_id || ""), usersById[String(roster.owner_id || "")]]));
  return transactions
    .filter((transaction) => transaction.status === "complete")
    .map((transaction) => {
      const adds = transactionPlayers(objectValue(transaction.adds), players, rosterOwners);
      const drops = transactionPlayers(objectValue(transaction.drops), players, rosterOwners);
      return {
        week,
        transaction_id: transaction.transaction_id,
        type: transaction.type,
        status: transaction.status,
        created: transaction.created,
        roster_ids: transaction.roster_ids || [],
        adds,
        drops,
        adds_summary: adds.map((player) => player.name).join(", "),
        drops_summary: drops.map((player) => player.name).join(", ")
      };
    })
    .sort((a, b) => sortNumber(b.created, a.created));
}

function transactionPlayers(
  playerToRoster: JsonMap,
  players: Record<string, JsonMap>,
  rosterOwners: Record<string, JsonMap>
): JsonMap[] {
  return Object.entries(playerToRoster).map(([playerId, rosterId]) => {
    const player = objectValue(players[playerId]);
    const owner = objectValue(rosterOwners[String(rosterId)]);
    return {
      player_id: playerId,
      name: playerName(player, playerId),
      team: String(player.team || ""),
      position: String(player.position || ""),
      roster_id: rosterId,
      team_name: ownerDisplayName(owner)
    };
  });
}

function topPlayersByPosition(rows: JsonMap[], limit: number): JsonMap[] {
  const grouped: Record<string, JsonMap[]> = {};
  for (const row of rows) {
    const position = String(row.position || "");
    if (!position) {
      continue;
    }
    grouped[position] = [...(grouped[position] || []), row];
  }
  const leaders: JsonMap[] = [];
  for (const [position, positionRows] of Object.entries(grouped)) {
    [...positionRows]
      .sort((a, b) => sortNumber(b.points, a.points))
      .slice(0, limit)
      .forEach((row, index) => leaders.push({ position_rank: index + 1, ...leaderRow(row) }));
  }
  return leaders.sort((a, b) => `${a.position}${a.position_rank}`.localeCompare(`${b.position}${b.position_rank}`));
}

function rankRowsByPosition(rows: JsonMap[]): JsonMap[] {
  const grouped: Record<string, JsonMap[]> = {};
  for (const row of rows) {
    const position = String(row.position || "");
    if (!position) {
      continue;
    }
    grouped[position] = [...(grouped[position] || []), row];
  }

  const rankedRows: JsonMap[] = [];
  for (const positionRows of Object.values(grouped)) {
    [...positionRows]
      .sort((a, b) => sortNumber(b.points, a.points))
      .forEach((row, index) => rankedRows.push({ position_rank: index + 1, ...row }));
  }
  return rankedRows;
}

function compareRankedWeeks(input: {
  previousWeek: number;
  currentWeek: number;
  previousRows: JsonMap[];
  currentRows: JsonMap[];
  limit: number;
}): JsonMap {
  const previousByKey = keyedPlayerRows(input.previousRows);
  const currentByKey = keyedPlayerRows(input.currentRows);
  const previousKeys = new Set(Object.keys(previousByKey));
  const currentKeys = new Set(Object.keys(currentByKey));
  const sharedKeys = [...currentKeys].filter((key) => previousKeys.has(key));
  const appearedKeys = [...currentKeys].filter((key) => !previousKeys.has(key));
  const disappearedKeys = [...previousKeys].filter((key) => !currentKeys.has(key));
  const movers = sharedKeys.map((key) => movementRow(previousByKey[key], currentByKey[key]));

  return {
    previous_week: input.previousWeek,
    current_week: input.currentWeek,
    top_risers: [...movers]
      .sort((a, b) => sortNumber(b.points_delta, a.points_delta) || sortNumber(b.current_points, a.current_points))
      .slice(0, input.limit),
    top_fallers: [...movers]
      .sort((a, b) => sortNumber(a.points_delta, b.points_delta) || sortNumber(a.current_points, b.current_points))
      .slice(0, input.limit),
    appeared: appearedKeys
      .map((key) => appearanceRow(currentByKey[key], true))
      .sort((a, b) =>
        String(a.position || "").localeCompare(String(b.position || ""))
        || sortNumber(a.current_rank, b.current_rank)
      )
      .slice(0, input.limit),
    disappeared: disappearedKeys
      .map((key) => appearanceRow(previousByKey[key], false))
      .sort((a, b) =>
        String(a.position || "").localeCompare(String(b.position || ""))
        || sortNumber(a.previous_rank, b.previous_rank)
      )
      .slice(0, input.limit)
  };
}

function keyedPlayerRows(rows: JsonMap[]): Record<string, JsonMap> {
  return Object.fromEntries(
    rows
      .filter((row) => row.position && row.player_id)
      .map((row) => [`${String(row.position)}:${String(row.player_id)}`, row])
  );
}

function movementRow(previous: JsonMap, current: JsonMap): JsonMap {
  const previousPoints = numberValue(previous.points) || 0;
  const currentPoints = numberValue(current.points) || 0;
  const previousRank = numberValue(previous.position_rank) || 0;
  const currentRank = numberValue(current.position_rank) || 0;
  return {
    player_id: current.player_id || "",
    name: current.name || "",
    team: current.team || "",
    position: current.position || "",
    previous_points: previousPoints,
    current_points: currentPoints,
    points_delta: round(currentPoints - previousPoints, 2),
    previous_rank: previousRank,
    current_rank: currentRank,
    rank_delta: previousRank - currentRank
  };
}

function appearanceRow(row: JsonMap, current: boolean): JsonMap {
  const output: JsonMap = {
    player_id: row.player_id || "",
    name: row.name || "",
    team: row.team || "",
    position: row.position || ""
  };
  if (current) {
    output.current_points = row.points || 0;
    output.current_rank = row.position_rank || "";
  } else {
    output.previous_points = row.points || 0;
    output.previous_rank = row.position_rank || "";
  }
  return output;
}

async function fetchRecentActuals(
  season: number,
  week: number,
  positions: string[],
  scoring: JsonMap | undefined,
  weeksBack: number,
  env: Env
): Promise<Record<string, JsonMap[]>> {
  if (weeksBack === 0) {
    return {};
  }
  const recentRows: Record<string, JsonMap[]> = {};
  for (let targetWeek = Math.max(1, week - weeksBack); targetWeek < week; targetWeek += 1) {
    const rows = await fetchRowsForPositions(season, targetWeek, positions, "stats", scoring, env);
    for (const row of rows) {
      const playerId = String(row.player_id || "");
      if (!playerId) {
        continue;
      }
      recentRows[playerId] = [...(recentRows[playerId] || []), { week: targetWeek, points: row.points || 0 }];
    }
  }
  return recentRows;
}

function enrichWaiverCandidates(
  candidates: JsonMap[],
  dropTrends: JsonMap[],
  recentRows: Record<string, JsonMap[]>
): JsonMap[] {
  const dropsByPlayer = Object.fromEntries(
    dropTrends.map((trend) => [String(trend.player_id || ""), numberValue(trend.count) || 0])
  );
  return candidates
    .map((row): JsonMap => {
      const playerId = String(row.player_id || "");
      const recentPoints = recentRows[playerId] || [];
      const recentAverage = recentPoints.length
        ? round(recentPoints.reduce((sum, item) => sum + (numberValue(item.points) || 0), 0) / recentPoints.length, 2)
        : 0;
      const dropCount = dropsByPlayer[playerId] || 0;
      const addCount = numberValue(row.trend_count) || 0;
      const projectedPoints = numberValue(row.projected_points) || 0;
      return {
        ...row,
        drop_trend_count: dropCount,
        net_trend_count: addCount - dropCount,
        recent_actual_points: recentPoints,
        recent_average_points: recentAverage,
        watch_score: round(projectedPoints + recentAverage + ((addCount - dropCount) / 100), 2)
      };
    })
    .sort((a, b) =>
      sortNumber(b.watch_score, a.watch_score)
      || sortNumber(b.projected_points, a.projected_points)
      || sortNumber(b.net_trend_count, a.net_trend_count)
    );
}

function starterSlots(league: JsonMap): string[] {
  const configured = listValue(league.roster_positions)
    .map((slot) => String(slot).toUpperCase())
    .filter((slot) => !NON_STARTER_SLOTS.has(slot));
  return configured.length ? configured : DEFAULT_STARTER_SLOTS;
}

function orderedPlayerIds(playerIds: unknown[]): string[] {
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const playerId of playerIds) {
    if (playerId === null || playerId === undefined) {
      continue;
    }
    const normalized = String(playerId);
    if (seen.has(normalized)) {
      continue;
    }
    ordered.push(normalized);
    seen.add(normalized);
  }
  return ordered;
}

function playerContext(
  playerId: string,
  input: {
    players: Record<string, JsonMap>;
    projection?: JsonMap;
    marketHint?: string;
  }
): JsonMap {
  const player = objectValue(input.players[playerId]);
  const projection = objectValue(input.projection);
  const byeWeek = firstPresent(player, "bye_week", "bye");
  const contextSources = ["sleeper_players", ...(Object.keys(projection).length ? ["sleeper_projections"] : [])];
  return {
    team: String(player.team || projection.team || ""),
    position: String(player.position || projection.position || ""),
    fantasy_positions: listValue(player.fantasy_positions),
    status: String(player.status || ""),
    injury_status: String(player.injury_status || ""),
    depth_chart_order: firstPresent(player, "depth_chart_order"),
    depth_chart_position: firstPresent(player, "depth_chart_position"),
    bye_week: byeWeek,
    market_type: normalizeMarketType(input.marketHint),
    context_sources: contextSources,
    source_metadata: {
      player_context: contextSources,
      market: KNOWN_MARKET_TYPES.has(String(input.marketHint || "")) && input.marketHint !== "unknown" ? "sleeper_explicit" : "unknown",
      bye_week: byeWeek === "" ? "missing" : "sleeper_players"
    }
  };
}

function firstPresent(row: JsonMap, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = row[key];
    if (value !== null && value !== undefined && value !== "") {
      return value;
    }
  }
  return "";
}

function inferMarketType(player: JsonMap, row: JsonMap, fallback: string): string {
  for (const source of [row, player]) {
    const explicit = firstPresent(source, "market_type", "acquisition_market", "acquisition_type");
    const normalized = normalizeMarketType(explicit);
    if (normalized !== "unknown") {
      return normalized;
    }
    if (source.waiver === true || source.is_waiver === true) {
      return "waiver";
    }
    if (source.waiver === false || source.is_waiver === false) {
      return "free_agent";
    }
  }
  return normalizeMarketType(fallback);
}

function normalizeMarketType(value: unknown): string {
  const normalized = String(value || "").trim().toLowerCase();
  if (["fa", "free-agent", "free agent", "free_agent"].includes(normalized)) return "free_agent";
  if (["waiver", "waivers", "claim"].includes(normalized)) return "waiver";
  if (KNOWN_MARKET_TYPES.has(normalized)) return normalized;
  return "unknown";
}

function playerLineupSummary(
  playerId: string,
  input: {
    players: Record<string, JsonMap>;
    projectionsByPlayer: Record<string, JsonMap>;
    playerPoints: JsonMap;
    slot: string;
    lineupStatus: string;
  }
): JsonMap {
  const projection = objectValue(input.projectionsByPlayer[playerId]);
  const actualPoints = input.playerPoints[playerId] || 0;
  const player = objectValue(input.players[playerId]);
  const activeRosterSpot = ["starter", "bench"].includes(input.lineupStatus);
  return {
    slot: input.slot,
    lineup_status: input.lineupStatus,
    player_id: playerId,
    name: playerName(player, playerId),
    ...playerContext(playerId, {
      players: input.players,
      projection,
      marketHint: inferMarketType(player, projection, "unknown")
    }),
    actual_points: actualPoints,
    points_so_far: actualPoints,
    projected_points: projection.points || 0,
    sleeper_projected_points: projection.sleeper_points || "",
    rostered_percent: rosteredPercent(player),
    active_roster_spot: activeRosterSpot,
    stash_value: input.lineupStatus === "reserve"
  };
}

function eligibleRosterRecommendations(starter: JsonMap, bench: JsonMap[], minDelta: number): JsonMap[] {
  const slot = String(starter.slot || "");
  const starterPoints = numberValue(starter.projected_points) || 0;
  const rows: JsonMap[] = [];
  for (const candidate of bench) {
    if (!isPlayerEligibleForSlot(candidate, slot)) {
      continue;
    }
    const candidatePoints = numberValue(candidate.projected_points) || 0;
    const projectedGain = round(candidatePoints - starterPoints, 2);
    if (projectedGain < minDelta) {
      continue;
    }
    rows.push({
      action: "start",
      slot,
      start_player_id: candidate.player_id,
      start_name: candidate.name,
      start_position: candidate.position,
      start_team: candidate.team,
      start_projected_points: candidatePoints,
      sit_player_id: starter.player_id,
      sit_name: starter.name,
      sit_position: starter.position,
      sit_team: starter.team,
      sit_projected_points: starterPoints,
      projected_gain: projectedGain,
      evidence: [
        `${String(candidate.name || "")} is eligible for ${slot}`,
        "recommendation is based on projected point delta"
      ]
    });
  }
  return rows;
}

function mergeAvailableCandidates(input: {
  projectionCandidates: JsonMap[];
  trendCandidates: JsonMap[];
  players: Record<string, JsonMap>;
  addTrends: JsonMap[];
  dropTrends: JsonMap[];
}): JsonMap[] {
  const addCounts = trendCounts(input.addTrends);
  const dropCounts = trendCounts(input.dropTrends);
  const byPlayer = Object.fromEntries(input.projectionCandidates.map((row) => [String(row.player_id || ""), { ...row }]));
  for (const row of input.trendCandidates) {
    const playerId = String(row.player_id || "");
    if (playerId) {
      byPlayer[playerId] = { ...objectValue(byPlayer[playerId]), ...row };
    }
  }

  return Object.entries(byPlayer).map(([playerId, row]) => {
    const player = objectValue(input.players[playerId]);
    const addCount = addCounts[playerId] || 0;
    const dropCount = dropCounts[playerId] || 0;
    const rosteredPct = rosteredPercent(player);
    const projectedPoints = numberValue(row.projected_points) || 0;
    let priorityScore = projectedPoints + trendPriorityBoost(addCount - dropCount);
    if (rosteredPct !== undefined) {
      priorityScore += rosteredPct / 20;
    }
    const marketType = inferMarketType(player, row, addCount ? "unknown" : "free_agent");
    return {
      ...row,
      add_trend_count: addCount,
      drop_trend_count: dropCount,
      net_trend_count: addCount - dropCount,
      rostered_percent: rosteredPct,
      market_type: marketType,
      acquisition_action: acquisitionAction(marketType, 0),
      priority_score: round(priorityScore, 2)
    };
  });
}

function compareAvailablePlayer(candidate: JsonMap, rosterPlayers: JsonMap[]): JsonMap {
  const [dropCandidate, dropReason, rejectedDrops] = bestDropCandidate(candidate, rosterPlayers);
  const projectedPoints = numberValue(candidate.projected_points) || 0;
  const dropPoints = numberValue(dropCandidate.projected_points) || 0;
  const projectedGain = round(projectedPoints - dropPoints, 2);
  const marketType = normalizeMarketType(candidate.market_type);
  const action = acquisitionAction(marketType, projectedGain);
  const byeWarnings = moveByeWarnings(candidate, dropCandidate, rosterPlayers);
  const priorityScore = round((numberValue(candidate.priority_score) || 0) + Math.max(projectedGain, 0) * 1.5 - (2 * byeWarnings.length), 2);
  const row: JsonMap = {
    action,
    acquisition_action: action,
    add_player_id: candidate.player_id,
    add_name: candidate.name,
    add_position: candidate.position,
    add_team: candidate.team,
    add_projected_points: projectedPoints,
    add_status: candidate.status || "",
    add_injury_status: candidate.injury_status || "",
    depth_chart_order: candidate.depth_chart_order || "",
    depth_chart_position: candidate.depth_chart_position || "",
    bye_week: candidate.bye_week || "",
    drop_player_id: dropCandidate.player_id || "",
    drop_name: dropCandidate.name || "",
    drop_position: dropCandidate.position || "",
    drop_team: dropCandidate.team || "",
    drop_lineup_status: dropCandidate.lineup_status || "",
    drop_projected_points: dropPoints,
    drop_reason: dropReason,
    drop_reasoning: dropReason,
    selected_drop_reasoning: dropReason,
    rejected_drop_reasoning: rejectedDrops,
    projected_gain_over_drop: projectedGain,
    market_type: marketType,
    add_trend_count: candidate.add_trend_count || 0,
    drop_trend_count: candidate.drop_trend_count || 0,
    net_trend_count: candidate.net_trend_count || 0,
    rostered_percent: candidate.rostered_percent,
    urgency: addUrgency(projectedGain, candidate),
    add_reasoning: addReasoning(candidate, projectedGain),
    bye_week_warnings: byeWarnings,
    source_metadata: candidate.source_metadata || {},
    priority_score: priorityScore
  };
  if (marketType === "waiver") {
    const faabHint = buildFaabHint(projectedGain, candidate);
    row.faab_bid_pct = faabHint.bid_pct;
    row.faab_tier = faabHint.tier;
    row.faab_reasoning = faabHint.reasoning;
  }
  return row;
}

function watchlistRow(candidate: JsonMap): JsonMap {
  return {
    player_id: candidate.player_id,
    name: candidate.name,
    team: candidate.team,
    position: candidate.position,
    projected_points: candidate.projected_points || 0,
    add_trend_count: candidate.add_trend_count || 0,
    drop_trend_count: candidate.drop_trend_count || 0,
    net_trend_count: candidate.net_trend_count || 0,
    rostered_percent: candidate.rostered_percent,
    market_type: candidate.market_type,
    acquisition_action: candidate.acquisition_action || acquisitionAction(candidate.market_type, 0),
    urgency: addUrgency(0, candidate),
    priority_score: candidate.priority_score || 0,
    status: candidate.status || "",
    injury_status: candidate.injury_status || "",
    depth_chart_order: candidate.depth_chart_order || "",
    depth_chart_position: candidate.depth_chart_position || "",
    bye_week: candidate.bye_week || "",
    source_metadata: candidate.source_metadata || {}
  };
}

function trendCounts(trends: JsonMap[]): Record<string, number> {
  return Object.fromEntries(
    trends
      .filter((row) => row.player_id)
      .map((row) => [String(row.player_id), numberValue(row.count) || 0])
  );
}

function samePositionFamily(left: JsonMap, right: JsonMap): boolean {
  const leftPosition = String(left.position || "").toUpperCase();
  const rightPosition = String(right.position || "").toUpperCase();
  if (leftPosition === rightPosition) {
    return true;
  }
  return ["RB", "WR", "TE"].includes(leftPosition) && ["RB", "WR", "TE"].includes(rightPosition);
}

function bestDropCandidate(candidate: JsonMap, rosterPlayers: JsonMap[]): [JsonMap, string, JsonMap[]] {
  const selectedPool: JsonMap[] = [];
  const rejected: JsonMap[] = [];
  for (const row of rosterPlayers) {
    const protectedReason = dropProtectionReason(row, rosterPlayers);
    if (protectedReason) {
      rejected.push({
        player_id: row.player_id || "",
        name: row.name || "",
        position: row.position || "",
        reason: protectedReason
      });
      continue;
    }
    selectedPool.push(row);
  }
  const dropCandidate = [...selectedPool]
    .sort((a, b) =>
      sortNumber(dropEaseScore(candidate, a, rosterPlayers), dropEaseScore(candidate, b, rosterPlayers))
      || sortNumber(a.projected_points, b.projected_points)
    )[0] || {};
  if (!Object.keys(dropCandidate).length) return [{}, "no unprotected drop candidate", rejected];
  if (samePositionFamily(candidate, dropCandidate)) {
    return [dropCandidate, "lowest risk active roster cut with comparable position coverage", rejected];
  }
  return [dropCandidate, "lowest risk active roster cut across positions", rejected];
}

function dropProtectionReason(row: JsonMap, rosterPlayers: JsonMap[]): string {
  if (!row.player_id || row.player_id === "0") return "placeholder roster row";
  if (row.active_roster_spot === false || String(row.lineup_status || "").toLowerCase() === "reserve") {
    return "reserve/IR stash does not consume an active bench spot";
  }
  if (String(row.lineup_status || "").toLowerCase() === "starter") return "current starter";
  const position = String(row.position || "").toUpperCase();
  if (!position) return "";
  const activePositionRows = rosterPlayers.filter(
    (player) => String(player.position || "").toUpperCase() === position && player.active_roster_spot !== false
  );
  const playableCount = activePositionRows.filter(
    (player) => (numberValue(player.projected_points) || 0) >= playableThreshold(position)
  ).length;
  if (playableCount <= desiredDepth(position) && (numberValue(row.projected_points) || 0) >= playableThreshold(position)) {
    return "last playable backup at position";
  }
  const riskyStarters = rosterPlayers.filter(
    (player) =>
      String(player.lineup_status || "").toLowerCase() === "starter"
      && samePositionFamily(row, player)
      && isAvailabilityRisk(player)
  );
  if (riskyStarters.length && (numberValue(row.projected_points) || 0) > 0) {
    return "coverage for questionable starter";
  }
  return "";
}

function dropEaseScore(candidate: JsonMap, row: JsonMap, rosterPlayers: JsonMap[]): number {
  let score = numberValue(row.projected_points) || 0;
  if (!samePositionFamily(candidate, row)) score += 1.5;
  const position = String(row.position || "").toUpperCase();
  const positionCount = rosterPlayers.filter(
    (player) => String(player.position || "").toUpperCase() === position && player.active_roster_spot !== false
  ).length;
  if (positionCount <= desiredDepth(position)) score += 4;
  return score;
}

function isPlayerEligibleForSlot(player: JsonMap, slot: string): boolean {
  const normalizedSlot = slot.toUpperCase();
  const positions = new Set(listValue(player.fantasy_positions).map((position) => String(position).toUpperCase()).filter(Boolean));
  if (player.position) {
    positions.add(String(player.position).toUpperCase());
  }
  const flexPositions = FLEX_SLOT_POSITIONS[normalizedSlot];
  if (flexPositions) {
    return flexPositions.some((position) => positions.has(position));
  }
  if (POSITION_SLOTS.has(normalizedSlot)) {
    return positions.has(normalizedSlot);
  }
  return positions.has(normalizedSlot);
}

function rosteredPercent(player: JsonMap): number | undefined {
  for (const key of ["rostered_percent", "rostered_pct", "percent_rostered", "percent_owned", "owned_percent"]) {
    const parsed = numberValue(player[key]);
    if (parsed !== undefined) {
      return round(parsed, 2);
    }
  }
  return undefined;
}

function faabBidPct(projectedGain: number, candidate: JsonMap): number {
  return numberValue(buildFaabHint(projectedGain, candidate).bid_pct) || 0;
}

function buildFaabHint(projectedGain: number, candidate: JsonMap): JsonMap {
  const netTrendCount = numberValue(candidate.net_trend_count) || 0;
  const rosteredPct = numberValue(candidate.rostered_percent) || 0;
  const projectedPoints = numberValue(candidate.projected_points) || 0;
  let bidPct = 0;
  let tier = "pass";
  if (projectedGain >= 8 || (projectedGain >= 5 && netTrendCount >= 1000)) {
    bidPct = 14;
    tier = "aggressive";
  } else if (projectedGain >= 4) {
    bidPct = 9;
    tier = "standard";
  } else if (projectedGain >= 1.5) {
    bidPct = 5;
    tier = "speculative";
  } else if (projectedGain > 0 || netTrendCount >= 1000 || rosteredPct >= 40) {
    bidPct = 2;
    tier = "watch";
  }

  const reasons = [projectedGain > 0
    ? `projects ${projectedGain.toFixed(2)} points above the drop candidate`
    : "does not project above the drop candidate"];
  if (netTrendCount > 0) reasons.push(`net add trend is +${netTrendCount}`);
  if (netTrendCount < 0) reasons.push(`net add trend is ${netTrendCount}`);
  if (rosteredPct) reasons.push(`rostered percentage signal is ${rosteredPct.toFixed(1)}`);
  if (projectedPoints <= 0) reasons.push("projection is currently zero");
  return { bid_pct: bidPct, tier, reasoning: reasons.join("; ") };
}

function groupWaiverOptionsByPosition(
  candidates: JsonMap[],
  rosterPlayers: JsonMap[],
  positions: string[],
  perPositionLimit: number
): JsonMap {
  const grouped: Record<string, JsonMap[]> = Object.fromEntries(positions.map((position) => [position, []]));
  for (const candidate of candidates) {
    const position = String(candidate.position || "").toUpperCase();
    if (!Object.hasOwn(grouped, position)) {
      continue;
    }
    const row = compareAvailablePlayer(candidate, rosterPlayers);
    if ((numberValue(row.projected_gain_over_drop) || 0) > 0) {
      grouped[position].push(row);
    }
  }
  return Object.fromEntries(Object.entries(grouped).map(([position, rows]) => [
    position,
    rows
      .sort((a, b) =>
        sortNumber(b.priority_score, a.priority_score)
        || sortNumber(b.projected_gain_over_drop, a.projected_gain_over_drop)
      )
      .slice(0, perPositionLimit)
  ]));
}

function acquisitionAction(marketType: unknown, projectedGain: number): string {
  const normalized = normalizeMarketType(marketType);
  if (projectedGain <= 0) return "watch";
  if (normalized === "waiver") return "submit_waiver_claim";
  if (normalized === "free_agent") return "add_now";
  return "watch";
}

function addUrgency(projectedGain: number, candidate: JsonMap): string {
  const netTrendCount = numberValue(candidate.net_trend_count) || 0;
  if (projectedGain >= 6 || netTrendCount >= 1500) return "high";
  if (projectedGain >= 2 || netTrendCount >= 250) return "medium";
  return "low";
}

function addReasoning(candidate: JsonMap, projectedGain: number): string {
  const reasons = [projectedGain > 0
    ? `projects ${projectedGain.toFixed(2)} points above the selected drop`
    : "does not project above an unprotected drop"];
  if (candidate.depth_chart_order !== undefined && candidate.depth_chart_order !== "") {
    reasons.push(`depth chart ${String(candidate.depth_chart_position || candidate.position || "")} ${String(candidate.depth_chart_order)}`);
  }
  if (candidate.injury_status) {
    reasons.push(`injury status is ${String(candidate.injury_status)}`);
  }
  return reasons.join("; ");
}

function isAvailabilityRisk(row: JsonMap): boolean {
  if (row.injury_status) return true;
  const status = String(row.status || "").trim().toLowerCase();
  return Boolean(status && status !== "active");
}

function byePressureWarnings(rows: JsonMap[]): JsonMap[] {
  const counts: Record<number, number> = {};
  for (const row of rows) {
    const bye = numberValue(row.bye_week);
    if (bye === undefined || row.active_roster_spot === false) {
      continue;
    }
    counts[bye] = (counts[bye] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([week, count]) => ({ week: Number(week), player_count: count }))
    .filter((row) => row.player_count >= 4)
    .sort((a, b) => sortNumber(a.week, b.week))
    .map((row) => ({
      ...row,
      severity: row.player_count >= 5 ? "high" : "medium",
      reason: `${row.player_count} active roster players share a bye week`
    }));
}

function moveByeWarnings(candidate: JsonMap, dropCandidate: JsonMap, rosterPlayers: JsonMap[]): JsonMap[] {
  const candidateBye = numberValue(candidate.bye_week);
  if (candidateBye === undefined) return [];
  const after = rosterPlayers.filter((row) => row.player_id !== dropCandidate.player_id);
  after.push(candidate);
  return byePressureWarnings(after).filter((warning) => warning.week === candidateBye);
}

function buildTradeOpportunities(input: {
  leagueId: string;
  rosterId: number;
  season: number;
  week: number;
  lineup: JsonMap;
  users: JsonMap[];
  rosters: JsonMap[];
  players: Record<string, JsonMap>;
  projectionRows: JsonMap[];
  positions: string[];
  targetsPerTeam: number;
  offersPerTeam: number;
}): JsonMap {
  const usersById = Object.fromEntries(input.users.map((user) => [String(user.user_id || ""), user]));
  const projectionsByPlayer = Object.fromEntries(input.projectionRows.map((row) => [String(row.player_id || ""), row]));
  const lineupRows = arrayValue(input.lineup.lineup_table).length
    ? arrayValue(input.lineup.lineup_table)
    : [...arrayValue(input.lineup.starters), ...arrayValue(input.lineup.bench)];
  const myRosterPlayers = lineupRows.filter((row) => String(row.player_id || "") !== "0");
  const myStarters = myRosterPlayers.filter((row) => String(row.lineup_status || "").toLowerCase() === "starter");
  const myBench = myRosterPlayers.filter((row) => String(row.lineup_status || "").toLowerCase() === "bench" && row.active_roster_spot !== false);
  const myOfferChips = tradeOfferChips(myRosterPlayers);
  const allowed = new Set(input.positions.map((position) => position.toUpperCase()));
  const myUpgradeSlots = myStarters
    .filter((row) => allowed.has(String(row.position || "").toUpperCase()))
    .sort((a, b) => sortNumber(a.projected_points, b.projected_points));

  const teams = input.rosters
    .filter((roster) => Number(roster.roster_id) !== input.rosterId)
    .map((roster) => {
      const owner = objectValue(usersById[String(roster.owner_id || "")]);
      const rosterRows = rosterProjectionRows(listValue(roster.players), input.players, projectionsByPlayer, input.positions);
      const needs = rosterNeeds(rosterRows, input.positions);
      const surplus = rosterSurplus(rosterRows, input.positions);
      const targets = tradeTargets(rosterRows, myUpgradeSlots, surplus, input.targetsPerTeam);
      const offerAngles: JsonMap[] = [];
      for (const target of targets) {
        offerAngles.push(...buildOfferAngles({
          target,
          myOfferChips,
          myBench,
          myRosterPlayers,
          opponentNeeds: needs,
          offersPerTeam: input.offersPerTeam
        }));
        if (offerAngles.length >= input.offersPerTeam) {
          break;
        }
      }
      return {
        roster_id: roster.roster_id,
        team_name: ownerDisplayName(owner),
        needs,
        surplus,
        targets,
        offer_angles: offerAngles
          .sort((a, b) =>
            sortNumber(b.trade_score, a.trade_score)
            || sortNumber(b.opponent_fit_score, a.opponent_fit_score)
            || sortNumber(b.my_gain, a.my_gain)
          )
          .slice(0, input.offersPerTeam),
        reasoning: tradeReasoning(needs, surplus, offerAngles)
      };
    });

  return {
    league_id: input.leagueId,
    roster_id: input.rosterId,
    team_name: input.lineup.team_name,
    season: input.season,
    week: input.week,
    teams,
    evidence: [
      "trade opportunities are projection-based screens, not trade value rankings",
      "each opposing roster is included even when no attractive offer angle is found",
      "offer angles must match an opponent need and prefer bench or surplus players before core starters",
      "projected lineup gain compares the target to the lowest projected comparable starter"
    ]
  };
}

function rosterProjectionRows(
  playerIds: unknown[],
  players: Record<string, JsonMap>,
  projectionsByPlayer: Record<string, JsonMap>,
  positions: string[]
): JsonMap[] {
  const allowed = new Set(positions.map((position) => position.toUpperCase()));
  return playerIds
    .map((playerId) => playerProjectionSummary(String(playerId), players, projectionsByPlayer))
    .filter((row) => row.position && allowed.has(String(row.position).toUpperCase()))
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points));
}

function rosterNeeds(rows: JsonMap[], positions: string[]): JsonMap[] {
  const needs: JsonMap[] = [];
  for (const position of positions) {
    const positionRows = rows.filter((row) => String(row.position || "").toUpperCase() === position);
    const topProjection = Math.max(0, ...positionRows.map((row) => numberValue(row.projected_points) || 0));
    const playableCount = positionRows.filter((row) => (numberValue(row.projected_points) || 0) >= playableThreshold(position)).length;
    if (playableCount < desiredDepth(position) || topProjection < playableThreshold(position)) {
      needs.push({
        position,
        playable_count: playableCount,
        top_projected_points: round(topProjection, 2),
        reason: "thin playable depth"
      });
    }
  }
  return needs;
}

function rosterSurplus(rows: JsonMap[], positions: string[]): JsonMap[] {
  const surplus: JsonMap[] = [];
  for (const position of positions) {
    const playable = rows.filter(
      (row) =>
        String(row.position || "").toUpperCase() === position
        && (numberValue(row.projected_points) || 0) >= playableThreshold(position)
    );
    if (playable.length > desiredDepth(position)) {
      surplus.push({
        position,
        playable_count: playable.length,
        top_names: playable.slice(0, 3).map((row) => row.name)
      });
    }
  }
  return surplus;
}

function tradeTargets(rosterRows: JsonMap[], myUpgradeSlots: JsonMap[], opponentSurplus: JsonMap[], targetsPerTeam: number): JsonMap[] {
  const surplusPositions = new Set(opponentSurplus.map((row) => String(row.position || "").toUpperCase()));
  return rosterRows
    .flatMap((player): JsonMap[] => {
      if (surplusPositions.size && !surplusPositions.has(String(player.position || "").toUpperCase())) {
        return [];
      }
      const replaced = comparableUpgradeSlot(player, myUpgradeSlots);
      if (!replaced) {
        return [];
      }
      const gain = round((numberValue(player.projected_points) || 0) - (numberValue(replaced.projected_points) || 0), 2);
      if (gain <= 0) {
        return [];
      }
      return [{
        ...player,
        projected_lineup_gain: gain,
        upgrade_over: {
          player_id: replaced.player_id,
          name: replaced.name,
          position: replaced.position,
          team: replaced.team,
          projected_points: replaced.projected_points,
          status: replaced.status || "",
          injury_status: replaced.injury_status || ""
        },
        opponent_surplus_position: surplusPositions.has(String(player.position || "").toUpperCase())
      }];
    })
    .sort((a, b) =>
      sortNumber(b.projected_lineup_gain, a.projected_lineup_gain)
      || sortNumber(b.projected_points, a.projected_points)
    )
    .slice(0, targetsPerTeam);
}

function comparableUpgradeSlot(target: JsonMap, myUpgradeSlots: JsonMap[]): JsonMap | undefined {
  const comparable = myUpgradeSlots.filter((row) => samePositionFamily(target, row));
  return [...comparable].sort((a, b) => sortNumber(a.projected_points, b.projected_points))[0];
}

function buildOfferAngles(input: {
  target: JsonMap;
  myOfferChips: JsonMap[];
  myBench: JsonMap[];
  myRosterPlayers: JsonMap[];
  opponentNeeds: JsonMap[];
  offersPerTeam: number;
}): JsonMap[] {
  const needPositions = new Set(input.opponentNeeds.map((row) => String(row.position || "").toUpperCase()));
  const angles: JsonMap[] = [];
  const direct = input.myOfferChips.filter(
    (chip) => needPositions.has(String(chip.position || "").toUpperCase()) && (numberValue(chip.projected_points) || 0) > 0
  );
  const packagePool = [...input.myBench]
    .filter((chip) => needPositions.has(String(chip.position || "").toUpperCase()) && (numberValue(chip.projected_points) || 0) > 0)
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points));

  for (const chip of direct.slice(0, input.offersPerTeam)) {
    angles.push(tradeAngle({
      target: input.target,
      offer: [chip],
      angleType: "need_fit",
      reasoning: `${String(chip.name || "")} addresses their ${String(chip.position || "")} need.`,
      opponentNeeds: input.opponentNeeds,
      myRosterPlayers: input.myRosterPlayers
    }));
  }
  if (packagePool.length >= 2 && new Set(packagePool.slice(0, 2).map((chip) => chip.player_id)).size === 2) {
    angles.push(tradeAngle({
      target: input.target,
      offer: packagePool.slice(0, 2),
      angleType: "need_fit_package",
      reasoning: "Package addresses an opponent need while consolidating your depth into a starter upgrade.",
      opponentNeeds: input.opponentNeeds,
      myRosterPlayers: input.myRosterPlayers
    }));
  }
  return angles
    .sort((a, b) => sortNumber(b.trade_score, a.trade_score))
    .slice(0, input.offersPerTeam);
}

function tradeAngle(input: {
  target: JsonMap;
  offer: JsonMap[];
  angleType: string;
  reasoning: string;
  opponentNeeds: JsonMap[];
  myRosterPlayers: JsonMap[];
}): JsonMap {
  const needPositions = new Set(input.opponentNeeds.map((row) => String(row.position || "").toUpperCase()));
  const matchedNeeds = [...new Set(input.offer
    .map((row) => String(row.position || "").toUpperCase())
    .filter((position) => needPositions.has(position)))]
    .sort();
  const myGain = numberValue(input.target.projected_lineup_gain) || 0;
  const opponentFitScore = opponentTradeFitScore(input.offer, matchedNeeds);
  const backupRisk = tradeBackupRisk(input.offer, input.myRosterPlayers);
  const byeWeekRisk = tradeByeWeekRisk(input.target, input.offer, input.myRosterPlayers);
  const rosterBalanceAfter = rosterBalanceAfterTrade(input.target, input.offer, input.myRosterPlayers);
  const tradeScore = round(
    myGain * 10
    + opponentFitScore
    - (numberValue(backupRisk.penalty) || 0)
    - (numberValue(byeWeekRisk.penalty) || 0)
    - (numberValue(rosterBalanceAfter.penalty) || 0),
    2
  );
  return {
    angle_type: input.angleType,
    ask_for: input.target,
    offer: input.offer.map((row) => ({
      player_id: row.player_id,
      name: row.name,
      position: row.position,
      team: row.team,
      projected_points: row.projected_points,
      status: row.status || "",
      injury_status: row.injury_status || ""
    })),
    offer_projected_points: round(input.offer.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0), 2),
    projected_lineup_gain: input.target.projected_lineup_gain || 0,
    my_gain: myGain,
    opponent_fit_score: opponentFitScore,
    opponent_need_matched: matchedNeeds,
    backup_risk: backupRisk,
    bye_week_risk: byeWeekRisk,
    roster_balance_after: rosterBalanceAfter,
    trade_score: tradeScore,
    reasoning: input.reasoning
  };
}

function tradeOfferChips(rosterPlayers: JsonMap[]): JsonMap[] {
  const byPosition: Record<string, JsonMap[]> = {};
  for (const row of rosterPlayers) {
    const position = String(row.position || "").toUpperCase();
    byPosition[position] = [...(byPosition[position] || []), row];
  }
  const chips: JsonMap[] = [];
  for (const [position, rows] of Object.entries(byPosition)) {
    [...rows]
      .sort((a, b) => sortNumber(b.projected_points, a.projected_points))
      .slice(desiredDepth(position))
      .filter((row) => (numberValue(row.projected_points) || 0) > 0 && !dropProtectionReason(row, rosterPlayers))
      .forEach((row) => chips.push(row));
  }
  rosterPlayers
    .filter((row) =>
      String(row.lineup_status || "").toLowerCase() === "bench"
      && (numberValue(row.projected_points) || 0) > 0
      && !dropProtectionReason(row, rosterPlayers)
      && !chips.includes(row)
    )
    .forEach((row) => chips.push(row));
  return chips.sort((a, b) => sortNumber(b.projected_points, a.projected_points));
}

function opponentTradeFitScore(offer: JsonMap[], opponentNeedMatched: string[]): number {
  const offerPoints = offer.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0);
  return round(opponentNeedMatched.length * 35 + Math.min(offerPoints, 30), 2);
}

function tradeBackupRisk(offer: JsonMap[], rosterPlayers: JsonMap[]): JsonMap {
  const reasons: string[] = [];
  let penalty = 0;
  for (const row of offer) {
    const reason = dropProtectionReason(row, rosterPlayers);
    if (reason) {
      reasons.push(`${String(row.name || "")} is protected: ${reason}`);
      penalty += 25;
    }
    const position = String(row.position || "").toUpperCase();
    const remainingPlayable = rosterPlayers.filter(
      (player) =>
        player.player_id !== row.player_id
        && String(player.position || "").toUpperCase() === position
        && player.active_roster_spot !== false
        && (numberValue(player.projected_points) || 0) >= playableThreshold(position)
    );
    if (remainingPlayable.length < desiredDepth(position)) {
      reasons.push(`${position} depth would fall below desired playable coverage`);
      penalty += 10;
    }
  }
  return {
    level: penalty >= 25 ? "high" : penalty ? "medium" : "low",
    penalty,
    reasons
  };
}

function tradeByeWeekRisk(target: JsonMap, offer: JsonMap[], rosterPlayers: JsonMap[]): JsonMap {
  const outgoingIds = new Set(offer.map((row) => row.player_id));
  const after = rosterPlayers.filter((row) => !outgoingIds.has(row.player_id));
  after.push(target);
  const counts: Record<number, number> = {};
  for (const row of after) {
    const bye = numberValue(row.bye_week);
    if (bye === undefined || row.active_roster_spot === false) continue;
    counts[bye] = (counts[bye] || 0) + 1;
  }
  const clusteredByes = Object.fromEntries(
    Object.entries(counts).filter(([, count]) => count >= 4)
  );
  const penalty = Object.values(clusteredByes).reduce((sum, count) => sum + (Number(count) - 3) * 4, 0);
  return {
    level: penalty ? "medium" : "low",
    penalty,
    clustered_byes: clusteredByes,
    incoming_bye_week: target.bye_week || "",
    outgoing_bye_weeks: offer.map((row) => row.bye_week || "")
  };
}

function rosterBalanceAfterTrade(target: JsonMap, offer: JsonMap[], rosterPlayers: JsonMap[]): JsonMap {
  const outgoingIds = new Set(offer.map((row) => row.player_id));
  const counts: Record<string, number> = {};
  for (const row of [...rosterPlayers.filter((player) => !outgoingIds.has(player.player_id)), target]) {
    const position = String(row.position || "").toUpperCase();
    if (!position) continue;
    counts[position] = (counts[position] || 0) + 1;
  }
  const warnings: string[] = [];
  let penalty = 0;
  for (const [position, count] of Object.entries(counts)) {
    if (count < desiredDepth(position)) {
      warnings.push(`${position} depth below desired roster balance`);
      penalty += 8;
    }
  }
  return {
    position_counts: Object.fromEntries(Object.entries(counts).sort(([left], [right]) => left.localeCompare(right))),
    warnings,
    penalty
  };
}

function tradeReasoning(needs: JsonMap[], surplus: JsonMap[], offerAngles: JsonMap[]): string[] {
  const reasons: string[] = [];
  if (needs.length) {
    reasons.push(`Needs: ${needs.slice(0, 3).map((row) => `${String(row.position)} depth`).join(", ")}`);
  }
  if (surplus.length) {
    reasons.push(`Surplus: ${surplus.slice(0, 3).map((row) => `${String(row.position)} depth`).join(", ")}`);
  }
  reasons.push(offerAngles.length
    ? "At least one offer angle matches an opponent need and creates a projected lineup upgrade for you."
    : "No clear mutual-fit offer angle from current roster depth.");
  return reasons;
}

function playableThreshold(position: string): number {
  return {
    QB: 14,
    RB: 8,
    WR: 8,
    TE: 6,
    K: 5,
    DEF: 5
  }[position.toUpperCase()] || 6;
}

function desiredDepth(position: string): number {
  return {
    QB: 1,
    RB: 3,
    WR: 4,
    TE: 1,
    K: 1,
    DEF: 1
  }[position.toUpperCase()] || 1;
}

function trendPriorityBoost(netTrendCount: number): number {
  if (netTrendCount === 0) {
    return 0;
  }
  const direction = netTrendCount > 0 ? 1 : -1;
  return round(direction * Math.log10(Math.abs(netTrendCount) + 1), 2);
}

function leaderRow(row: JsonMap): JsonMap {
  const leader: JsonMap = {
    player_id: row.player_id || "",
    name: row.name || "",
    team: row.team || "",
    position: row.position || "",
    points: row.points || 0
  };
  if ("sleeper_points" in row) {
    leader.sleeper_points = row.sleeper_points;
  }
  if ("scoring_rules_matched" in row) {
    leader.scoring_rules_matched = row.scoring_rules_matched;
  }
  return leader;
}

function playerProjectionSummary(playerId: string, players: Record<string, JsonMap>, projectionsByPlayer: Record<string, JsonMap>): JsonMap {
  const player = objectValue(players[playerId]);
  const projection = objectValue(projectionsByPlayer[playerId]);
  return {
    player_id: playerId,
    name: playerName(player, playerId),
    ...playerContext(playerId, {
      players,
      projection,
      marketHint: inferMarketType(player, projection, "unknown")
    }),
    projected_points: projection.points || 0,
  };
}

function rosteredPlayerIds(rosters: JsonMap[]): Set<string> {
  const playerIds = new Set<string>();
  for (const roster of rosters) {
    for (const playerId of listValue(roster.players)) {
      if (playerId !== null && playerId !== undefined) {
        playerIds.add(String(playerId));
      }
    }
  }
  return playerIds;
}

function findPlayerRow(rows: JsonMap[], playerId: string): JsonMap | undefined {
  return rows.find((row) => String(row.player_id || "") === playerId);
}

async function getPlayers(env: Env): Promise<Record<string, JsonMap>> {
  return recordValue(await getApp("/players/nfl", env));
}

async function getTrending(trendType: string, lookbackHours: number, limit: number, env: Env): Promise<JsonMap[]> {
  return arrayValue(await getApp(`/players/nfl/trending/${trendType}`, env, {
    lookback_hours: String(lookbackHours),
    limit: String(limit)
  }));
}

async function getApp(path: string, env: Env, params: Record<string, string> = {}): Promise<unknown> {
  return getJson(`${APP_BASE_URL}${path}`, params, env);
}

async function getData(path: string, env: Env, params: Record<string, string> = {}): Promise<unknown> {
  return getJson(`${DATA_BASE_URL}${path}`, params, env);
}

async function getJson(rawUrl: string, params: Record<string, string>, env: Env): Promise<unknown> {
  const url = new URL(rawUrl);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.append(key, value);
  }
  const cacheKey = url.toString();
  const cached = await cacheGet(cacheKey, env);
  if (cached !== undefined) {
    return cached;
  }

  const response = await fetch(url, {
    headers: {
      "Accept": "application/json",
      "User-Agent": "sleeper-mcp-worker/0.1"
    }
  });
  if (!response.ok) {
    throw new Error(`Sleeper API request failed: ${response.status} ${url}`);
  }
  const payload = await response.json();
  await cacheSet(cacheKey, payload, ttlForUrl(cacheKey), env);
  return payload;
}

async function cacheGet(cacheKey: string, env: Env): Promise<unknown | undefined> {
  if (!env.SLEEPER_CACHE_DB) {
    return undefined;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const row = await env.SLEEPER_CACHE_DB.prepare(
    "SELECT response_json FROM api_response_cache WHERE cache_key = ? AND expires_at > ?"
  ).bind(cacheKey, nowSeconds()).first<{ response_json: string }>();
  return row ? JSON.parse(row.response_json) : undefined;
}

async function cacheSet(cacheKey: string, payload: unknown, ttlSeconds: number, env: Env): Promise<void> {
  if (!env.SLEEPER_CACHE_DB) {
    return;
  }
  const responseJson = JSON.stringify(payload);
  if (responseJson.length > MAX_D1_RESPONSE_BYTES) {
    return;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const now = nowSeconds();
  await env.SLEEPER_CACHE_DB.prepare(
    "INSERT OR REPLACE INTO api_response_cache (cache_key, url, response_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?)"
  ).bind(cacheKey, cacheKey, responseJson, now + ttlSeconds, now).run();
}

async function ensureCacheTable(db: D1Database): Promise<void> {
  await db.prepare(
    "CREATE TABLE IF NOT EXISTS api_response_cache (cache_key TEXT PRIMARY KEY, url TEXT NOT NULL, response_json TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)"
  ).run();
}

function ttlForUrl(url: string): number {
  if (url.includes("/state/nfl")) return 300;
  if (url.includes("/trending/")) return 300;
  if (url.includes("/players/nfl")) return 21600;
  if (url.includes("/stats/") || url.includes("/projections/")) return 900;
  if (url.includes("/matchups/") || url.includes("/transactions/")) return 900;
  return 3600;
}

function optionalLeagueId(args: JsonMap, env: Env): string | undefined {
  const value = String(args.league_id || env.SLEEPER_DEFAULT_LEAGUE_ID || "").trim();
  return value || undefined;
}

function requireLeagueId(args: JsonMap, env: Env): string {
  const value = optionalLeagueId(args, env);
  if (!value) {
    throw new Error("league_id is required; pass league_id or set SLEEPER_DEFAULT_LEAGUE_ID");
  }
  return value;
}

function requireRosterId(args: JsonMap, env: Env): number {
  const value = numberValue(args.roster_id ?? env.SLEEPER_DEFAULT_ROSTER_ID);
  if (value === undefined) {
    throw new Error("roster_id is required; pass roster_id or set SLEEPER_DEFAULT_ROSTER_ID");
  }
  return value;
}

function extractLeagueId(leagueRef: string): string {
  const value = leagueRef.trim();
  if (/^\d+$/.test(value)) {
    return value;
  }
  try {
    const parsed = new URL(value);
    const pathLeagueId = parsed.pathname.split("/").find((segment) => /^\d+$/.test(segment));
    if (pathLeagueId) {
      return pathLeagueId;
    }
  } catch {
    // Fall back to scanning below.
  }
  const match = value.match(/\b\d{8,}\b/);
  if (match) {
    return match[0];
  }
  throw new Error("Could not find a Sleeper league_id in the provided league URL or value.");
}

function resolveLeagueContextFromRows(
  leagueId: string,
  teamName: string,
  users: JsonMap[],
  rosters: JsonMap[]
): JsonMap {
  const query = normalizeForMatch(teamName);
  if (!query) {
    throw new Error("team_name must not be empty");
  }
  const candidates = rosters.map((roster) =>
    buildLeagueContextCandidate(leagueId, roster, userForRoster(users, roster))
  );

  const exactMatches = findLeagueContextMatches(candidates, query, "exact");
  if (exactMatches.length === 1) {
    return withLeagueContextEnv(exactMatches[0]);
  }
  if (exactMatches.length > 1) {
    throw new Error(ambiguousLeagueContextMessage(teamName, exactMatches));
  }

  const partialMatches = findLeagueContextMatches(candidates, query, "partial");
  if (partialMatches.length === 1) {
    return withLeagueContextEnv(partialMatches[0]);
  }
  if (partialMatches.length > 1) {
    throw new Error(ambiguousLeagueContextMessage(teamName, partialMatches));
  }

  throw new Error(
    `No roster matched ${JSON.stringify(teamName)}. Available teams: ${candidates.map(candidateLabel).join(", ")}`
  );
}

function buildLeagueContextCandidate(leagueId: string, roster: JsonMap, user: JsonMap): JsonMap {
  const userMetadata = objectValue(user.metadata);
  const rosterMetadata = objectValue(roster.metadata);
  const ownerId = stringValue(roster.owner_id);
  const userId = stringValue(user.user_id) || ownerId;
  return {
    league_id: leagueId,
    roster_id: roster.roster_id,
    owner_id: ownerId,
    user_id: userId,
    display_name: stringValue(user.display_name),
    username: stringValue(user.username),
    team_name: stringValue(userMetadata.team_name) || stringValue(rosterMetadata.team_name),
    matched_on: "",
    match_value: ""
  };
}

function findLeagueContextMatches(candidates: JsonMap[], query: string, mode: "exact" | "partial"): JsonMap[] {
  const fields = ["team_name", "display_name", "username", "owner_id", "user_id", "roster_id"];
  const matches: JsonMap[] = [];
  for (const candidate of candidates) {
    for (const field of fields) {
      const rawValue = candidate[field];
      const normalizedValue = normalizeForMatch(String(rawValue || ""));
      if (!normalizedValue) {
        continue;
      }
      if (mode === "exact" && normalizedValue !== query) {
        continue;
      }
      if (mode === "partial" && !normalizedValue.includes(query)) {
        continue;
      }
      matches.push({ ...candidate, matched_on: field, match_value: rawValue });
      break;
    }
  }
  return matches;
}

function userForRoster(users: JsonMap[], roster: JsonMap): JsonMap {
  const ownerId = String(roster.owner_id || "");
  return users.find((user) => String(user.user_id || "") === ownerId) || {};
}

function withLeagueContextEnv(context: JsonMap): JsonMap {
  return {
    ...context,
    env: {
      SLEEPER_DEFAULT_LEAGUE_ID: String(context.league_id || ""),
      SLEEPER_DEFAULT_ROSTER_ID: String(context.roster_id || ""),
      SLEEPER_DEFAULT_OWNER_ID: String(context.owner_id || "")
    }
  };
}

function renderContextEnv(context: JsonMap, leagueRef: string): string {
  const lines = [
    "# Generated by sleeper resolve_league_context.",
    `SLEEPER_DEFAULT_LEAGUE_URL=${envValue(leagueRef)}`,
    `SLEEPER_DEFAULT_LEAGUE_ID=${String(context.league_id || "")}`,
    `SLEEPER_DEFAULT_ROSTER_ID=${String(context.roster_id || "")}`
  ];
  if (context.owner_id) {
    lines.push(`SLEEPER_DEFAULT_OWNER_ID=${String(context.owner_id)}`);
  }
  if (context.team_name) {
    lines.push(`SLEEPER_DEFAULT_TEAM_NAME=${envValue(context.team_name)}`);
  }
  if (context.display_name) {
    lines.push(`SLEEPER_DEFAULT_DISPLAY_NAME=${envValue(context.display_name)}`);
  }
  if (context.username) {
    lines.push(`SLEEPER_DEFAULT_USERNAME=${envValue(context.username)}`);
  }
  return `${lines.join("\n")}\n`;
}

function ambiguousLeagueContextMessage(teamName: string, matches: JsonMap[]): string {
  return (
    `Multiple rosters matched ${JSON.stringify(teamName)}: `
    + `${matches.map(candidateLabel).join(", ")}. `
    + "Use a more specific team name, display name, roster_id, or owner_id."
  );
}

function candidateLabel(candidate: JsonMap): string {
  return (
    `roster_id=${String(candidate.roster_id || "")} `
    + `team=${JSON.stringify(candidate.team_name || "(no team name)")} `
    + `display=${JSON.stringify(candidate.display_name || "(no display name)")}`
  );
}

function normalizeForMatch(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function envValue(value: unknown): string {
  return `"${String(value)
    .replace(/\\/g, "\\\\")
    .replace(/"/g, "\\\"")
    .replace(/\$/g, "\\$")
    .replace(/`/g, "\\`")}"`;
}

function withContext(rows: JsonMap[], context: JsonMap): JsonMap[] {
  return rows.map((row) => ({ ...context, ...row }));
}

function ownerDisplayName(user: JsonMap): string {
  const metadata = objectValue(user.metadata);
  return String(metadata.team_name || user.display_name || user.username || "Unknown");
}

function playerName(player: JsonMap, playerId: string): string {
  if (typeof player.full_name === "string" && player.full_name) {
    return player.full_name;
  }
  const name = `${String(player.first_name || "")} ${String(player.last_name || "")}`.trim();
  return name || playerId;
}

function isInjuryRelevant(player: JsonMap): boolean {
  if (player.injury_status) {
    return true;
  }
  const status = String(player.status || "");
  return Boolean(status && status.toLowerCase() !== "active");
}

function parsePositions(value: string): string[] {
  return value.split(",").map((position) => position.trim().toUpperCase()).filter(Boolean);
}

function stringArg(args: JsonMap, key: string, fallback: string): string {
  return typeof args[key] === "string" && args[key] ? String(args[key]) : fallback;
}

function stringValue(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function numberArg(args: JsonMap, key: string, fallback: number): number {
  return numberValue(args[key]) ?? fallback;
}

function validateStatSource(source: string): void {
  if (!["stats", "projections"].includes(source)) {
    throw new Error("source must be 'stats' or 'projections'");
  }
}

function objectValue(value: unknown): JsonMap {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonMap : {};
}

function recordValue(value: unknown): Record<string, JsonMap> {
  const record = objectValue(value);
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [key, objectValue(item)]));
}

function arrayValue(value: unknown): JsonMap[] {
  return Array.isArray(value) ? value.map((item) => objectValue(item)) : [];
}

function listValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberValue(value: unknown): number | undefined {
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

function sortNumber(left: unknown, right: unknown): number {
  return (numberValue(left) || 0) - (numberValue(right) || 0);
}

function round(value: number, digits: number): number {
  const multiplier = 10 ** digits;
  return Math.round(value * multiplier) / multiplier;
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function currentSeasonYear(): number {
  return new Date().getFullYear();
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
